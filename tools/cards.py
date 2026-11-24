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

On a guest's episode the eye is the guest's (AGE-159; show.face reads the
claim from episode.json). The waking and the closing stay the show's: the
title card wakes through `intro` and lands on the guest's pose where it
would land on `alert`, or plays the guest's chain once awake; the end card
starts from the guest's pose, or plays their chain, and then `signoff`
closes it. A chapter card holds the pose, or the chain's last step. An
episode with no claim renders exactly as before.

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

from eplib import ROOT, ep_dir, load_json, need, run, save_json
from eyeanim import CHAINS, HOLD, TRANS, frame_key, timeline
from show import ASSETS, build, face, face_key

CARDS = {
    # name: (layout, chain, note)
    "title": ("titlecard", "intro", "wakes as the sting starts, holds alert"),
    "end": ("endcard", "signoff", "warm, then closed, held under the outro"),
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
            [
                "google-chrome",
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                f"--force-device-scale-factor={ss}",
                f"--window-size={w},{h}",
                f"--screenshot={out}",
                f"file://{src}",
            ],
            check=True,
            capture_output=True,
        )
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


def chain_frames(name, rate):
    """A named chain's frames at its own pace."""
    c = CHAINS[name]
    return timeline(
        c["seq"],
        rate,
        c.get("hold", HOLD),
        c.get("trans", TRANS),
        blink=False,
        jitter=c.get("jitter", 0.0),
        roll=c.get("roll"),
    )


def card_frames(card, chain, rate, guest=None, room=None):
    """Every sub-frame of an animated card's motion, before the hold.

    Without a guest's face it is the card's chain as CARDS names it. With one
    (show.face), the show's chain wraps the guest's:

      title   `intro` up to its last step, which becomes the guest's pose; or,
              for a guest's chain, the wake to attentive and then the chain,
              eased into from attentive (a chain that itself starts asleep
              is the waking). He ends where the chain ends, and holds there
              under the title.
      end     the guest's pose held, or their chain played, then the rest of
              `signoff` from there: warm, then closed.

    `room` is how many sub-frames the card runs for. A chain that would not
    fit with a second to spare gives way to its last pose, so the card never
    cuts a movement off half way."""
    show = CHAINS[chain]
    pace = (show.get("hold", HOLD), show.get("trans", TRANS))
    if not guest:
        return chain_frames(chain, rate)

    def steps(seq):
        return timeline(seq, rate, *pace, blink=False)

    hold_n = max(1, round(pace[0] * rate))
    if card == "title":
        wake = show["seq"][:-1]  # asleep, attentive: the waking is the show's
        if guest["chain"]:
            played = chain_frames(guest["chain"], rate)
            # A chain that opens where `intro` does (startle, from asleep) is a waking of its own.
            opens_asleep = CHAINS[guest["chain"]]["seq"][0] == show["seq"][0]
            lead = [] if opens_asleep else steps([*wake, played[0]])[:-hold_n]  # the chain brings its own first hold
            if room is None or len(lead) + len(played) + rate <= room:
                return lead + played
        return steps([*wake, guest["pose"]])
    tail = show["seq"][1:]  # amused, closed: the closing is the show's
    if guest["chain"]:
        played = chain_frames(guest["chain"], rate)
        close = steps([played[-1], *tail])[hold_n:]
        if room is None or len(played) + len(close) + rate <= room:
            return played + close
    return steps([guest["pose"], *tail])


def animate(layout, chain, theme, dur, fps, blur, ss, jobs, fields, out_dir, slug, guest=None):
    """Frames for a card: the chain's motion, then a hold to `dur`. `guest` is
    the episode's face (show.face), when a guest's Seymour is its eye."""
    frames = card_frames(slug, chain, fps * blur, guest, round(dur * fps) * blur)
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
        html, _, _ = build(
            layout, theme, fields.get("title", ""), fr, **{x: y for x, y in fields.items() if x != "title"}
        )
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
    run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-framerate",
            fps,
            "-i",
            seq / "f_%04d.png",
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "15",
            "-pix_fmt",
            "yuv420p",
            "-r",
            fps,
            mp4,
        ]
    )
    shutil.copyfile(seq / f"f_{n - 1:04d}.png", out_dir / f"{slug}.png")
    shutil.rmtree(tmp)
    return mp4, n, len(uniq)


def static(layout, theme, out, expr=None, **fields):
    """A still card. `expr` is the eye's expression when it is not the layout's own: the guest's pose."""
    from banner import render

    html, w, h = build(layout, theme, fields.pop("title", ""), expr, **fields)
    out.parent.mkdir(parents=True, exist_ok=True)
    render(html, out, w, h)
    return out


def same_face(d, guest):
    """The stills under cards/ are kept from render to render (assemble.py and
    clips.py make one only when it is missing), so they would keep the eye
    they were first drawn with. cards/face.json says whose eye that was; when
    the episode's is another, the chapter cards and the clip plate go, and
    the tools that want them draw them again. Returns what was removed."""
    cards = d / "cards"
    rec = cards / "face.json"
    was = load_json(rec).get("face") if rec.exists() else None
    now = face_key(guest)
    gone = []
    if was != now:
        for p in [*sorted(cards.glob("chapter-*.png")), cards / "clip-plate.png"]:
            if p.exists():
                p.unlink()
                gone.append(p.name)
    if was != now or not rec.exists():
        cards.mkdir(exist_ok=True)
        save_json(rec, {"face": now})
    return gone


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--title", default=None)
    ap.add_argument("--number", default=None, help="episode number")
    ap.add_argument("--hosts", default=None)
    ap.add_argument(
        "--chapter", nargs=2, metavar=("N", "TITLE"), action="append", help="render one chapter card (repeatable)"
    )
    ap.add_argument("--only", choices=sorted(CARDS), help="one animated card")
    ap.add_argument(
        "--title-dur", type=float, default=7.0, help="seconds the opening card runs; the sting lands at 6.4"
    )
    ap.add_argument("--end-dur", type=float, default=19.0, help="seconds the closing card runs; the outro is 17.6")
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
    fields = {
        "title": a.title if a.title is not None else cfg.get("title", ""),
        "number": a.number if a.number is not None else cfg.get("number"),
        "hosts": a.hosts if a.hosts is not None else cfg.get("hosts"),
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    theme = "newsprint" if a.newsprint else "noir"
    out_dir = d / "cards"
    out_dir.mkdir(exist_ok=True)
    if a.preview:
        a.ss, a.blur = 1, 1
    guest = face(cfg, lambda msg: print(f"  warning: {msg}", file=sys.stderr))
    gone = same_face(d, guest)
    if guest:
        whose = (guest.get("owner") or {}).get("name") or "a guest"
        print(f"  the eye is {whose}'s: the {guest['kind']} {guest['name']}")
    if gone:
        print(f"  drawn with another eye, removed to be drawn again: {', '.join(gone)}")

    if a.chapter:
        for n, title in a.chapter:
            out = static(
                "chapter",
                theme,
                out_dir / f"chapter-{int(n):02d}.png",
                expr=guest["pose"] if guest else None,
                title=title,
                number=n,
            )
            print(f"{out.relative_to(ROOT)}")
        if not a.only:
            return 0

    for slug, (layout, chain, _note) in CARDS.items():
        if a.only and slug != a.only:
            continue
        dur = a.title_dur if slug == "title" else a.end_dur
        f = dict(fields) if slug == "title" else {}
        mp4, n, uniq = animate(layout, chain, theme, dur, a.fps, a.blur, a.ss, a.jobs, f, out_dir, slug, guest)
        print(
            f"{mp4.relative_to(ROOT)}  {n} frames  {n / a.fps:.2f}s  {uniq} distinct renders  chain {chain}"
            + (f", around the {guest['kind']} {guest['name']}" if guest else "")
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
