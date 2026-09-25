#!/usr/bin/env python3
"""
The eye, with eyelids — an expression system for the show assets.

    ./tools/eye.py --sheet                  # contact sheet of every expression
    ./tools/eye.py sceptical --size 600     # one expression, on its own
    ./tools/eye.py --list

The mark on the site can already look around: the iris translates and the whole
eye blinks. What it cannot do is *squint*, because `eye-outline` is a fixed path
and there is nothing to occlude the sclera with. So an eye that has been asked
to look sceptical just looks away, which reads as evasive rather than doubtful.

The fix is two lids sliding in from above and below. Each is a copy of the eye's
own curve, closed off into a filled shape, so the travelling edge is an arc
rather than a straight line — a rectangle reads as a letterbox bar crossing the
eye, which is the first thing this tried and the first thing it got wrong.
Four numbers then describe any expression: how far each lid has travelled, and
where the iris is looking. A fifth, the pupil scale, does the work for surprise,
because a pupil that shrinks reads as alarm in a way no lid movement does.

Lids are filled with `paper` rather than `paper_2`. The sclera is paper_2, so
a lid in the *background* colour reads as the mark closing over itself rather
than as a grey bar crossing it. The outline stays put throughout: a closed eye
that stops being an almond stops being our mark.

Rendered through headless Chrome against the same theme table banner.py uses,
for the same reason banner.py exists — an expression drawn by hand in an editor
drifts the first time a colour changes, and nobody diffs a PNG.
"""

import argparse
import base64
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from banner import FONT, OUTDIR, ROOT, THEMES, render  # noqa: E402

# name -> (lid_top, lid_bottom, iris_x, iris_y, pupil_scale, note)
#
# Lids are 0 (clear of the eye) to 1 (met at the centreline). Iris offsets are
# viewBox units, and the site caps its own tracking at 19 x 10 — going past
# that pushes the iris under the outline and looks like a bug rather than a
# glance, so nothing here exceeds it.
EXPRESSIONS = {
    "attentive":   (0.00, 0.00,   0,   0, 1.00, "Default. Lower thirds, wordmark lockups."),
    "curious":     (0.00, 0.00,  -9,  -6, 1.00, "Chapter marks, question cards."),
    "thinking":    (0.19, 0.00,  -6,  -9, 1.00, "Going into a question."),
    "sceptical":   (0.34, 0.05,  11,   2, 1.00, "The steel-man beats."),
    "scrutiny":    (0.26, 0.20,   0,   0, 1.00, "Technical sections. Reading something closely."),
    "surprised":   (0.00, 0.00,   0,  -1, 0.55, "Reveals. The pupil does this, not the lids."),
    # The pupil has to survive, or a lowered lid reads as blank rather than
    # unimpressed. Same reason asleep stops short of closed: two slivers of
    # iris is the difference between dozing and switched off.
    "unimpressed": (0.40, 0.00,   0,   5, 1.00, "Comedy beat. The Docker Hub section."),
    "asleep":      (0.64, 0.14,   0,   6, 1.00, "Standby card, 'starting soon'."),
    # Just short of 1.0: at exactly 1.0 the lid edge lands on the lower
    # outline and disappears into it, and a shut eye reads as a dark hole.
    # Stopping short leaves the lid margin visible as its own line.
    "closed":      (0.94, 0.00,   0,   0, 1.00, "End card. Sign-off."),
}

# The eye's corners. Both lids are pinned here and only their middles travel,
# which is what an eyelid does — it pivots at the canthi.
#
# Translating a lid instead, as this first did, drags the corners with it: at
# a modest squint the upper lid's corners sit *below* the lower lid's, so the
# two cross and the top appears to fold underneath the bottom. Nothing about
# that is recoverable by tuning the numbers; the motion itself was wrong.
#
# So a lid is a cubic from corner to corner whose two control points slide
# between the open curve and the opposite one. Corners are fixed, so the lids
# can only meet, never cross — and they meet exactly when lid_top + lid_bottom
# reaches 1, which is a property of the geometry rather than a rule to obey.
LID_OPEN, LID_SHUT = 104, 178   # control-point y, open and fully closed


def lid_paths(lid_top, lid_bottom):
    """(top_fill, top_edge, bottom_fill, bottom_edge) for a given pair."""
    ty = LID_OPEN + (LID_SHUT - LID_OPEN) * lid_top
    by = LID_SHUT - (LID_SHUT - LID_OPEN) * lid_bottom
    top = f"M44 142C72 {ty:.2f} 168 {ty:.2f} 196 142"
    bot = f"M44 142C72 {by:.2f} 168 {by:.2f} 196 142"
    # Each fill closes away from the aperture, so at rest it sits outside the
    # clip entirely and costs nothing.
    return top + " L196 -60 L44 -60 Z", top, bot + " L196 340 L44 340 Z", bot


def mark_svg(lid_top=0.0, lid_bottom=0.0):
    """The mark. Lid geometry is computed here; iris and pupil stay as CSS
    custom properties, because those really are transforms."""
    tf, te, bf, be = lid_paths(lid_top, lid_bottom)
    return f"""
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
      <g class="iris">
        <circle class="iris-outer" cx="120" cy="142" r="31"/>
        <circle class="iris-inner" cx="120" cy="142" r="22"/>
        <circle class="pupil"      cx="120" cy="142" r="13.5"/>
        <circle class="glint"      cx="109" cy="131" r="6"/>
        <circle class="glint sm"   cx="132" cy="152" r="2.6"/>
      </g>
      <g class="lid lid-top" style="--lid:{lid_top:.4f}">
        <path class="lid-fill" d="{tf}"/>
        <path class="lid-edge" vector-effect="non-scaling-stroke" d="{te}"/>
      </g>
      <g class="lid lid-bottom" style="--lid:{lid_bottom:.4f}">
        <path class="lid-fill" d="{bf}"/>
        <path class="lid-edge" vector-effect="non-scaling-stroke" d="{be}"/>
      </g>
    </g>
    <path class="eye-outline" vector-effect="non-scaling-stroke"
          d="M44 142C72 104 168 104 196 142C168 178 72 178 44 142Z"/>
  </g>
</svg>"""


def mark_css(t):
    """Everything the mark needs, given a theme dict. Shared by every asset so
    they cannot drift from each other."""
    return f"""
.mark svg {{ width: 100%; height: auto; display: block; overflow: visible; }}
.hat-crown, .hat-brim {{ fill: {t['ink']}; }}
.hat-band   {{ fill: {t['paper']}; opacity: .16; }}
.hat-buckle {{ fill: {t['signal']}; }}
.sclera     {{ fill: {t['paper_2']}; }}
.eye-outline {{ fill: none; stroke: {t['ink']};
                stroke-width: calc(var(--mark-w) * .025); stroke-linejoin: round; }}
.iris-outer {{ fill: {t['signal']}; }}
.iris-inner {{ fill: {t['signal']}; filter: brightness(.82); }}
/* Pinned, not swapped — see the note beside .pupil in styles.css. */
.pupil      {{ fill: {t['pupil']}; }}
.glint      {{ fill: {t['glint']}; opacity: .92; }}
.glint.sm   {{ opacity: .5; }}

/* Iris and pupil really are transforms, so they stay in CSS. Lid geometry
   does not — see lid_paths(). */
.iris {{ transform: translate(calc(var(--iris-x, 0) * 1px),
                              calc(var(--iris-y, 0) * 1px)); }}
.pupil {{ transform-box: fill-box; transform-origin: center;
          transform: scale(var(--pupil-s, 1)); }}
/* A lid is two things: a fill that hides the iris, and the line that *is* the
   eyelid. Both are pinned rather than swapped, like the pupil and the glint:
   the lid is a lit surface, so it takes the light end of whichever palette is
   running and its margin takes the dark end. Filled with `paper` instead it
   vanished in noir, where paper and paper_2 are three points apart, and the
   eye read as a hole with a line across it. Filled with `ink` in both, the
   light theme got a heavy black bar competing with the hat. Only the fill used to exist, so the edge read purely as the boundary
   between two fills — visible in noir where paper is near-black against the
   sclera, invisible on newsprint where the two are a shade apart. The mark is
   drawn in line; its lid should be too.

   The edge sits inside the clip, so it stops at the aperture rather than
   running the full width of the shape it belongs to. Same weight and same
   non-scaling-stroke as eye-outline, or a closing eye changes line weight
   halfway down. */
.lid-fill {{ fill: {t['lid']}; }}
.lid-edge {{ fill: none; stroke: {t['lid_edge']}; stroke-linecap: round;
             stroke-width: calc(var(--mark-w) * .025); }}
/* At rest a lid edge lies exactly on eye-outline, and two coincident
   antialiased strokes composite heavier than one — 170 pixels' worth on a
   1500x500 banner. Fading it in over the first sliver of travel keeps a
   fully-open eye pixel-identical to one with no lids at all, which is what
   lets banner.py share this markup without changing a single existing PNG. */
.lid-edge {{ opacity: clamp(0, calc(var(--lid, 0) * 40), 1); }}
"""


def expr_vars(name):
    """Only the axes that are still CSS. Lids come from mark_svg()."""
    _lt, _lb, ix, iy, ps, _ = EXPRESSIONS[name]
    return f"--iris-x:{ix}; --iris-y:{iy}; --pupil-s:{ps};"


def expr_mark(name):
    """The markup for an expression, lids baked in."""
    lt, lb, *_ = EXPRESSIONS[name]
    return mark_svg(lt, lb)


def font_b64():
    return base64.b64encode(FONT.read_bytes()).decode()


PAGE = """<!doctype html><meta charset="utf-8"><title>eye</title>
<style>
@font-face {{
  font-family: 'Newsreader';
  src: url('data:font/woff2;base64,{font}') format('woff2');
  font-weight: 300 700; font-style: normal;
}}
* {{ box-sizing: border-box; margin: 0; }}
html, body {{ width: {w}px; height: {h}px; overflow: hidden; }}
body {{
  background: {paper}; color: {ink};
  font-family: 'Newsreader', Georgia, serif;
  display: grid; place-items: center;
}}
{mark_css}
{extra}
</style>
{body}"""


def build_single(name, theme, size):
    t = THEMES[theme]
    pad = int(size * 0.12)
    return PAGE.format(
        font=font_b64(), w=size, h=size, paper=t["paper"], ink=t["ink"],
        mark_css=mark_css(t),
        extra=f".mark {{ --mark-w: {size - pad * 2}px; width: var(--mark-w); }}",
        body=f'<div class="mark" style="{expr_vars(name)}">{expr_mark(name)}</div>',
    )


def build_sheet(theme, cell=300):
    """Every expression at once, labelled. Expressions are a judgement call and
    a contact sheet is the only honest way to review them — one at a time you
    talk yourself into anything."""
    t = THEMES[theme]
    cols, rows = 3, 3
    w, h = cols * cell, rows * (cell + 54)
    cells = "".join(
        f'<figure><div class="mark" style="{expr_vars(n)}">{expr_mark(n)}</div>'
        f'<figcaption><b>{n}</b>{EXPRESSIONS[n][5]}</figcaption></figure>'
        for n in EXPRESSIONS
    )
    extra = f"""
body {{ display: block; padding: 0; }}
.grid {{ display: grid; grid-template-columns: repeat({cols}, {cell}px); }}
figure {{ padding: {cell // 10}px {cell // 12}px {cell // 18}px; text-align: center; }}
.mark {{ --mark-w: {int(cell * .66)}px; width: var(--mark-w); margin: 0 auto; }}
figcaption {{ margin-top: {cell // 14}px; font-family: ui-monospace, Menlo, monospace;
              font-size: {max(9, cell // 26)}px; letter-spacing: .06em;
              color: {t['ink_3']}; line-height: 1.5; }}
figcaption b {{ display: block; color: {t['signal']}; font-weight: 400;
                text-transform: uppercase; letter-spacing: .16em;
                margin-bottom: .35em; }}
"""
    return PAGE.format(font=font_b64(), w=w, h=h, paper=t["paper"], ink=t["ink"],
                       mark_css=mark_css(t), extra=extra,
                       body=f'<div class="grid">{cells}</div>'), w, h


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("expression", nargs="?", choices=sorted(EXPRESSIONS))
    ap.add_argument("--sheet", action="store_true", help="contact sheet of all expressions")
    ap.add_argument("--newsprint", action="store_true", help="light theme (default is noir)")
    ap.add_argument("--size", type=int, default=512)
    ap.add_argument("--out")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list or not (a.expression or a.sheet):
        for k, (lt, lb, ix, iy, ps, note) in EXPRESSIONS.items():
            print(f"  {k:<12} lid {lt:>4}/{lb:<4} iris {ix:>3},{iy:<3} pupil {ps}   {note}")
        return 0

    theme = "newsprint" if a.newsprint else "noir"
    suffix = "" if theme == "noir" else "-newsprint"
    OUTDIR.mkdir(parents=True, exist_ok=True)

    if a.sheet:
        html, w, h = build_sheet(theme)
        out = pathlib.Path(a.out) if a.out else OUTDIR / f"eye-expressions{suffix}.png"
    else:
        html = build_single(a.expression, theme, a.size)
        w = h = a.size
        out = pathlib.Path(a.out) if a.out else OUTDIR / f"eye-{a.expression}{suffix}.png"

    render(html, out, w, h)
    # --out may point anywhere, so relative_to(ROOT) is not safe to assume.
    try:
        shown = out.relative_to(ROOT)
    except ValueError:
        shown = out
    print(f"{shown}  {w}x{h}  {theme}  {out.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
