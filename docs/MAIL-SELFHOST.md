# Self-hosted mail — build spec

Own the inbox, rent the reputation. The buy-instead path stays in
[MAIL-MIGRATION.md](MAIL-MIGRATION.md) and is still the fallback.

**Status: spec, nothing built.**

## The shape

```
                 inbound                          outbound
                    │                                 │
 sender ──MX──► mail.agentsee.work            Stalwart ──465──► Amazon SES ──► world
                    │  (your VPS)                                    │
                    ▼                                          rented reputation
              Stalwart: SMTP · IMAP · JMAP
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
   Apple Mail (IMAP)      agents (JMAP/HTTPS)
```

**The VPS never sends directly to the internet.** Every outbound message goes to
SES over authenticated submission. Three consequences, and they are the whole
reason this design works:

- **Our IP reputation is irrelevant.** The unwinnable part of self-hosting is
  bought, for pennies.
- **Outbound port 25 blocks don't matter.** Hetzner and most clouds block it by
  default; we never use it.
- **What remains is ordinary sysadmin** — a service, a disk, backups. No
  reputation, no deliverability guesswork, no silent junking.

## Parts and cost

| Part | Choice | Per year |
|---|---|---|
| Host | Hetzner CX22, 2 vCPU / 4 GB (Stalwart needs 512 MB) | ≈ £47 |
| Server | Stalwart — SMTP, IMAP, JMAP, CalDAV, CardDAV, spam filter, ACME | £0 |
| Relay | Amazon SES à la carte, **$0.10 / 1000**, no minimum | ≈ £1 |
| Backups | restic → Cloudflare R2 (no egress fees, account already exists) | ≈ £1 |
| **Total** | | **≈ £50** |

Cheaper than Fastmail, more than kSuite, plus your time — which is the real
price and doesn't appear in the table. UK alternative to Hetzner if the
jurisdiction matters: Mythic Beasts.

At two people's correspondence volume SES costs cents per month. **The one
hurdle is sandbox exit** — new SES accounts are capped and can only send to
verified addresses until you request production access. Do that on day one; it
is not instant.

## DNS

All on Cloudflare, which already runs the zone.

```
mail.agentsee.work   A      <vps-ip>          ← DNS ONLY. Grey cloud.
agentsee.work        MX 10  mail.agentsee.work
agentsee.work        TXT    v=spf1 include:amazonses.com ~all
<sel>._domainkey     TXT    <Stalwart's public key>
_dmarc               TXT    v=DMARC1; p=none; … → p=reject once aligned
```

**`mail.agentsee.work` must be grey-clouded.** Cloudflare proxies HTTP, not SMTP;
orange-clouding it silently breaks inbound mail. This is the same class of trap
as the Email Address Obfuscation one in the runbook — a Cloudflare default doing
something helpful to something that isn't HTTP.

**SPF names SES, not the VPS**, because the VPS never sends. Listing its IP would
be authorising something that doesn't happen.

**Let Stalwart hold the DKIM key, not SES.** Both can sign, and it's tempting to
let SES do it. Don't: a key that lives with the relay has to be rebuilt if the
relay ever changes, and DKIM is the thing standing between `p=reject` and our own
mail disappearing. Ours, published in our zone, signed before handoff.

Set the VPS **PTR** to `mail.agentsee.work` at the host. It matters less when
relaying, but a mismatched PTR is free suspicion on inbound connections.

## Relay configuration

Stalwart has first-class relay support: **Settings → SMTP → Outbound → Relay
Hosts**, then point routing at it under **Outbound → Routing**.

```
address    email-smtp.<region>.amazonaws.com
port       465
protocol   SMTP,  tls.implicit = true
auth       SES SMTP credentials (NOT your AWS access keys — separate things)
```

⚠ **Disable DANE and MTA-STS on the relay route.** Both assert things about
direct-to-MX delivery that are false when a smarthost is in the path, and
leaving them on produces delivery failures that look like TLS bugs.

## Backups are the whole risk

Downtime is not the danger — senders retry for days, so an hour offline costs
nothing. **Disk loss is unrecoverable and permanent.** Everything else in this
document is reversible; this isn't.

```
restic → Cloudflare R2, nightly, encrypted, with a retention policy
```

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

- Firewall to **25, 443, 993, 22** only. SSH key-only, no passwords.
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
   for the old format. **Request SES production access now too** — it is the
   slowest step and blocks nothing else.
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
