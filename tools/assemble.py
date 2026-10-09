#!/usr/bin/env python3
"""
Render the episode from the edit list, the cues and the cards.

    ./tools/assemble.py ep0
    ./tools/assemble.py ep0 --audio-only          # the feed, no video
    ./tools/assemble.py ep0 --fast                # quick encodes, for checking the cut
    ./tools/assemble.py ep0 --dialogue-in 6.0 --bed-out 24 --no-bed

Everything before this decided; this one renders. It reads
build/episodes/<slug>/{manifest,edl}.json, transcript/words.json, cards/ and
the cues in build/audio/delivery, and writes build/episodes/<slug>/out/:

  episode.wav / episode.mp3   the mix, -16 LUFS -1 dBTP, mp3 tagged with cover and chapters
  episode.mp4                 1080p24, title card, two-up, chapter cards, end card
  captions.srt                for YouTube, in the output's time
  chapters.txt                YouTube's description format
  transcript.txt              speaker turns, in the output's time
  thumbnail.png
  contact.png                 eight frames of the video, for a glance
  qc.json                     what was measured, not what was predicted

The shape of the opening is fixed here and worth stating once. The sting
plays from 0 and lands on its tonic at 6.4s with three beats of silence
after it — that gap was written so a title can resolve into it. Dialogue
enters at --dialogue-in (6.0s), just as the sting lands, with the bed loop
under it, and the bed fades out by --bed-out. The title card crossfades
into the two-up over the first word. At the end the outro starts half a
second after the last word, the end card fades in with it, and the episode
ends when the outro does.

Levels. Each speaker's track is gated by their own word timing (not a
threshold — a threshold gate breathes), matched to the other speaker,
summed, and normalised to -16 LUFS. Cues arrive already levelled by
cues.py: foreground at -16, the bed at -30. The whole mix is then measured,
limited and normalised once more, and the number reported is a measurement
of the written file. See cues.py for why not the forecast.
"""

import argparse
import datetime as dt
import math
import pathlib
import shutil
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from eplib import (CUES, RATE, ROOT, caption_cues, cut_with_crossfades, die,  # noqa: E402
                   ep_dir, hms, level_to, load_json, map_words, measure, need, normalise,
                   run, save_json, speech_intervals, write_srt)

CUE = {"sting": "01-sting.wav", "bed": "bed-loop.wav", "button": "04-button.wav",
       "outro": "02-outro.wav"}
XF_IN, XF_END = 0.6, 1.0        # crossfades: card -> two-up, two-up -> end card
OUTRO_GAP = 0.5                 # silence between the last word and the outro


# ── audio helpers ─────────────────────────────────────────────────────────────

def load_mono(path):
    x, r = sf.read(str(path), dtype="float64", always_2d=True)
    if r != RATE:
        die(f"{path} is {r} Hz, not {RATE}; ingest should have resampled it")
    return x.mean(axis=1)


def load_stereo(path):
    x, r = sf.read(str(path), dtype="float64", always_2d=True)
    if r != RATE:
        die(f"{path} is {r} Hz, not {RATE}")
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    return x[:, :2]


def highpass(x, hz=80.0):
    from scipy.signal import butter, sosfiltfilt
    return sosfiltfilt(butter(2, hz, "hp", fs=RATE, output="sos"), x)


def sound_intervals(track, ivs, min_len=0.25, below=10.0):
    """Speech-level sound on a track that the transcript has no word for: a
    laugh, an interjection Whisper dropped. The gate has to open for it —
    gating by the words alone is what made a reply vanish."""
    hop = RATE // 100
    n = len(track) // hop * hop
    env = 20 * np.log10(np.sqrt((track[:n].reshape(-1, hop) ** 2).mean(1)) + 1e-9)
    inw = np.zeros(len(env), dtype=bool)
    for a, b in ivs:
        inw[max(0, int(a * 100)):int(b * 100) + 1] = True
    if not inw.any():
        return []
    peaks = [env[max(0, int(a * 100)):int(b * 100) + 1].max() for a, b in ivs if int(b * 100) + 1 > int(a * 100)]
    level = float(np.percentile(peaks, 30)) - below
    loud = np.append((env > level) & ~inw, False)
    out, start = [], None
    for i, v in enumerate(loud):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if (i - start) / 100 >= min_len:
                out.append([start / 100, i / 100])
            start = None
    return out


def merge_intervals(ivs, gap):
    out = []
    for a, b in sorted(ivs):
        if out and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def gate_envelope(n, intervals, floor_db, pre=0.2, post=0.35, ramp=0.15):
    """1 inside speech (widened by pre/post), floor outside, smooth edges.
    Built from the words, so it opens for speech and nothing else."""
    from scipy.ndimage import uniform_filter1d
    mask = np.zeros(n, dtype=np.float64)
    for a, b in intervals:
        i0, i1 = max(0, int((a - pre) * RATE)), min(n, int((b + post) * RATE))
        if i1 > i0:
            mask[i0:i1] = 1.0
    k = max(1, int(ramp * RATE))
    smooth = uniform_filter1d(uniform_filter1d(mask, k), k)
    floor = 10 ** (floor_db / 20)
    return floor + (1 - floor) * smooth


def fade(x, seconds, at_start=True):
    n = min(len(x), int(seconds * RATE))
    if n <= 0:
        return x
    env = 0.5 - 0.5 * np.cos(np.linspace(0, math.pi, n))
    if at_start:
        x[:n] *= env[:, None] if x.ndim == 2 else env
    else:
        x[-n:] *= env[::-1][:, None] if x.ndim == 2 else env[::-1]
    return x


def add(mix, clip, at):
    i0 = int(round(at * RATE))
    i1 = min(len(mix), i0 + len(clip))
    if i1 > i0:
        mix[i0:i1] += clip[:i1 - i0]


def measured(path, label, qc):
    m = measure(path)
    qc[label] = m
    return m


# ── stages ────────────────────────────────────────────────────────────────────

def dialogue_bus(d, m, words, args, qc):
    """Per-speaker gate, match, sum, and a plain gain to the target. Returns
    the path of the full-length dialogue as a float file, peaks and all:
    compression and limiting wait until after the cut (see eplib.COMPRESSOR).
    It does not depend on the edit, so a variant reuses the last one
    unless --force."""
    mix_dir = d / "mix"
    mix_dir.mkdir(exist_ok=True)
    norm = mix_dir / "dialogue.wav"
    if norm.exists() and (mix_dir / "dialogue.json").exists() and not args.force:
        cached = load_json(mix_dir / "dialogue.json")
        qc.update(cached)
        print(f"  dialogue bus reused ({cached['dialogue_bus']['lufs']:.1f} LUFS)")
        return norm
    n = int(math.ceil(m["duration"] * RATE))
    bus = np.zeros(n)
    for p in m["participants"]:
        x = load_mono(d / p["audio"]["file"])
        x = highpass(x, args.highpass)
        off = int(round(p["offset"] * RATE))
        track = np.zeros(n)
        seg = x[:max(0, n - off)]
        track[off:off + len(seg)] = seg
        mine = [w for w in words if w["spk"] == p["short"]]
        ivs = speech_intervals(mine, merge_gap=0.5)
        extra = sound_intervals(track, ivs)
        ivs = merge_intervals(ivs + extra, 0.5)
        track *= gate_envelope(n, ivs, args.gate_db)
        tmp = mix_dir / f"_{p['short']}.wav"
        sf.write(tmp, track.astype(np.float32), RATE, subtype="FLOAT")
        meas = measure(tmp, args.speaker_lufs)
        if meas is None:
            die(f"{p['short']}: nothing measurable on the gated track")
        gain = 10 ** ((args.speaker_lufs - meas["lufs"]) / 20)
        qc[f"speaker_{p['short']}"] = {"gated_lufs": meas["lufs"], "gain_db": round(20 * math.log10(gain), 2),
                                       "speech_seconds": round(sum(b - a for a, b in ivs), 1)}
        print(f"  {p['short']:<8} gated {meas['lufs']:6.1f} LUFS -> {args.speaker_lufs} "
              f"({20*math.log10(gain):+.1f} dB), speaks {qc[f'speaker_{p['short']}']['speech_seconds']:.0f}s, "
              f"{len(extra)} sounds with no word kept open")
        bus += track * gain
        tmp.unlink()
    raw = mix_dir / "dialogue-raw.wav"
    sf.write(raw, bus.astype(np.float32), RATE, subtype="FLOAT")
    meas = level_to(raw, norm, args.target)
    qc["dialogue_bus"] = meas
    save_json(mix_dir / "dialogue.json",
              {k: v for k, v in qc.items() if k == "dialogue_bus" or k.startswith("speaker_")})
    print(f"  dialogue bus {meas['lufs']:.1f} LUFS, peaks to {meas['tp']:.1f} dBTP, uncontrolled until the cut")
    return norm


def cut_audio(norm_path, edl):
    """The dialogue in output order, crossfaded across every join, silent
    under the cards. See eplib.cut_with_crossfades for why 20 ms."""
    return cut_with_crossfades(load_mono(norm_path), edl["items"], xf=0.02)


def music_and_mix(d, edl, dialogue, args, qc):
    """Lay the cues around the cut dialogue. Returns (wav path, timings)."""
    cues = {k: load_stereo(CUES / v) for k, v in CUE.items()}
    din = args.dialogue_in
    dlen = len(dialogue) / RATE
    outro_start = din + dlen + OUTRO_GAP
    total = outro_start + len(cues["outro"]) / RATE
    mix = np.zeros((int(math.ceil(total * RATE)), 2))

    # dialogue, mono into both channels at -3 dB so it measures as the mono did
    add(mix, np.repeat(dialogue[:, None] / math.sqrt(2), 2, axis=1), din)

    add(mix, cues["sting"], 0.0)
    if not args.no_bed:
        loop = cues["bed"]
        reps = int(math.ceil((args.bed_out - args.bed_in) / (len(loop) / RATE))) + 1
        bed = np.tile(loop, (reps, 1))[:int((args.bed_out - args.bed_in) * RATE)].copy()
        bed *= 10 ** (args.bed_gain / 20)
        fade(bed, 0.5, True)
        fade(bed, args.bed_fade, False)
        add(mix, bed, args.bed_in)
    buttons = []
    for it in edl["items"]:
        if it["type"] == "card":
            b = cues["button"][:int(it["dur"] * RATE)].copy()
            fade(b, 0.25, False)
            add(mix, b, din + it["out"])
            buttons.append(round(din + it["out"], 3))
    add(mix, cues["outro"], outro_start)

    mix_dir = d / "mix"
    full = mix_dir / (f"full-raw-{args.tag}.wav" if getattr(args, "tag", None) else "full-raw.wav")
    sf.write(full, mix.astype(np.float32), RATE, subtype="FLOAT")
    out = d / "out"
    out.mkdir(exist_ok=True)
    wav = out / (f"variant-{args.tag}.wav" if getattr(args, "tag", None) else "episode.wav")
    meas = normalise(full, wav, args.target, args.tp, limiter=True)
    qc["episode_wav"] = meas
    timings = {"dialogue_in": din, "dialogue_len": round(dlen, 3),
               "outro_start": round(outro_start, 3), "total": round(total, 3),
               "buttons": buttons, "bed": None if args.no_bed else
               {"in": args.bed_in, "out": args.bed_out, "fade": args.bed_fade}}
    print(f"  mix {meas['lufs']:.1f} LUFS, {meas['tp']:.1f} dBTP, {total:.1f}s "
          f"(dialogue in at {din}s, outro at {outro_start:.1f}s)")
    return wav, timings


def ffmetadata(chapters, total, path, title):
    lines = [";FFMETADATA1", f"title={title}", "artist=AgentSee", ""]
    for i, c in enumerate(chapters):
        end = chapters[i + 1]["t"] if i + 1 < len(chapters) else total
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(c['t'] * 1000)}",
                  f"END={int(end * 1000)}", f"title={c['title']}", ""]
    path.write_text("\n".join(lines))
    return path


def cover_jpeg(path):
    from PIL import Image
    src = ROOT / "public/assets/brand/show-cover.png"
    Image.open(src).convert("RGB").resize((1400, 1400), Image.LANCZOS).save(path, quality=90)
    return path


def mp3(d, wav, meta, cfg, chapters_path, qc):
    out = d / "out" / "episode.mp3"
    cover = cover_jpeg(d / "mix" / "cover.jpg")
    year = (cfg.get("date") or dt.date.today().isoformat())[:4]
    run(["ffmpeg", "-nostdin", "-y", "-i", wav, "-i", cover, "-i", chapters_path,
         "-map", "0:a", "-map", "1:v", "-map_metadata", "2", "-map_chapters", "2",
         "-c:a", "libmp3lame", "-b:a", "192k", "-id3v2_version", "3",
         "-metadata", f"title={meta['title']}", "-metadata", "artist=AgentSee",
         "-metadata", "album=AgentSee", "-metadata", f"track={cfg.get('number', '')}",
         "-metadata", f"date={year}", "-metadata", "genre=Podcast",
         "-c:v", "copy", "-metadata:s:v", "title=Album cover",
         "-metadata:s:v", "comment=Cover (front)", out])
    qc["episode_mp3"] = measure(out)
    r = run(["ffprobe", "-v", "error", "-show_chapters", "-of", "csv=p=0", out])
    qc["mp3_chapters"] = len([ln for ln in r.stdout.splitlines() if ln.strip()])
    return out


def chapter_cards(d, edl, theme):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from cards import static
    made = []
    for it in edl["items"]:
        if it["type"] == "card":
            p = d / "cards" / f"{it['name']}.png"
            if not p.exists():
                static("chapter", theme, p, title=it["title"], number=it["number"])
                made.append(p.name)
    return made


def two_up(d, m, args):
    """The full-length picture: every participant side by side, at the
    arrangement the XML described (negative centre = left), no audio."""
    out = d / "mix" / "twoup.mp4"
    if out.exists() and not args.force:
        return out
    parts = sorted(m["participants"],
                   key=lambda p: (p.get("layout") or {}).get("centre", 0))
    n = len(parts)
    pw, ph = 1920 // n, 1080
    cmd = ["ffmpeg", "-nostdin", "-y"]
    filt = []
    for i, p in enumerate(parts):
        cmd += ["-i", d / p["video"]["file"]]
        w, h = p["video"]["width"], p["video"]["height"]
        # centre crop to the panel's aspect, then scale to the panel
        cw = min(w, int(h * pw / ph))
        ch = min(h, int(w * ph / pw))
        filt.append(f"[{i}:v]tpad=start_duration={p['offset']},"
                    f"crop={cw}:{ch}:(iw-{cw})/2:(ih-{ch})/2,"
                    f"scale={pw}:{ph}:flags=lanczos,setsar=1,fps={m['fps']}[p{i}]")
    filt.append("".join(f"[p{i}]" for i in range(n)) +
                (f"hstack=inputs={n}[v]" if n > 1 else "null[v]"))
    cmd += ["-filter_complex", ";".join(filt), "-map", "[v]", "-t", m["duration"],
            "-c:v", "libx264", "-preset", "ultrafast" if args.fast else "fast",
            "-crf", "20" if args.fast else "14", "-pix_fmt", "yuv420p", out]
    run(cmd)
    return out


def frame_plan(items, fps):
    """How many frames each item gets, from the cumulative output time, so
    the picture never drifts from the sound. Trimming each stretch by
    seconds rounds every one up to a frame, and seventy of those put the
    video a quarter of a second behind the audio by the end."""
    plan, t = [], 0.0
    for it in items:
        length = (it["t1"] - it["t0"]) if it["type"] == "src" else it["dur"]
        f0, f1 = round(t * fps), round((t + length) * fps)
        plan.append(max(1, f1 - f0))
        t += length
    return plan


def episode_video(d, edl, twoup, wav, timings, chapters_path, args, m):
    """The whole picture in one encode: the kept stretches of the two-up
    trimmed frame-exact and joined, chapter cards held for their length,
    the title and end cards faded in and out, the mixed audio and the
    chapter marks muxed in. It used to be two encodes — the joined
    stretches to an intermediate file, then that file between the cards —
    and the intermediate took a minute and a half on every render to be
    thrown away."""
    cards = d / "cards"
    title, end = cards / "title.mp4", cards / "end.mp4"
    for p in (title, end):
        if not p.exists():
            die(f"{p.relative_to(ROOT)} missing — run cards.py first")
    out = d / "out" / "episode.mp4"
    fps = m["fps"]
    din, total, outro = timings["dialogue_in"], timings["total"], timings["outro_start"]
    pad = OUTRO_GAP + XF_END
    cmd = ["ffmpeg", "-nostdin", "-y", "-i", twoup, "-i", title, "-i", end, "-i", wav,
           "-i", chapters_path]
    filt, labels = [], []
    k = 5
    plan = frame_plan(edl["items"], fps)
    for i, (it, nf) in enumerate(zip(edl["items"], plan)):
        if it["type"] == "src":
            sf_ = round(it["t0"] * fps)
            filt.append(f"[0:v]trim=start_frame={sf_}:end_frame={sf_ + nf},"
                        f"setpts=PTS-STARTPTS[s{i}]")
        else:
            cmd += ["-loop", "1", "-framerate", str(fps), "-t", str(it["dur"]),
                    "-i", cards / f"{it['name']}.png"]
            filt.append(f"[{k}:v]format=yuv420p,setsar=1,trim=start_frame=0:end_frame={nf},"
                        f"setpts=PTS-STARTPTS[s{i}]")
            k += 1
        labels.append(f"[s{i}]")
    # one timebase and format for the three streams xfade sees
    filt.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0,format=yuv420p,fps={fps},"
                f"tpad=stop_mode=clone:stop_duration={pad}[cut]")
    filt.append(f"[1:v]format=yuv420p,fps={fps}[title]")
    filt.append(f"[2:v]format=yuv420p,fps={fps}[end]")
    filt.append(f"[title][cut]xfade=transition=fade:duration={XF_IN}:offset={din}[a]")
    filt.append(f"[a][end]xfade=transition=fade:duration={XF_END}:offset={outro}[v]")
    cmd += ["-filter_complex", ";".join(filt), "-map", "[v]", "-map", "3:a",
            "-map_metadata", "4", "-map_chapters", "4",
            "-c:v", "libx264", "-preset", "ultrafast" if args.fast else "slow",
            "-crf", "23" if args.fast else "18", "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", total, out]
    run(cmd)
    return out


def src_to_out_safe(edl, t, shift):
    """The output second just before a cut at source second t."""
    best = None
    for it in edl["items"]:
        if it["type"] == "src" and it["t1"] <= t + 1e-3:
            best = it["out"] + (it["t1"] - it["t0"])
    return None if best is None else best + shift


def contact(video, out):
    info = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=p=0", video]).stdout.strip()
    dur = float(info)
    run(["ffmpeg", "-nostdin", "-y", "-i", video,
         "-vf", f"fps=8/{dur:.3f},scale=480:-1,tile=4x2", "-frames:v", "1", out])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--audio-only", action="store_true")
    ap.add_argument("--video-only", action="store_true", help="reuse out/episode.wav")
    ap.add_argument("--text-only", action="store_true",
                    help="only captions, transcript, chapters and thumbnail, from the last render")
    ap.add_argument("--edl", help="an edit list other than edl.json")
    ap.add_argument("--tag", help="a variant: audio only, to out/variants/<tag>.mp3, nothing else touched")
    ap.add_argument("--fast", action="store_true", help="fast, lower-quality encodes")
    ap.add_argument("--force", action="store_true", help="re-render the two-up")
    ap.add_argument("--dialogue-in", type=float, default=6.0)
    ap.add_argument("--bed-in", type=float, default=6.4)
    ap.add_argument("--bed-out", type=float, default=24.0)
    ap.add_argument("--bed-fade", type=float, default=6.0)
    ap.add_argument("--bed-gain", type=float, default=0.0, help="dB on top of the bed's -30 LUFS")
    ap.add_argument("--no-bed", action="store_true")
    ap.add_argument("--target", type=float, default=-16.0)
    ap.add_argument("--tp", type=float, default=-1.0)
    ap.add_argument("--speaker-lufs", type=float, default=-18.0)
    ap.add_argument("--gate-db", type=float, default=-18.0, help="level of a track while the other speaks")
    ap.add_argument("--highpass", type=float, default=80.0)
    ap.add_argument("--newsprint", action="store_true")
    a = ap.parse_args()
    need("ffmpeg", "ffprobe")

    d = ep_dir(a.slug)
    m = load_json(d / "manifest.json")
    edl = load_json(a.edl) if a.edl else load_json(d / "edl.json")
    if a.tag:
        a.audio_only = True
    words = load_json(d / "transcript" / "words.json")
    cfg = load_json(d / "episode.json") if (d / "episode.json").exists() else {}
    title = cfg.get("title") or a.slug
    theme = "newsprint" if a.newsprint else "noir"
    for k, v in CUE.items():
        if not (CUES / v).exists():
            die(f"cue {v} not in {CUES.relative_to(ROOT)} — run cues.py / loop.py first")
    out = d / "out"
    out.mkdir(exist_ok=True)
    qc = {"slug": a.slug, "rendered": dt.datetime.now().isoformat(timespec="seconds")}

    # audio
    if (a.video_only or a.text_only) and (out / "episode.wav").exists():
        timings = load_json(out / "qc.json")["timings"]
        wav = out / "episode.wav"
    else:
        print("dialogue")
        norm = dialogue_bus(d, m, words["words"], a, qc)
        dialogue = cut_audio(norm, edl)
        # levelled and limited now, on the cut, so the compressor's state
        # runs on across every join
        import soundfile as sf
        suffix = f"-{a.tag}" if a.tag else ""
        cut_raw = d / "mix" / f"dialogue-cut-raw{suffix}.wav"
        cut_norm = d / "mix" / f"dialogue-cut{suffix}.wav"
        sf.write(cut_raw, dialogue.astype(np.float32), RATE, subtype="FLOAT")
        qc["dialogue"] = normalise(cut_raw, cut_norm, a.target, a.tp, limiter=True, compress=True)
        cut_raw.unlink(missing_ok=True)
        dialogue = load_mono(cut_norm)
        print(f"  cut dialogue {qc['dialogue']['lufs']:.1f} LUFS, {qc['dialogue']['tp']:.1f} dBTP, "
              f"range {qc['dialogue']['lra']:.1f} LU")
        print("mix")
        wav, timings = music_and_mix(d, edl, dialogue, a, qc)
    din = timings["dialogue_in"]

    if a.tag:
        vdir = out / "variants"
        vdir.mkdir(exist_ok=True)
        mp3_path = vdir / f"{a.tag}.mp3"
        chapters_v = [{"title": edl["chapters"][0]["title"] if edl["chapters"] else title, "t": 0.0}]
        chapters_v += [{"title": c["title"], "t": round(din + c["out"], 3)} for c in edl["chapters"][1:]]
        chapters_v.append({"title": "Outro", "t": timings["outro_start"]})
        ffm = ffmetadata(chapters_v, timings["total"], d / "mix" / f"chapters-{a.tag}.ffmeta", f"{title} [{a.tag}]")
        cover = cover_jpeg(d / "mix" / "cover.jpg")
        run(["ffmpeg", "-nostdin", "-y", "-i", wav, "-i", cover, "-i", ffm, "-map", "0:a", "-map", "1:v",
             "-map_metadata", "2", "-map_chapters", "2", "-c:a", "libmp3lame", "-b:a", "192k",
             "-id3v2_version", "3", "-metadata", f"title={title} [{a.tag}]", "-metadata", "artist=AgentSee",
             "-c:v", "copy", "-metadata:s:v", "title=Album cover", mp3_path])
        got = measure(mp3_path)
        words_kept = len(map_words(edl, words["words"], shift=din))
        report = {"tag": a.tag, "params": edl["params"], "stats": edl["stats"],
                  "duration": round(timings["total"], 2), "dialogue_seconds": round(edl["duration"], 2),
                  "removed_seconds": edl["removed_seconds"], "cuts": len(edl["removed"]),
                  "words_kept": words_kept, "words_total": len(words["words"]),
                  "lufs": got["lufs"] if got else None}
        save_json(vdir / f"{a.tag}.json", report)
        src_edl = pathlib.Path(a.edl) if a.edl else d / "edl.json"
        if src_edl.resolve() != (vdir / f"{a.tag}.edl.json").resolve():
            shutil.copyfile(src_edl, vdir / f"{a.tag}.edl.json")
        lines = [f"{a.tag}: {report['duration']}s, {report['cuts']} cuts, {report['removed_seconds']}s removed, "
                 f"params {edl['params']}", ""]
        for r in edl["removed"]:
            ws_ = " ".join(w["w"] for w in words["words"] if w["t0"] < r["t1"] and w["t1"] > r["t0"])
            o = src_to_out_safe(edl, r["t0"], din)
            lines.append(f"  at {hms(o, False) if o is not None else '--:--:--'}  cut {r['t1'] - r['t0']:.2f}s  {r['why']:8}  {ws_}")
        (vdir / f"{a.tag}.txt").write_text("\n".join(lines) + "\n")
        wav.unlink(missing_ok=True)
        (d / "mix" / f"full-raw-{a.tag}.wav").unlink(missing_ok=True)
        print(f"{mp3_path.relative_to(ROOT)}  {report['duration']}s  {report['cuts']} cuts  "
              f"{report['removed_seconds']}s removed  {got['lufs'] if got else '?'} LUFS")
        return 0

    # chapters, in output time: the title chapter at 0, cards at their time, outro last
    chapters = [{"title": edl["chapters"][0]["title"] if edl["chapters"] else title, "t": 0.0}]
    chapters += [{"title": c["title"], "t": round(din + c["out"], 3)} for c in edl["chapters"][1:]]
    chapters.append({"title": "Outro", "t": timings["outro_start"]})
    (out / "chapters.txt").write_text("\n".join(
        f"{hms(c['t'], False)[3:] if c['t'] < 3600 else hms(c['t'], False)} {c['title']}"
        for c in chapters) + "\n")
    chapters_path = ffmetadata(chapters, timings["total"], d / "mix" / "chapters.ffmeta", title)

    if a.text_only:
        qc = load_json(out / "qc.json")
    if not (a.video_only or a.text_only):
        mp3(d, wav, {"title": title}, cfg, chapters_path, qc)
        print(f"  mp3 {qc['episode_mp3']['lufs']:.1f} LUFS, {qc['mp3_chapters']} chapters")

    # text deliverables, in output time
    mapped = map_words(edl, words["words"], shift=din)
    write_srt(caption_cues(mapped), out / "captions.srt")
    turns, cur = [], None
    for w in mapped:
        if cur is None or w["spk"] != cur["spk"] or w["t0"] - cur["t1"] > 2.0:
            cur = {"spk": w["spk"], "t0": w["t0"], "t1": w["t1"], "words": [w["w"]]}
            turns.append(cur)
        else:
            cur["words"].append(w["w"])
            cur["t1"] = w["t1"]
    names = words.get("speakers", {})
    (out / "transcript.txt").write_text("\n".join(
        f"[{hms(t['t0'], False)}] {names.get(t['spk'], t['spk'])}: {' '.join(t['words'])}\n"
        for t in turns))
    qc["captions"] = len(caption_cues(mapped))
    qc["words_kept"] = len(mapped)
    qc["words_total"] = len(words["words"])

    # thumbnail
    from banner import render
    from show import build
    html, w, h = build("thumbnail", theme, cfg.get("thumb") or title, None)
    render(html, out / "thumbnail.png", w, h)

    # video
    if not a.audio_only and not a.text_only:
        print("video")
        made = chapter_cards(d, edl, theme)
        if made:
            print(f"  chapter cards: {', '.join(made)}")
        twoup = two_up(d, m, a)
        print(f"  {twoup.relative_to(ROOT)}")
        (d / "mix" / "cut.mp4").unlink(missing_ok=True)
        video = episode_video(d, edl, twoup, wav, timings, chapters_path, a, m)
        qc["episode_mp4"] = measure(video)
        qc["episode_mp4_duration"] = float(run(["ffprobe", "-v", "error", "-show_entries",
                                                "format=duration", "-of", "csv=p=0",
                                                video]).stdout.strip())
        contact(video, out / "contact.png")
        print(f"  {video.relative_to(ROOT)}  {qc['episode_mp4_duration']:.1f}s  "
              f"{qc['episode_mp4']['lufs']:.1f} LUFS")

    qc["timings"] = timings
    qc["chapters"] = chapters
    qc["edl"] = {"items": len(edl["items"]), "removed_seconds": edl["removed_seconds"],
                 "duration": edl["duration"]}
    save_json(out / "qc.json", qc)
    print(f"{(out / 'qc.json').relative_to(ROOT)}")
    for k in ("dialogue", "episode_wav", "episode_mp3", "episode_mp4"):
        if qc.get(k):
            flag = "" if abs(qc[k]["lufs"] - a.target) <= 1.0 and qc[k]["tp"] <= a.tp + 0.3 \
                else "  OFF TARGET"
            print(f"  {k:<12} {qc[k]['lufs']:6.1f} LUFS  {qc[k]['tp']:5.1f} dBTP{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
