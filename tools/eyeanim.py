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


def render_alpha(html, out, w, h):
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
                "--default-background-color=00000000", "--force-device-scale-factor=1",
                f"--window-size={w},{h}", f"--screenshot={out}", f"file://{src}"],
               check=True, capture_output=True)
    finally:
        pathlib.Path(src).unlink(missing_ok=True)

OUT = ROOT / "build" / "eye"

# Chains worth having to hand. Named so they can be asked for by intent rather
# than reconstructed from memory every time.
CHAINS = {
    "intro":    (["asleep", "attentive", "curious"], "Wake up and pay attention. Opening title."),
    "doubt":    (["attentive", "sceptical"], "Into a steel-man beat."),
    "reveal":   (["scrutiny", "surprised", "attentive"], "Found something."),
    "signoff":  (["attentive", "thinking", "closed"], "End card."),
    "deadpan":  (["attentive", "unimpressed"], "Comedy beat."),
    "idle":     (["attentive", "curious", "thinking", "attentive"], "Loopable filler."),
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


def slice_grid(path, n, cols, cell):
    from PIL import Image
    im = Image.open(path).convert("RGBA")
    out = []
    for i in range(n):
        x, y = (i % cols) * cell, (i // cols) * cell
        out.append(im.crop((x, y, x + cell, y + cell)))
    return out


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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("chain", nargs="*", help="expressions in order, or a named chain")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--hold", type=float, default=0.5, help="seconds on each expression")
    ap.add_argument("--trans", type=float, default=0.38, help="seconds between them")
    ap.add_argument("--size", type=int, default=480, help="frame size, square")
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
    ap.add_argument("--list-chains", action="store_true")
    a = ap.parse_args()

    if a.list_chains or not a.chain:
        print("named chains:")
        for k, (seq, note) in CHAINS.items():
            print(f"  {k:<9} {' → '.join(seq):<46} {note}")
        print("\nexpressions:", ", ".join(EXPRESSIONS))
        return 0

    if len(a.chain) == 1 and a.chain[0] in CHAINS:
        slug, chain = a.chain[0], CHAINS[a.chain[0]][0]
    else:
        chain = a.chain
        slug = "-".join(chain)
    bad = [c for c in chain if c not in EXPRESSIONS]
    if bad:
        print(f"unknown expression: {', '.join(bad)}", file=sys.stderr)
        return 2

    theme = "newsprint" if a.newsprint else "noir"
    frames = timeline(chain, a.fps, a.hold, a.trans, a.blink)
    html, cols, rows = sheet_html(frames, theme, a.size, a.glow)

    d = OUT / f"{slug}{'' if theme == 'noir' else '-newsprint'}"
    if d.exists():
        shutil.rmtree(d)
    (d / "frames").mkdir(parents=True)

    grid = d / "_grid.png"
    render_alpha(html, grid, cols * a.size, rows * a.size)
    for i, im in enumerate(slice_grid(grid, len(frames), cols, a.size)):
        im.save(d / "frames" / f"f_{i:04d}.png")
    grid.unlink()

    dur = len(frames) / a.fps
    print(f"{d.relative_to(ROOT)}/frames/  {len(frames)} frames  "
          f"{a.size}x{a.size}  {a.fps}fps  {dur:.2f}s  alpha")

    if a.format != "none":
        out, err = encode(d / "frames", len(frames), a.fps, a.format, slug)
        print(f"  {out.relative_to(ROOT)}  {out.stat().st_size:,} bytes" if out
              else f"  {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
