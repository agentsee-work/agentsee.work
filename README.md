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
docs/MAIL-MIGRATION.md        proposed move to hosted mail — not yet executed
docs/CREDENTIALS.md           where shared secrets live — not yet set up
docs/AGENT-MAIL.md            inbound mail as an agent trigger — design only
docs/MAIL-SELFHOST.md         own the inbox, rent the reputation — build spec
docs/MAIL-BUILD-RUNBOOK.md    standing it up, phase by phase, with checkpoints
infra/                        the mail server declared — OpenTofu, not applied
stalwart/                     mail server config — declarative plan, not applied
tools/portraits.py            regenerates the portraits from source photos
workers/email-fanout/         fans hello@ and show@ out to both of us
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

Cloudflare Email Routing forwards `@agentsee.work` to personal inboxes. The
domain **receives only — nothing sends as `@agentsee.work`**, and the DNS says
so:

```
SPF    v=spf1 include:_spf.mx.cloudflare.net ~all
DKIM   cf2024-1._domainkey            (Cloudflare's, for forwarded mail)
DMARC  v=DMARC1; p=reject; sp=reject; rua=mailto:dmarc@agentsee.work; fo=1
```

`p=reject` tells the world that any mail claiming to be from us is forged.
That is true today and it is the strongest anti-spoofing position available.
It does not affect *inbound* forwarding — DMARC applies to the sender's domain,
and Cloudflare rewrites the envelope on forward so SPF still passes.

### ⚠ Before you send mail as @agentsee.work

**Relax DMARC first, or your own mail will be rejected.** Free Gmail
"Send mail as" relays through Google with a Gmail DKIM signature and envelope,
so neither SPF nor DKIM aligns to `agentsee.work` — under `p=reject` recipients
are being explicitly instructed to throw it away. The failure is silent from
the sender's side, which is the worst kind.

The order that works:

1. Set `p=none` on `_dmarc.agentsee.work` (keep `rua`).
2. Set up sending, and add its sender to SPF — for Google that is
   `include:_spf.google.com`; for a provider like Postmark or Fastmail, use
   whatever they specify, and add their DKIM record too.
3. Watch the aggregate reports at `dmarc@agentsee.work` until your own mail is
   passing with alignment.
4. Only then go back to `p=reject`.

Aggregate reports arrive at `dmarc@agentsee.work` as daily XML attachments.
For a domain that sends nothing they are mostly a spoofing tripwire; drop the
`rua=` tag if the noise isn't worth it.

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
