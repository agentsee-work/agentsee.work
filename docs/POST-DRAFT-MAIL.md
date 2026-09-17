# Draft — "We built our own mail server. Here is what it cost."

**Status: draft, not published.** Lives in `docs/` so it stays out of `public/`.
When it is ready it becomes Issue No. 2 under `public/issues/` and gets a row in
the register, per the rule in the README: never list an issue that has not been
published.

Written to inform a podcast on the same subject, so it is structured as an
argument with evidence rather than a chronology.

---

## The shape of the piece

**Thesis:** owning your own mail is now a reasonable thing to do, working with
an AI made it *possible* in a fortnight rather than a quarter, and the honest
accounting is that we spent time to buy understanding and control — not money.
Anyone telling you it is cheaper is selling something.

**Anti-thesis to avoid:** "AI built our mail server." It did not. It read a
great deal, wrote the configuration, and was confidently wrong often enough that
the interesting content is *where* and *how that got caught*.

---

## 1. What we set out to do

Two people, building in public. The pitch involves deploying client work as
infrastructure-as-code, and that claim is worth much less if your own first
server was clicked together in a dashboard.

The starting position was worse than nothing: Cloudflare Email Routing forwarded
`@agentsee.work` to personal inboxes and **structurally could not send**. Every
account signup, every reply to a guest, every password reset went out from a
personal Gmail. The domain published `p=reject`, which was *true* — nothing was
authorised to send as us, because nothing could.

The design that emerged: **own the inbox, rent the reputation.** Stalwart on a
VPS handles inbound and stores the mail; everything outbound goes to a relay
over authenticated submission. That single decision removes the unwinnable part
of self-hosting — a fresh IP has no sending history, and "no history" is
indistinguishable from "a spammer rented this an hour ago".

## 2. The decisions, and what they actually turned on

Worth writing up individually; each is a small essay.

**SES → SMTP2GO.** SES is cheaper per message and was the first choice. It lost
on a non-technical property: new accounts start in a sandbox, and leaving it
needs a support request reviewed by a human with no API and no timebox. An
unbounded wait sat on the critical path. SMTP2GO's free tier is 1,000/month with
no card — more than two people's correspondence.

**Hetzner → Infomaniak, on conduct.** This is the one most worth arguing
properly, because the criterion has two sides that have to be held together: a
host should refuse to carry fascists and organised harassment, *and* hold the
line when a state or corporation leans on legitimate journalism. Vultr failed
the second — it took FreeWeChat offline on a Tencent-instigated complaint,
ignored a rebuttal and a letter from 17 press-freedom organisations, then
terminated GreatFire's account "without cause". Infomaniak is a certified B Corp,
employee-owned, Swiss. It costs about £15–30/year more and buys nothing
technical. That is a values purchase and the piece should say so plainly rather
than dressing it as risk mitigation.

**The cost, honestly.** ≈£60–80/year against ≈£37 for hosted kSuite. Self-hosting
is *not* cheaper at two users. It wins on control and on learning, and the write-up
is worthless if it pretends otherwise.

## 3. The failures — the actual content

Sixty-five commits. The ones worth telling, each with the same shape: *it looked
like one thing and was another.*

**One missing package silently emptied the whole box.** `cloud-init` installs a
package list atomically. `docker-compose-plugin` does not exist in Debian, so
the whole list failed — docker, restic, fail2ban, unattended-upgrades, all
absent. The instance came up, SSH worked, files were written. The only evidence
was in `cloud-init status --long`, which nothing prompts you to run.

**The setup wizard that kept reappearing.** Stalwart's config path is
`/etc/stalwart/config.json` *inside the container*. Ours was mounted somewhere
nothing reads. So it found no configuration, entered bootstrap mode, and the
wizard ran happily to completion against storage that did not persist. Restart,
fresh wizard, fresh password, as though nothing had happened. There is no error
in that sequence anywhere.

**Then the same class of bug again**, one layer down: the volume was mounted at
`/opt/stalwart` while the wizard defaulted the datastore to `/var/lib/stalwart`
— a path that existed only inside the container's ephemeral layer. RocksDB wrote
happily to somewhere that vanished on every recreate. The fix was mounting the
volume at the *same path* inside and out, which sounds like tidiness and is
actually a correctness property for any service whose config contains absolute
paths.

**The mail server banned the entire internet.** Docker's published ports rewrite
the source address, so Stalwart saw every external connection as coming from the
bridge gateway. It did what a mail server should do with an address making
repeated half-open connections and banned it. That address was everyone. We
found it because our own monitoring got blocked; the unlucky version is one
spammer taking inbound mail down for the world, presenting as ports that accept
connections and then do nothing.

**ACME, three times.** The apex is a Cloudflare Pages site, so TLS-ALPN-01 can
never validate `agentsee.work` — and Let's Encrypt fails the *whole order* if any
identifier fails. Then: leaving Subject Alternative Names empty does not mean no
SANs, it means *default* SANs — four hostnames that do not exist. Then, days
later: **editing the SAN list destroys the working certificate and does not
re-issue it.** Adding a hostname to a mail server's certificate turns out to be
a destructive operation, which is not how it reads in the UI.

**Silence by default.** None of the above was visible until a Console tracer was
added, because Stalwart logs nothing to stdout out of the box — and the obvious
choice, a File tracer, writes to a directory that does not exist in the container
and fails on every startup. *Every* problem in that phase was invisible until
logging worked. If the piece has one practical lesson for readers, it is: make
the system able to tell you things before you need it to.

**A silent fallback that made every wrong guess look identical.** The outbound
relay would not engage. The route existed, the expression referenced it, and
mail kept going direct-to-MX. An unresolvable route name falls back to MX with
no warning and no log line — so four consecutive wrong hypotheses produced
byte-identical output. What broke the deadlock was reading the object with the
CLI instead of the form, which is also what later exposed `main.agentsee.work`
where `mail.agentsee.work` was meant.

**DKIM signatures that failed because the relay rewrote headers.** Stalwart
signs `Subject:To:From:Date:Message-ID` by default. SMTP2GO rewrites `Message-Id`
*and* regenerates `Date` — the latter being genuinely hard to spot, because the
relay usually stamps the same second the message was signed. The diagnostic that
cracked it is worth teaching: the body hash was identical across our signature
and the relay's, and theirs passed, so the body was untouched and only a signed
header could be at fault. That one comparison removes most of the search space.

**The plan that differed by machine.** `terraform.tfvars` was gitignored on the
assumption that tfvars is where credentials live — true before secrets moved to
1Password, false afterwards. So the file holding *every value that decides what
the infrastructure looks like* existed on exactly one laptop. A plan from the
second offered to destroy the apex MX, both DKIM records and all three relay
CNAMEs, and downgrade DMARC to `p=none`. This was caught by a human noticing
"that's a lot of changes" and not explaining it away. **The version that looks
right on the machine you always use is the dangerous one.**

## 4. What we gained

- **Infrastructure that actually applies.** `op run -- tofu apply`, credentials
  resolved from 1Password at the moment of use — nothing exported into a shell,
  no plaintext credential files on disk.
- **A configuration snapshot.** `plan.json`, taken from the running server by one
  command that refuses to write if it finds a secret. The clicking happened once.
- **A runbook transcribed from reality**, not from the design. Every trap above
  is in it, in the file where the next person will look.
- **Backups at two companies**, nightly, alerting separately, with restores that
  have been proven to boot — including the discovery that the *restore test
  itself* was broken and would have re-delivered the queue if it had worked.
- **Understanding.** DKIM alignment, ACME challenge types, MTA-STS, VERP
  return-paths, why SPF can correctly authorise nobody. Bought expensively and
  not otherwise obtainable.

## 5. What working with an AI was actually like

The section people will read for. It must not be a commercial.

**What it was good at:** reading a great deal quickly; writing configuration
with the reasoning attached; noticing the class of a bug from a log line; and
— unexpectedly the most valuable — writing down *why*, at the moment the reason
was still known. Sixty-five commit messages that explain their own decisions are
a better artefact than the configuration they accompany.

**What it got wrong, specifically.** Claimed Stalwart serves Apple mobileconfig
(it does not; that was a hosting panel built on it). Put CAA at the apex where
Cloudflare silently drops it, having moved it there from where the tool had
correctly generated it. Asserted Email Routing subdomains survive disabling the
service, unverified. Suggested a lowercase route name against a case-sensitive
field. Wrote a restore test that could not have passed and would have been
dangerous if it had.

**The pattern in those.** Every one is a confident claim about a system's
behaviour that had not been tested. The successes have the opposite shape:
calibrated probes with known-good and known-bad controls, reading objects rather
than forms, checking a fix landed before concluding it failed.

**The division of labour that emerged:** the model proposes and writes; the human
supplies the anomaly detection — "that's a lot of changes", "are you sure?" —
and the authority for anything irreversible. Both halves were necessary. The
apex-MX near-miss is the cleanest example: a human noticed something looked odd,
and the investigation found a latent fault nobody was looking for.

## 6. What to say about whether to do this

Recommend it for: people who want to understand the system, or whose positioning
requires the receipts. Not for: people who want cheaper mail, or who have not
decided in advance what would make them stop.

The thing that made it defensible was writing the abandonment criteria *before*
starting — a failed restore test unfixed within a week, two silent delivery
failures in a quarter, or it stops being interesting. And keeping the exit
costed and current: same addresses, same domain, about an hour.

---

## Notes for the podcast

- The "silence by default" thread — logging, `cloud-init status`, silent
  fallbacks — is the strongest single theme and probably the episode's spine.
- The conduct-of-providers discussion is the most distinctive thing here and the
  least like everyone else's self-hosting content.
- "AI made me faster at being wrong, and the discipline is what made that
  survivable" is the honest headline. Resist the urge to soften it.
- Have the `p=reject` detail ready: the plan assumed we would have to relax it
  during migration and we never did, because a verified sender domain aligns from
  the first message. The domain was never briefly spoofable. Small, concrete,
  and the kind of thing that signals the account is real.
