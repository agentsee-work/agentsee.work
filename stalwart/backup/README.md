# Backups

The datastore holds mail, accounts, configuration and the DKIM private key. It
is **the only thing on the box that cannot be rebuilt from this repo.**

```
backup.sh                  nightly: stop, restic ×2, start, prune, check
restore-test.sh            prove a restore actually boots. quarterly, and before cutover
stalwart-backup.service    systemd unit
stalwart-backup.timer      03:20 nightly, jittered
```

## Two repositories, at two companies

| | Where | Why |
|---|---|---|
| **Primary** | Infomaniak Swiss Backup | 1 TB, already bought, and restic is documented against it |
| **Secondary** | Cloudflare R2 | So that no single account holds both the server and its backups |

The server runs on Infomaniak. Putting its only backup there too means one
suspended account, one billing failure, one compromised login takes the mail
server and every copy of the mail in the same instant — which is precisely the
event a backup exists for. That is this repo's recurring rule again: no recovery
path may run through the thing it recovers.

R2's free tier is 10 GB-month with no egress charges, and this repository sits
well under it. The second copy is therefore approximately free, which is why it
is not a trade-off worth agonising over.

**Two independent `restic backup` passes, not `restic copy`.** Mirroring one
repository into the other would be tidier, and it does not work: both are S3 and
restic reads `AWS_ACCESS_KEY_ID` once per process, so it cannot hold two sets of
credentials at once. Two passes also give genuinely independent repositories
rather than a mirror that can faithfully replicate a problem.

Both passes run while the container is stopped. Backing the secondary up after
restarting would make it a hot copy of a live RocksDB — the exact thing the stop
exists to prevent, in the copy you would reach for on the worst day.

### What two copies does not protect against

**Both credentials live on this box.** A compromised server can delete both
repositories, and Infomaniak's object-lock immutability is Acronis-only — it is
explicitly unavailable on the S3 and Swift protocols restic uses. So the second
copy defends against *losing the Infomaniak account*, not against someone owning
the box.

Closing that would mean pulling backups from elsewhere rather than pushing them
from here, or a `rest-server` in append-only mode. Both are more machinery than
two people's mail currently justifies. Recorded so nobody mistakes two copies
for two kinds of protection.

### Each gets its own dead-man's switch

`HEALTHCHECK_URL` and `SECONDARY_HEALTHCHECK_URL` are separate on purpose. One
shared switch would go on reporting healthy while the secondary silently failed
for months, and "we have two copies" would be false in the way that is only ever
discovered when both are needed.

A failing secondary is **not** fatal to the run. A secondary that cannot be
written is bad; a primary reported as broken because of it is worse, and mail
staying down over it is worse still.

## The one secret that matters most

**`RESTIC_PASSWORD` encrypts the repository. Lose it and every backup is
unrecoverable ciphertext** — R2 will happily keep bytes you can never read.

It is more important than the server, the mail, or any API token, because
everything else can be rebuilt and this cannot. It belongs in the vault
(the **Security** vault, [../../docs/CREDENTIALS.md](../../docs/CREDENTIALS.md))
**and** on paper, offline, with the vault's own recovery codes.

Storing it only in the vault creates a loop: if the vault is what you lost, the
backups are gone too.

It is in Security rather than Engineering because that vault is specifically for
things whose loss cannot be undone. A leaked R2 token is a bad afternoon and you
mint another; this one has no replacement.

## Setup

```sh
# On the box
install -d -m 0700 /etc/agentsee
install -d -m 0700 /opt/agentsee/stalwart/backup

# Repo passphrase — generate, then record it in the vault AND on paper
openssl rand -base64 32 > /etc/agentsee/restic-password
chmod 0400 /etc/agentsee/restic-password

cat > /etc/agentsee/backup.env <<'EOF'
# Primary — Infomaniak Swiss Backup. The 0N is your cluster; it is in the
# credentials email, and guessing it gives a confusing connection error.
RESTIC_REPOSITORY=s3:https://s3.swiss-backup0N.infomaniak.com/<bucket>
RESTIC_PASSWORD_FILE=/etc/agentsee/restic-password
AWS_ACCESS_KEY_ID=<swiss-backup-key>
AWS_SECRET_ACCESS_KEY=<swiss-backup-secret>
HEALTHCHECK_URL=https://hc-ping.com/<uuid>

# Secondary — Cloudflare R2. A different company, so a different credential.
SECONDARY_REPOSITORY=s3:https://<account>.r2.cloudflarestorage.com/agentsee-mail-backup
SECONDARY_AWS_ACCESS_KEY_ID=<r2-token-id>
SECONDARY_AWS_SECRET_ACCESS_KEY=<r2-token-secret>
SECONDARY_HEALTHCHECK_URL=https://hc-ping.com/<a-second-uuid>
EOF
chmod 0600 /etc/agentsee/backup.env

set -a; . /etc/agentsee/backup.env; set +a

# Both repositories need initialising, and they share one passphrase.
restic init
RESTIC_REPOSITORY="$SECONDARY_REPOSITORY" \
  AWS_ACCESS_KEY_ID="$SECONDARY_AWS_ACCESS_KEY_ID" \
  AWS_SECRET_ACCESS_KEY="$SECONDARY_AWS_SECRET_ACCESS_KEY" \
  restic init

systemctl enable --now stalwart-backup.timer
systemctl start stalwart-backup.service   # don't wait until 03:20 to find a typo
journalctl -u stalwart-backup -f
```

**One passphrase for both repositories, deliberately.** Two would double what
has to be on paper for no real gain — both live in the same vault, so whoever
has one has the other. What matters is that this single passphrase is not lost,
and that is already the loudest rule in this file.

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
/opt/agentsee/stalwart/backup/restore-test.sh secondary
```

**Test both.** The secondary exists for the day the primary's whole account is
gone, which makes it the copy most likely to be reached for in an emergency and
the least likely ever to have been exercised. An untested second copy is not
redundancy, it is the belief in redundancy — worse than knowing you have one.

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
