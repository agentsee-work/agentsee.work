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
| **Hetzner** | needed | Payment card **and photo ID** — see below |
| **SMTP2GO** | needed | Free tier, no card. The outbound relay — see below |
| healthchecks.io | needed | Free. The backup dead-man's switch |
| Cal.com | later | Free tier. Guest booking, not on this path |

### ⚠ Hetzner signup now needs government ID

This entry used to warn about a manual fraud review taking a day or two. That
is out of date. Hetzner moved cloud onboarding to iDenfy, which asks for a
government-issued ID document and a biometric selfie, and is **automated** —
faster than the process it replaced, though edge cases still route to a human.

Two things follow that are easy to trip on:

- Hetzner advises against signing up with a free email provider. Our own rule
  says Hetzner must **not** use `@agentsee.work`, because nothing in the
  recovery path for mail may depend on mail. A personal Gmail is the collision
  of those two, and it is the one to expect friction from.
- Don't sign up over a VPN.

**Their network blocks outbound 25 and 465 for roughly the first month**,
lifted by a limit request after the first invoice. Inbound 25 is open from day
one, which is the port an MX actually needs. The outbound block is why the
relay route uses **8465** — see phase 3. Nothing in this build waits on that
limit request.

**The relay is SMTP2GO.** Free tier is 1,000/month with no card, which two
people's correspondence will not approach. It was chosen over AWS SES because
SES starts in a sandbox and leaving it needs a support request reviewed by a
human — no API, no timebox — which put an unbounded wait on the critical path.

SES is cheaper per message and the better answer at volume. Moving to it later
is one `MtaRoute` object plus a DNS change. Don't buy the heavier signup before
there is volume to justify it.

⚠ **Verifying the sender domain is a manual step and it matters.** SMTP2GO has
no OpenTofu provider, so adding the domain and copying its three CNAMEs back
into `terraform.tfvars` is done by hand. Skip it and mail still sends — signed
as `smtp2go.com` rather than as us. Nothing appears wrong until `p=reject`, at
which point our own mail is silently discarded.

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
| SMTP2GO SMTP user | vault, `infra`. Sending > SMTP Users — not the account login |
| R2 tokens ×2 | vault, `infra`. One for state, one for backups. Separate deliberately |
| OpenTofu ≥ 1.8 | local — `tofu version` |
| SSH key | local — public half goes in `terraform.tfvars` |

Total working time is roughly a day, but **it cannot be done in a day** —
phase 4 blocks on DNS propagation and phase 5 wants a night of backups. Plan
for a few days of elapsed time with idle gaps. This is shorter than it was when
the relay was SES, and that is the main thing the switch bought.

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
put both in the vault. Separate deliberately: one credential that can both
delete the backups and rewrite the infrastructure is a poor blast radius for
something that ends up on an internet-facing box.

Only the state token goes in `~/.aws/credentials`. The backup token is read by
the box from `/etc/agentsee/backup.env` in phase 5 and never belongs here:

```ini
[r2-tfstate]
aws_access_key_id     = ...
aws_secret_access_key = ...
```

**Then create the SMTP2GO account and verify the sender domain.** Sending >
Verified Senders > Sender Domains > Add `agentsee.work`. It returns three
CNAMEs — return-path, DKIM and link tracking. Keep them; they go into
`terraform.tfvars` in phase 1.

While you are there: **Sending > SMTP Users**, create one. Those credentials go
to the vault. They are not managed by OpenTofu and never enter state.

**Turn link tracking off.** It rewrites URLs in the message body, which is both
unwanted for correspondence and likely to invalidate the DKIM signature
Stalwart applies before handoff.

> ✅ **Checkpoint 0** — both buckets exist, `aws --profile r2-tfstate s3 ls`
> authenticates, and SMTP2GO shows the sender domain with three CNAMEs to
> publish and an SMTP user created.

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

Put the three SMTP2GO CNAMEs into `terraform.tfvars` as
`smtp2go_cname_records` before applying — they are the one input phase 0
produced.

`enable_apex_mx` stays `false`. The plan should create the server, firewall,
PTR, the `mail.` records, the `test.` MX and SPF, DMARC at `p=none`, and the
three relay CNAMEs — **and nothing at the apex**. If you see the apex MX in the
plan, stop and check the variable.

```sh
tofu output next_steps
tofu output smtp2go_records_present    # must be true
```

> ✅ **Checkpoint 1** — `dig +short mail.agentsee.work` returns the Hetzner IP,
> `dig +short agentsee.work MX` still returns **Cloudflare**, you can
> `ssh root@mail.agentsee.work`, and SMTP2GO's dashboard shows the sender
> domain as verified rather than pending.

**Rollback:** `tofu destroy`. Nothing live has changed.

---

## Phase 2 — Install Stalwart

```sh
ssh root@mail.agentsee.work
install -d -m 0755 /opt/agentsee
git clone <repo> /opt/agentsee/repo
ln -s /opt/agentsee/repo/stalwart /opt/agentsee/stalwart

cd /opt/agentsee/stalwart
echo "RELAY_SMTP_PASSWORD=<from the vault>" > .env && chmod 0600 .env

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
`dmarc@` to both, the DKIM signature, and the SMTP2GO relay route per
[`stalwart/relay-smtp2go.reference.json`](../stalwart/relay-smtp2go.reference.json).

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

**Read that grep output before committing.** The repo is public. If the relay
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
on.

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

**Expect two DKIM signatures**, ours from Stalwart and SMTP2GO's from the
delegated CNAME. Both should show `header.d=agentsee.work`. If only SMTP2GO's
appears, the sender domain is verified but *our* signature is being invalidated
downstream — see the link-tracking check below. If `header.d=smtp2go.com`, the
sender domain was never verified and everything else here is cosmetic.

⚠ **This is where the link-tracking question gets answered.** Send a message
containing an `<a href="https://...">` link, and compare the received body with
what was sent. If the URL was rewritten, the body changed after Stalwart signed
it and our signature cannot survive — turn link tracking off at SMTP2GO and
re-test. The design assumes this is true; it has not been confirmed against a
real message.

> ✅ **Checkpoint 4** — inbound arrives, outbound sends, all three pass,
> `header.d=agentsee.work`, and a message containing a link comes through with
> that link untouched.

**If outbound fails:** SMTP2GO's dashboard logs every accepted message, and
"nothing in the log" versus "in the log but not delivered" points at completely
different halves of the system.

Nothing in the log means we never got there — check the SMTP user's
credentials, then that egress to `mail.smtp2go.com:8465` actually opens:

```sh
openssl s_client -connect mail.smtp2go.com:8465 -quiet
```

If that hangs, the host is filtering the port. **Do not "fix" it by moving to
465** — that is the port Hetzner blocks for the first month. 2525 is the
fallback.

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
