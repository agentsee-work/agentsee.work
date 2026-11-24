# Working on this

Two people, one page, no ceremony. This file exists so nobody has to ask
permission to fix their own bio.

## Editing your own section

Your block in `public/index.html` is marked with comments:

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

No build step. Open `public/index.html`, or:

```sh
cd public && python3 -m http.server 8000
```

Check it in both light and dark — the page follows the system theme and it is
easy to make something that only works in one.

## Deploying

```sh
export CLOUDFLARE_ACCOUNT_ID=...
export CLOUDFLARE_API_TOKEN=...     # Account > Cloudflare Pages > Edit
npx wrangler pages deploy public --project-name=agentsee --commit-dirty=true
```

This uploads straight from your working copy to Cloudflare. It does **not** go
through GitHub, deliberately — a forge outage should never be able to stop us
shipping. Ask James for a token.

## A new face for Seymour

Seymour is the eye in the fedora. He has a table of poses and a table of
chains (a few poses in a row, with a pace of their own), and
[agentsee.work/seymour](https://agentsee.work/seymour/) plays every one of
them. A guest of the podcast keeps one for good. If the one you want is not
on the board, draw it: open a pull request that adds a pose or a chain, made
with whatever AI assistant you like, which is rather the show's point. Once
it is merged it is free like any new one, and you swap to it from the office;
hold one from the board in the meantime if you want to.

Anyone may send one, guest or not. You need Python 3 and, to look at what you
made, Google Chrome and `ffmpeg` on the machine; nothing else.

**Where they are.** A pose is one line in `EXPRESSIONS` in `tools/eye.py`:
a name, the axes it moves away from rest, and a note. The eight axes are
described above the table: the two lids (`lt`, `lb`), the tilt of their
curve, the hat's angle, the iris on two axes (`ix`, `iy`), the pupil's size
(`ps`) and the catchlight's (`gl`). A chain is one entry in `CHAINS` in
`tools/eyeanim.py`: a `seq` of pose names, optionally its own `hold` and
`trans` in seconds and a `jitter`, and a note.

```python
"smug": dict(lt=0.28, lb=0.22, tilt=0.30, hat=1.5, ix=4, note="Pleased with itself. Both lids, one corner up.", by="Your Name"),
```

`by` is your name as you want it shown: it goes on the pad on the site,
whoever ends up holding the pose.

**Finding the numbers.** Open [the board](https://agentsee.work/seymour/),
press the pose nearest to what you want, and drag the eight sliders under
"The eight numbers". "Copy the numbers" gives them as the table writes them;
the link in the address bar carries them too, so you can show someone.

**Seeing it before you send it.**

```sh
./tools/eye.py --sheet                      # every pose on one sheet: is yours distinct?
./tools/eye.py smug --size 200              # yours alone, small
./tools/eyeanim.py attentive smug --blink   # into yours and held, as video
./tools/eyeanim.py yourchain --contact      # a chain as a filmstrip
./tools/eyeanim.py --reel --blink           # every chain end to end, labelled
./tools/eyeanim.py --export                 # then this, and commit what it writes
```

The stills land in `public/assets/brand/` (do not commit those) and the
video in `build/eye/`, which git ignores. `--export` rewrites
`public/assets/seymour-data.js`, which the site and the guest office both
play from: run it and commit the result, or the check on your pull request
fails (`./tools/eyeanim.py --check` is what it runs). To see yours on the
board itself, serve the site (`cd public && python3 -m http.server 8000`)
and open `/seymour/`.

**What a good one is.**

- Distinct. On the contact sheet it should not be mistaken for a neighbour;
  a pose that is another with one number nudged will not be merged. The
  board is not padded.
- Readable small. He is drawn about 60 pixels wide on a clip and in a
  podcast app. Look at it at `--size 200` or smaller before you decide.
- One plain word for a name, lower case (two with a hyphen if it must), and
  a note of one line that says what it is for or how it works.
- For a chain: pose names that exist, and a pace that suits it. A double
  take is fast; deadpan is slow. A chain that needs a pose that does not
  exist adds the pose too.
- In both lights. Add `--newsprint` to see it on the light theme.

**What happens to it.** James and Abrar review it like any other change, and
may retune it or settle a different name in the pull request: the name they
merge is the show's name for it. Three poses and six chains are the show's
own and stay as they are (`RESERVED` in `tools/eye.py`, `RESERVED_CHAINS` in
`tools/eyeanim.py`). A pose or chain that somebody holds is not renamed or
removed: the claim follows the name.

## Things worth not breaking

- **No third-party requests.** No CDNs, no fonts from Google, no analytics, no
  embeds. The `Content-Security-Policy` in `_headers` is `default-src 'none'`
  and will simply block anything you add. That's on purpose.
- **It works without JavaScript.** JS only adds the eye's pointer-tracking and
  the day counter. Don't make content depend on it.
- **`prefers-reduced-motion` is honoured.** If you add motion, add the opt-out.
- **Nothing on the page should be able to go stale on its own.** No hardcoded
  dates or counts that quietly drift out of date while nobody's looking.
