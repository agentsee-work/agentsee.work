# Backups

The datastore holds mail, accounts, configuration and the DKIM private key. It
is **the only thing on the box that cannot be rebuilt from this repo.**

```
backup.sh                  nightly: stop, restic, start, prune, check
restore-test.sh            prove a restore actually boots. quarterly, and before cutover
stalwart-backup.service    systemd unit
stalwart-backup.timer      03:20 nightly, jittered
```

## The one secret that matters most

**`RESTIC_PASSWORD` encrypts the repository. Lose it and every backup is
unrecoverable ciphertext** — R2 will happily keep bytes you can never read.

It is more important than the server, the mail, or any API token, because
everything else can be rebuilt and this cannot. It belongs in the vault
(`infra` collection, [../../docs/CREDENTIALS.md](../../docs/CREDENTIALS.md))
**and** on paper, offline, with the vault's own recovery codes.

Storing it only in the vault creates a loop: if the vault is what you lost, the
backups are gone too.

## Setup

```sh
# On the box
install -d -m 0700 /etc/agentsee
install -d -m 0700 /opt/agentsee/stalwart/backup

# Repo passphrase — generate, then record it in the vault AND on paper
openssl rand -base64 32 > /etc/agentsee/restic-password
chmod 0400 /etc/agentsee/restic-password

cat > /etc/agentsee/backup.env <<'EOF'
RESTIC_REPOSITORY=s3:https://<account>.r2.cloudflarestorage.com/agentsee-mail-backup
RESTIC_PASSWORD_FILE=/etc/agentsee/restic-password
AWS_ACCESS_KEY_ID=<r2-token-id>
AWS_SECRET_ACCESS_KEY=<r2-token-secret>
HEALTHCHECK_URL=https://hc-ping.com/<uuid>
EOF
chmod 0600 /etc/agentsee/backup.env

set -a; . /etc/agentsee/backup.env; set +a
restic init

systemctl enable --now stalwart-backup.timer
systemctl start stalwart-backup.service   # don't wait until 03:20 to find a typo
journalctl -u stalwart-backup -f
```

Use a **separate R2 bucket and a separate token** from the OpenTofu state. One
credential that can both delete your backups and rewrite your infrastructure is
a bad blast radius for a token sitting on an internet-facing box.

## Why it stops the server

RocksDB is being written to. Copying its files live can capture an inconsistent
set, and an inconsistent RocksDB restores into a corrupt database — which you
discover during the incident, when it is worth the most.

So: stop, copy cold, start. Downtime is seconds, and sending servers retry for
days. Nobody notices. The script restarts the container from a trap on **every**
exit path, so a failed backup cannot leave mail down.

## `restic check` is not a restore test

`backup.sh` runs `restic check` nightly, which verifies the repository is
internally coherent. It says nothing about whether the contents restore into a
working mail server.

`restore-test.sh` answers that, and it is the only thing that does. It restores
the latest snapshot to scratch, asserts the RocksDB `CURRENT` and `MANIFEST`
files exist — their absence means we have been backing up the wrong path, the
exact silent failure this guards against — and then **boots a throwaway server
against the restored data** and checks it responds without logging corruption.
The live server is untouched throughout.

```sh
set -a; . /etc/agentsee/backup.env; set +a
/opt/agentsee/stalwart/backup/restore-test.sh
```

**Before cutover, then quarterly.** A first restore attempted after real mail
exists is not a test, it is an incident. If it fails and isn't fixed that week,
[MAIL-SELFHOST.md](../../docs/MAIL-SELFHOST.md) says move to a hosted provider —
that criterion exists so the decision isn't taken mid-outage.

## Don't alert about mail by email

If the mail server is the thing that's broken, an email alert is a message you
will never receive. The failure mode is silence, which reads exactly like
success.

`HEALTHCHECK_URL` is a dead-man's switch: the script pings on success and
`/fail` on failure, and the external service alerts when a ping *doesn't*
arrive. That catches the cases a script-based alert cannot — the box being off,
the disk being full, the timer never firing. healthchecks.io's free tier covers
this; so would a Cloudflare Worker with a cron trigger.
