# agentsee.work

The landing page for AgentSee, set as a broadsheet. One page, no build step, no
framework, no tracking.

If you want to know what we're doing, the page says it better than this file does.

## The publishing system

The page is **Issue No. 1**, and it is built to be the first of a series rather
than a one-off. That is a deliberate constraint, not decoration: everything we
publish gets a number and a date so it can be held against us later.

- The **masthead** and **dateline** are standing furniture. The dateline
  computes itself — `Day N · <today>` — so the page can never claim to be
  fresher or staler than it is.
- The **register** (section 04) lists every issue. No. 1 is this document.
  No. 2 is listed as unwritten, and should stay that way until it exists.
  Never list an issue that hasn't been published.
- **Day 1 is 18 August 2026**, the day the domain was registered. That epoch
  lives in `public/assets/eye.js` and nowhere else.

When there is a second issue, it becomes its own page under `/issues/`, keeps
the same masthead and dateline, and gets a row in the register here.

## What's here

**Only `public/` is published.** Everything else — docs, tooling, the Worker —
stays in the repo and off the website. That separation is deliberate: before it
existed, `wrangler pages deploy .` was serving the README, the runbook and the
Worker source from the marketing site.

```
public/                       ← the only thing that gets deployed
  index.html                  the whole page
  404.html                    Issue No. 404, never printed
  robots.txt / sitemap.xml    so crawlers get those, not the homepage
  apple-touch-icon.png        iOS home screen; from assets/brand/
  _headers                    Cloudflare Pages security + cache headers
  assets/styles.css           the whole stylesheet
  assets/eye.js               pointer tracking, saccades, day counter
  assets/og.png               social card, rendered from the site's own CSS
  assets/*-hartt|mahmood.jpg  portraits (see tools/portraits.py)
  assets/brand/               org avatars, not used by the page
  fonts/newsreader-latin.woff2  self-hosted display face (SIL OFL)

docs/RUNBOOK.md               where the infrastructure lives, and its traps
docs/SOCIAL.md                the handle, and what signup will throw at you
docs/MAIL-MIGRATION.md        the buy-instead path — kept as the abandonment exit
docs/CREDENTIALS.md           1Password Business: the vaults and the rules
docs/AGENT-MAIL.md            inbound mail as an agent trigger — design only
docs/MAIL-SELFHOST.md         own the inbox, rent the reputation — built, running
docs/MAIL-BUILD-RUNBOOK.md    how it was built, and everything that bit on the way
docs/POST-DRAFT-MAIL.md       draft of Issue No. 2 — not published, stays in docs/
infra/                        the mail server declared — OpenTofu, applied
stalwart/                     mail server config, snapshotted from the running server
tools/portraits.py            regenerates the portraits from source photos
workers/email-fanout/         was the hello@/show@ fan-out; now the agent-intake prototype
```

There is no bundler and nothing to install:

```sh
cd public && python3 -m http.server 8000
```

## Deliberate constraints

These are choices, not shortcuts. If we can't hold a one-page site to them, we
have no business claiming taste anywhere else.

- **Zero third-party requests.** No CDN, no Google Fonts, no analytics, no
  embeds. The typeface is self-hosted. The favicon is an inline data URI. The
  page makes exactly the requests it serves itself and no others.
- **No cookies, no storage, no tracking.** Nothing to consent to, so no banner.
- **Content-Security-Policy `default-src 'none'`.** Enforced in `public/_headers`.
  Adding a third-party script would break the page, which is the point.
- **Works without JavaScript.** JS adds the eye's pointer tracking and the live
  day counter. Without it the eye still blinks (CSS), the counter reads "Early
  days" rather than a number that would silently go stale, and every contact
  link is a real `mailto:`. Nothing else depends on it.
- **Cloudflare's Email Address Obfuscation is off** for this zone, on purpose.
  It rewrites `mailto:` links into `/cdn-cgi/l/email-protection#…` and injects a
  decoder script, which breaks the contact links without JS and adds a script we
  didn't write. If the CTAs ever stop working, check that setting first.
- **`prefers-reduced-motion` is honoured.** The eye stops moving and blinking.
- **The eye pauses when off-screen or backgrounded**, via IntersectionObserver
  and the visibility API.

## Mail

`@agentsee.work` runs on our own server — Stalwart on a VPS, cut over from
Cloudflare Email Routing on **14 September 2026**. It receives directly and
sends through a relay, so the domain both receives *and* sends.

```
MX     mail.agentsee.work              ← our server. DNS-only, never proxied
SPF    v=spf1 ~all                     ← authorises nothing. see below
DKIM   v1-rsa-…_domainkey              (ours, signed before handoff)
       s989721._domainkey              (the relay's, CNAMEd to them)
DMARC  v=DMARC1; p=reject; sp=reject; rua=mailto:dmarc@agentsee.work; fo=1
```

**`p=reject` was never relaxed.** The usual migration advice is to drop to
`p=none` while you get alignment working, and it exists because sending through
something like Gmail's "send mail as" can never align. Relaying through a
provider with a verified sender domain aligns from the first message, so the
domain was never briefly spoofable. Verify alignment before assuming you need to
relax anything.

**SPF authorises nobody, and that is correct.** The relay uses VERP: the
return-path is a subdomain of ours CNAMEd to them, so SPF is evaluated against
*that* name and their record. Our server never sends direct-to-MX either, so
there is nothing at the apex to authorise.

Two DKIM signatures, deliberately. The relay's leaves when the relay does; ours
is published in our own zone and survives changing supplier. DMARC passes if
either validates.

The whole build — why, what broke, and every checkpoint — is in
[docs/MAIL-SELFHOST.md](docs/MAIL-SELFHOST.md) and
[docs/MAIL-BUILD-RUNBOOK.md](docs/MAIL-BUILD-RUNBOOK.md).

## Deploying

Deploys go straight from a working copy to Cloudflare Pages. They do **not** pass
through a git host, so no forge outage can block a deploy:

```sh
export CLOUDFLARE_ACCOUNT_ID=...
export CLOUDFLARE_API_TOKEN=...        # needs Account > Cloudflare Pages > Edit
npx wrangler pages deploy public --project-name=agentsee --commit-dirty=true
```

Git remotes are for reading and for history. Push targets can be mirrored so
that no single host is a dependency:

```sh
git remote set-url --add --push origin <primary>
git remote set-url --add --push origin <mirror>
git push   # goes to both
```

## Licence

Code is MIT. The Newsreader typeface is under the SIL Open Font License.
The words and the logo are ours.
