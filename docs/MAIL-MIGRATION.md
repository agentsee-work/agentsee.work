# Mail migration — plan, not yet done

**Status: proposed. Nothing here has been executed.** When it has, this file
folds into the README's "Mail" section and gets deleted. Until then the live
setup is still Cloudflare Email Routing, forward-only, and the README describes
what is actually true.

## Why we're moving

Email Routing forwards. It does not host and it cannot send. That was the right
call for a landing page with two `mailto:` links, and it stops being the right
call the moment we need to *reply* as `@agentsee.work` — guest outreach for the
show, press, and account recovery on the social platforms. A support appeal that
says "reply from the address on the account" is currently impossible for us.

The second reason is ownership. Mail that lands only in two personal Gmail
inboxes is mail that belongs to two people rather than to the thing we're
building. Same problem we identified with the social accounts.

## Not self-hosting

Worth stating plainly so it doesn't get relitigated at midnight.

Running our own SMTP means owning IP reputation, and a fresh VPS IP under a
six-month-old domain has none. Gmail and Outlook will junk us silently — no
bounce, no signal, just guests who never reply and no way to tell that from
disinterest. Building that reputation back takes months of clean volume we don't
have. One leaked credential and we're on Spamhaus with the same problem again.
It is also a 24/7 obligation: a Pages deploy that breaks can be rolled back in a
minute, and mail that bounces during an outage is a conversation that didn't
happen.

The sovereignty we actually care about is **owning `agentsee.work` and its DNS**,
and we already have that. It means the provider is swappable in an afternoon.
That is the real independence; running the SMTP daemon ourselves is the costume.

The site's zero-third-party rule is about what a *visitor's browser* is made to
fetch. It has never been a rule against using vendors — the whole thing runs on
Cloudflare.

## Choosing a provider

**Provider choice is still open.** The requirement changed after the first pass:
we want a *suite* — calendar, shared files, video, docs — not two nice mailboxes.
That rules out the pure-mail options we started with. Recorded here so the
reasoning survives.

Priced for **two users, per year**, everything converted to GBP at August 2026
rates. Suite column is what you get that isn't an inbox.

| Provider / plan | 2 users/yr | Beyond the inbox |
|---|---|---|
| **Infomaniak kSuite** (Standard) | ≈ **£37** | kDrive, kMeet video, kChat, Docs. Swiss, own datacentres |
| **Zoho Workplace** (Standard) | ≈ **£57** | WorkDrive, Writer/Sheet/Show, Cliq, Meeting. 30 GB mail |
| **mailbox.org** (Standard, annual) | ≈ **£61** | Calendar, contacts, 10 GB cloud, office editing, video |
| **Fastmail** (Business Standard) | **£108** | Calendar, contacts, masked email — and not much else |
| **Microsoft 365** (Business Basic) | **£130** | 1 TB OneDrive *each*, Teams, 100 GB mailbox, web Office |
| **Google Workspace** (Starter) | **£142** | 30 GB, Drive, Docs, Meet 100. Booking is Standard-only |
| **Infomaniak kSuite Business** | ≈ **£155** | As above, 3 TB/user, 5 addresses |
| **Proton** (Workspace Standard) | ≈ **£250** | Everything + Pass + VPN. 2.5× the budget |

**Fastmail is withdrawn.** It is the best pure email on the list and that is now
the wrong axis — £108/year for mail, calendar and contacts is poor value next to
Microsoft at £130 with a terabyte each, and indefensible next to Zoho at £57 with
an actual suite.

**Proton is out on price**, which is a shame: its Pass vaults would have solved
the shared-credential problem in [SOCIAL.md](SOCIAL.md) in the same purchase. Buy
a password manager separately for a tenth of the difference.

**Google and Microsoft are out on the feature we'd actually use.** The one thing
a show needs is guest booking, and it is above the entry tier on both — Google
puts appointment booking in Standard (£198+/year for two) and Microsoft puts
Bookings in Business Standard. Paying £142 to *not* get the feature is the worst
outcome on the table.

### Decision: Infomaniak kSuite Standard

Swiss, running its own datacentres on renewable power, and the positioning maps
onto ours exactly — this is a site that self-hosts its typeface and makes zero
third-party requests, and it would sit badly to route all our mail through an
ad company.

Figures below are from Infomaniak's own pricing pages, read on 23 August 2026.
**Third-party summaries of this product are unreliable** — they disagree with
each other and with the source on both price and storage, because the range was
recently renamed. Anything quoting "kSuite Pro" as a tier is describing the old
lineup: `kSuite Pro` is the product family, and the tiers inside it are
Standard, Business and Enterprise.

| Tier | Per user/month | Storage/user | Addresses/user |
|---|---|---|---|
| **Standard** | **CHF 1.76** | 50 GB | 2 |
| Business | CHF 7.33 | 3 TB | 5 |
| Enterprise | CHF 13.83 | 6 TB | 10 |

**Standard, two users: CHF 42/year — about £37.** The earlier open questions are
both settled favourably: a custom domain is not merely supported on Standard, it
is *required* on every paid tier, and storage is 50 GB per user rather than the
15 GB a search result claimed. Mail, kDrive, kChat, kMeet, Calendar and the
online office suite are all included at Standard; Business and Enterprise buy
storage, more addresses and priority support, not the apps themselves.

Sending limits are 1440 messages/day and 100 recipients/message — far above
anything guest outreach will do, and low enough to confirm this is not a bulk
sending platform. Newsletter stays a separate decision.

**Two things to check at signup**, neither a blocker:

- **"2 email addresses per user" is a mailbox count, not an alias count.** Our
  plan needs `james@` and `abrar@` as mailboxes plus four aliases. Aliases are
  listed as a standard feature, so this should be four mailboxes' worth of
  headroom and unlimited aliases — but confirm before deleting the Cloudflare
  rules, because the whole migration depends on it.
- **Whether the first user is genuinely free.** One source claims a free user
  for life on Standard; the pricing page says "From CHF 1.76 / month". If true
  the bill halves. Don't plan around it.

**Zoho Workplace remains the fallback** at ≈£57/year if the alias limit turns out
to be real: denser suite, IMAP and SMTP throughout. The cost is aesthetic —
Zoho's UI is enterprise-cluttered in a way that will irritate us daily.

### Budget the difference, don't pocket it

Both leading options come in far under £100, and the gap should go to the thing
no mail suite at this price does well:

- **Guest booking** — Cal.com's free tier covers a two-person show, and it beats
  the paid booking in Google Standard anyway.
- **Shared credential vault** — needed regardless, per [SOCIAL.md](SOCIAL.md).
- **Newsletter sending** — we publish numbered issues. When Issue No. 2 wants a
  mailing list, none of these suites is the tool, and sending bulk from our own
  domain has its own reputation consequences. That is a separate decision, and
  it should not be made by accident.

## Order of operations

The sequencing is load-bearing. **DMARC comes down before anything sends, and
DKIM goes up before MX moves.** Doing it in a different order means either our
own mail gets rejected, or it flows unsigned for a window.

Every DNS step below is scripted in [`tools/mail-cutover.sh`](../tools/mail-cutover.sh)
— dry-run by default, `--confirm` to write. The values are Infomaniak's,
confirmed against their own zones on 24 August 2026: MX `mta-gw.infomaniak.ch`
priority 5 (a single record, not a pair), SPF `include:spf.infomaniak.ch`.

### 0. Set up, touch no DNS

Sign up, add `agentsee.work`, complete Fastmail's domain ownership check. Do not
change MX yet. Everything so far is reversible.

### 1. DMARC to `p=none` — first, and alone

```
_dmarc.agentsee.work   TXT   v=DMARC1; p=none; sp=none; rua=mailto:dmarc@agentsee.work; fo=1
```

Both `p` and `sp` — the current record sets `sp=reject` too. Keep `rua`; the
aggregate reports are how we prove the next steps worked. Let the TTL pass before
continuing.

### 2. DKIM — before the cutover, not after

> ⚠ **Infomaniak's automatic DKIM does not apply to us.** Their docs are explicit
> that keys are activated automatically "for customers whose domain name has its
> DNS zone managed by Infomaniak". Ours is on Cloudflare and is staying there —
> the zone runs the website. So DKIM is a **manual** step: take the selector and
> public key from the Infomaniak Mail admin once the domain is added, and publish
> it here yourself.
>
> This is the one value in the whole migration that can't be written in advance,
> and the one most likely to get skipped — nothing visibly breaks without it.
> What breaks is invisible: SPF alone survives until the first forwarded message,
> and `p=reject` without DKIM means our own mail starts vanishing.

```sh
./tools/mail-cutover.sh dkim <selector> "<v=DKIM1; k=rsa; p=…>" --confirm
```

### 3. The cutover — SPF and MX together

This is the one-way door. Pick a quiet hour.

```
agentsee.work   TXT   v=spf1 include:spf.infomaniak.ch ~all
agentsee.work   MX    5   mta-gw.infomaniak.ch
```

The script drops the three Cloudflare Email Routing MX records and the old
`include:_spf.mx.cloudflare.net` SPF in the same step. Keep `~all` rather than
Infomaniak's documented `-all` until alignment is proven; tighten afterwards.

> **Email Routing locks its own records.** The API refuses to delete apex MX/SPF
> while Email Routing is enabled for the zone. Disable it first in the dashboard
> — Email → Email Routing → Settings → Disable — then re-run. This does not
> affect subdomains, so the `in.agentsee.work` pipeline in
> [AGENT-MAIL.md](AGENT-MAIL.md) is untouched.

The moment MX changes, Email Routing stops receiving and `email-fanout` stops
running. Mail in flight is not lost: sending servers retry for days, so a gap of
minutes costs nothing. MX records are never proxied, so the orange cloud on the
apex is irrelevant here — the website is unaffected throughout.

### 4. Real mailboxes, real aliases

| Address | Kind |
|---|---|
| `james@`, `abrar@` | Mailboxes |
| `hello@`, `show@`, `accounts@`, `dmarc@` | Aliases → both mailboxes |

A Fastmail alias can deliver to more than one user in the account, which is the
thing Email Routing refused to do. **Verify this on day one** — the entire
`email-fanout` Worker exists because we assumed fan-out was easy once and were
wrong. If it works, the Worker is dead code.

### 5. Prove alignment, then re-arm DMARC

Send to a Gmail address, open *Show original*, and require all three:

```
spf=pass   header.from=agentsee.work
dkim=pass  header.d=agentsee.work
dmarc=pass
```

`header.d` must say `agentsee.work`, not `messagingengine.com`. That is the
alignment the README's warning is about — and the reason this works now when
Gmail's "Send mail as" wouldn't: Fastmail signs as *our* domain, so DKIM aligns
and we can genuinely go back to `p=reject` rather than living at `p=none`.

Watch the aggregate reports at `dmarc@` for a few days, then restore:

```
_dmarc.agentsee.work   TXT   v=DMARC1; p=reject; sp=reject; rua=mailto:dmarc@agentsee.work; fo=1
```

### 6. Clean up

- Delete `cf2024-1._domainkey` — Cloudflare's DKIM, only ever for forwarded mail.
- Delete the Email Routing rules and the `hello@`/`show@` Worker route.
- **Keep `workers/email-fanout` deployed.** Its fan-out job ends when aliases
  take over, but the Worker is the working prototype of the agent-trigger
  pipeline in [AGENT-MAIL.md](AGENT-MAIL.md) — it receives raw mail
  programmatically, which is the whole hard part. Do not delete it in cleanup.
- Rewrite the README "Mail" section. The ⚠ block becomes a completed procedure
  rather than a warning, and the runbook's "Email Routing cannot fan one address
  out to two people" becomes history rather than a live constraint.
- **Keep the Email Routing token scopes.** They looked droppable until the agent
  substrate turned out to need Email Routing on `in.agentsee.work`.

## What this costs us

Email Routing was free. This is £108/year, and it removes a Worker, two
Cloudflare permissions and a documented limitation in exchange. The thing we
lose that's worth naming: Cloudflare Email Routing gave us unlimited addresses at
zero marginal cost, so `anything@agentsee.work` was free. Fastmail aliases are
also unlimited, so this is a wash — but only on Business/Standard, not on the
plans that meter them.

## Sources

- [Fastmail pricing](https://www.fastmail.com/pricing/)
- [Fastmail: setting up your domain, MX only](https://www.fastmail.help/hc/en-us/articles/1500000280261-Setting-up-your-domain-MX-only)
- [Migadu pricing](https://www.migadu.com/pricing/)
