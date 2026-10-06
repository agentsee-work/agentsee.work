# stalwart/

Mail server configuration, versioned. Deployed onto the box that
[`infra/`](../infra/) provisions.

**Status: live.** `plan.json` describes the running server. `stalwart-cli apply`
has been used against it — but only ever on a hand-carved subset, never on the
whole file; see "A full `apply` is not a safe way to change one thing" below for
why that distinction matters.

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
| `plan.json` | **Snapshotted from the running server 14 September 2026**, plus one hand edit on 6 October 2026 (the `dmarc@` member and the `reports` account) that was applied as a subset and verified against the server. So it describes what is running, but it is no longer a pure snapshot — the next `./snapshot.sh` makes it one again |

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
./snapshot.sh
```

That is the whole interface, and it is a script rather than a documented
sequence because the sequence was skipped the first time it was used — the
snapshot was taken, committed, and lost its `matchOn` keys in the process.

It does three things and refuses to write `plan.json` if the third fails:

1. snapshots the configuration object types (not state)
2. runs `add-matchon.py` for the keys the CLI cannot infer
3. **checks for secret values and aborts rather than writing them**

The underlying command, for reference:

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

### ⚠ Some objects have no label, and `apply` would duplicate them

`snapshot` warns when an object type has no label property. For those, `apply`
matches by **value** — so an object whose fields changed is *created* rather
than updated, and a rebuild produces duplicate accounts.

`add-matchon.py` fixes that, and should be run over every snapshot before
committing:

```sh
stalwart-cli ... snapshot --output plan.json ... && ./add-matchon.py plan.json
```

`Account` matches on **`name` AND `domainId`**. `name` alone would be wrong:
`james` exists on `agentsee.work`, and nothing stops a `james` on another domain
later — matching on name alone would quietly merge two different people.

`Tracer` is not in that list on purpose. It has no field that identifies it
either, and the right fix was to delete the broken `Log` tracer rather than
invent a key for it — it wrote to `/var/log/stalwart/`, a directory that does
not exist in the container, and failed on every startup. With one tracer left,
match-by-value is harmless.

Everything with a natural label already carries one from `snapshot`:
`matchOn: ["name"]`, `["selector"]`, `["emailAddress"]`, `["description"]`.

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
2. **Accounts** — `james@`, `abrar@` as real accounts; `hello@`, `show@` and
   `accounts@` as lists delivering to both. (Fanning one address to two people
   is the thing Cloudflare Email Routing refused to do and why
   `workers/email-fanout` exists — see [RUNBOOK](../docs/RUNBOOK.md).)
   `dmarc@` is a list too, but with a single member — see below.
3. **DkimSignature** — generate it here, then publish the public half via
   `dkim_public_key` in [`infra/`](../infra/). Ours *as well as* the relay's:
   SMTP2GO signs via a CNAME delegated to them, so that key is theirs and
   leaves when they do. Two aligned signatures is legal and DMARC passes if
   either validates.
4. **ACME** — Let's Encrypt, so TLS renews itself. Needs port 80 reachable,
   which `infra/server.tf` already allows.
5. **MtaRoute** — the SMTP2GO relay, per the reference file.
6. **Spam filter** — Stalwart's is built in; the defaults are sane.

## `dmarc@` delivers to a sink, not to people

It used to have two members, so every daily report arrived **twice** — one real
copy into each personal mailbox. Three things land there, not one:

| Source | Shape |
|---|---|
| DMARC `rua` | gzipped aggregate XML, daily, from every receiver that gets mail claiming to be us |
| TLS-RPT `rua` | JSON, daily |
| CAA `iodef` | certificate-issuance incident mail, rare |

The list now has one member: the `reports` account.

**It stays a list rather than becoming a mailbox** because those three things are
*DNS records* — `_dmarc`, `_smtp._tls` and the CAA `iodef` tag, all in
[`infra/dns.tf`](../infra/dns.tf), all naming `dmarc@agentsee.work`. Keep the
address and who reads the reports is a one-word config change. Turn the address
into a mailbox and it becomes a DNS change, and the moment a second `rua` URI
lives on someone else's domain it also becomes an RFC 7489 §7.1 external
destination verification — a `<our-domain>._report._dmarc.<their-domain>` TXT
record that receivers check before they will send. When that record is missing,
reporting **stops silently**. The failure is indistinguishable from the problem
being solved.

**The account is `reports`, not `dmarc`,** because two of the three things
arriving are not DMARC.

### ⚠ Nothing reads it yet

`credentials` is `{}` — the plan carries no secrets, so the mailbox receives mail
and no one can log in to it. That is fine as a resting state and **not** fine as
an end state: under `p=reject` the aggregate reports are the only way we find out
that a legitimate sender of ours is being rejected somewhere, and we are adding
senders. Set a password in the WebAdmin, store it in 1Password, attach it as a
second account in a client — or point a second `rua` URI at an analyser and
accept the verification step above.

`quotas` is `{}`, like every other account here. A sink grows forever and a quota
is the obvious guard, but the field shape is not something to guess at in a file
that gets applied to a live server — take it from a snapshot after setting one in
the WebAdmin.

### Applied 6 October 2026

Not with `plan.json`, but with a two-object subset carved out of it — for the
reason in the next section, which is the more useful half of this change.

## ⚠ A full `apply` is not a safe way to change one thing

`plan.json` upserts every account, and the `Account` object carries
`credentials` — for all five, with the secrets stripped, because that is the
right default:

```
james   Password + AppPassword "Thunderbird" + AppPassword "iOS Mail"
abrar   Password + AppPassword "Email client access"
admin   Password
```

Structure, no values. The limitation is recorded above as a *rebuild* caveat —
"`apply` plus re-entering credentials from the vault" — which reads like a
cold-start problem. It is not only that. **Upserting a stripped credential onto
an account that has a real one is untested**, and if it replaces rather than
patches, the cost is both mailboxes and the admin login in the same second.

So the blast radius of a twelve-line apply is thirty objects, three of which
are the only way anyone gets into this server. Changing one mailing list member
does not need that.

### Carving a subset out of the plan

Extract only the objects that change, into their own file:

```sh
stalwart-cli --url https://mail.agentsee.work --user admin@agentsee.work \
  apply --file subset.json --dry-run
```

**Rewrite `#`-prefixed references to literal ids first.** They resolve *within
one plan file only* — drop the line that declares the target and apply refuses,
with an unusually clear error:

```
error: Account: match property `domainId` references unresolved id `#domain-b`
       (no create, upsert, or reconcile operation in this plan produced it)
```

`#domain-b` looks like it encodes the real server id, because `snapshot` names
keys `<type>-<id>` and the live ids really are `b`, `d`, `e`, `f`. It does not
resolve that way. `query Domain` gives the id; write `"b"`.

### Reading the summary

```
Plan: 0 destroy, 0 update, 0 create, 2 upsert, 0 reconcile (2 objects)
```

It counts **what the plan asks for, by `@type`** — not a computed diff of what
will change. `snapshot` emits `upsert` for everything, so a brand-new object
shows up under `upsert` and `create` stays at `0`. The full file reports
`11 upsert … (30 objects), 1 update` whatever state the server is in.

Two things it is still good for: `destroy` and the object count. Nothing here
removes objects, so **any destroy means you are applying the wrong file** — and
the count is specific enough to identify which file parsed. Adding the `reports`
account took the full plan from 29 objects to 30.

### `--dry-run` needs credentials

Its help says "without calling the server". It still refuses without them,
because the CLI is schema-driven and fetches the schema it validates against.
Worth knowing before you conclude the flag is broken.

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
