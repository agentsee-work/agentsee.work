# Self-hosted mail — build spec

Own the inbox, rent the reputation. The buy-instead path stays in
[MAIL-MIGRATION.md](MAIL-MIGRATION.md) and is still the fallback.

**Status: spec, nothing built.**

## The shape

```
                 inbound                          outbound
                    │                                 │
 sender ──MX──► mail.agentsee.work           Stalwart ──8465──► SMTP2GO ──► world
                    │  (your VPS)                                    │
                    ▼                                          rented reputation
              Stalwart: SMTP · IMAP · JMAP
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
   Apple Mail (IMAP)      agents (JMAP/HTTPS)
```

**The VPS never sends directly to the internet.** Every outbound message goes to
the relay over authenticated submission. Three consequences, and they are the
whole reason this design works:

- **Our IP reputation is irrelevant.** The unwinnable part of self-hosting is
  bought, for pennies.
- **Outbound port 25 blocks don't matter.** Infomaniak blocks it by default,
  as does nearly every host; we never use it, and never have to ask.
- **What remains is ordinary sysadmin** — a service, a disk, backups. No
  reputation, no deliverability guesswork, no silent junking.

## Parts and cost

| Part | Choice | Per year |
|---|---|---|
| Host | Infomaniak `a2-ram4-disk0`, 2 vCPU / 4 GB | **€5.29/mo** ≈ £54 |
| Root disk | `-disk20-perf1` instead, for a 20 GB root | *confirm at signup* |
| Volume | 20 GB for `/var/lib/stalwart` | *confirm at signup* |
| Server | Stalwart — SMTP, IMAP, JMAP, CalDAV, CardDAV, spam filter, ACME | £0 |
| Relay | SMTP2GO free tier, 1,000 messages/month, no card | £0 |
| Backups | restic → Infomaniak Swiss Backup, mirrored to R2 | see below |
| **Total** | | **£60–80** |

The €5.29 is Infomaniak's published figure for the `disk0` flavour, where the
root disk is only as large as the image. The variants with a real root disk and
the price of block storage are **not** figures I have, so the total is a range
rather than a number — settle it on the pricing page before committing, and
replace this table with what you actually paid.

**Backups are deliberately not in that total.** Swiss Backup was bought as a
1 TB general backup product — laptops, `/opt/lab`, whatever else — and mail uses
single-digit GB of it. Loading its subscription onto the mail line would make
self-hosting look far more expensive than it is. The marginal cost of mail's
backups is the R2 second copy, which the free tier covers.

More than kSuite either way, plus your time, which is the real price and
doesn't appear here at all. The host is essentially the entire bill, so the
honest comparison is one VPS against a hosted mailbox, and it is not a saving.

### Why Infomaniak, and why that costs more

Hetzner is cheaper — around £47/yr for the equivalent box — and was the
original choice. It lost on grounds that are not technical.

Provider conduct is a selection criterion for this agency, on two sides that
have to be held together. A host should refuse to carry fascists and organised
harassment of marginalised people, and should hold the line when a state or a
corporation leans on legitimate journalism. Those are not in tension; the
second is not a licence for the first.

- **Vultr** failed the second outright. It took FreeWeChat offline on a
  Tencent-instigated complaint, ignored a claim-by-claim rebuttal and a letter
  from 17 press-freedom organisations, then terminated GreatFire's account
  "without cause" in November 2025.
- **Hetzner** terminated Unredacted, a non-profit running censorship-evasion
  services. Open public proxies attract genuine abuse, so this is a closer call
  than Vultr's — but it lands on the wrong side of the line.
- **Infomaniak** is a certified B Corp, employee-owned, has never taken outside
  investment, runs on 100% renewable energy and offsets 200%. B Corp matters
  here because it is third-party assessed rather than self-declared.

The difference is roughly £15–30/year. It buys nothing technical, and the honest
framing is that it is a values purchase rather than a risk mitigation — nothing
we host will ever attract a Tencent-scale takedown. That is still a reason.

Swiss jurisdiction is the incidental bonus, and the fact that they run email
for a living means running an MX is not a policy argument.

### Why SMTP2GO and not SES

SES is cheaper per message ($0.10/1000) and the better answer at volume. It was
the original choice here and it lost on one thing: **new SES accounts start in a
sandbox**, capped and able to deliver only to addresses you have verified, and
leaving it requires a support request reviewed by a human. No API, no timebox.
That review sat squarely on the critical path for the whole build.

SMTP2GO's free tier is 1,000 messages a month with no card. Two people's
correspondence does not approach that, and it is enough to prove the entire
pipeline end to end. If we outgrow it, Starter is $10/month for 10,000 — and
moving to SES instead is one `MtaRoute` object plus a DNS change.

The cost of the swap is real but small: SES has an OpenTofu provider and
SMTP2GO does not, so its sender domain is registered by hand. See
[../infra/README.md](../infra/README.md).

## DNS

All on Cloudflare, which already runs the zone.

```
mail.agentsee.work   A      <vps-ip>          ← DNS ONLY. Grey cloud.
agentsee.work        MX 10  mail.agentsee.work
agentsee.work        TXT    v=spf1 ~all             ← authorises nothing. see below
<3 CNAMEs>                   → smtp2go                ← issued by them, per account
<sel>._domainkey     TXT    <Stalwart's public key>
_dmarc               TXT    v=DMARC1; p=none; … → p=reject once aligned
```

**`mail.agentsee.work` must be grey-clouded.** Cloudflare proxies HTTP, not SMTP;
orange-clouding it silently breaks inbound mail. This is the same class of trap
as the Email Address Obfuscation one in the runbook — a Cloudflare default doing
something helpful to something that isn't HTTP.

**SPF authorises nobody, and that is correct.** It looks like an omission, so:
SMTP2GO does not use an SPF include. Since 2019 they use VERP, setting the
return-path to a subdomain of ours that is CNAMEd to them — so SPF is evaluated
against *that* subdomain and their record. Their own docs say you do not need to
change your existing SPF record. DMARC still passes, because relaxed alignment
accepts a return-path on a subdomain of the From domain.

The VPS never sends directly either, so listing its IP would authorise something
that doesn't happen. Nothing sends with an apex envelope-from, so nothing is
authorised to. Tighten `~all` to `-all` in the same change that sets `p=reject`.

**Let Stalwart hold a DKIM key of its own, as well as the relay's.** SMTP2GO
signs via the second of its three CNAMEs — delegated to their DNS, so they hold
that private key and it leaves when they do. Sign with our own key too, before
handoff, published as a TXT record in our zone. Two aligned signatures is legal
and DMARC passes if either validates; ours is the one that survives changing
relay, and DKIM is what stands between `p=reject` and our own mail disappearing.

⚠ **Turn SMTP2GO's link tracking off.** It rewrites URLs in the message body,
and Stalwart signed that body before handoff — a rewrite after signing should
invalidate our signature. That is a deduction from how DKIM body hashing works
rather than something SMTP2GO documents, so verify it at the alignment check
rather than trusting it. It is unwanted regardless: this is correspondence.

**PTR is nearly irrelevant here, and that is worth stating** because every
mail-server guide insists on it. A PTR is checked on connections a mail server
*makes*, and this one never delivers direct-to-MX — everything outbound goes to
the relay, whose reputation is what matters. On Infomaniak's `ext-net1` the PTR
is platform-assigned and changing it is a support request; leave it. If we ever
send direct, that flips, and `infra/server.tf` explains what to change.

## Relay configuration

Stalwart has first-class relay support: **Settings → SMTP → Outbound → Relay
Hosts**, then point routing at it under **Outbound → Routing**.

```
address    mail.smtp2go.com
port       8465                      (implicit TLS, on a port nobody filters)
protocol   SMTP,  tls.implicit = true
auth       an SMTP user from Sending > SMTP Users — not the account login
```

⚠ **Use 8465, not 465.** Infomaniak blocks outbound 25 by default and will
open it on request; most hosts block some combination of 25, 465 and 587.
8465 is SMTP2GO's implicit-TLS port on a number nobody filters, so the relay
path stops depending on the host's port policy at all. 2525 is the fallback.
Don't drop to a STARTTLS port without requiring TLS on the route — a stripped
connection would offer these credentials in clear.

⚠ **Disable DANE and MTA-STS on the relay route.** Both assert things about
direct-to-MX delivery that are false when a smarthost is in the path, and
leaving them on produces delivery failures that look like TLS bugs.

## Backups are the whole risk

Downtime is not the danger — senders retry for days, so an hour offline costs
nothing. **Disk loss is unrecoverable and permanent.** Everything else in this
document is reversible; this isn't.

```
restic → Infomaniak Swiss Backup  (primary)   nightly, encrypted, retention
restic → Cloudflare R2            (secondary)  same run, different company
```

**Two copies at two companies, because the server is at one of them.** Swiss
Backup is 1 TB already bought and Infomaniak documents restic against it, so it
is the obvious primary. It is also the same account that runs the mail server —
so one suspension, billing failure or compromised login would take the server
and every copy of the mail at the same moment, which is exactly the event a
backup exists for.

R2's free tier is 10 GB-month with no egress, and this repository is well under
it, so the independent second copy costs approximately nothing. Details and the
`restic copy` trap are in
[`stalwart/backup/README.md`](../stalwart/backup/README.md).

⚠ **Two copies is not two kinds of protection.** Both credentials live on the
box, and Infomaniak's immutability is Acronis-only — unavailable on the S3 and
Swift protocols restic uses. So this defends against losing the *account*, not
against someone owning the *box*.

**Restore-test it on a schedule.** An untested backup is a hope. Put the first
restore test in the calendar before the first real message arrives, and repeat
it quarterly — the failure you're guarding against is the backup having silently
covered the wrong directory for eight months.

## Monitoring

A port check is not enough: an open port with a full disk or a wedged queue
still looks healthy.

- External uptime check on **25** and **993**.
- **A daily canary** — an external job sends to a canary address and alerts if it
  doesn't land within N minutes. This is what actually catches silent failure.
- Alert on **queue depth** and **disk usage**. Both fail slowly and then all at
  once.

## Security

The VPS IP is public by necessity — that's what an MX is.

- Two security groups. `agentsee-mail` opens **25, 80, 443, 465, 993** to the
  world and **22** to named CIDRs; `agentsee-lab` starts empty and is where
  ports for experiments go. Splitting them means widening the box for something
  you're testing is never the same change as widening the mail server.
- SSH key-only, no passwords. The login is `debian`, not root.
- Unattended security upgrades on.
- Stalwart supports **encryption at rest** with your own S/MIME or PGP key, so
  disk access alone doesn't read the mail. Worth turning on given it holds
  everything.
- The agent trigger pipeline stays on the Cloudflare Worker at
  `in.agentsee.work` — see [AGENT-MAIL.md](AGENT-MAIL.md). It could run on this
  box now, and it shouldn't: the quarantine boundary is the point, and it's
  worth more than the convenience of having it all in one place.

## Order of build

The phase-by-phase procedure, with checkpoints and rollbacks, is in
[MAIL-BUILD-RUNBOOK.md](MAIL-BUILD-RUNBOOK.md). The shape of it:

Do not cut the apex over to an unproven server.

1. **Provision.** `cd infra && tofu apply` — see [infra/](../infra/). Provisioning
   is no longer a step so much as an artifact; installing Stalwart on top of the
   box is the only hand work.
2. **Deploy Stalwart** — [`stalwart/`](../stalwart/). Note its config is *not*
   a `config.toml`: since v0.16 everything lives in the datastore and is
   reconciled declaratively with `stalwart-cli apply`. Most guides online are
   for the old format. **Verify the sender domain at SMTP2GO now too** — the
   three CNAMEs it issues have to propagate before alignment can pass.
3. **Prove it on a subdomain.** Point `test.agentsee.work` MX at the box and send
   it real mail from outside. The apex keeps working on Cloudflare Routing
   throughout, so there is no window where the show's mail is at risk.
4. **Verify alignment from the test domain** — Gmail → *Show original* →
   `spf=pass`, `dkim=pass` with `header.d=agentsee.work`, `dmarc=pass`.
5. **Backups and restore test before cutover**, not after. If the first restore
   test happens after real mail exists, it isn't a test.
6. **Lower the apex MX TTL**, wait for it to take effect, then cut over.
7. **Restore `p=reject`** once aggregate reports show our own mail aligning.

Step 3 is what makes this safe. Everything is proven against a subdomain nobody
depends on, and the apex moves only once.

## When to abandon it

Worth deciding now, while it's still a nice idea rather than a sunk cost:

- A restore test fails and isn't fixed the same week.
- The canary catches two silent delivery failures in a quarter.
- It stops being interesting and becomes a chore.

Any of those, move to kSuite. The addresses don't change, the domain doesn't
change, and [MAIL-MIGRATION.md](MAIL-MIGRATION.md) is the runbook for it. That
exit staying cheap is what makes this worth trying.
