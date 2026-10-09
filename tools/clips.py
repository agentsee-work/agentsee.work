#!/usr/bin/env python3
"""
Vertical clips for social, from the episode's own tracks and transcript.

    ./tools/clips.py ep0 --propose 8          # candidate passages, to choose from
    ./tools/clips.py ep0                      # render every clip in clips.json
    ./tools/clips.py ep0 --only 2 --fast

A clip is a 30-60 second passage that stands on its own. Choosing one is a
judgement; rendering it is not, and this does the rendering. `--propose`
offers candidates by a simple rule — sentence-bounded windows with dense
speech and few restarts — and writes them to clips.proposed.json with their
text, so that whoever chooses (a person, or Claude reading the transcript)
has something to read. Copy the keepers into clips.json and run again.

clips.json: [{"slug": "the-old-shape", "t0": 212.4, "t1": 258.9, "title": "..."}]
in source seconds. Edges snap to the nearest silence so no word is clipped.

The frame is 1080x1920, composed rather than cropped, as AGE-58 asked:
both hosts stacked in the 4:5 band of show.py's clip plate, each a 1080x675
cut of their 720p camera — no upscaling — with the speaking panel marked by
a bar in the signal colour, captions below from the word timing with the
current word lit, and the show's contact strip at the foot. Audio is both
tracks mixed at -14 LUFS, which is what the platforms normalise to.
"""

import argparse
import json
import math
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from banner import THEMES, render  # noqa: E402
from eplib import (RATE, ROOT, caption_cues, caption_words, cut_with_crossfades, dialogue_sum,  # noqa: E402
                   die, edl_finalise, ep_dir, hms, load_json, map_words, measure, need,
                   normalise, run, save_json, speech_intervals, wrap_two)
from assemble import frame_plan  # noqa: E402
from show import CLIP_ZONES, build  # noqa: E402

FONT_DIR = ROOT / "build" / "fonts"
FONT_SRC = ROOT / "public" / "fonts" / "newsreader-latin.woff2"
W, H = 1080, 1920


# ── proposals ─────────────────────────────────────────────────────────────────

def propose(words, segments, n, lo, hi):
    """Windows of consecutive sentences, lo..hi seconds, scored for density
    and for ending cleanly. Crude on purpose: it is a shortlist, not a pick."""
    sents = [s for s in segments if s["t1"] - s["t0"] > 0.3]
    cands = []
    for i in range(len(sents)):
        for j in range(i, len(sents)):
            t0, t1 = sents[i]["t0"], sents[j]["t1"]
            if t1 - t0 < lo:
                continue
            if t1 - t0 > hi:
                break
            ws = [w for w in words if t0 <= w["t0"] < t1]
            if not ws:
                continue
            text = " ".join(w["w"] for w in ws)
            density = len(ws) / (t1 - t0)
            restarts = len(re.findall(r"\b(\w+)[,]? \1\b", text.lower()))
            fillers = len(re.findall(r"\b(um|uh|erm|like|you know)\b", text.lower()))
            spk = {}
            for w in ws:
                spk[w["spk"]] = spk.get(w["spk"], 0) + 1
            lead = max(spk.values()) / len(ws)
            ends_clean = 1.0 if re.search(r"[.?!]$", sents[j]["text"]) else 0.3
            score = density * ends_clean * (0.7 + 0.3 * lead) - 0.15 * restarts - 0.1 * fillers
            cands.append({"t0": round(t0, 2), "t1": round(t1, 2), "score": round(score, 3),
                          "lead": max(spk, key=spk.get), "text": text})
    cands.sort(key=lambda c: -c["score"])
    picked = []
    for c in cands:
        if all(c["t1"] <= p["t0"] or c["t0"] >= p["t1"] for p in picked):
            picked.append(c)
        if len(picked) >= n:
            break
    picked.sort(key=lambda c: c["t0"])
    return picked


# ── rendering ─────────────────────────────────────────────────────────────────

def caption_font():
    """libass wants a static TTF; the site ships a variable woff2. Make one
    instance at a reading weight, once."""
    out = FONT_DIR / "Newsreader-Captions.ttf"
    if out.exists():
        return out
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    f = TTFont(FONT_SRC)
    f.flavor = None
    f = instancer.instantiateVariableFont(f, {"wght": 500, "opsz": 18})
    for rec in f["name"].names:
        if rec.nameID in (1, 4, 16):
            rec.string = "Newsreader Captions"
        if rec.nameID == 2:
            rec.string = "Regular"
    f.save(out)
    return out


def ass_colour(hex_rgb):
    r, g, b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    return f"&H00{b}{g}{r}".upper()


def ass_file(cues, t_start, theme, path, max_chars=26):
    """One event per spoken word, each showing its cue with that word lit.
    Times are clip-relative."""
    t = THEMES[theme]
    ink, lit = ass_colour(t["ink"]), ass_colour(t["signal"])
    cz = CLIP_ZONES["captions"]
    centre = (W // 2, (cz[0] + cz[1]) // 2)   # the block sits in the middle of its zone
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Newsreader Captions,62,{ink},{ink},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,70,70,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    def ts(x):
        x = max(0.0, x)
        h, r = divmod(x, 3600)
        m, s = divmod(r, 60)
        return f"{int(h)}:{int(m):02d}:{s:05.2f}"

    events = []
    for cue in cues:
        words = [w["w"] for w in cue]
        lines = wrap_two(" ".join(words), max_chars)
        # which line each word sits on, so the lit word is found after wrapping
        flat = [w for ln in lines for w in ln.split()]
        for i, w in enumerate(cue):
            start = w["t0"] - t_start
            end = (cue[i + 1]["t0"] if i + 1 < len(cue) else cue[-1]["t1"]) - t_start
            if end <= start:
                end = start + 0.05
            parts, k = [], 0
            for ln in lines:
                toks = []
                for tok in ln.split():
                    toks.append(f"{{\\c{lit}}}{tok}{{\\c{ink}}}" if k == i else tok)
                    k += 1
                parts.append(" ".join(toks))
            text = f"{{\\an5\\pos({centre[0]},{centre[1]})}}" + "\\N".join(parts)
            events.append(f"Dialogue: 0,{ts(start)},{ts(end)},Cap,,0,0,0,,{text}")
    path.write_text(head + "\n".join(events) + "\n")
    return path


def snap(t, words, before=True):
    """Move an edge off a word. If `t` falls inside a word, a start moves
    to just before it and an end to just after it. It does not move to the
    edge of the whole speech stretch — in a dense conversation that could
    be a minute away."""
    for w in words:
        if w["t0"] <= t <= w["t1"]:
            return max(0.0, w["t0"] - 0.15) if before else w["t1"] + 0.15
    return t


def sub_edl(edl, t0, t1):
    """The episode's edit, restricted to a clip's range: the kept stretches
    inside [t0, t1], with output times from 0. Cards are not part of a clip.
    Without an edit list the clip is the raw range."""
    items = []
    if edl:
        for it in edl["items"]:
            if it["type"] != "src" or it["t1"] <= t0 or it["t0"] >= t1:
                continue
            items.append({"type": "src", "t0": round(max(it["t0"], t0), 3),
                          "t1": round(min(it["t1"], t1), 3)})
    if not items:
        items = [{"type": "src", "t0": t0, "t1": t1}]
    dur = edl_finalise(items)
    return {"items": items, "duration": dur}


def render_clip(d, m, words, bus, clip, idx, args, theme, plate, fontdir, edl):
    out_dir = d / "out" / "clips"
    out_dir.mkdir(parents=True, exist_ok=True)
    t0, t1 = clip["t0"], clip["t1"]
    slug = clip.get("slug") or f"clip-{idx:02d}"
    out = out_dir / f"{idx:02d}-{slug}.mp4"
    band = CLIP_ZONES["band"]
    parts = sorted(m["participants"], key=lambda p: (p.get("layout") or {}).get("centre", 0))
    n = len(parts)
    ph = (band[1] - band[0]) // n
    pw = W
    sig = THEMES[theme]["signal"]
    local = sub_edl(edl, t0, t1)
    dur = local["duration"]
    plan = frame_plan(local["items"], m["fps"])

    # words that survive the edit, in clip time: captions and the speaking bar
    ws = map_words(local, [w for w in words if t0 <= w["t0"] < t1])
    cues = caption_cues(caption_words(ws), max_chars=26, max_lines=2, max_dur=5.0, gap=1.0, fill=0.7)
    ass = ass_file(cues, 0.0, theme, d / "mix" / f"clip-{idx:02d}.ass")

    # audio: the summed dialogue, cut with the same crossfades as the episode
    tmp_a = d / "mix" / f"clip-{idx:02d}-a.wav"
    import soundfile as sf
    cut = cut_with_crossfades(bus, local["items"], xf=0.02)
    # Bring the raw bus near the target first. loudnorm's linear pass gives
    # up and goes dynamic when the gain it needs would push the true peak
    # past the ceiling, and the raw mics sit twenty dB low.
    peak = float(abs(cut).max()) or 1.0
    cut = cut * (10 ** (-3.0 / 20) / peak)
    sf.write(tmp_a, cut.astype("float32"), RATE, subtype="FLOAT")
    tmp_n = d / "mix" / f"clip-{idx:02d}-n.wav"
    normalise(tmp_a, tmp_n, args.target, -1.0, limiter=True, compress=True)
    tmp_a.unlink(missing_ok=True)

    # video: each panel is its kept stretches concatenated, cut hard where the audio crossfades
    cmd = ["ffmpeg", "-nostdin", "-y", "-loop", "1", "-framerate", str(m["fps"]),
           "-t", f"{dur:.3f}", "-i", plate]
    filt = []
    for i, p in enumerate(parts):
        cmd += ["-i", d / p["video"]["file"]]
        w, h = p["video"]["width"], p["video"]["height"]
        cw = min(w, int(h * pw / ph))
        ch = min(h, int(w * ph / pw))
        pieces = []
        for k, (it, nf) in enumerate(zip(local["items"], plan)):
            sf_ = max(0, round((it["t0"] - p["offset"]) * m["fps"]))
            filt.append(f"[{i + 1}:v]trim=start_frame={sf_}:end_frame={sf_ + nf},"
                        f"setpts=PTS-STARTPTS[q{i}_{k}]")
            pieces.append(f"[q{i}_{k}]")
        src = f"[q{i}_0]" if len(pieces) == 1 else f"[j{i}]"
        if len(pieces) > 1:
            filt.append("".join(pieces) + f"concat=n={len(pieces)}:v=1:a=0[j{i}]")
        mine = speech_intervals([x for x in ws if x["spk"] == p["short"]], merge_gap=0.6, pad=0.1)
        on = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in mine) or "0"
        filt.append(f"{src}crop={cw}:{ch}:(iw-{cw})/2:(ih-{ch})/2,"
                    f"scale={pw}:{ph}:flags=lanczos,setsar=1,fps={m['fps']},"
                    f"drawbox=x=0:y=0:w=8:h=ih:color={sig}@1.0:t=fill:enable='gt({on},0)'[v{i}]")
    prev = "[0:v]"
    for i in range(n):
        filt.append(f"{prev}[v{i}]overlay=0:{band[0] + i * ph}:shortest=1[o{i}]")
        prev = f"[o{i}]"
    filt.append(f"{prev}ass={ass}:fontsdir={fontdir}[v]")
    cmd += ["-i", tmp_n]
    filt.append(f"[{n + 1}:a]aformat=channel_layouts=stereo[a]")
    cmd += ["-filter_complex", ";".join(filt), "-map", "[v]", "-map", "[a]",
            "-r", str(m["fps"]), "-t", f"{dur:.3f}",
            "-c:v", "libx264", "-preset", "ultrafast" if args.fast else "slow",
            "-crf", "23" if args.fast else "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", str(RATE), "-movflags", "+faststart", out]
    run(cmd)
    tmp_n.unlink(missing_ok=True)
    got = measure(out, args.target)
    run(["ffmpeg", "-nostdin", "-y", "-ss", f"{min(dur / 2, 3.0):.2f}", "-i", out,
         "-frames:v", "1", out.with_suffix(".png")])
    return out, got, len(cues), dur, len(local["items"])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--propose", type=int, metavar="N", help="shortlist N candidates")
    ap.add_argument("--min", type=float, default=28.0)
    ap.add_argument("--max", type=float, default=60.0)
    ap.add_argument("--only", type=int, help="render one clip by its index")
    ap.add_argument("--target", type=float, default=-14.0)
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--raw", action="store_true", help="ignore edl.json; the clip is the raw range")
    ap.add_argument("--jobs", type=int, default=4, help="clips rendered at once")
    ap.add_argument("--newsprint", action="store_true")
    a = ap.parse_args()
    need("ffmpeg", "ffprobe")

    d = ep_dir(a.slug)
    m = load_json(d / "manifest.json")
    t = load_json(d / "transcript" / "words.json")
    words, segments = t["words"], t["segments"]
    theme = "newsprint" if a.newsprint else "noir"

    if a.propose:
        picks = propose(words, segments, a.propose, a.min, a.max)
        save_json(d / "clips.proposed.json", picks)
        print(f"{(d / 'clips.proposed.json').relative_to(ROOT)}")
        for c in picks:
            print(f"\n  {hms(c['t0'], False)} - {hms(c['t1'], False)}  ({c['t1'] - c['t0']:.0f}s, "
                  f"mostly {c['lead']}, score {c['score']})\n  {c['text'][:400]}")
        return 0

    path = d / "clips.json"
    if not path.exists():
        die(f"{path.relative_to(ROOT)} not found — write one, or --propose first")
    clips = load_json(path)
    plate = d / "cards" / "clip-plate.png"
    if not plate.exists():
        html, w, h = build("clip", theme)
        plate.parent.mkdir(exist_ok=True)
        render(html, plate, w, h)
    caption_font()
    (d / "mix").mkdir(exist_ok=True)
    edl = load_json(d / "edl.json") if (d / "edl.json").exists() and not a.raw else None
    print("  " + ("following edl.json" if edl else "raw ranges, no edit applied"))
    bus = dialogue_sum(d, m)

    def one(i, c):
        c = dict(c)
        c["t0"] = round(snap(c["t0"], words, before=True), 3)
        c["t1"] = round(snap(c["t1"], words, before=False), 3)
        out, got, ncues, dur, nitems = render_clip(d, m, words, bus, c, i, a, theme, plate,
                                                   FONT_DIR, edl)
        lvl = f"{got['lufs']:.1f} LUFS" if got else "?"
        return (f"{out.relative_to(ROOT)}  {dur:.1f}s from {c['t1'] - c['t0']:.1f}s  "
                f"{nitems} stretches  {ncues} captions  {lvl}")

    # each clip is its own ffmpeg, and a 1080x1920 encode cannot use the
    # whole machine, so several run at once; every temp file carries the
    # clip's number, so they do not collide
    from concurrent.futures import ThreadPoolExecutor
    todo = [(i, c) for i, c in enumerate(clips, 1) if not a.only or i == a.only]
    with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        for line in ex.map(lambda ic: one(*ic), todo):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
