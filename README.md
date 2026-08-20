# agentsee.work

The landing page for AgentSee. One page, no build step, no framework, no tracking.

If you want to know what we're doing, the page says it better than this file does.

## What's here

```
index.html                    the whole page
assets/styles.css             the whole stylesheet
assets/eye.js                 pointer tracking, saccades, day counter
assets/og.png                 social card, rendered from the site's own CSS
assets/*-hartt|mahmood.jpg    portraits (see tools/portraits.py)
fonts/newsreader-latin.woff2  self-hosted display face (SIL OFL)
tools/portraits.py            regenerates the portraits from source photos
_headers                      Cloudflare Pages security + cache headers
```

There is no bundler and nothing to install. Open `index.html` in a browser, or:

```sh
python3 -m http.server 8000
```

## Deliberate constraints

These are choices, not shortcuts. If we can't hold a one-page site to them, we
have no business claiming taste anywhere else.

- **Zero third-party requests.** No CDN, no Google Fonts, no analytics, no
  embeds. The typeface is self-hosted. The favicon is an inline data URI. The
  page makes exactly the requests it serves itself and no others.
- **No cookies, no storage, no tracking.** Nothing to consent to, so no banner.
- **Content-Security-Policy `default-src 'none'`.** Enforced in `_headers`.
  Adding a third-party script would break the page, which is the point.
- **Works without JavaScript.** JS adds the eye's pointer tracking and the live
  day counter. Without it the eye still blinks (CSS) and the counter falls back
  to a static value. Nothing else depends on it.
- **`prefers-reduced-motion` is honoured.** The eye stops moving and blinking.
- **The eye pauses when off-screen or backgrounded**, via IntersectionObserver
  and the visibility API.

## Deploying

Deploys go straight from a working copy to Cloudflare Pages. They do **not** pass
through a git host, so no forge outage can block a deploy:

```sh
export CLOUDFLARE_ACCOUNT_ID=...
export CLOUDFLARE_API_TOKEN=...        # needs Account > Cloudflare Pages > Edit
npx wrangler pages deploy . --project-name=agentsee --commit-dirty=true
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
