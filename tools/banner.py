#!/usr/bin/env python3
"""
Render profile banners from the site's own palette and typeface.

    ./tools/banner.py bluesky            # -> public/assets/brand/banner-bluesky.png
    ./tools/banner.py bluesky --newsprint
    ./tools/banner.py --list

Every platform wants a different aspect ratio for what is visually the same
object, and cropping one to fit another is how you end up with half a wordmark.
So the composition is described once and laid out per ratio, rather than
exported once and squeezed.

Rendered through headless Chrome against the real stylesheet values, for the
same reason og.png was: if the banner is drawn by hand in an image editor it
drifts from the site the first time a colour changes, and nobody notices because
nobody diffs a PNG.

The typeface is inlined as a data URI rather than linked. Chrome treats fonts
loaded over file:// as cross-origin and silently substitutes a fallback — the
output looks fine, just not in our typeface, which is the kind of wrong that
survives review.
"""
import argparse
import base64
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONT = ROOT / "public/fonts/newsreader-latin.woff2"
OUTDIR = ROOT / "public/assets/brand"

# name -> (w, h, basis, note). Sizes are each platform's own recommendation.
#
# `basis` is what the type is scaled against, and it is deliberately NOT always
# the height. Scaling off the canvas works only when the whole canvas is usable.
# YouTube's is not: it renders at 2560x1440 but guarantees only the centre
# 1235x338 across devices, so type sized off 1440 marches straight out of the
# safe area on a phone. LinkedIn has the opposite problem — at 191px tall,
# height-scaled type is technically correct and too small to read.
PRESETS = {
    "bluesky":  (1500, 500,  500, "3:1. Avatar overlaps bottom-left."),
    "x":        (1500, 500,  500, "3:1. Avatar overlaps bottom-left."),
    "linkedin": (1128, 191,  300, "5.9:1. Very letterboxed — mark only."),
    "youtube":  (2560, 1440, 790, "16:9, but only the centre 1235x338 is safe everywhere."),
}
# No `og` preset on purpose. assets/og.png is a different composition — masthead,
# three-column dateline, headline — and a preset of that name here would quietly
# produce something else under the same filename.

# Centred box, per platform, that survives every device. Content outside this is
# not guaranteed to be seen. YouTube's is the platform's own published figure and
# is brutal — 1235x338 out of 2560x1440, under 12% of the area. The others are
# our own margin against responsive cropping rather than documented guarantees.
SAFE = {
    "bluesky":  (1400, 420),
    "x":        (1400, 420),
    "linkedin": (980, 165),
    "youtube":  (1235, 338),
}

THEMES = {
    "noir": dict(
        paper="#0a0a0b", paper_2="#131211", ink="#efe9e0", ink_2="#a89e91",
        ink_3="#6a625a", rule="#efe9e0", hair="#2b2722", signal="#e0923f",
        wash="rgba(224,146,63,.09)", glow="rgba(224,146,63,.34)",
    ),
    "newsprint": dict(
        paper="#f7f3ea", paper_2="#efe9dc", ink="#1a1712", ink_2="#4a4238",
        ink_3="#7d7365", rule="#1a1712", hair="#cfc4b0", signal="#a8431d",
        wash="rgba(168,67,29,.07)", glow="transparent",
    ),
}

MARK = """
<svg viewBox="0 0 240 210" role="img" aria-label="An eye wearing a fedora">
  <defs><clipPath id="eyeclip">
    <path d="M44 142C72 104 168 104 196 142C168 178 72 178 44 142Z"/>
  </clipPath></defs>
  <g class="hat">
    <path class="hat-crown" d="M70 96C68 58 74 36 92 34C100 44 140 44 148 34C166 36 172 58 170 96Z"/>
    <path class="hat-band"  d="M69 62C100 69 140 69 171 62L170 90L70 90Z"/>
    <path class="hat-buckle" d="M78 64L89 65.8L88.4 88L77.6 88Z"/>
    <ellipse class="hat-brim" cx="120" cy="96" rx="97" ry="13.5"/>
  </g>
  <g class="eye">
    <path class="sclera" d="M44 142C72 104 168 104 196 142C168 178 72 178 44 142Z"/>
    <g clip-path="url(#eyeclip)">
      <circle class="iris-outer" cx="120" cy="142" r="31"/>
      <circle class="iris-inner" cx="120" cy="142" r="22"/>
      <circle class="pupil"      cx="120" cy="142" r="13.5"/>
      <circle class="glint"      cx="109" cy="131" r="6"/>
      <circle class="glint sm"   cx="132" cy="152" r="2.6"/>
    </g>
    <path class="eye-outline" vector-effect="non-scaling-stroke"
          d="M44 142C72 104 168 104 196 142C168 178 72 178 44 142Z"/>
  </g>
</svg>"""

PAGE = """<!doctype html><meta charset="utf-8"><title>banner</title>
<style>
@font-face {{
  font-family: 'Newsreader';
  src: url('data:font/woff2;base64,{font}') format('woff2');
  font-weight: 300 700; font-style: normal;
}}
* {{ box-sizing: border-box; margin: 0; }}
html, body {{ width: {w}px; height: {h}px; overflow: hidden; }}
body {{
  background: {paper};
  color: {ink};
  font-family: 'Newsreader', Georgia, serif;
  display: grid;
  place-items: center;
  position: relative;
}}
/* the noir spotlight, straight off body::before in styles.css */
body::before {{
  content: ''; position: absolute; inset: 0;
  background: radial-gradient(ellipse 52% 40% at 50% 6%, {wash}, transparent 72%);
}}
.stack {{
  position: relative;
  display: flex; flex-direction: column; align-items: center;
  gap: {gap}px;
  /* Bluesky and X hang the avatar over the bottom-left corner only, so centred
     content clears it without being pushed up the frame. */
  transform: translateY(-{lift}px);
}}
.row {{ display: flex; align-items: center; gap: {markgap}px; }}
.mark {{ --mark-w: {mark}px; width: var(--mark-w); flex: none;
         filter: drop-shadow(0 0 {glowblur}px {glow}); }}
.mark svg {{ width: 100%; height: auto; display: block; overflow: visible; }}
.wordmark {{ font-size: {word}px; font-weight: 600; letter-spacing: -.022em; line-height: 1; }}
.rule {{ width: {rulew}px; border-top: 3px double {rule}; opacity: .85; }}
.rule.hair {{ border-top: 1px solid {rule}; opacity: .5; }}
.strip {{
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: {strip}px; letter-spacing: .18em; text-transform: uppercase;
  color: {ink_3}; white-space: nowrap;
}}
.strip b {{ color: {signal}; font-weight: 400; }}

.hat-crown, .hat-brim {{ fill: {ink}; }}
.hat-band   {{ fill: {paper}; opacity: .16; }}
.hat-buckle {{ fill: {signal}; }}
.sclera     {{ fill: {paper_2}; }}
.eye-outline {{ fill: none; stroke: {ink};
                stroke-width: calc(var(--mark-w) * .025); stroke-linejoin: round; }}
.iris-outer {{ fill: {signal}; }}
.iris-inner {{ fill: {signal}; filter: brightness(.82); }}
.pupil      {{ fill: {ink}; }}
.glint      {{ fill: {paper}; opacity: .92; }}
.glint.sm   {{ opacity: .5; }}
</style>
<div class="stack">
  <div class="row">
    <div class="mark">{mark_svg}</div>
    {wordmark}
  </div>
  {rule_el}
  {strip_el}
  {hair_el}
</div>"""


def build(preset, theme, minimal=False):
    w, h, basis = PRESETS[preset][:3]
    t = THEMES[theme]
    u = basis / 500

    # LinkedIn at 191px tall cannot hold a strip and a rule legibly. Rather than
    # shrink the type until it is technically present, drop the furniture.
    tight = minimal or h < 240

    return PAGE.format(
        font=base64.b64encode(FONT.read_bytes()).decode(),
        w=w, h=h, mark_svg=MARK,
        wordmark='<p class="wordmark">AgentSee</p>',
        rule_el='' if tight else '<div class="rule"></div>',
        hair_el='' if tight else '<div class="rule hair"></div>',
        strip_el='' if tight else
            '<p class="strip">Two people building in public'
            ' &nbsp;·&nbsp; <b>agentsee.work</b></p>',
        mark=round(128 * u), word=round(96 * u), markgap=round(28 * u),
        gap=round(22 * u), rulew=round(660 * u), strip=round(19 * u),
        glowblur=round(34 * u), lift=0 if tight else round(6 * u),
        **t)


def check_safe(path, preset):
    """Measure what was actually drawn, rather than trusting the layout maths.

    Everything here is scaled arithmetic, and arithmetic that is slightly wrong
    produces an image that looks perfect at desk size and loses the top of the
    mark on a phone. The only honest check is the pixels: find the bounding box
    of everything that is not background, and compare it to the guaranteed area.
    """
    from PIL import Image, ImageChops
    im = Image.open(path).convert("RGB")
    w, h = im.size
    # The radial spotlight is a real gradient, so "not background" has to mean
    # "meaningfully brighter than the corner", not "any difference at all".
    bg = Image.new("RGB", im.size, im.getpixel((2, h - 2)))
    diff = ImageChops.difference(im, bg).convert("L").point(lambda v: 255 if v > 26 else 0)
    box = diff.getbbox()
    if not box:
        return True, "nothing drawn"

    sw, sh = SAFE[preset]
    sx0, sy0 = (w - sw) // 2, (h - sh) // 2
    sx1, sy1 = sx0 + sw, sy0 + sh
    ok = box[0] >= sx0 and box[1] >= sy0 and box[2] <= sx1 and box[3] <= sy1
    detail = (f"content {box[2]-box[0]}x{box[3]-box[1]} at ({box[0]},{box[1]}) "
              f"vs safe {sw}x{sh} at ({sx0},{sy0})")
    if not ok:
        over = []
        if box[0] < sx0: over.append(f"left by {sx0-box[0]}px")
        if box[1] < sy0: over.append(f"top by {sy0-box[1]}px")
        if box[2] > sx1: over.append(f"right by {box[2]-sx1}px")
        if box[3] > sy1: over.append(f"bottom by {box[3]-sy1}px")
        detail += " — overflows " + ", ".join(over)
    return ok, detail


def render(html, out, w, h):
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html)
        src = f.name
    try:
        subprocess.run(
            ["google-chrome", "--headless=new", "--disable-gpu", "--hide-scrollbars",
             "--force-device-scale-factor=1", f"--window-size={w},{h}",
             f"--screenshot={out}", f"file://{src}"],
            check=True, capture_output=True)
    finally:
        pathlib.Path(src).unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("preset", nargs="?", choices=sorted(PRESETS))
    ap.add_argument("--newsprint", action="store_true", help="light theme (default is noir)")
    ap.add_argument("--minimal", action="store_true", help="mark and wordmark only")
    ap.add_argument("--out")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list or not a.preset:
        for k, (w, h, basis, note) in sorted(PRESETS.items()):
            print(f"  {k:<10} {w}x{h:<6} {note}")
        return 0

    theme = "newsprint" if a.newsprint else "noir"
    w, h = PRESETS[a.preset][:2]
    suffix = "" if theme == "noir" else "-newsprint"
    out = pathlib.Path(a.out) if a.out else OUTDIR / f"banner-{a.preset}{suffix}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    render(build(a.preset, theme, a.minimal), out, w, h)
    print(f"{out.relative_to(ROOT)}  {w}x{h}  {theme}  {out.stat().st_size:,} bytes")
    ok, detail = check_safe(out, a.preset)
    print(f"  {'ok  ' if ok else 'FAIL'} {detail}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
