#!/usr/bin/env python3
"""
An episode's cards, rendered — the animated ones as video, the rest as PNG.

    ./tools/cards.py ep0 --title "Introductions and the premise" --number 0
    ./tools/cards.py ep0 --chapter 2 "The evolution of software agencies"
    ./tools/cards.py ep0 --preview            # fast, for checking the words

The title card opens the episode over the sting and the end card closes it
under the outro. Both are show.py layouts with the mark animated through one
of eyeanim.py's chains — `intro` wakes the eye as the music starts, `signoff`
closes it at the end — so there is one copy of the layout and one model of
how the eye moves, and this file only joins them.

Chapter cards are static. They sit between segments for the length of the
button cue, and assemble.py asks for them as it needs them.

Output lands in build/episodes/<slug>/cards/:
  title.mp4, title.png      the opening card, held to --title-dur seconds
  end.mp4, end.png          the closing card, held to --end-dur seconds
  chapter-NN.png            one per chapter boundary

Rendering follows eyeanim.py's rules, learned the hard way there: one Chrome
launch per distinct frame, never a grid; supersample anything that moves;
average sub-frames so edges do not crawl. A card is 95% hold, so the distinct
frames are few and the cost is small.
"""

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from eplib import ROOT, die, ep_dir, load_json, need, run  # noqa: E402
from eyeanim import CHAINS, frame_key, timeline  # noqa: E402
from show import ASSETS, build  # noqa: E402

CARDS = {
    # name: (layout, chain, note)
    "title": ("titlecard", "intro", "wakes as the sting starts, holds alert"),
    "end":   ("endcard", "signoff", "warm, then closed, held under the outro"),
}


def render_ss(html, out, w, h, ss=2):
    """Opaque page at ss x resolution, downsampled. banner.render() is pinned
    to scale 1, which is right for a still and wrong for a frame that moves:
    on a shallow curve a one-pixel antialiasing ramp still stair-steps."""
    from PIL import Image
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html)
        src = f.name
    try:
        subprocess.run(
            ["google-chrome", "--headless=new", "--disable-gpu", "--hide-scrollbars",
             f"--force-device-scale-factor={ss}", f"--window-size={w},{h}",
             f"--screenshot={out}", f"file://{src}"],
            check=True, capture_output=True)
    finally:
        pathlib.Path(src).unlink(missing_ok=True)
    im = Image.open(out).convert("RGB")
    if im.size != (w, h):
        im = im.resize((w, h), Image.LANCZOS)
    return im


def average(ims):
    import numpy as np
    from PIL import Image
    acc = sum(np.asarray(im, dtype=np.float64) for im in ims) / len(ims)
    return Image.fromarray(np.clip(acc + 0.5, 0, 255).astype("uint8"), "RGB")


def animate(layout, chain, theme, dur, fps, blur, ss, jobs, fields, out_dir, slug):
    """Frames for a card: the chain's motion, then a hold to `dur`."""
    c = CHAINS[chain]
    frames = timeline(c["seq"], fps * blur, c.get("hold", 0.5), c.get("trans", 0.38),
                      blink=False, jitter=c.get("jitter", 0.0), roll=c.get("roll"))
    n_total = round(dur * fps) * blur
    if len(frames) < n_total:
        frames += [dict(frames[-1]) for _ in range(n_total - len(frames))]
    frames = frames[:n_total]

    w, h = ASSETS[layout][0], ASSETS[layout][1]
    uniq = {}
    for fr in frames:
        uniq.setdefault(frame_key(fr), fr)
    tmp = out_dir / f"_{slug}_tmp"
    tmp.mkdir(parents=True, exist_ok=True)

    def one(item):
        k, fr = item
        html, _, _ = build(layout, theme, fields.get("title", ""), fr,
                           **{x: y for x, y in fields.items() if x != "title"})
        return k, render_ss(html, tmp / f"u_{abs(hash(k)) % 10**9:09d}.png", w, h, ss)

    cache = {}
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        for k, im in ex.map(one, uniq.items()):
            cache[k] = im
    seq = tmp / "frames"
    seq.mkdir(exist_ok=True)
    n = 0
    for start in range(0, len(frames) - blur + 1, blur):
        group = [cache[frame_key(frames[start + j])] for j in range(blur)]
        (group[0] if blur == 1 else average(group)).save(seq / f"f_{n:04d}.png")
        n += 1
    mp4 = out_dir / f"{slug}.mp4"
    run(["ffmpeg", "-nostdin", "-y", "-framerate", fps, "-i", seq / "f_%04d.png",
         "-c:v", "libx264", "-preset", "slow", "-crf", "15", "-pix_fmt", "yuv420p",
         "-r", fps, mp4])
    shutil.copyfile(seq / f"f_{n-1:04d}.png", out_dir / f"{slug}.png")
    shutil.rmtree(tmp)
    return mp4, n, len(uniq)


def static(layout, theme, out, **fields):
    from banner import render
    html, w, h = build(layout, theme, fields.pop("title", ""), None, **fields)
    out.parent.mkdir(parents=True, exist_ok=True)
    render(html, out, w, h)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--title", default=None)
    ap.add_argument("--number", default=None, help="episode number")
    ap.add_argument("--hosts", default=None)
    ap.add_argument("--chapter", nargs=2, metavar=("N", "TITLE"), action="append",
                    help="render one chapter card (repeatable)")
    ap.add_argument("--only", choices=sorted(CARDS), help="one animated card")
    ap.add_argument("--title-dur", type=float, default=7.0,
                    help="seconds the opening card runs; the sting lands at 6.4")
    ap.add_argument("--end-dur", type=float, default=19.0,
                    help="seconds the closing card runs; the outro is 17.6")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--blur", type=int, default=2, help="sub-frames averaged per frame")
    ap.add_argument("--ss", type=int, default=2, choices=[1, 2, 3])
    ap.add_argument("--jobs", type=int, default=24, help="frames rendered at once (one Chrome each)")
    ap.add_argument("--preview", action="store_true", help="ss 1, blur 1, fast")
    ap.add_argument("--newsprint", action="store_true")
    a = ap.parse_args()
    need("ffmpeg", "google-chrome")

    d = ep_dir(a.slug)
    cfg = load_json(d / "episode.json") if (d / "episode.json").exists() else {}
    fields = {"title": a.title if a.title is not None else cfg.get("title", ""),
              "number": a.number if a.number is not None else cfg.get("number"),
              "hosts": a.hosts if a.hosts is not None else cfg.get("hosts")}
    fields = {k: v for k, v in fields.items() if v is not None}
    theme = "newsprint" if a.newsprint else "noir"
    out_dir = d / "cards"
    out_dir.mkdir(exist_ok=True)
    if a.preview:
        a.ss, a.blur = 1, 1

    if a.chapter:
        for n, title in a.chapter:
            out = static("chapter", theme, out_dir / f"chapter-{int(n):02d}.png",
                         title=title, number=n)
            print(f"{out.relative_to(ROOT)}")
        if not a.only:
            return 0

    for slug, (layout, chain, _note) in CARDS.items():
        if a.only and slug != a.only:
            continue
        dur = a.title_dur if slug == "title" else a.end_dur
        f = dict(fields) if slug == "title" else {}
        mp4, n, uniq = animate(layout, chain, theme, dur, a.fps, a.blur, a.ss,
                               a.jobs, f, out_dir, slug)
        print(f"{mp4.relative_to(ROOT)}  {n} frames  {n/a.fps:.2f}s  "
              f"{uniq} distinct renders  chain {chain}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
