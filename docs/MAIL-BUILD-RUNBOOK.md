# Build runbook — mail

Standing the mail server up, in order, with a checkpoint at each phase.

[MAIL-SELFHOST.md](MAIL-SELFHOST.md) is the *spec* — what we're building and
why. This is the *procedure*. [RUNBOOK.md](RUNBOOK.md) stays what it is: how the
existing infrastructure works once it's running.

**Phases 0 and 1 have been executed** — 11 September 2026. Those two are now
transcribed from a real run rather than written from the design, and carry the
things that actually went wrong.

**Phases 2 onward have not been run.** They are still written from the design.
Expect to correct them as you go, and **correct them in this file as you do** —
a runbook that drifts from reality is worse than none, because it gets trusted.

## Before you start

### Accounts

| Account | State | Notes |
|---|---|---|
| Cloudflare | ✅ have | Zone, R2, Pages |
| GitHub | ✅ have | `agentsee-work` org |
| 1Password | ✅ Business, vaults created | [CREDENTIALS.md](CREDENTIALS.md) — the second Owner is still outstanding, and `op run` needs the three Engineering items to exist |
| **Infomaniak** | needed | Payment card. Public Cloud, not the hosted mail — see below |
| **SMTP2GO** | needed | Free tier, no card. The outbound relay — see below |
| healthchecks.io | needed | Free. **Two** checks — one per backup repository |
| Swiss Backup | ✅ 1 TB bought | Primary backup target. S3 protocol, not Swift |
| Cal.com | later | Free tier. Guest booking, not on this path |

### The host is Infomaniak Public Cloud

**Public Cloud**, their OpenStack IaaS — not kSuite, which is the hosted-mail
product this build exists instead of. Same company, different thing, and it is
easy to sign up for the wrong one.

Chosen over Hetzner on conduct rather than technology, and it costs roughly
£15–30/year more. The reasoning is in
[MAIL-SELFHOST.md](MAIL-SELFHOST.md#why-infomaniak-and-why-that-costs-more).

**Outbound port 25 is blocked by default**, openable by a support request. We
never need it: nothing is ever sent direct-to-MX, and the relay is reached on
8465. Don't file the request — an unused open port is only a liability.

✅ **Inbound 25 reaches the instance** — tested 11 September 2026, no support
ticket required. Their docs describe the block as *outgoing* without saying
anything about ingress, and an MX is port 25 or nothing, so this had to be
settled before building. It can be settled in five seconds:

```sh
probe() { timeout 8 bash -c "exec 3<>/dev/tcp/mail.agentsee.work/$1"; \
          echo "$1 -> rc=$?"; }
probe 8080   # control: NOT in any security group
probe 25     # the question
```

**Calibrate it, don't just run it.** A bare "connection refused" on 25 proves
nothing on its own — it looks the same whether the port is open with nothing
listening or you mistyped the host. The control is what makes it an answer:

| Result | Means |
|---|---|
| timeout, ~8s (`rc=124`) | packet dropped — filtered. This is what 8080 does |
| `Connection refused`, ~100ms | packet **reached the host**, which replied RST |

25 refuses as fast as 443 and 993 do, so it is reachable and merely has nothing
listening yet. Same method as the handle checks in [SOCIAL.md](SOCIAL.md):
without a known-blocked control, the result is a guess.

**Public Cloud is a separate product to order**, not something the account has
by default, and **projects live inside an ordered Public Cloud** — which is why
there is no mention of projects anywhere until you have one. Verifying your
identity gets you an Infomaniak account; it does not get you a project.

At `manager.infomaniak.com`, find the Public Cloud product section and use the
button to order one. The listing then gives each Public Cloud a tree icon that
opens its projects page, and an arrow that opens the OpenStack Horizon
dashboard directly.

This is the only part of the build done by hand, and it is the tenancy rather
than the server — see [infra/README.md](../infra/README.md#the-tenancy-is-clicked-the-vps-is-not).

#### The create-project form, field by field

| Field | Put in | Why |
|---|---|---|
| Project name | `agentsee` | Not `agentsee-mail`. The box is also the lab, and a project is a tenancy boundary — leaving the name generic keeps `client-<name>` projects available later without this one looking misnamed |
| Access description | `opentofu` | The `PCU-` name itself is generated and not editable. The description is the only place to record what the credential is *for*, and a second one will eventually exist for console access |
| Password | **Create now** | Not "send procedure" |

**Create now, not the emailed form.** The emailed link is one more step and it
is the step that gets left unread — and the PCU password is the credential the
entire apply depends on.

⚠ **Put the password in the vault before submitting the form.** Generate it in
1Password, save the `Infomaniak Public Cloud` item in **Engineering**, *then*
paste it into the browser. Generate it in the browser first and a mistyped copy
means a password reset rather than a correction. The `username` and `project`
fields of that item do not exist yet; add them from `clouds.yaml` afterwards.

The rules are ≥8 characters with an upper, a lower, a digit and a symbol.
1Password's generator clears that comfortably — check the generated string
actually contains all four classes, because a random 32-character password can
legitimately come out with no digit.
Nothing about the box is created this way.

Creating the project mints an OpenStack user named `PCU-XXXXXXX` — auto-
generated, not editable — and the project itself gets `PCP-XXXXXXX`.

⚠ **Set the user's password during that wizard**, and understand what it is:
the PCU user has its **own** password, unrelated to the Infomaniak account you
just signed into. This is the step people skip, because the wizard also offers
to email a link instead and an email is easy to leave unread. If it was skipped,
the manager will re-send it.

Then in the manager, open the project's **user management** section, pick
**clouds.yaml**, choose region `dc3-a`, and Download — but **don't save it to
disk**. Read three values out of it and put them in an `Infomaniak Public Cloud`
item in the **Engineering** vault:

| Vault field | From clouds.yaml | Looks like |
|---|---|---|
| `username` | `username` | `PCU-XXXXXXX` |
| `password` | `password` | the PCU password you set |
| `project` | `project_name` | `PCP-XXXXXXX` |

`infra/op.env` resolves those three at apply time, which is why there is no
credentials file left anywhere to leak or forget to `chmod`. Everything else in
that file — auth URL, region, both domains — is already in `op.env` and matches.

⚠ **SMTP2GO refuses free email addresses at signup.** Use
`smtp2go@ops.agentsee.work` — a subdomain kept on Cloudflare Email Routing
forever, so it is a domain address that does not depend on our mail server. See
[CREDENTIALS.md](CREDENTIALS.md#when-a-vendor-refuses-a-free-email-address).
`ops.` must never point at Stalwart.

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

**Infomaniak, the relay provider, Cloudflare and the password manager must
NOT use `@agentsee.work`.** Use personal addresses.

If mail breaks and the recovery link for the server that runs your mail is sent
to your broken mail, you are locked out of the thing you need to fix it. The
rule: **nothing in the recovery path for mail may depend on mail.**

This is one instance of a pattern with four of them — the vault's 2FA isn't in
the vault, the Emergency Kit isn't only in the vault, mail alerts don't go by
mail, mail infrastructure doesn't recover by mail. When a system is down, every
recovery channel that runs through it is down too.

Everything else — social platforms, Cal.com, anything not load-bearing — uses
`accounts@agentsee.work`.

### Tools

| Need | Where |
|---|---|
| Cloudflare token | **Engineering** vault. Zone > DNS > Edit. **Not** the Pages-only CI token |
| 1Password CLI | local — `op --version`. `op run` injects everything below |
| Infomaniak Public Cloud | **Engineering** vault. Username, project, password from the RC file. The *account login* is IT |
| SMTP2GO SMTP user | **Engineering** vault. Sending > SMTP Users — the account login is IT |
| R2 tokens ×2 | **Engineering** vault. One for state, one for backups. Separate deliberately |
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

Put the state token in the vault as `R2 tfstate token` — `infra/op.env`
resolves it into `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` at apply time, so
there is no `~/.aws/credentials` file. The backup token is read by the box from
`/etc/agentsee/backup.env` in phase 5 and never touches this machine.

**Then create the SMTP2GO account and verify the sender domain.** Sending >
Verified Senders > Sender Domains > Add `agentsee.work`. It returns three
CNAMEs — return-path, DKIM and link tracking. Keep them; they go into
`terraform.tfvars` in phase 1.

While you are there: **Sending > SMTP Users**, create one. Those credentials go
to the **Engineering** vault. They are not managed by OpenTofu and never enter
state.

**Turn tracking off — it is per SMTP user, not a global switch.** Sending >
SMTP Users > *your username* > **Tracking & Status**, and turn off **both**
Click Tracking and Open Tracking.

Both modify the message body: click tracking rewrites URLs, open tracking
injects a pixel. Stalwart signs the body before handoff, so either should
invalidate our DKIM signature — and both are unwanted for correspondence
regardless. Because the setting lives on the user, a second SMTP user created
later starts with tracking **on** again.

> ✅ **Checkpoint 0** — both buckets exist, SMTP2GO shows the sender domain
> with three CNAMEs to publish and an SMTP user created, every item named in
> `infra/op.env` resolves, and **Infomaniak has confirmed inbound 25 in
> writing.**

Prove the last-but-one before going near `tofu`, because a missing vault item
surfaces as a provider auth error that reads like a wrong credential:

```sh
cd infra && op run --env-file=op.env -- env | grep -E 'CLOUDFLARE|AWS_ACCESS|OS_USERNAME'
```

`op run` masks the values, so this shows that each reference *resolved* without
printing what it resolved to.

**If inbound 25 had turned out to be filtered**, the answer was to stop and
re-read [MAIL-SELFHOST.md](MAIL-SELFHOST.md): owning the inbox is the whole
point of this path, and without it there is no build, only kSuite. It is not
filtered — but the probe is worth re-running after any host change, because it
is the one failure that would make everything else pointless.

---

## Phase 1 — Provision

```sh
cd infra
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars          # ssh_public_key at minimum

# ⚠ Confirm the image name exists before the first plan — they move:
op run --env-file=op.env -- openstack image list | grep -i debian

tofu fmt -check                    # no credentials needed
tofu validate

op run --env-file=op.env -- tofu init
op run --env-file=op.env -- tofu plan     # READ IT
op run --env-file=op.env -- tofu apply
```

Put the three SMTP2GO CNAMEs into `terraform.tfvars` as
`smtp2go_cname_records` before applying — they are the one input phase 0
produced.

`enable_apex_mx` stays `false`. The plan should create the instance, the two
security groups, the data volume, the `mail.` A and AAAA records, the `test.`
MX and SPF, DMARC at `p=none`, and the three relay CNAMEs — **and nothing at
the apex**. If you see the apex MX in the plan, stop and check the variable.

```sh
op run --env-file=op.env -- tofu output next_steps
op run --env-file=op.env -- tofu output smtp2go_records_present   # must be true
```

Every command touching state needs `op run`, including reads — the backend
credentials come from the vault too.

> ✅ **Checkpoint 1 — PASSED 11 September 2026.** `mail.agentsee.work` resolves
> to `188.213.129.223` and `2001:1600:10:100::7f5`, `dig +short agentsee.work MX`
> still returns **Cloudflare**, `_dmarc` still reads `p=reject`, and
> `ssh -i ~/.ssh/agentsee_mail debian@mail.agentsee.work` works.

SMTP2GO is *not* part of this checkpoint. It is outbound, and nothing before
phase 3 touches it.

The login is **`debian`**, not root — OpenStack images inject the keypair into
the image's default user.

### What actually bit, in order

All three survive a `tofu validate`, so none of them can be caught before a real
run:

**Both Keystone domains are lowercase `default`.** Not `Default`. Domain names
are case-sensitive and the failure is a bare `401 The request you have made
requires authentication`, which names nothing it could have been. Take the
values from a downloaded `clouds.yaml`, never from documentation.

**`PCU-` and `PCP-` differ by one letter and are otherwise the same string.**
`PCU-` is the user, `PCP-` the project, and the rest of the string is identical
between them. Pasting the project id
into both vault fields gives the same bare 401. A wrong *project* with a right
user gives a 404 naming the project, so a 401 means the problem is the username,
password or user domain — that distinction is the fastest way to halve the
search.

**`op run` masks resolved secret values in its output.** A vault field holding
`agentsee` concealed the domain in every line of the plan. Confusing for about a
minute, then useful: it proves at a glance when a field holds something it
shouldn't.

**Cloudflare rejects a DNS record comment over 100 characters** — error 9313,
and it fails the apply *after* every other resource has been created.

### ⚠ The DMARC record already exists — import it, don't create it

The zone has carried a DMARC record since August 2026. Applying without
importing creates a **second** TXT at `_dmarc`, and two DMARC records means
receivers treat the domain as having no policy at all — silently switching off
the `p=reject` the README is proud of.

```sh
op run --env-file=op.env -- tofu import cloudflare_dns_record.dmarc \
  <zone_id>/<record_id>
```

Set `dmarc_policy = "reject"` in `terraform.tfvars` first, so the import is a
zero diff. It drops to `none` at phase 3 — **before sending starts, not weeks
early.** While nothing sends, `p=reject` is both true and free protection.

**Rollback:** `op run --env-file=op.env -- tofu destroy`. Nothing live has changed.

⚠ It will refuse, because the data volume is `prevent_destroy`. That guard is
the point: destroying the instance should be routine and destroying the mail
should not. To actually tear everything down, remove the `lifecycle` block in
`server.tf` first — a deliberate edit, which is the intent.

---

## Phase 2 — Install Stalwart

First, the data volume. It is attached but raw, and Stalwart's datastore lives
on it so that rebuilding the instance does not take the mail with it:

```sh
ssh debian@mail.agentsee.work
lsblk                                  # expect vdb, ~20G, no filesystem

# ⚠ CHECK lsblk FIRST. mkfs on the wrong device is unrecoverable, and on a
# rebuilt box the volume already holds the mail — formatting it is the one
# irreversible mistake available in this phase.
sudo mkfs.ext4 -L stalwart /dev/vdb    # ONLY if it has no filesystem

sudo install -d -m 0755 /var/lib/stalwart
echo 'LABEL=stalwart /var/lib/stalwart ext4 defaults,noatime 0 2' | sudo tee -a /etc/fstab
sudo mount -a && df -h /var/lib/stalwart

# ⚠ The container runs as uid 2000, NOT root. A root-owned volume gives
# "Permission denied (os error 13)" from inside the setup wizard, at the point
# it tries to save the data store settings — long after everything looked fine.
sudo chown -R 2000:2000 /var/lib/stalwart
```

Confirm the uid rather than trusting this number; a future image could change it:

```sh
sudo docker exec stalwart id     # expect uid=2000(stalwart)
```

Mounting by **label** rather than `/dev/vdb` on purpose: device names are
assignment order, and a second volume added later can renumber them. A mail
server whose datastore silently mounted the wrong disk is a bad morning.

Then the application:

```sh
sudo install -d -m 0755 /opt/agentsee
sudo git clone <repo> /opt/agentsee/repo
sudo ln -s /opt/agentsee/repo/stalwart /opt/agentsee/stalwart

cd /opt/agentsee/stalwart
echo "RELAY_SMTP_PASSWORD=<from the vault>" | sudo tee .env >/dev/null
sudo chmod 0600 .env

sudo docker compose up -d
sudo docker compose logs -f
```

⚠ **Verify the image name first.** The project renamed from `mail-server` to
`stalwart` and the Docker Hub tag may not have followed.

Complete the setup wizard — admin account, domain `agentsee.work`, listeners,
and ACME. Turn **DNS management off**: it would need Cloudflare credentials on
the box, and anything it wrote would be drift `tofu plan` knows nothing about.

### ⚠ ACME: the apex cannot validate, and empty SANs are not empty

Two traps here, and together they cost an hour.

**The certificate is requested for the Domain object's own name**, and SANs are
*additional* to it — there is no way to exclude it. Our mail domain is
`agentsee.work`, which resolves to **Cloudflare Pages**, because the apex is the
marketing site. TLS-ALPN-01 validates by connecting to `agentsee.work:443`, so
that identifier can never pass, and Let's Encrypt fails the **whole order** if
any single identifier fails.

So: set `agentsee.work` to **Manual** certificate management, and create a
**second Domain object for `mail.agentsee.work`** with ACME. Its own name points
at us, so it validates. A domain object existing for a certificate rather than
for mail is odd, and it is still better than the alternative — DNS-01 would work
for the apex but needs a Cloudflare DNS-edit token on an internet-facing box,
and that token can repoint the domain.

**Then set Additional Hostnames explicitly**, in full form:

```
mail.agentsee.work
```

Leaving it empty does not mean "no SANs". It means *default* SANs, which are
`autoconfig.`, `autodiscover.`, `mta-sts.` and `ua-auto-config.` prefixed to the
domain — four names that do not exist, four NXDOMAINs, and a failed order. A
non-empty list replaces the defaults.

**None of this appears until logging works** — see below. The only symptom is
`WARN No TLS certificates available` every thirty seconds, which says nothing
about why.

### ⚠ Set up a tracer, or the server is silent

Stalwart logs nothing to stdout by default, so `docker compose logs` shows only
the startup banner and `docker logs` is useless for everything after it.

In the admin UI, add a tracer of type **Console** — not File. A file tracer
writes to `/var/log/stalwart/` *inside the container*, which does not exist,
fails every startup, and is lost on recreate anyway.

Do this **before** debugging anything else. Every problem in this phase was
invisible until it was done.

> ✅ **Checkpoint 2 — PASSED 11 September 2026.** Volume mounted, and a real
> Let's Encrypt certificate (`CN=mail.agentsee.work`) served on 443, on 25 via
> STARTTLS, and on 993.

Check all three from **off the box**, not just the browser:

```sh
for p in 443 993; do
  openssl s_client -connect mail.agentsee.work:$p </dev/null 2>/dev/null \
    | openssl x509 -noout -issuer -subject
done
openssl s_client -connect mail.agentsee.work:25 -starttls smtp </dev/null 2>/dev/null \
  | openssl x509 -noout -issuer -subject
```

`issuer=...Let's Encrypt...`, not `CN=rcgen self signed cert` — that string is
Stalwart's built-in fallback and means ACME has not succeeded.

That last one is the real test of inbound 25, and it has to be run from off the
box. If it fails, the answer Infomaniak gave in phase 0 was about egress only,
and the build stops here rather than at cutover.

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

### ⚠ Creating the route is not enough — nothing selects it

An `MtaRoute` on its own is inert. Routes are chosen by the **route expression
on `MtaOutboundStrategy`** (Settings → MTA → Outbound → Strategies), which is a
singleton:

```
IF    is_local_domain(rcpt_domain)
THEN  'local'
ELSE  'SMTP2GO'
```

**Use the route's `Name`, single-quoted, exact case.** Not its opaque id, and
not a lowercased version. `'local'` and `'mx'` are built-in names, which is the
clue that names are what this field takes.

**An unresolvable route name falls back to MX silently.** No warning, no log
line — mail simply goes direct, which looks identical to the route not existing.
That single behaviour cost most of an afternoon: every wrong guess produced
exactly the same output as the previous wrong guess.

The test that tells you it worked is the delivery log:

```sh
sudo docker compose logs --since 2m | grep delivery.connect
```

`hostname = "mail.smtp2go.com"` means routed. `hostname = "gmail-smtp-in.l.google.com"`
means it is still going direct, whatever the config page says.

⚠ Stalwart's built-in **delivery test goes direct regardless** — it bypasses
routing, so it will happily report success while the relay is unused. Send a
real message.

### ⚠ The relay username is the SMTP user, not your login

SMTP2GO's SMTP users have their own generated usernames and their own passwords,
separate from the account you sign in with. Using the account email gives
`535 Incorrect authentication data`, which reads like a wrong password.

Prove the pair independently before blaming the wiring:

```sh
python3 - <<'PY'
import smtplib, ssl, getpass
u = input("SMTP2GO username: ").strip()
s = smtplib.SMTP_SSL("mail.smtp2go.com", 8465, context=ssl.create_default_context())
try:    s.login(u, getpass.getpass("password: ")); print("AUTH OK")
except Exception as e: print("AUTH FAILED:", e)
PY
```

### ⚠ `docker compose restart` does not re-read `.env`

`env_file` is read when the container is **created**. After editing `.env`:

```sh
sudo docker compose up -d --force-recreate
```

A plain `restart` leaves the old value in place, so a corrected password appears
to change nothing.

Then capture it, and from here it's declarative:

```sh
export STALWART_URL=https://mail.agentsee.work STALWART_TOKEN=...
stalwart-cli snapshot > plan.json

grep -iE '"@type"\s*:\s*"(Value|Password)"|secret|password' plan.json
```

**Read that grep output before committing.** The repo is public. If the relay
password inlined itself, switch the route's `authSecret` to the
`EnvironmentVariable` variant and re-snapshot.

Publish DKIM by putting the public keys in `infra/terraform.tfvars` and
re-applying. **Plural** — Stalwart signs with both Ed25519 and RSA, and the
selector carries its generation date, so there are at least two and they change
on rotation:

```sh
cd infra && $EDITOR terraform.tfvars   # dkim_records = { "v1-rsa-…" = "…" }
op run --env-file=op.env -- tofu apply
```

⚠ **Nothing breaks while these are missing.** Receivers report
`dkim=permerror (no key for signature)` and DMARC still passes, because the
relay's own aligned signature carries it. The failure only surfaces the day the
relay changes — which is the day you are least able to investigate it.

> ✅ **Checkpoint 3** — `plan.json` committed and clean, and
> `dig +short stalwart._domainkey.agentsee.work TXT` returns the key.

---

## Phase 4 — Prove it on the test subdomain

The phase that makes this safe. Real mail, real senders, a name nothing depends
on.

Send from an external account to `anything@test.agentsee.work`. It should
arrive.

⚠ **Give the test domain somewhere to deliver first** — a Catch-All Address on
the domain, or an account on it. Without one, transport succeeds completely and
then fails at the last inch with `550 Mailbox not found`, which looks like a
delivery problem and is not one.

The line that means it worked is `message-ingest.ham` with an `accountId`.
Everything before it — IPREV, SPF, DKIM, DMARC, TLS — can pass while the message
still goes nowhere.

**Inbound proven 14 September 2026:** real mail from Gmail, over IPv6, TLS 1.3,
all four checks passing, ingested into a mailbox.

### What the first bounce taught us

The `550 Mailbox not found` generated a DSN back to Gmail, and Google rejected
*that* with:

> Unauthenticated email from agentsee.work is not accepted due to domain's
> DMARC policy

Which is `p=reject` working exactly as intended — nothing is authorised to send
as us yet. Two things follow:

- **Bounces from this server are undeliverable until the relay is configured.**
  Not a fault; worth knowing before it looks like one.
- **The DSN went direct-to-MX**, not through SMTP2GO, which is the proof that
  outbound routing is still unset. That is the next step, not a bug.

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

### ⚠ Sign only headers the relay will not rewrite

Our signatures failed for two rounds before this was understood, and the failure
mode changes as you fix it, which is confusing in itself:

| Symptom | Means |
|---|---|
| `permerror (no key for signature)` | DNS lookup failed — key missing, or negative caching |
| `fail` | Key found, hash mismatch — **something was modified after signing** |
| `neutral (no key)` | Receiver does not support the algorithm |

Stalwart's default signed set is `Subject:To:From:Date:Message-ID`. SMTP2GO
rewrites **both** `Message-Id` and `Date` — the `Date` rewrite is easy to miss
because the relay usually stamps the same second the message was signed.

Working set for relayed mail:

```
From:Subject:To
```

The body hash is the tell for narrowing this down. If `bh=` is identical across
your signature and the relay's, and theirs passes, the body is untouched and the
problem is in the signed headers — no need to look anywhere else.

**Ed25519 will report `neutral (no key)` at Gmail.** It does not validate
RFC 8463 signatures. Neutral does not fail DMARC and the RSA signature carries
alignment, so this is not a fault to chase.

> ✅ **Checkpoint 4 — PASSED 14 September 2026.** Inbound from Gmail ingested
> into a mailbox; outbound routed through SMTP2GO with `spf=pass`,
> `dmarc=pass (p=REJECT)`, and **two** passing DKIM signatures — the relay's
> (`s989721`) and our own (`v1-rsa-20260911`), both `header.i=@agentsee.work`.

**If outbound fails:** SMTP2GO's dashboard logs every accepted message, and
"nothing in the log" versus "in the log but not delivered" points at completely
different halves of the system.

Nothing in the log means we never got there — check the SMTP user's
credentials, then that egress to `mail.smtp2go.com:8465` actually opens:

```sh
openssl s_client -connect mail.smtp2go.com:8465 -quiet
```

If that hangs, the host is filtering the port. **Do not "fix" it by moving to
465** — that is a port hosts commonly block. 2525 is the fallback.

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
/opt/agentsee/stalwart/backup/restore-test.sh secondary
```

Both must print **RESTORE TEST PASSED**. It restores to scratch and
boots a throwaway server against the restored data — if that server doesn't come
up, the backup is bytes rather than a recovery.

### ⚠ Deploying by `git pull` needs the exec bit committed

The scripts were committed `0644`, so installing them meant `chmod +x` on the
box — which git then reports as a local modification, and the next `git pull`
**aborts**. The old script keeps running, the fix appears not to have worked,
and you debug something you already fixed. Mode is committed now; deployment is
a pull and nothing else.

### What the first restore test actually found

It failed, and the failure was in the test rather than the backups — which is
the good outcome, found on a Monday afternoon rather than during an incident.

The throwaway container ran the image's default
`--config /etc/stalwart/config.json`, a path absent from the restored tree, so
it started in **bootstrap mode** with an empty datastore. Had the port check
matched, it would have reported a healthy server and passed while proving
nothing. There is now an explicit assertion that the bootstrap banner is
**absent**.

It also ran with a working network, and the restored datastore contains the
**MTA queue** — a server booted on real data with real credentials would have
re-delivered those messages. It now runs `--network none`, so the evidence is
the startup log rather than an HTTP response. That is a more direct test anyway:
those lines only appear once RocksDB has opened and the config has been read out
of it.

> ✅ **Checkpoint 5 — restores PASSED 14 September 2026**, both repositories:
> snapshot restored, RocksDB intact, server booted, config read, no corruption.
>
**Healthchecks: one per repository**, both proven 14 September 2026 — green on a
real run, and `/fail` produces an alert that arrives at a **personal** address.
Settings are *Simple*, period 1 day, grace 2 hours: the timer is 03:20 with 20
minutes of deliberate jitter, so a tighter grace would cry wolf, and a check
that cries wolf gets muted.

Two checks rather than one because a shared check stays green on the primary's
ping while the secondary fails silently — and "we have two copies" would then be
false in the way that is only discovered when both are needed.

> Still outstanding before cutover: the restic passphrase on **paper** as well
> as in the vault.

Test the second healthcheck separately. A shared switch would keep reporting
healthy while the secondary silently failed, and two copies would be false in
the way that is only discovered when both are needed.

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
op run --env-file=op.env -- tofu plan    # should be ~2 creates. Nothing else.
op run --env-file=op.env -- tofu apply
```

Then watch:

```sh
dig +short agentsee.work MX        # → mail.agentsee.work
```

Send to `hello@agentsee.work` from an external account. Reply. Confirm it lands.

> ✅ **Checkpoint 6** — apex MX is ours, mail flows both ways, `header.d` is
> still `agentsee.work`.

**Rollback:** set `enable_apex_mx = false`, `op run --env-file=op.env -- tofu
apply`, re-enable Email Routing. Mail sent during the gap is not lost — senders retry for days.

---

## Phase 7 — Re-arm DMARC

**This phase turned out to be unnecessary, and that is worth understanding.**

The plan assumed we would relax DMARC to `p=none` during the migration and
restore `p=reject` afterwards, because that is what sending through Gmail's
"send mail as" requires — it signs as Gmail, so nothing aligns.

Relaying through a provider with a **verified sender domain** is different from
the start: SMTP2GO signs as `agentsee.work` and the VERP return-path puts SPF on
a subdomain of ours. Both align immediately. Our first outbound message scored:

```
dmarc=pass (p=REJECT sp=REJECT dis=NONE) header.from=agentsee.work
dkim=pass  header.i=@agentsee.work header.s=s989721
spf=pass   smtp.mailfrom=…@em989721.agentsee.work
```

So `p=reject` was never relaxed, and the domain was never briefly spoofable.
Keep this phase for the case where it is needed — a future sender that cannot
align — but do not relax DMARC preemptively. Verify the sender domain at the
relay first and check whether you need to at all.

If you do need it: wait for aggregate reports at `dmarc@agentsee.work` to show
our own mail passing with alignment. Days, not hours.

```sh
cd infra
$EDITOR terraform.tfvars          # dmarc_policy = "reject"
op run --env-file=op.env -- tofu apply
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
