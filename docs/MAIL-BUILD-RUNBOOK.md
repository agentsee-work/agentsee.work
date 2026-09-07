# Build runbook — mail

Standing the mail server up, in order, with a checkpoint at each phase.

[MAIL-SELFHOST.md](MAIL-SELFHOST.md) is the *spec* — what we're building and
why. This is the *procedure*. [RUNBOOK.md](RUNBOOK.md) stays what it is: how the
existing infrastructure works once it's running.

**Nothing here has been executed.** Every command is written from the design,
not transcribed from a successful run. Expect to correct it as you go, and
**correct it in this file as you do** — a runbook that drifts from reality is
worse than none, because it gets trusted.

## Before you start

### Accounts

| Account | State | Notes |
|---|---|---|
| Cloudflare | ✅ have | Zone, R2, Pages |
| GitHub | ✅ have | `agentsee-work` org |
| **Bitwarden** | needed **first** | [CREDENTIALS.md](CREDENTIALS.md). Everything below produces a credential |
| **Hetzner** | needed | Payment card. ⚠ new accounts are often held for manual fraud review — **a day or two, and it can block phase 1** |
| **Outbound relay** | needed | SMTP2GO or AWS — see below |
| healthchecks.io | needed | Free. The backup dead-man's switch |
| Cal.com | later | Free tier. Guest booking, not on this path |

**Relay: start with SMTP2GO.** Free tier is 1,000/month with no card, it is
built for exactly this, and it takes SES's production-access review — a human,
several days, no API — off the critical path entirely. AWS SES is cheaper per
message and the better long-run answer at volume; moving to it later is one
`MtaRoute` object. Don't buy the heavier signup before there is volume to
justify it.

### The bootstrap is not actually blocked

Creating these needs an email address, and we're building email. But **inbound
already works**: Cloudflare Email Routing forwards `@agentsee.work` today. It
receives fine — it only can't *send*. So verification links arrive now.

Set up `accounts@agentsee.work` fanned to both of us before anything else
(step 1 of [SOCIAL.md](SOCIAL.md)), and use it for signups.

### ⚠ Except for the accounts mail depends on

**Hetzner, the relay provider, Cloudflare and Bitwarden must NOT use
`@agentsee.work`.** Use personal addresses.

If mail breaks and the recovery link for the server that runs your mail is sent
to your broken mail, you are locked out of the thing you need to fix it. The
rule: **nothing in the recovery path for mail may depend on mail.**

This is the third instance of one pattern — the vault's 2FA isn't in the vault,
mail alerts don't go by mail, mail infrastructure doesn't recover by mail. When
a system is down, every recovery channel that runs through it is down too.

Everything else — social platforms, Cal.com, anything not load-bearing — uses
`accounts@agentsee.work`.

### Tools

| Need | Where |
|---|---|
| Cloudflare token | vault, `infra`. Zone > DNS > Edit. **Not** the Pages-only CI token |
| Hetzner token | vault, `infra`. Project, read+write |
| Relay SMTP creds | vault, `infra` |
| R2 tokens ×2 | vault, `infra`. One for state, one for backups. Separate deliberately |
| OpenTofu ≥ 1.8 | local — `tofu version` |
| SSH key | local — public half goes in `terraform.tfvars` |

Total working time is roughly a day, but **it cannot be done in a day** —
phase 1 blocks on SES review and phase 5 wants a night of backups. Plan for a
week of elapsed time with idle gaps.

Throughout: the apex keeps working on Cloudflare Email Routing. Nothing in
phases 0–6 touches live mail.

---

## Phase 0 — Bootstrap the things that can't be automated

The chicken-and-egg layer. Roughly 30 minutes, then waiting.

```sh
# State bucket. Must exist before there is state to put in it.
npx wrangler r2 bucket create agentsee-tfstate
npx wrangler r2 bucket create agentsee-mail-backup
```

Mint **two separate R2 tokens** in the Cloudflare dashboard, one per bucket, and
put both in the vault. Then add them to `~/.aws/credentials` as distinct
profiles — the backend and the AWS provider both read `AWS_ACCESS_KEY_ID`, so
sharing one sends credentials to the wrong service:

```ini
[r2-tfstate]
aws_access_key_id     = ...
aws_secret_access_key = ...

[agentsee-ses]
aws_access_key_id     = ...
aws_secret_access_key = ...
```

**Then request SES production access.** Do it now. A human reviews it, there is
no API, and everything in phase 4 blocks on it. Say what the mail is:
correspondence for a two-person studio, low volume, own domain, double opt-in
not applicable.

> ✅ **Checkpoint 0** — both buckets exist, both profiles authenticate
> (`aws --profile r2-tfstate s3 ls`), SES request submitted.

---

## Phase 1 — Provision

```sh
cd infra
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars          # ssh_public_key at minimum

export CLOUDFLARE_API_TOKEN=...
export HCLOUD_TOKEN=...

tofu init
tofu fmt -check
tofu validate
tofu plan                          # READ IT
tofu apply
```

`enable_apex_mx` stays `false`. The plan should create the server, firewall,
PTR, the `mail.` records, the `test.` MX and SPF, DMARC at `p=none`, and the SES
identity — **and nothing at the apex**. If you see the apex MX in the plan, stop
and check the variable.

```sh
tofu output next_steps
tofu output -raw ses_smtp_password    # → vault, infra collection, now
```

> ✅ **Checkpoint 1** — `dig +short mail.agentsee.work` returns the Hetzner IP,
> `dig +short agentsee.work MX` still returns **Cloudflare**, and you can
> `ssh root@mail.agentsee.work`.

**Rollback:** `tofu destroy`. Nothing live has changed.

---

## Phase 2 — Install Stalwart

```sh
ssh root@mail.agentsee.work
install -d -m 0755 /opt/agentsee
git clone <repo> /opt/agentsee/repo
ln -s /opt/agentsee/repo/stalwart /opt/agentsee/stalwart

cd /opt/agentsee/stalwart
echo "SES_SMTP_PASSWORD=<from the vault>" > .env && chmod 0600 .env

docker compose up -d
docker compose logs -f
```

⚠ **Verify the image name first.** The project renamed from `mail-server` to
`stalwart` and the Docker Hub tag may not have followed.

Complete the setup wizard at `https://mail.agentsee.work` — admin account,
domain `agentsee.work`, listeners, and ACME so TLS renews itself. Port 80 is
open for the challenge.

> ✅ **Checkpoint 2** — WebAdmin loads over **valid** TLS (not self-signed), and
> `openssl s_client -connect mail.agentsee.work:25 -starttls smtp` completes.

---

## Phase 3 — Configure, then capture it

Configure in the WebAdmin: domains (`agentsee.work` and `test.agentsee.work`),
accounts (`james@`, `abrar@`), aliases fanning `hello@`, `show@`, `accounts@`,
`dmarc@` to both, the DKIM signature, and the SES relay route per
[`stalwart/relay-ses.reference.json`](../stalwart/relay-ses.reference.json).

⚠ **Disable DANE and MTA-STS on the relay route.** They assert properties of
direct-to-MX delivery that are false with a smarthost in the path, and the
failures present as TLS errors — sending you to debug certificates instead of
routing.

Then capture it, and from here it's declarative:

```sh
export STALWART_URL=https://mail.agentsee.work STALWART_TOKEN=...
stalwart-cli snapshot > plan.json

grep -iE '"@type"\s*:\s*"(Value|Password)"|secret|password' plan.json
```

**Read that grep output before committing.** The repo is public. If the SES
password inlined itself, switch the route's `authSecret` to the
`EnvironmentVariable` variant and re-snapshot.

Publish DKIM by putting the public key in `infra/terraform.tfvars` and
re-applying:

```sh
cd infra && $EDITOR terraform.tfvars   # dkim_public_key = "v=DKIM1; k=rsa; p=..."
tofu apply
```

> ✅ **Checkpoint 3** — `plan.json` committed and clean, and
> `dig +short stalwart._domainkey.agentsee.work TXT` returns the key.

---

## Phase 4 — Prove it on the test subdomain

The phase that makes this safe. Real mail, real senders, a name nothing depends
on. **Blocks on SES production access.**

Send from an external account to `anything@test.agentsee.work`. It should
arrive.

Then reply, and open *Show original* in Gmail:

```
spf=pass    header.from=agentsee.work
dkim=pass   header.d=agentsee.work      ← must be OUR domain
dmarc=pass
```

`header.d` is the one that matters. Anything else means DKIM is signing as
someone other than us, and restoring `p=reject` would then reject our own mail.

> ✅ **Checkpoint 4** — inbound arrives, outbound sends, all three pass, and
> `header.d=agentsee.work`.

**If outbound fails:** check SES is out of sandbox before anything else. In
sandbox it only delivers to verified addresses, and the rejection reads like a
configuration error.

---

## Phase 5 — Backups, and prove them

Do not skip to phase 6. This is the phase that decides whether a bad day is an
inconvenience or the end of the business.

Follow [`stalwart/backup/README.md`](../stalwart/backup/README.md), then:

```sh
systemctl start stalwart-backup.service
journalctl -u stalwart-backup -n 50

set -a; . /etc/agentsee/backup.env; set +a
/opt/agentsee/stalwart/backup/restore-test.sh
```

The restore test must print **RESTORE TEST PASSED**. It restores to scratch and
boots a throwaway server against the restored data — if that server doesn't come
up, the backup is bytes rather than a recovery.

> ✅ **Checkpoint 5** — a nightly backup has run unattended at least once, the
> restore test passes, the restic passphrase is in the vault **and on paper**,
> and the healthcheck alerts when you deliberately skip a run.

---

## Phase 6 — Cutover

Everything above is reversible. This is not.

**First, disable Cloudflare Email Routing** for the apex — Email → Email Routing
→ Settings → Disable. It locks its own MX records and the API will refuse to
write otherwise. Subdomains are unaffected, so the `in.agentsee.work` pipeline in
[AGENT-MAIL.md](AGENT-MAIL.md) survives.

```sh
cd infra
$EDITOR terraform.tfvars          # enable_apex_mx = true
tofu plan                          # should be ~2 creates. Nothing else.
tofu apply
```

Then watch:

```sh
dig +short agentsee.work MX        # → mail.agentsee.work
```

Send to `hello@agentsee.work` from an external account. Reply. Confirm it lands.

> ✅ **Checkpoint 6** — apex MX is ours, mail flows both ways, `header.d` is
> still `agentsee.work`.

**Rollback:** set `enable_apex_mx = false`, `tofu apply`, re-enable Email
Routing. Mail sent during the gap is not lost — senders retry for days.

---

## Phase 7 — Re-arm DMARC

Wait for aggregate reports at `dmarc@agentsee.work` to show our own mail passing
with alignment. Days, not hours.

```sh
cd infra
$EDITOR terraform.tfvars          # dmarc_policy = "reject"
tofu apply
```

> ✅ **Checkpoint 7** — `dig +short _dmarc.agentsee.work TXT` shows `p=reject`,
> and mail still arrives at Gmail. If it doesn't, revert to `none` immediately:
> under `p=reject` the failure is silent discarding, which you will not see.

---

## After

- Update [RUNBOOK.md](RUNBOOK.md): mail is no longer Cloudflare Email Routing.
- Update the README "Mail" section — the ⚠ warning about not being able to send
  becomes history.
- Drop the Email Routing scopes from the main token? **No** — `in.agentsee.work`
  still needs them.
- Put the quarterly restore test in a calendar both of you can see.
- `workers/email-fanout` keeps running. Its fan-out job is over; it is the
  prototype for [AGENT-MAIL.md](AGENT-MAIL.md).

## If it goes wrong later

The abandonment criteria from [MAIL-SELFHOST.md](MAIL-SELFHOST.md), restated
because this is where you'll look:

- A restore test fails and isn't fixed that week.
- The canary catches two silent delivery failures in a quarter.
- It stops being interesting and becomes a chore.

Any of those: [MAIL-MIGRATION.md](MAIL-MIGRATION.md) moves us to kSuite. Same
addresses, same domain, about an hour. That exit staying cheap is what made this
worth trying.
