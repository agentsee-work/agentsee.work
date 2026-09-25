#!/usr/bin/env python3
"""
The show's furniture — cover art, cards, thumbnail, clip plate.

    ./tools/show.py --all                    # render the set
    ./tools/show.py cover                    # one asset
    ./tools/show.py titlecard --title "Running our own mail server"
    ./tools/show.py --sheet                  # contact sheet for approval
    ./tools/show.py --list

Same argument as banner.py: composed once and laid out per ratio, drawn through
headless Chrome against the shared theme table, so a colour change moves every
asset at once instead of leaving eight PNGs to drift apart in a folder.

Expressions come from eye.py. Each asset has a default that matches its job —
the standby card dozes, the end card closes, the title card is curious — which
is the whole reason the lids exist.

Cover art is the one with a hard external constraint. Podcast apps draw it at
about 55px in a list, so `--proof` renders it at the sizes that actually matter
and lets you see what survives. Everything else here is checked by eye.
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from banner import FONT, OUTDIR, ROOT, THEMES, render  # noqa: E402
from eye import EXPRESSIONS, expr_mark, expr_vars, font_b64, mark_css  # noqa: E402

# name -> (w, h, basis, expression, note)
#
# `basis` is the height the type scale is derived from, so a 3000px cover and a
# 720p thumbnail get proportionate furniture rather than the same pixel sizes.
ASSETS = {
    "cover":     (3000, 3000, 1400, "attentive", "Podcast cover. Square, must read at 55px."),
    "titlecard": (1920, 1080,  620, "curious",   "Episode title, top of show."),
    "standby":   (1920, 1080,  620, "asleep",    "'Starting soon' holding card."),
    "endcard":   (1920, 1080,  620, "closed",    "Sign-off."),
    "thumbnail": (1280,  720,  520, "sceptical", "YouTube. Read as a small rectangle in a feed."),
    "clip":      (1080, 1920,  560, "attentive", "9:16 plate. Video centre, captions below."),
}

PAGE = """<!doctype html><meta charset="utf-8"><title>{name}</title>
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
  position: relative;
}}
body::before {{
  content: ''; position: absolute; inset: 0;
  background: radial-gradient(ellipse 52% 40% at 50% 6%, {wash}, transparent 72%);
}}
.mark {{ width: var(--mark-w); flex: none;
         filter: drop-shadow(0 0 {glowblur}px {glow}); }}
.wordmark {{ font-weight: 600; letter-spacing: -.022em; line-height: 1; }}
.rule {{ border-top: {rule3}px double {rule}; opacity: .85; }}
.rule.hair {{ border-top: 1px solid {rule}; opacity: .5; }}
.strip {{
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  letter-spacing: .18em; text-transform: uppercase;
  color: {ink_3}; white-space: nowrap;
}}
.strip b {{ color: {signal}; font-weight: 400; }}
{mark_css}
{layout}
</style>
{body}"""


def page(name, theme, w, h, basis, expr, layout, body):
    t = THEMES[theme]
    u = basis / 500
    return PAGE.format(
        name=name, font=font_b64(), w=w, h=h, mark_css=mark_css(t),
        layout=layout, body=body, glowblur=round(34 * u),
        rule3=max(2, round(3 * u)), **t)


def _mark(expr, extra=""):
    return f'<div class="mark" style="{expr_vars(expr)}{extra}">{expr_mark(expr)}</div>'


# ─────────────────────────────────────────────────────────────────────────────
# Layouts. Each returns (css, html).

def lay_cover(u, expr, **kw):
    """Square. Everything that is not the mark is a bet that the listener is
    looking at it larger than 55px, so there is very little of it."""
    css = f"""
body {{ display: grid; place-items: center; }}
.stack {{ display: flex; flex-direction: column; align-items: center;
          gap: {round(38*u)}px; position: relative; }}
/* Sized off the 55px proof, not the 3000px render. At list size the mark
   carries it and the wordmark is the first thing to go illegible, so both
   run larger than they would if this were only ever seen full size. */
.mark {{ --mark-w: {round(640*u)}px; }}
.wordmark {{ font-size: {round(212*u)}px; }}
.rule {{ width: {round(760*u)}px; }}
.strip {{ font-size: {round(30*u)}px; }}
"""
    body = f"""<div class="stack">
  {_mark(expr)}
  <p class="wordmark">AgentSee</p>
  <div class="rule"></div>
  <p class="strip">The agents see the work &nbsp;·&nbsp; <b>a podcast</b></p>
</div>"""
    return css, body


def lay_titlecard(u, expr, title="", **kw):
    css = f"""
body {{ display: flex; flex-direction: column; justify-content: center;
        padding: {round(120*u)}px {round(140*u)}px; }}
.head {{ display: flex; align-items: center; gap: {round(34*u)}px;
         margin-bottom: {round(54*u)}px; }}
.mark {{ --mark-w: {round(150*u)}px; }}
.wordmark {{ font-size: {round(76*u)}px; }}
.title {{ font-size: {round(112*u)}px; font-weight: 300; line-height: 1.12;
          letter-spacing: -.02em; max-width: 86%; }}
.rule {{ width: {round(300*u)}px; margin: {round(50*u)}px 0 {round(34*u)}px; }}
.strip {{ font-size: {round(26*u)}px; }}
"""
    body = f"""<div class="head">{_mark(expr)}<p class="wordmark">AgentSee</p></div>
<p class="title">{title or 'Episode title goes here'}</p>
<div class="rule hair"></div>
<p class="strip">Two people building in public &nbsp;·&nbsp; <b>agentsee.work</b></p>"""
    return css, body


def lay_standby(u, expr, title="Starting soon", **kw):
    css = f"""
body {{ display: grid; place-items: center; }}
.stack {{ display: flex; flex-direction: column; align-items: center;
          gap: {round(40*u)}px; }}
.mark {{ --mark-w: {round(300*u)}px; }}
.title {{ font-size: {round(92*u)}px; font-weight: 300; letter-spacing: -.015em; }}
.strip {{ font-size: {round(26*u)}px; }}
"""
    body = f"""<div class="stack">
  {_mark(expr)}
  <p class="title">{title}</p>
  <p class="strip"><b>agentsee.work</b></p>
</div>"""
    return css, body


def lay_endcard(u, expr, title="", **kw):
    css = f"""
body {{ display: grid; place-items: center; }}
.stack {{ display: flex; flex-direction: column; align-items: center;
          gap: {round(38*u)}px; }}
.mark {{ --mark-w: {round(280*u)}px; }}
.wordmark {{ font-size: {round(96*u)}px; }}
.rule {{ width: {round(420*u)}px; }}
.strip {{ font-size: {round(27*u)}px; line-height: 2; text-align: center; }}
"""
    body = f"""<div class="stack">
  {_mark(expr)}
  <p class="wordmark">AgentSee</p>
  <div class="rule"></div>
  <p class="strip"><b>agentsee.work</b><br>Hove &amp; London</p>
</div>"""
    return css, body


def lay_thumbnail(u, expr, title="", **kw):
    """A feed draws this about 360px wide. Few words, large, high contrast."""
    css = f"""
body {{ display: flex; flex-direction: column; justify-content: space-between;
        padding: {round(72*u)}px {round(82*u)}px; }}
.title {{ font-size: {round(118*u)}px; font-weight: 600; line-height: 1.04;
          letter-spacing: -.028em; max-width: 78%; }}
.foot {{ display: flex; align-items: center; gap: {round(26*u)}px; }}
.mark {{ --mark-w: {round(132*u)}px; }}
.wordmark {{ font-size: {round(58*u)}px; }}
.spacer {{ flex: 1; }}
.strip {{ font-size: {round(24*u)}px; }}
"""
    body = f"""<p class="title">{title or 'Three words max'}</p>
<div class="foot">
  {_mark(expr)}
  <p class="wordmark">AgentSee</p>
  <div class="spacer"></div>
  <p class="strip"><b>agentsee.work</b></p>
</div>"""
    return css, body


def lay_clip(u, expr, title="", **kw):
    """Background plate for a vertical clip. The 16:9 source drops into the
    middle band; captions live in the lower third. Both zones are left empty
    on purpose — anything drawn there is something the video has to cover."""
    band = round(1080 * 9 / 16)  # the 16:9 video, full width
    css = f"""
body {{ display: flex; flex-direction: column; align-items: center; }}
.top {{ height: {round((1920-band)*0.42)}px; display: flex; flex-direction: column;
        align-items: center; justify-content: center; gap: {round(26*u)}px; }}
.mark {{ --mark-w: {round(200*u)}px; }}
.wordmark {{ font-size: {round(62*u)}px; }}
/* paper_2 against paper is nearly invisible in noir, which is right for the
   plate and useless as a guide. The hairlines mark where the 16:9 source
   lands without relying on a fill you cannot see. */
.band {{ width: 100%; height: {band}px; background: {{paper_2}};
         border-top: 1px solid {{hair}}; border-bottom: 1px solid {{hair}}; }}
.foot {{ flex: 1; display: flex; align-items: flex-end; justify-content: center;
         padding-bottom: {round(74*u)}px; }}
.strip {{ font-size: {round(26*u)}px; }}
"""
    body = f"""<div class="top">{_mark(expr)}<p class="wordmark">AgentSee</p></div>
<div class="band"></div>
<div class="foot"><p class="strip"><b>agentsee.work</b></p></div>"""
    return css, body


LAYOUTS = {
    "cover": lay_cover, "titlecard": lay_titlecard, "standby": lay_standby,
    "endcard": lay_endcard, "thumbnail": lay_thumbnail, "clip": lay_clip,
}


# --title is an episode's title, so it belongs only on the assets that carry
# one. Routing it everywhere put "Running our own mail server" on the card that
# should have said "Starting soon".
TITLED = {"titlecard", "thumbnail"}


def build(name, theme, title="", expr=None):
    w, h, basis, default_expr, _ = ASSETS[name]
    kw = {"title": title} if name in TITLED else {}
    css, body = LAYOUTS[name](basis / 500, expr or default_expr, **kw)
    for k in ("paper_2", "hair"):
        css = css.replace("{" + k + "}", THEMES[theme][k])
    return page(name, theme, w, h, basis, expr or default_expr, css, body), w, h


def proof(cover_path, theme):
    """Cover art at the sizes a podcast app actually draws it. A cover that
    works at 3000px and dies at 55px is the normal failure, and the only way
    to know is to look at 55px."""
    from PIL import Image
    t = THEMES[theme]
    src = Image.open(cover_path).convert("RGB")
    sizes = [55, 120, 300, 600]
    pad, gap = 60, 50
    w = pad * 2 + sum(sizes) + gap * (len(sizes) - 1)
    h = pad * 2 + max(sizes)
    sheet = Image.new("RGB", (w, h), t["paper_2"])
    x = pad
    for s in sizes:
        sheet.paste(src.resize((s, s), Image.LANCZOS), (x, pad + (max(sizes) - s) // 2))
        x += s + gap
    out = cover_path.parent / f"{cover_path.stem}-proof.png"
    sheet.save(out)
    return out, sizes


def sheet(theme, title):
    """Everything at once, scaled to a common width. Assets get approved as a
    set or not at all — one at a time you lose track of whether they look like
    each other."""
    from PIL import Image
    t = THEMES[theme]
    cell, pad, gap = 520, 54, 44
    imgs = []
    for name in ASSETS:
        p = OUTDIR / f"show-{name}{'' if theme == 'noir' else '-newsprint'}.png"
        if not p.exists():
            continue
        im = Image.open(p).convert("RGB")
        im.thumbnail((cell, cell), Image.LANCZOS)
        imgs.append((name, im))
    cols = 3
    rows = (len(imgs) + cols - 1) // cols
    cw = cell + gap
    ch = cell + gap + 30
    out_im = Image.new("RGB", (pad * 2 + cols * cw - gap, pad * 2 + rows * ch - gap),
                       t["paper_2"])
    for i, (name, im) in enumerate(imgs):
        cx = pad + (i % cols) * cw + (cell - im.width) // 2
        cy = pad + (i // cols) * ch + (cell - im.height) // 2
        out_im.paste(im, (cx, cy))
    out = OUTDIR / f"show-contact{'' if theme == 'noir' else '-newsprint'}.png"
    out_im.save(out)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("asset", nargs="?", choices=sorted(ASSETS))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--sheet", action="store_true", help="contact sheet of what is rendered")
    ap.add_argument("--proof", action="store_true", help="cover art at podcast-app sizes")
    ap.add_argument("--title", default="", help="text for titlecard / thumbnail / standby")
    ap.add_argument("--expression", choices=sorted(EXPRESSIONS))
    ap.add_argument("--newsprint", action="store_true", help="light theme (default is noir)")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list or not (a.asset or a.all or a.sheet):
        for k, (w, h, _b, e, note) in ASSETS.items():
            print(f"  {k:<10} {w}x{h:<5} {e:<11} {note}")
        return 0

    theme = "newsprint" if a.newsprint else "noir"
    suffix = "" if theme == "noir" else "-newsprint"
    OUTDIR.mkdir(parents=True, exist_ok=True)

    names = list(ASSETS) if a.all else ([a.asset] if a.asset else [])
    for name in names:
        html, w, h = build(name, theme, a.title, a.expression)
        out = OUTDIR / f"show-{name}{suffix}.png"
        render(html, out, w, h)
        print(f"{out.relative_to(ROOT)}  {w}x{h}  {out.stat().st_size:,} bytes")
        if a.proof and name == "cover":
            p, sizes = proof(out, theme)
            print(f"  proof {p.relative_to(ROOT)}  at {', '.join(str(s) for s in sizes)}px")

    if a.sheet:
        print(f"{sheet(theme, a.title).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
