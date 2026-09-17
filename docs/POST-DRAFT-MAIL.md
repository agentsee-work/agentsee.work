# Draft — "We built our own mail server. Here is what it cost."

**Status: draft, not published.** Lives in `docs/` so it stays out of `public/`.
When it is ready it becomes Issue No. 2 under `public/issues/` and gets a row in
the register, per the rule in the README: never list an issue that has not been
published.

Written to inform a podcast on the same subject.

---

## The shape of the piece

Owning your own mail is a reasonable thing to do again. Working with an AI made
it three afternoons of actual work, spread over a fortnight because that is how
evenings and weekends go. The honest accounting is that we spent time to buy
understanding and control. We did not save money, and anyone who tells you
self-hosting is cheaper at this scale is selling something.

The version to avoid writing is "AI built our mail server", because it did not.
It read a great deal, wrote the configuration, and was wrong often enough that
the useful content is where that got caught.

---

## 1. What we set out to do

Two people, building in public. The pitch involves deploying client work as
infrastructure-as-code, and that claim is worth much less if your own first
server was clicked together in a dashboard.

The starting position was worse than nothing. Cloudflare Email Routing forwarded
`@agentsee.work` to personal inboxes and could not send at all. Every account
signup, every reply to a guest, every password reset went out from a personal
Gmail. The domain published `p=reject`, which was true: nothing was authorised
to send as us, because nothing could.

The design that emerged was to own the inbox and rent the reputation. Stalwart
on a VPS handles inbound and stores the mail. Everything outbound goes to a
relay over authenticated submission. That removes the unwinnable part of
self-hosting, which is that a fresh IP has no sending history, and no history
looks identical to a spammer who rented the address an hour ago.

## 2. The decisions, and what they turned on

**SES to SMTP2GO.** SES is cheaper per message and was the first choice. It lost
on a property that has nothing to do with email: new accounts start in a
sandbox, and leaving it needs a support request reviewed by a human, with no API
and no timebox. That put an unbounded wait on the critical path. SMTP2GO's free
tier is a thousand messages a month with no card, which is more than two people
write.

**Hetzner to Infomaniak.** Hetzner was the obvious choice and roughly £47 a
year. Vultr was on the shortlist too, mostly for the spread of datacentre
locations, until we looked at what happened to GreatFire: Vultr took FreeWeChat
offline after a complaint instigated by Tencent, ignored a claim-by-claim
rebuttal, ignored a letter co-signed by seventeen press-freedom organisations,
then terminated the account without cause. That put provider conduct on the
table as a selection criterion, and once it was there Hetzner had its own
problem, having terminated a non-profit running censorship-evasion services.

The criterion has two sides that have to be held together. A host should refuse
to carry fascists and organised harassment of marginalised people. A host should
also hold the line when a state or a corporation leans on legitimate
journalism. Those are not in tension, and the second is not a licence for the
first.

Infomaniak is a certified B Corp, employee-owned, has never taken outside
investment, runs on renewable energy and offsets twice what it uses. It costs
about £15 to £30 a year more than Hetzner and buys nothing technical. That is a
values purchase and the piece should say so, rather than dressing it up as risk
mitigation. Nothing we host will ever attract a takedown at that scale.

**What it costs.** Around £60 to £80 a year, against about £37 for hosted
kSuite. Self-hosting is more expensive at two users. It wins on control and on
learning, and the write-up is worthless if it pretends otherwise.

## 3. The failures

Sixty-five commits. The ones worth telling share a shape: it looked like one
thing and was another.

### One missing package emptied the whole box

`cloud-init` installs its package list atomically. `docker-compose-plugin` does
not exist in Debian, so the entire list failed, taking docker, restic, fail2ban
and unattended-upgrades with it. The instance came up. SSH worked. Files were
written. The only evidence was in `cloud-init status --long`, which nothing
prompts you to run.

### The setup wizard that kept reappearing

Stalwart reads its configuration from `/etc/stalwart/config.json` inside the
container. Ours was mounted somewhere nothing reads. So it found no
configuration, entered bootstrap mode, and the setup wizard ran to completion
against storage that did not persist. Restart, fresh wizard, fresh password, as
though nothing had happened. There is no error anywhere in that sequence.

The same class of bug turned up one layer down. The volume was mounted at
`/opt/stalwart` while the wizard defaulted the datastore to `/var/lib/stalwart`,
a path that existed only inside the container's ephemeral layer. RocksDB wrote
to somewhere that vanished on every recreate. Mounting the volume at the same
path inside and out fixed it, which sounds like tidiness and is a correctness
property for any service whose configuration holds absolute paths.

### The mail server banned the entire internet

Docker's published ports rewrite the source address, so Stalwart saw every
external connection as coming from the bridge gateway. It did what a mail server
should do with an address making repeated half-open connections, and banned it.
That address was everyone. We found it because our own monitoring got blocked.
The unlucky version is a single spammer taking inbound mail down for the world,
presenting as ports that accept connections and then sit there.

### ACME, three times

The apex serves a Cloudflare Pages site, so TLS-ALPN-01 can never validate
`agentsee.work`, and Let's Encrypt fails an entire order if any one identifier
fails. Then: leaving the Subject Alternative Names empty does not mean no SANs,
it means the default set, which is four hostnames that do not exist. Then, days
later, editing that SAN list destroyed the working certificate without ordering
a replacement. Adding a hostname to a mail server's certificate is a destructive
operation, which is not how it reads in the interface.

### Silence by default

None of the above was visible until we added a Console tracer, because Stalwart
logs nothing to stdout out of the box. The obvious alternative, a File tracer,
writes to a directory that does not exist in the container and fails on every
startup. Every problem in that phase was invisible until logging worked. If the
piece leaves readers with one practical habit, it should be this one: make the
system able to tell you things before you need it to.

### A silent fallback that made every wrong guess look the same

The outbound relay would not engage. The route existed, the expression
referenced it, and mail kept going direct to MX. An unresolvable route name
falls back to MX with no warning and no log line, so four consecutive wrong
hypotheses produced identical output. Reading the object with the CLI instead of
the web form broke the deadlock, and the same move later exposed
`main.agentsee.work` sitting where `mail.agentsee.work` should have been.

### DKIM signatures broken by the relay

Stalwart signs `Subject:To:From:Date:Message-ID` by default. SMTP2GO rewrites
`Message-Id` and regenerates `Date`. The second one is genuinely hard to spot,
because the relay usually stamps the same second the message was signed. The
diagnostic that cracked it is worth teaching: the body hash was identical across
our signature and the relay's, and the relay's passed, so the body was untouched
and only a signed header could be at fault. That comparison removes most of the
search space in one step.

### The plan that differed by machine

`terraform.tfvars` was gitignored on the assumption that tfvars is where
credentials live. True before the secrets moved to 1Password, false afterwards.
So the file holding every value that decides what the infrastructure looks like
existed on exactly one laptop. A plan run from the second offered to destroy the
apex MX, both DKIM records and all three relay CNAMEs, and to downgrade DMARC to
`p=none`.

That one was caught by a human noticing there were more changes than expected
and not explaining it away. The version that looks right on the machine you
always use is the dangerous one.

## 4. What we gained

Infrastructure that applies cleanly, with credentials resolved from 1Password at
the moment of use. Nothing is exported into a shell and there are no plaintext
credential files on disk.

A configuration snapshot, `plan.json`, taken from the running server by one
command that refuses to write if it finds a secret value. The clicking happened
once.

A runbook transcribed from what actually happened rather than from the design.
Every trap above is in it, in the file where the next person will look.

Backups at two companies, nightly, alerting separately, with restores proven to
boot. That included discovering the restore test itself was broken, and would
have re-delivered the queue if it had worked.

Understanding. DKIM alignment, ACME challenge types, MTA-STS, VERP return
paths, why an SPF record can correctly authorise nobody. Bought expensively and
not otherwise obtainable.

## 5. What working with an AI was actually like

The model was good at reading widely and quickly, writing configuration with the
reasoning attached, recognising the class of a problem from a log line, and
recording why a decision was made while the reason was still known. Sixty-five
commit messages that explain themselves turned out to be a better artefact than
the configuration they describe.

It was also wrong in ways worth naming. It claimed Stalwart generates Apple
mobileconfig profiles, which it does not; that came from documentation for a
hosting panel built on Stalwart. It moved the CAA record to the apex, where
Cloudflare silently drops it, from the mail host where the tool had correctly
generated it. It asserted without checking that Cloudflare Email Routing
subdomains survive disabling the service. It suggested a lowercase route name
against a case-sensitive field. It wrote a restore test that could not have
passed, and that would have re-sent the mail queue if it had.

Every one of those is a confident claim about how a system behaves that had not
been tested. The things that went well have the opposite shape: probes
calibrated against known-good and known-bad controls, reading objects rather
than forms, checking that a fix had landed before concluding it had failed.

A division of labour emerged without being designed. The model proposes and
writes. The human supplies anomaly detection, the "that looks wrong" that
precedes any investigation, and the authority for anything irreversible. Both
halves were necessary, and the near-miss with the apex MX is the clearest
example: someone noticed a plan looked odd, and the investigation turned up a
fault nobody was hunting for.

## 6. Whether to do this

Worth it if you want to understand the system, or if your positioning requires
the receipts. Not worth it if you want cheaper mail, or if you have not decided
in advance what would make you stop.

What made it defensible was writing the abandonment criteria before starting: a
failed restore test not fixed within a week, two silent delivery failures in a
quarter, or it stops being interesting and becomes a chore. And keeping the exit
costed and current. Same addresses, same domain, about an hour to move to hosted
mail.

---

## Notes for the podcast

The silence-by-default thread runs through half the failures: logging off by
default, `cloud-init` failing quietly, a route falling back with no log line. It
is probably the episode's spine, and it transfers well beyond mail.

Provider conduct is the most distinctive material here and the least like other
people's self-hosting content. It might deserve its own episode rather than a
segment.

"AI made me faster at being wrong, and the discipline is what made that
survivable" is the honest headline. Worth resisting the urge to soften it.

Keep the `p=reject` detail to hand. The plan assumed we would have to relax it
during the migration, and we never did, because a relay with a verified sender
domain aligns from the first message. The domain was never briefly spoofable.
Small, concrete, and it signals that the account is real.
