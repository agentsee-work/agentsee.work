#!/usr/bin/env python3
"""
The eye, moving — expression chains as frames and video, for editing.

    ./tools/eyeanim.py attentive sceptical closed
    ./tools/eyeanim.py curious surprised --blink --size 720 --format mov
    ./tools/eyeanim.py --list-chains

Output lands in `build/eye/` which is gitignored. Video does not belong in this
repo; the recipe for making it does.

Frames come out as PNG with an alpha channel, so the result drops straight onto
footage. Every editor takes a PNG sequence, which means this is useful even
with no encoder installed — the video files are a convenience on top, not the
product.

HOW IT RENDERS

One Chrome launch, not one per frame. The whole animation is laid out as a grid
of cells on a single page, screenshotted once, and sliced with Pillow. Ninety
frames through ninety headless launches is about forty seconds of process
spawning; as a grid it is one. The eye is pure SVG driven by five custom
properties, so a frame is just a different set of numbers on the same markup —
there is nothing to animate, only to enumerate.

BLINKS

`--blink` closes the eye through each transition rather than sliding the lids
between two open states. Real eyes change gaze with a saccade and very often a
blink, and interpolating a wide-open eye into a squint without one looks like a
slider being dragged, which is exactly what it is.
"""

import argparse
import math
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from banner import ROOT, THEMES  # noqa: E402
from eye import EXPRESSIONS, font_b64, mark_css, mark_svg  # noqa: E402


MAX_GRID = 8000   # px per side; Chrome will go further, but not happily


def render_alpha(html, out, w, h, ss=1):
    """banner.render() deliberately paints an opaque page — a banner with a
    transparent background is a bug. Overlays are the opposite, so this is the
    same call plus --default-background-color=00000000. Without it Chrome
    composites onto white and the alpha channel comes back fully opaque, which
    looks fine in a viewer and fails the moment it goes over footage."""
    import subprocess as sp
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html)
        src = f.name
    try:
        sp.run(["google-chrome", "--headless=new", "--disable-gpu", "--hide-scrollbars",
                "--default-background-color=00000000",
                f"--force-device-scale-factor={ss}",
                f"--window-size={w},{h}", f"--screenshot={out}", f"file://{src}"],
               check=True, capture_output=True)
    finally:
        pathlib.Path(src).unlink(missing_ok=True)

OUT = ROOT / "build" / "eye"

# Chains worth having to hand. Named so they can be asked for by intent rather
# than reconstructed from memory every time.
CHAINS = {
    # Named for the moment they serve, not the expressions they contain — the
    # point of having them is to ask for a beat rather than reconstruct one.
    "blink":       (["attentive", "attentive"], "Just a blink. Pure filler, loops."),
    "listen":      (["attentive", "curious", "attentive"], "Workhorse cutaway. Loops."),
    "idle":        (["attentive", "curious", "thinking", "attentive"], "Longer filler. Loops."),
    "intro":       (["asleep", "attentive", "curious"], "Wake up and pay attention. Opening title."),
    "startle":     (["asleep", "surprised", "attentive"], "Woken suddenly. Good for going live."),
    "consider":    (["attentive", "thinking", "sceptical"], "Weighing a claim."),
    "concede":     (["sceptical", "thinking", "attentive"], "Coming round. The reverse of consider."),
    "doubt":       (["attentive", "sceptical"], "Into a steel-man beat."),
    "unconvinced": (["curious", "sceptical", "unimpressed"], "Doubt hardening."),
    "scrutinise":  (["curious", "scrutiny"], "Going into the technical section."),
    "reveal":      (["scrutiny", "surprised", "attentive"], "Found something."),
    "doubletake":  (["attentive", "surprised", "scrutiny", "surprised"], "Comedy. Did that say what I think."),
    "deadpan":     (["attentive", "unimpressed"], "Comedy beat."),
    "lose-interest": (["attentive", "thinking", "asleep"], "Drifting off. The Docker Hub beat."),
    "hardno":      (["curious", "scrutiny", "unimpressed", "closed"], "A rejection, in four steps."),
    "signoff":     (["attentive", "thinking", "closed"], "End card."),
    "sleep":       (["attentive", "asleep"], "Into the standby card."),
}

AXES = ("lid_top", "lid_bottom", "iris_x", "iris_y", "pupil_s")


def axes(name):
    lt, lb, ix, iy, ps, _ = EXPRESSIONS[name]
    return dict(lid_top=lt, lid_bottom=lb, iris_x=ix, iris_y=iy, pupil_s=ps)


def ease(t):
    """Cubic in-out. Lids accelerate and settle; a linear lid reads mechanical."""
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def lerp(a, b, t):
    return {k: a[k] + (b[k] - a[k]) * t for k in AXES}


def blink_shape(t):
    """Closed at the midpoint, open at both ends. Sine rather than a triangle
    because a lid that reverses direction at a sharp corner reads as a glitch."""
    return math.sin(math.pi * t) ** 0.7


def timeline(chain, fps, hold, trans, blink):
    """Every frame's five numbers, in order."""
    frames = []
    hold_n, trans_n = max(1, round(hold * fps)), max(2, round(trans * fps))
    for i, name in enumerate(chain):
        a = axes(name)
        frames += [dict(a) for _ in range(hold_n)]
        if i + 1 < len(chain):
            b = axes(chain[i + 1])
            for f in range(1, trans_n):
                t = ease(f / trans_n)
                fr = lerp(a, b, t)
                if blink:
                    # Drive the upper lid shut and back rather than adding to
                    # it, or an already-lowered lid overshoots past closed.
                    s = blink_shape(f / trans_n)
                    fr["lid_top"] = fr["lid_top"] * (1 - s) + 1.0 * s
                frames.append(fr)
    return frames


def style(fr):
    """Lids are geometry now, so only the transform axes come through here."""
    return (f"--iris-x:{fr['iris_x']:.3f}; --iris-y:{fr['iris_y']:.3f}; "
            f"--pupil-s:{fr['pupil_s']:.4f};")


def sheet_html(frames, theme, cell, glow):
    t = THEMES[theme]
    cols = math.ceil(math.sqrt(len(frames)))
    rows = math.ceil(len(frames) / cols)
    cells = "".join(f'<i style="{style(f)}">'
                f'{mark_svg(f["lid_top"], f["lid_bottom"])}</i>' for f in frames)
    return f"""<!doctype html><meta charset="utf-8"><title>frames</title>
<style>
@font-face {{ font-family:'Newsreader';
  src:url('data:font/woff2;base64,{font_b64()}') format('woff2'); }}
* {{ box-sizing:border-box; margin:0; }}
html,body {{ background:transparent; }}
.grid {{ display:grid; grid-template-columns:repeat({cols},{cell}px);
         width:{cols*cell}px; }}
i {{ display:block; width:{cell}px; height:{cell}px; padding:{round(cell*.06)}px;
     --mark-w:{round(cell*.88)}px;
     {f'filter: drop-shadow(0 0 {round(cell*.07)}px {t["glow"]});' if glow else ''} }}
i svg {{ width:100%; height:100%; display:block; overflow:visible; }}
{mark_css(t)}
</style>
<div class="grid">{cells}</div>""", cols, rows


def merge(ims):
    """Average sub-frames into one, through premultiplied alpha.

    Averaging straight alpha blends RGB across pixels that are transparent and
    therefore carry no meaningful colour — black, here — which drags a dark
    fringe into every soft edge. Premultiply first and the transparent pixels
    contribute nothing, which is the point of them."""
    import numpy as np
    acc = None
    for im in ims:
        a = np.asarray(im, dtype=np.float64) / 255.0
        pm = np.dstack([a[..., :3] * a[..., 3:4], a[..., 3:4]])
        acc = pm if acc is None else acc + pm
    acc /= len(ims)
    al = acc[..., 3:4]
    rgb = np.divide(acc[..., :3], al, out=np.zeros_like(acc[..., :3]), where=al > 1e-6)
    from PIL import Image
    out = np.clip(np.dstack([rgb, al]) * 255.0 + 0.5, 0, 255).astype("uint8")
    return Image.fromarray(out, "RGBA")


def render_frames(frames, theme, cell, glow, ss, frames_dir, tmp, blur=1):
    """Frames to disk, in as few Chrome launches as the grid limit allows.

    One launch per frame is most of a minute of process spawning for a two
    second clip. One launch for everything stops working the moment a cell is
    big enough to be useful in a timeline: 65 frames at 1080px is a 9720px
    grid before supersampling. So: as many frames per launch as fit, then the
    next batch."""
    from PIL import Image
    phys = cell * ss
    per_side = max(1, MAX_GRID // phys)
    per_chunk = max(1, per_side * per_side)
    rendered, n = [], 0


    for start in range(0, len(frames), per_chunk):
        chunk = frames[start:start + per_chunk]
        html, cols, rows = sheet_html(chunk, theme, cell, glow)
        render_alpha(html, tmp, cols * cell, rows * cell, ss)
        im = Image.open(tmp).convert("RGBA")
        for i in range(len(chunk)):
            x, y = (i % cols) * phys, (i // cols) * phys
            cellim = im.crop((x, y, x + phys, y + phys))
            if ss != 1:
                cellim = cellim.resize((cell, cell), Image.LANCZOS)
            rendered.append(cellim)
            while len(rendered) >= blur:
                out = rendered[0] if blur == 1 else merge(rendered[:blur])
                del rendered[:blur]
                out.save(frames_dir / f"f_{n:04d}.png")
                n += 1
    tmp.unlink(missing_ok=True)
    return n


def encode(frames_dir, n, fps, fmt, slug):
    """ffmpeg if it is here, APNG via Pillow if it is not. The PNG sequence is
    the deliverable either way — this is the convenience layer."""
    src = str(frames_dir / "f_%04d.png")
    ff = shutil.which("ffmpeg")
    if not ff:
        return None, ("ffmpeg not installed — PNG sequence only. "
                      "`sudo apt install ffmpeg` to get video.")
    recipes = {
        # VP9 with alpha. Plays in browsers and OBS, keeps transparency.
        "webm": ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", "-b:v", "0", "-crf", "28"],
        # ProRes 4444. What an NLE actually wants for an alpha overlay.
        "mov":  ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le"],
        # Flattened. Preview only — no alpha, so it carries the theme background.
        "mp4":  ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18"],
    }
    out = frames_dir.parent / f"{slug}.{fmt}"
    cmd = [ff, "-y", "-framerate", str(fps), "-i", src, *recipes[fmt], str(out)]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        return None, r.stderr.decode()[-400:]
    return out, None


def contact_sheet(made, theme, per_row=9, cell=118):
    """One filmstrip per chain, evenly sampled, on a mid grey.

    Grey rather than the theme background on purpose: these are alpha assets
    and the point of reviewing them is to see what the alpha does. A halo hides
    perfectly against the dark ground they were authored on, which is how one
    survived several rounds of review here."""
    from PIL import Image, ImageDraw
    pad, gutter, label_h = 22, 8, 17
    w = pad * 2 + per_row * cell
    h = pad * 2 + len(made) * (cell + label_h + gutter)
    sheet = Image.new("RGB", (w, h), (92, 90, 88))
    draw = ImageDraw.Draw(sheet)
    for r, (slug, d, n) in enumerate(made):
        y = pad + r * (cell + label_h + gutter)
        draw.text((pad, y), f"{slug}   {n} frames", fill=(232, 230, 226))
        idx = [round(i * (n - 1) / (per_row - 1)) for i in range(per_row)]
        for c, i in enumerate(idx):
            f = d / "frames" / f"f_{i:04d}.png"
            im = Image.open(f).convert("RGBA").resize((cell, cell), Image.LANCZOS)
            sheet.paste(im, (pad + c * cell, y + label_h), im)
    out = OUT / f"contact{'' if theme == 'noir' else '-newsprint'}.png"
    sheet.save(out)
    return f"{out.relative_to(ROOT)}  {len(made)} chains"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("chain", nargs="*", help="expressions in order, or a named chain")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--hold", type=float, default=0.5, help="seconds on each expression")
    ap.add_argument("--trans", type=float, default=0.38, help="seconds between them")
    ap.add_argument("--size", type=int, default=1080,
                    help="frame size, square. Match the timeline, or it gets "
                         "upscaled and the antialiasing turns to stair-steps")
    ap.add_argument("--blur", type=int, default=3,
                    help="sub-frames averaged per output frame. 1 disables it, "
                         "and the edges then crawl as each frame antialiases "
                         "independently of the last")
    ap.add_argument("--ss", type=int, default=1, choices=[1, 2, 3],
                    help="spatial supersample. Costs a lot at 1080 because the grid shrinks to 3x3 a launch; worth it for a hero asset, not a batch")
    ap.add_argument("--blink", action="store_true", help="blink through each change")
    # Off by default, unlike every other asset here. The glow is a
    # background-dependent effect: against noir it reads as the mark catching
    # light, but it is a drop-shadow, so on a transparent frame it becomes a
    # translucent amber cloud reaching ~48px past the mark and covering 39% of
    # the frame in partial alpha. Over footage that is an unmotivated halo.
    # Opt in for a composite you know sits on the theme background.
    ap.add_argument("--glow", action="store_true",
                    help="amber bloom — only for frames that will sit on the theme background")
    ap.add_argument("--newsprint", action="store_true")
    ap.add_argument("--format", choices=["mov", "webm", "mp4", "none"], default="mov")
    ap.add_argument("--all", action="store_true", help="render every named chain")
    ap.add_argument("--contact", action="store_true",
                    help="one filmstrip row per chain, for reviewing a batch")
    ap.add_argument("--list-chains", action="store_true")
    a = ap.parse_args()

    if a.list_chains or not (a.chain or a.all):
        w = max(len(k) for k in CHAINS)
        print("named chains:")
        for k, (seq, note) in CHAINS.items():
            print(f"  {k:<{w}}  {' → '.join(seq):<46} {note}")
        print("\nexpressions:", ", ".join(EXPRESSIONS))
        return 0

    if a.all:
        jobs = [(k, seq) for k, (seq, _) in CHAINS.items()]
    elif len(a.chain) == 1 and a.chain[0] in CHAINS:
        jobs = [(a.chain[0], CHAINS[a.chain[0]][0])]
    else:
        jobs = [("-".join(a.chain), a.chain)]
    bad = sorted({c for _, seq in jobs for c in seq if c not in EXPRESSIONS})
    if bad:
        print(f"unknown expression: {', '.join(bad)}", file=sys.stderr)
        return 2

    theme = "newsprint" if a.newsprint else "noir"
    suffix = "" if theme == "noir" else "-newsprint"
    made = []

    for slug, chain in jobs:
        # Sub-frames are just a timeline at blur x the rate; averaging groups
        # of `blur` back down is what turns per-frame antialiasing into motion
        # blur, and with it the edge crawl into smooth movement.
        frames = timeline(chain, a.fps * a.blur, a.hold, a.trans, a.blink)

        d = OUT / f"{slug}{suffix}"
        if d.exists():
            shutil.rmtree(d)
        (d / "frames").mkdir(parents=True)

        n = render_frames(frames, theme, a.size, a.glow, a.ss,
                          d / "frames", d / "_grid.png", a.blur)
        made.append((slug, d, n))

        line = (f"{d.relative_to(ROOT)}/frames/  {n} frames  "
                f"{a.size}x{a.size}  {a.fps}fps  {n/a.fps:.2f}s  alpha"
                + (f"  blur x{a.blur}" if a.blur > 1 else ""))
        if a.format != "none":
            out, err = encode(d / "frames", n, a.fps, a.format, slug)
            line += (f"\n  {out.relative_to(ROOT)}  {out.stat().st_size:,} bytes"
                     if out else f"\n  {err}")
        print(line)

    if a.contact:
        print(contact_sheet(made, theme))
    return 0


if __name__ == "__main__":
    sys.exit(main())
