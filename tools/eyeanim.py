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
from eye import (AXES, EXPRESSIONS, REST, axes as eye_axes,
                 font_b64, mark_css, mark_svg)  # noqa: E402


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
# Chains carry their own pace. Timing is part of an emotion, not a global
# setting: a double take that eases like a sign-off is not a double take, and
# deadpan is funny precisely because nothing happens quickly.
#
#   hold/trans  seconds, overriding the defaults
#   jitter      iris tremor amplitude in viewBox units. Small and fast; this is
#               the difference between angry and merely squinting.
#
# A step is an expression name, or a dict of axis overrides for a waypoint that
# does not deserve a name of its own — which is how the eye roll is built.
CHAINS = {
    "blink":       dict(seq=["attentive", "attentive"], note="Just a blink. Loops."),
    "listen":      dict(seq=["attentive", "curious", "attentive"], note="Workhorse cutaway. Loops."),
    "idle":        dict(seq=["attentive", "curious", "thinking", "attentive"], note="Longer filler. Loops."),
    "intro":       dict(seq=["asleep", "attentive", "alert"], note="Wake and focus. Opening title."),
    "startle":     dict(seq=["asleep", "surprised", "alert"], hold=0.30, trans=0.14,
                        jitter=0.66, note="Woken suddenly. Fast, with a tremor."),
    "consider":    dict(seq=["attentive", "thinking", "wry"], trans=0.46,
                        note="Weighing it, and landing somewhere knowing."),
    "concede":     dict(seq=["sceptical", "closed", "amused"], trans=0.30,
                        note="Resistance, a beat shut, then warmth. Concession needs the beat."),
    "doubt":       dict(seq=["attentive", "sceptical"], trans=0.26, note="One sharp turn into doubt."),
    "unconvinced": dict(seq=["curious", "sceptical", "unimpressed"], note="Doubt hardening."),
    # Three steps, not two: the arc runs attentive -> attentive so the lid
    # stays out of the way, and only then does it land on unimpressed. Rolling
    # straight into a lowered lid hides the whole movement behind it, which is
    # what the first attempt did.
    "eyeroll":     dict(seq=["attentive", "attentive", "unimpressed"],
                        hold=0.22, trans=0.24,
                        roll=dict(dur=0.60, rx=15, ry=9.5, turns=1.0),
                        note="One unbroken sweep, eye open. Lands deadpan."),
    "scrutinise":  dict(seq=["curious", "scrutiny"], note="Into the technical section."),
    "reveal":      dict(seq=["scrutiny", "surprised", "alert"], trans=0.22,
                        note="Found something. Quick."),
    "doubletake":  dict(seq=["attentive", "surprised", "scrutiny", "surprised"],
                        hold=0.26, trans=0.15, note="Did that say what I think. Fast."),
    "deadpan":     dict(seq=["attentive", "unimpressed"], hold=1.10, trans=0.60,
                        note="Comedy beat. The stillness is the joke."),
    "lose-interest": dict(seq=["attentive", "curious", "unimpressed", "asleep"],
                          hold=0.60, trans=0.55,
                          note="Attention wanders, then droops. Not the same as going to sleep."),
    "hardno":      dict(seq=["curious", "angry", "angry"], hold=0.34, trans=0.16,
                        jitter=0.80, note="A refusal, with the hat down and a tremor."),
    "signoff":     dict(seq=["attentive", "amused", "closed"], note="End card, on a warm note."),
    "sleep":       dict(seq=["attentive", "asleep"], trans=0.55, note="Into the standby card."),
}



def step_axes(step):
    """A chain step is an expression name, or a dict of overrides on rest."""
    if isinstance(step, str):
        return eye_axes(step)
    return {**REST, **step}


def ease(t):
    """Cubic in-out. Lids accelerate and settle; a linear lid reads mechanical."""
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def lerp(a, b, t):
    return {k: a[k] + (b[k] - a[k]) * t for k in AXES}


def arc_frames(a, b, roll, fps, n_default=0.62):
    """One continuous sweep of the iris, superimposed on an ordinary ease.

    An eye roll built from waypoints is not an eye roll: the iris stops and
    restarts at every one, easing out and back in each time, and with blinks
    enabled it blinks between each pair. Sarcasm needs a single unbroken
    movement, so this is a real arc — the lids, hat and pupil ease from a to b
    underneath while the iris travels a circle on top of them.

    The circle's amplitude fades in and out at the ends so the iris leaves and
    rejoins its underlying position rather than snapping onto the path."""
    n = max(6, round(roll.get("dur", n_default) * fps))
    rx, ry = roll.get("rx", 12), roll.get("ry", 9)
    turns, out = roll.get("turns", 1.0), []
    for f in range(1, n):
        t = f / n
        fr = lerp(a, b, ease(t))
        # Start at the top: an eye roll goes up first, always.
        ang = -math.pi / 2 + 2 * math.pi * turns * ease(t)
        edge = 0.10
        env = (min(1.0, t / edge) if t < edge else
               min(1.0, (1 - t) / edge) if t > 1 - edge else 1.0)
        env = env * env * (3 - 2 * env)          # smoothstep the envelope
        fr["ix"] += rx * math.cos(ang) * env
        fr["iy"] += ry * math.sin(ang) * env
        out.append(fr)
    return out


def blink_shape(t):
    """Closed at the midpoint, open at both ends. Sine rather than a triangle
    because a lid that reverses direction at a sharp corner reads as a glitch."""
    return math.sin(math.pi * t) ** 0.7


def timeline(chain, fps, hold, trans, blink, jitter=0.0, roll=None):
    """Every frame's axis values, in order.

    `jitter` adds a fast, small iris tremor. It runs at a fixed frequency
    rather than per-frame noise, which would read as the rasterisation fault
    this tool spent a while removing rather than as a deliberate shake.

    Amplitude is in viewBox units, where the eye is 152 wide — so 0.8 is about
    half a percent of the eye. Motion blur then damps it further, averaging
    sub-frames across roughly half a cycle, so the pupil lands near 2px of
    actual swing at 1080. It wants to be
    near the threshold of being seen. Anything you can clearly track stops
    reading as tension and starts reading as a wobble."""
    frames = []
    hold_n, trans_n = max(1, round(hold * fps)), max(2, round(trans * fps))
    for i, name in enumerate(chain):
        a = step_axes(name)
        frames += [dict(a) for _ in range(hold_n)]
        if i + 1 < len(chain):
            b = step_axes(chain[i + 1])
            if roll and i == 0:
                # The arc *is* the transition. No blink — you cannot roll your
                # eyes with them shut.
                frames += arc_frames(a, b, roll, fps)
                continue
            for f in range(1, trans_n):
                t = ease(f / trans_n)
                fr = lerp(a, b, t)
                if blink:
                    # Drive the upper lid shut and back rather than adding to
                    # it, or an already-lowered lid overshoots past closed.
                    s = blink_shape(f / trans_n)
                    fr["lt"] = fr["lt"] * (1 - s) + 1.0 * s
                frames.append(fr)
    if jitter:
        for i, fr in enumerate(frames):
            ph = i / max(1, fps) * 17.0 * 2 * math.pi
            fr["ix"] += jitter * math.sin(ph)
            fr["iy"] += jitter * 0.55 * math.sin(ph * 1.7 + 1.1)
    return frames


def style(fr):
    """Lids are geometry now, so only the transform axes come through here."""
    return (f"--iris-x:{fr['ix']:.3f}; --iris-y:{fr['iy']:.3f}; "
            f"--pupil-s:{fr['ps']:.4f};")


def sheet_html(frames, theme, cell, glow):
    t = THEMES[theme]
    cols = math.ceil(math.sqrt(len(frames)))
    rows = math.ceil(len(frames) / cols)
    cells = "".join(f'<i style="{style(f)}">'
                f'{mark_svg(f["lt"], f["lb"], f["tilt"], f["hat"])}</i>' for f in frames)
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


def frame_key(fr):
    """Identity of a rendered frame: its axis values, rounded past what a
    pixel can show."""
    return tuple(round(float(fr[k]), 3) for k in sorted(fr))


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


def render_frames(frames, theme, cell, glow, ss, frames_dir, tmp, blur=1, jobs=8):
    """One Chrome launch per sub-frame, run in parallel.

    This began as one launch for the whole animation, laid out as a grid and
    sliced — ninety frames through ninety launches is most of a minute of
    process spawning. That optimisation was the direct cause of the flicker it
    was meant to help. Identical SVG in two cells of the *same* screenshot
    rasterises differently (948px of 360x360 differ, at full contrast), while
    the same content in two *separate* launches is bit-identical. So every
    static edge — the hat, which does not move at all — shimmered frame to
    frame for free.

    The arrangement does not matter: a single column still diverges. Only one
    frame per launch is stable. The cost comes back through parallelism
    instead, which is free correctness-wise and suits a 32-core desk.
    """
    from concurrent.futures import ThreadPoolExecutor
    from PIL import Image

    # A hold is the same frame over and over, and this was paying a Chrome
    # launch for each one: across a full run three quarters of every render
    # reproduced a picture already made. Key on the axis values and the
    # duplicates cost nothing.
    uniq = {}
    for fr in frames:
        uniq.setdefault(frame_key(fr), fr)

    def one(item):
        k, fr = item
        html, _, _ = sheet_html([fr], theme, cell, glow)
        out = tmp.parent / f"_sub_{abs(hash(k)) % 10**9:09d}.png"
        render_alpha(html, out, cell, cell, ss)
        im = Image.open(out).convert("RGBA")
        if ss != 1:
            im = im.resize((cell, cell), Image.LANCZOS)
        im.load()
        out.unlink(missing_ok=True)
        return k, im

    cache = {}
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        for k, im in ex.map(one, uniq.items()):
            cache[k] = im
    subs = {i: cache[frame_key(fr)] for i, fr in enumerate(frames)}

    n = 0
    for start in range(0, len(frames) - blur + 1, blur):
        group = [subs[start + k] for k in range(blur)]
        (group[0] if blur == 1 else merge(group)).save(frames_dir / f"f_{n:04d}.png")
        n += 1
    return n


def encode(frames_dir, n, fps, fmt, slug):
    """ffmpeg if it is here. The PNG sequence is the deliverable either way —
    every editor takes one — so this is the convenience layer, not the product."""
    src = str(frames_dir / "f_%04d.png")
    ff = shutil.which("ffmpeg")
    if not ff:
        return None, "ffmpeg not on PATH — PNG sequence only"
    recipes = {
        # VP9 with alpha. Plays in browsers and OBS. ffmpeg writes alpha_mode=1
        # but cannot read its own alpha back, so this path is offered unverified.
        "webm": ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0",
                 "-b:v", "0", "-crf", "28"],
        # ProRes 4444. What an NLE wants for an alpha overlay, and its alpha
        # survives a round-trip, which is why it is the default.
        "mov":  ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le"],
        # Flattened preview. No alpha.
        "mp4":  ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18"],
    }
    out = frames_dir.parent / f"{slug}.{fmt}"
    r = subprocess.run([ff, "-y", "-framerate", str(fps), "-i", src,
                        *recipes[fmt], str(out)], capture_output=True)
    if r.returncode != 0:
        return None, r.stderr.decode()[-400:]
    return out, None


REEL_TITLE_H = 170


def reel_h(cell):
    return round(cell * 0.88 * 210 / 240) + 2 * round(cell * 0.06) + REEL_TITLE_H


def reel_html(frame, theme, cell, title):
    """One reel frame: the chain's name in a band above, the mark below it.

    The band is its own row rather than an overlay, so nothing ever sits on
    top of the eye — the point of a review reel is to see the animation, not
    a caption competing with it."""
    t = THEMES[theme]
    return f"""<!doctype html><meta charset="utf-8">
<style>
@font-face {{{{ font-family:'Newsreader';
  src:url('data:font/woff2;base64,{{font}}') format('woff2'); font-weight:300 700; }}}}
* {{{{ box-sizing:border-box; margin:0; }}}}
html,body {{{{ width:{{w}}px; height:{{h}}px; background:{{paper}}; overflow:hidden; }}}}
.band {{{{ height:{REEL_TITLE_H}px; display:flex; align-items:center;
          justify-content:center; font-family:ui-monospace,Menlo,monospace;
          font-size:{{fs}}px; letter-spacing:.22em; text-transform:uppercase;
          color:{{signal}}; }}}}
i {{{{ display:block; width:{{cell}}px; height:{{ih}}px; padding:{{pad}}px;
      --mark-w:{{mark}}px; }}}}
i svg {{{{ width:100%; height:100%; display:block; overflow:visible; }}}}
{{mark_css}}
</style>
<div class="band">{{title}}</div>
<i style="{{st}}">{{svg}}</i>""".format(
        font=font_b64(), w=cell,
        h=round(cell * 0.88 * 210 / 240) + 2 * round(cell * 0.06) + REEL_TITLE_H,
        paper=t["paper"],
        signal=t["signal"], fs=round(cell * 0.035), cell=cell,
        pad=round(cell * 0.06), mark=round(cell * 0.88),
        ih=round(cell * 0.88 * 210 / 240) + 2 * round(cell * 0.06),
        mark_css=mark_css(t), title=title, st=style(frame),
        svg=mark_svg(frame["lt"], frame["lb"], frame["tilt"], frame["hat"]))


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


def build_reel(theme, cell, fps, blur, ss, blink, jobs, hold_ends=0.9):
    """Every chain end to end, labelled, as one flattened video for review.

    Each clip is bracketed by a longer hold on its first and last frame:
    reviewing a cut of eighteen animations, the hardest part is telling where
    one stops and the next begins, and a still moment does that better than a
    caption change.

    Flattened on purpose — this is for watching, not compositing."""
    from concurrent.futures import ThreadPoolExecutor
    from PIL import Image

    out_dir = OUT / "_reel"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    pad_n = max(1, round(hold_ends * fps * blur))

    jobs_list, n_out = [], 0
    for slug, c in CHAINS.items():
        fr = timeline(c["seq"], fps * blur, c.get("hold", 0.5),
                      c.get("trans", 0.38), blink, c.get("jitter", 0.0),
                      c.get("roll"))
        fr = [fr[0]] * pad_n + fr + [fr[-1]] * pad_n
        for f in fr:
            jobs_list.append((slug, f))

    # The reel pads each clip with a long still at both ends, so 81% of its
    # frames repeat. Render each distinct (chain, pose) once.
    uniq = {}
    for slug, fr in jobs_list:
        uniq.setdefault((slug, frame_key(fr)), (slug, fr))

    def one(item):
        k, (slug, fr) = item
        out = out_dir / f"u_{abs(hash(k)) % 10**9:09d}.png"
        render_alpha(reel_html(fr, theme, cell, slug), out, cell, reel_h(cell), ss)
        if ss != 1:
            Image.open(out).convert("RGB").resize(
                (cell, reel_h(cell)), Image.LANCZOS).save(out)
        return k, out

    cache = {}
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        for k, path in ex.map(one, uniq.items()):
            cache[k] = path
    for i, (slug, fr) in enumerate(jobs_list):
        shutil.copyfile(cache[(slug, frame_key(fr))], out_dir / f"r_{i:05d}.png")
    for path in set(cache.values()):
        path.unlink(missing_ok=True)

    # average sub-frames down, in place
    files = sorted(out_dir.glob("r_*.png"))
    for k in range(len(files) // blur):
        group = [Image.open(files[k * blur + j]).convert("RGBA") for j in range(blur)]
        (group[0] if blur == 1 else merge(group)).convert("RGB").save(
            out_dir / f"f_{k:05d}.png")
    for f in files:
        f.unlink()

    out = OUT / f"reel{'' if theme == 'noir' else '-newsprint'}.mp4"
    subprocess.run([shutil.which("ffmpeg"), "-y", "-framerate", str(fps),
                    "-i", str(out_dir / "f_%05d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "17",
                    str(out)], check=True, capture_output=True)
    shutil.rmtree(out_dir)
    return out, len(files) // blur


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
    ap.add_argument("--ss", type=int, default=2, choices=[1, 2, 3],
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
    ap.add_argument("--reel", action="store_true",
                    help="every chain end to end, labelled, as one video for review")
    ap.add_argument("--jobs", type=int, default=8,
                    help="parallel Chrome launches")
    ap.add_argument("--list-chains", action="store_true")
    a = ap.parse_args()

    if a.reel:
        theme = "newsprint" if a.newsprint else "noir"
        out, n = build_reel(theme, a.size, a.fps, a.blur, a.ss,
                            a.blink, a.jobs)
        print(f"{out.relative_to(ROOT)}  {n} frames  {n/a.fps:.1f}s  "
              f"{len(CHAINS)} chains")
        return 0

    if a.list_chains or not (a.chain or a.all):
        w = max(len(k) for k in CHAINS)
        print("named chains:")
        for k, c in CHAINS.items():
            seq = " → ".join(x if isinstance(x, str) else "·" for x in c["seq"])
            pace = "".join(f" {n}={c[n]}" for n in ("hold", "trans", "jitter") if n in c)
            print(f"  {k:<{w}}  {seq:<44}{pace:<26} {c['note']}")
        print("\nexpressions:", ", ".join(EXPRESSIONS))
        return 0

    if a.all:
        jobs = [(k, c) for k, c in CHAINS.items()]
    elif len(a.chain) == 1 and a.chain[0] in CHAINS:
        jobs = [(a.chain[0], CHAINS[a.chain[0]])]
    else:
        jobs = [("-".join(a.chain), dict(seq=list(a.chain)))]
    bad = sorted({x for _, c in jobs for x in c["seq"]
                  if isinstance(x, str) and x not in EXPRESSIONS})
    if bad:
        print(f"unknown expression: {', '.join(bad)}", file=sys.stderr)
        return 2

    theme = "newsprint" if a.newsprint else "noir"
    suffix = "" if theme == "noir" else "-newsprint"
    made = []

    for slug, conf in jobs:
        chain = conf["seq"]
        hold = conf.get("hold", a.hold)
        trans = conf.get("trans", a.trans)
        jit = conf.get("jitter", 0.0)
        # Sub-frames are just a timeline at blur x the rate; averaging groups
        # of `blur` back down is what turns per-frame antialiasing into motion
        # blur, and with it the edge crawl into smooth movement.
        frames = timeline(chain, a.fps * a.blur, hold, trans, a.blink, jit,
                          conf.get('roll'))

        d = OUT / f"{slug}{suffix}"
        if d.exists():
            shutil.rmtree(d)
        (d / "frames").mkdir(parents=True)

        n = render_frames(frames, theme, a.size, a.glow, a.ss,
                          d / "frames", d / "_grid.png", a.blur, a.jobs)
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
