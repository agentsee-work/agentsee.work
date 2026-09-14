# stalwart/

Mail server configuration, versioned. Deployed onto the box that
[`infra/`](../infra/) provisions.

**Status: scaffold. Not applied, and partly unvalidated — see below.**

## How Stalwart configuration actually works now

Worth reading before you copy a guide off the internet, because **most of them
are for the old format and will not work.**

Stalwart v0.16 moved essentially everything out of `config.toml` and into the
datastore as JMAP objects. What remains on disk is a single file whose only job
is to say where the database lives:

```json
{ "@type": "RocksDb", "path": "/opt/stalwart" }
```

Everything else — domains, accounts, DKIM signatures, listeners, routing, spam
rules — is an object in that store, managed through the API.

**This is good for us.** `stalwart-cli apply --file plan.json` takes a
declarative plan and idempotently reconciles the live server to match it,
creating what's missing, updating what changed, removing what the plan no longer
declares. It prints a plan summary first:

```
Plan: 0 destroy, 5 update, 6 create
```

That is Terraform's model, for the mail server. So the config genuinely is
infrastructure-as-code rather than a file we promise to keep in sync.

## The workflow

The honest bootstrap: **configure once, snapshot, commit, then declare forever.**

```sh
# 1. Bring the server up with only the bootstrap file
docker compose up -d

# 2. Complete the setup wizard in the WebAdmin (https://mail.agentsee.work)
#    — admin account, domain, listeners, ACME. Minimum to get running.

# 3. Take the server's own view of its state, in the server's own schema
export STALWART_URL=https://mail.agentsee.work
export STALWART_TOKEN=...
stalwart-cli snapshot > plan.json

# 4. Commit plan.json. From here it is declarative:
#    edit → stalwart-cli apply --file plan.json → commit
```

Step 3 matters. The CLI is **schema-driven** — it downloads the object schema
from the running server — so a snapshot is authoritative in a way a
hand-written plan isn't. Generate the plan from a server; don't invent it.

## There is no config.json in this directory

There was, and it was wrong in a way worth recording.

The image runs `--config /etc/stalwart/config.json`, a path **inside the
container**, so anything written there disappears on the next recreate. Our
compose mounted a read-only `config.json` at `/opt/stalwart/etc/config.json`
instead — a path nothing reads.

The result had no error in it anywhere. Stalwart found no configuration, entered
bootstrap mode, and printed a temporary admin password. The setup wizard ran
happily to completion against storage that did not persist, and the next restart
produced a fresh wizard and a fresh password, as though nothing had been done.

So the config now lives on the mounted volume, created by Stalwart itself:

```yaml
command: ["--config", "/opt/stalwart/etc/config.json"]
```

That path is inside `/var/lib/stalwart`, which means it survives a rebuild and
is covered by the backup. Nothing in this repo needs to hold it — since v0.16
the config file only says where the datastore is, and everything else lives in
the datastore, captured by `stalwart-cli snapshot` into `plan.json`.

**The tell for this class of bug:** a setup wizard that reappears. If Stalwart
ever greets you with a bootstrap password again, it has not lost its
configuration — it never had anywhere to put it.

## ⚠ What in here is unvalidated

Being explicit, because a config that is confidently wrong costs more than one
that admits a gap:

| File | State |
|---|---|
| `docker-compose.yml` | **Run on a real box 11 September 2026.** Image name, ports, the loopback bootstrap port and the `--config` path are all corrected from what actually happened rather than what was designed |
| `relay-smtp2go.reference.json` | **Superseded by `plan.json`.** Kept for its notes on ports, DANE and signed headers; the real object is in the plan |
| `plan.json` | **Snapshotted from the running server 14 September 2026.** Describes what is actually running |

## `plan.json` — the running server, as a file

Everything configured through the WebAdmin, captured with `stalwart-cli
snapshot`. This is what makes the IaC claim honest: the clicking happened once,
and the next server is an `apply`.

### The CLI is a separate download

It is **not in the Docker image** — the maintainers moved it to its own
repository, [`stalwartlabs/cli`](https://github.com/stalwartlabs/cli), with its
own version line (v1.0.12 while the server was v0.16.21). There is no CLI asset
in the server's releases, which is a confusing place to spend ten minutes.

```sh
V=v1.0.12
curl -fsSL -O "https://github.com/stalwartlabs/cli/releases/download/$V/stalwart-cli-x86_64-unknown-linux-gnu.tar.xz"
curl -fsSL "https://github.com/stalwartlabs/cli/releases/download/$V/stalwart-cli-x86_64-unknown-linux-gnu.tar.xz.sha256" \
  | sed 's|$|  stalwart-cli-x86_64-unknown-linux-gnu.tar.xz|' | sha256sum -c -
tar xf stalwart-cli-x86_64-unknown-linux-gnu.tar.xz
sudo install -m 0755 "$(find . -name stalwart-cli -type f | head -1)" /usr/local/bin/
```

### Taking a snapshot

```sh
stalwart-cli --url https://mail.agentsee.work --user admin@agentsee.work \
  snapshot --output plan.json \
  --allow-unresolved Directory,Tenant,DnsServer,Role,PublicKey \
  Domain Account MailingList AcmeProvider DkimSignature \
  MtaRoute MtaOutboundStrategy Tracer NetworkListener
```

**Expect to build that `--allow-unresolved` list by trial.** The tool refuses to
emit a plan with references it cannot resolve, and tells you one at a time.
`Role` and `PublicKey` in particular *cannot* be added — they form reference
cycles — so allow-unresolved is the only route. We use none of the five.

The object list is deliberately configuration only. Most of the 120-odd types
are state — queued messages, logs, metrics, spam samples, received reports —
and belong in the backup, not in a plan.

### ⚠ It captures structure, not secrets

Secrets are stripped by default, which is the right default and worth not
overriding. What survives is the shape:

```
authSecret  {"@type":"EnvironmentVariable","variableName":"RELAY_SMTP_PASSWORD"}
privateKey  {"@type":"Text"}          ← DKIM key, no value
credentials types and descriptions, no passwords
```

So **`apply` alone does not rebuild this server.** A rebuild is `apply` plus the
restored datastore, or `apply` plus re-entering credentials from the vault. Say
that plainly rather than claiming the plan is sufficient — it is exactly the
kind of thing that would be believed until the day it was tested.

Verify before every commit, on values rather than keys — a `grep` for `secret`
matches nearly every line of NDJSON and tells you nothing:

```sh
python3 - <<'EOF'
import json
for line in open('plan.json'):
    if not line.strip(): continue
    o = json.loads(line)
    for _, body in o.get("value", {}).items():
        if not isinstance(body, dict): continue
        for k in ("credentials", "authSecret", "privateKey", "secret"):
            if k in body: print(o.get("object"), k, "=", json.dumps(body[k])[:160])
EOF
```

### ⚠ Two objects have no label, and `apply` will duplicate them

`snapshot` warns that **`Account` and `Tracer` have no label property**, so
`apply` matches them by value — a changed object is *created* rather than
updated. Add a `matchOn` to those entries before the first real `apply`, or a
rebuild produces duplicate accounts.

Everything else already carries one: `matchOn: ["name"]`, `["selector"]`,
`["emailAddress"]`, `["description"]`.

## The relay: keeping the secret out of a public repo

`MtaRoute`'s `Relay` variant takes `authSecret`, which supports variants
`None`, `Value`, `EnvironmentVariable` and `File`.

**Use `EnvironmentVariable`.** It means the committed plan references
`RELAY_SMTP_PASSWORD` rather than containing it, so the plan is safe in a public
repo and the secret stays in the vault and the systemd unit. `Value` would
inline it — that is how a credential ends up in git history, where deleting it
doesn't remove it.

The variable is named for the *role*, not the provider. SMTP2GO is a for-now
choice, and `SMTP2GO_PASSWORD` would spread it into the compose file, the
systemd unit, the `.env` and the plan — making a one-object swap a rename in
five places.

See `relay-smtp2go.reference.json` for the object shape.

## ⚠ Disable DANE and MTA-STS on the relay route

Both assert things about direct-to-MX delivery that are **false when a smarthost
is in the path**. Left on, they produce delivery failures that present as TLS
errors, which sends you debugging certificates instead of routing.

The exact key for this differs by version — find it under the outbound strategy
for the route, and confirm it in your snapshot before cutover.

## What to configure

Roughly in this order:

1. **Domain** `agentsee.work`, plus `test.agentsee.work` for the proving stage.
2. **Accounts** — `james@`, `abrar@` as real accounts; `hello@`, `show@`,
   `accounts@`, `dmarc@` as aliases delivering to both. (Fanning one address to
   two people is the thing Cloudflare Email Routing refused to do and why
   `workers/email-fanout` exists — see [RUNBOOK](../docs/RUNBOOK.md).)
3. **DkimSignature** — generate it here, then publish the public half via
   `dkim_public_key` in [`infra/`](../infra/). Ours *as well as* the relay's:
   SMTP2GO signs via a CNAME delegated to them, so that key is theirs and
   leaves when they do. Two aligned signatures is legal and DMARC passes if
   either validates.
4. **ACME** — Let's Encrypt, so TLS renews itself. Needs port 80 reachable,
   which `infra/server.tf` already allows.
5. **MtaRoute** — the SMTP2GO relay, per the reference file.
6. **Spam filter** — Stalwart's is built in; the defaults are sane.

## Backups

The datastore holds everything: mail, accounts, config, DKIM private keys. It is
the only thing on the box that cannot be rebuilt from this repo.

```sh
restic -r s3:https://<account>.r2.cloudflarestorage.com/agentsee-mail-backup \
       backup /var/lib/stalwart
```

**Restore-test before cutover, not after.** A first restore attempted after real
mail exists is not a test, it's an incident. And `plan.json` in git is not a
backup of the mail — it only rebuilds the configuration.
