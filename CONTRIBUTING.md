# Working on this

Two people, one page, no ceremony. This file exists so nobody has to ask
permission to fix their own bio.

## Editing your own section

Your block in `index.html` is marked with comments:

```html
<!-- ABRAR: this block is yours. ... -->
<article class="person"> ... </article>
<!-- /ABRAR -->
```

Change whatever you like inside it — the prose, the role line, the links.
Nothing outside those markers reads anything from inside them, so you can't
break the rest of the page from in there.

Push straight to `main`. This is a two-person landing page in discovery, not a
regulated system; if we start needing review gates for a paragraph about
ourselves we've lost the plot. Open a PR when you actually want a second pair
of eyes.

## Replacing your photo

The two portraits are cropped to matching head-and-shoulders framing and mapped
onto the same ink/paper ramp, which is what stops them looking like two photos
from two different websites. `tools/portraits.py` does that:

```sh
python3 tools/portraits.py --abrar /path/to/your-photo.jpg
python3 tools/portraits.py --abrar /path/to/your-photo.jpg --colour   # skip the toning
```

Then look at it. Both faces should sit at roughly the same size in frame — if
one head is noticeably bigger, adjust the crop box in that file and run again.

## Seeing your change

No build step. Open `index.html`, or:

```sh
python3 -m http.server 8000
```

Check it in both light and dark — the page follows the system theme and it is
easy to make something that only works in one.

## Deploying

```sh
export CLOUDFLARE_ACCOUNT_ID=...
export CLOUDFLARE_API_TOKEN=...     # Account > Cloudflare Pages > Edit
npx wrangler pages deploy . --project-name=agentsee --commit-dirty=true
```

This uploads straight from your working copy to Cloudflare. It does **not** go
through GitHub, deliberately — a forge outage should never be able to stop us
shipping. Ask James for a token.

## Things worth not breaking

- **No third-party requests.** No CDNs, no fonts from Google, no analytics, no
  embeds. The `Content-Security-Policy` in `_headers` is `default-src 'none'`
  and will simply block anything you add. That's on purpose.
- **It works without JavaScript.** JS only adds the eye's pointer-tracking and
  the day counter. Don't make content depend on it.
- **`prefers-reduced-motion` is honoured.** If you add motion, add the opt-out.
- **Nothing on the page should be able to go stale on its own.** No hardcoded
  dates or counts that quietly drift out of date while nobody's looking.
