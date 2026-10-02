#!/usr/bin/env python3
"""
Normalise the show's music cues to consistent, documented levels.

Audacity 4 has no scripting pipe and no macros, so exporting the .aup4
projects is a manual step. It only needs doing once per cue. Everything after
it is repeatable, and this is that part.

The thing worth automating is not the format conversion — it is the loudness
relationship between cues. A sting plays alone and can sit at full level; the
bed plays underneath speech and has to sit well below it. Judging that gap by
ear, per episode, is exactly how a show ends up with a bed that buries the
dialogue in episode 4 and vanishes in episode 5. So the gap is set here, in
one place, and written down:

    foreground (sting, outro, button, theme)   -16 LUFS, -1 dBTP
    bed (plays under speech)                   -30 LUFS, -1 dBTP

14 dB is a starting point, not a law. Change TARGETS, re-run, and every cue
moves together.

Usage:
    python3 tools/cues.py masters/ -o delivery/
    python3 tools/cues.py masters/ -o delivery/ --bed-lufs -28
    python3 tools/cues.py masters/ --dry-run

Needs ffmpeg on PATH. Reads any format ffmpeg reads, writes 24-bit WAV at
--rate, which defaults to 48 kHz because that is what video wants. Any
resampling is reported rather than done quietly: if a cue is going to be
looped, cut the loop *after* resampling, since a resampler has no data beyond
the file's edges and the ends are exactly where a loop joins.
"""

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys

# A cue whose name matches this plays underneath speech, not on its own.
BED = re.compile(r"\bbed\b", re.I)

# EBU R128 integrated loudness gates on 3-second blocks, so anything shorter
# measures as near-silence and gets normalised to a scream. Short cues get
# peak-normalised instead, which is what you want for a one-bar button anyway.
MIN_DURATION = 3.0
SHORT_PEAK_DBFS = -3.0


def ffprobe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def measure(path, target, tp):
    """First loudnorm pass. Returns the measured values, or None if too quiet."""
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-i", str(path),
         "-af", f"loudnorm=I={target}:TP={tp}:LRA=11:print_format=json",
         "-f", "null", "-"],
        capture_output=True, text=True)
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not blocks:
        return None
    m = json.loads(blocks[-1])
    if m.get("input_i") in (None, "-inf", "inf"):
        return None
    return m


def verify(path, target):
    """Measure what was actually written.

    loudnorm's first pass reports a predicted output_i, and it is pessimistic
    — it under-reported a delivered -16.4 LUFS file as -19.0. The point of
    this tool is to guarantee levels, so it measures the result rather than
    trusting the forecast.
    """
    m = measure(path, target, -1.0)
    if m is None:
        return None, None
    return float(m["input_i"]), float(m["input_tp"])


def peak_dbfs(path):
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-i", str(path), "-af", "volumedetect",
         "-f", "null", "-"], capture_output=True, text=True)
    hit = re.search(r"max_volume:\s*(-?[\d.]+) dB", r.stderr)
    return float(hit.group(1)) if hit else None


def ffprobe_rate(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate", "-of", "default=nw=1:nk=1",
         str(path)], capture_output=True, text=True, check=True)
    return int(out.stdout.strip())


def write(path, out, filt, rate):
    """Render to `rate`, which must always be stated explicitly.

    loudnorm oversamples to 192 kHz internally to find true peaks, and that
    rate leaks into the output if -ar is left off — so "keep the source rate"
    still has to pass the source rate, not nothing.
    """
    # soxr rather than the default resampler: 44.1 -> 48 is 160:147, an
    # awkward ratio, and this is the one place quality is free.
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-i", str(path), "-af", filt,
         "-ar", str(rate), "-resampler", "soxr", "-precision", "28",
         "-c:a", "pcm_s24le", str(out)],
        capture_output=True, text=True, check=True)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="directory of exported masters, or one file")
    ap.add_argument("-o", "--out", default="delivery",
                    help="where to write normalised cues (default: delivery/)")
    ap.add_argument("--lufs", type=float, default=-16.0,
                    help="target for foreground cues (default: -16)")
    ap.add_argument("--bed-lufs", type=float, default=-30.0,
                    help="target for anything named *bed* (default: -30)")
    ap.add_argument("--tp", type=float, default=-1.0,
                    help="true-peak ceiling in dBTP (default: -1)")
    ap.add_argument("--rate", type=int, default=48000,
                    help="output sample rate (default: 48000; 0 keeps the input's)")
    ap.add_argument("--dry-run", action="store_true",
                    help="measure and report, write nothing")
    a = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found on PATH — `brew install ffmpeg` on macOS")

    src = pathlib.Path(a.src)
    files = sorted(p for p in ([src] if src.is_file() else src.iterdir())
                   if p.suffix.lower() in (".wav", ".flac", ".aiff", ".aif", ".mp3"))
    if not files:
        sys.exit(f"no audio files in {src}")

    out_dir = pathlib.Path(a.out)
    if not a.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    resampled = []
    out_label = "predicted" if a.dry_run else "measured"
    print(f"{'cue':28} {'role':5} {'secs':>5} {'in LUFS':>8} "
          f"{'target':>7} {out_label:>9} {'dBTP':>6}  note")
    for f in files:
        role = "bed" if BED.search(f.name) else "fore"
        target = a.bed_lufs if role == "bed" else a.lufs
        secs = ffprobe_duration(f)
        out = out_dir / (f.stem + ".wav")
        in_rate = ffprobe_rate(f)
        rate = a.rate or in_rate          # 0 means "whatever came in"
        if rate != in_rate:
            resampled.append((f.name, in_rate, rate))

        if secs < MIN_DURATION:
            pk = peak_dbfs(f)
            gain = SHORT_PEAK_DBFS - pk if pk is not None else 0.0
            if not a.dry_run:
                write(f, out, f"volume={gain:+.2f}dB", rate)
            print(f"{f.name:28} {role:5} {secs:>5.1f} {'peak':>8} "
                  f"{SHORT_PEAK_DBFS:>7.1f} {pk + gain:>9.1f} {'':>6}  "
                  f"too short to gate; peak-normalised")
            continue

        m = measure(f, target, a.tp)
        if m is None:
            print(f"{f.name:28} {role:5} {secs:>5.1f} {'-':>8} {'-':>7} "
                  f"{'-':>9} {'-':>6}  SKIPPED: no measurable loudness (silent?)")
            continue
        filt = (f"loudnorm=I={target}:TP={a.tp}:LRA=11:linear=true:"
                f"measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
                f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
                f"offset={m['target_offset']}")
        if a.dry_run:
            got, got_tp = float(m["output_i"]), float(m["output_tp"])
            note = ""
        else:
            write(f, out, filt, rate)
            got, got_tp = verify(out, target)
            note = "" if got is None or abs(got - target) <= 1.0 \
                   else f"OFF TARGET by {got - target:+.1f} dB"
        print(f"{f.name:28} {role:5} {secs:>5.1f} {float(m['input_i']):>8.1f} "
              f"{target:>7.1f} {got:>9.1f} {got_tp:>6.1f}  {note}")

    if resampled:
        print("\nRESAMPLED:")
        for name, was, now in resampled:
            print(f"  {name}: {was} -> {now} Hz")
        print("  If any of these get looped, cut the loop from the resampled\n"
              "  file, not the master — a resampler has nothing beyond the\n"
              "  file's edges and that is where a loop joins.")
    if a.dry_run:
        print("\n(dry run — nothing written)")
    else:
        print(f"\nwrote 24-bit WAV to {out_dir}/"
              + (f" at {a.rate} Hz" if a.rate else " at each source's own rate"))


if __name__ == "__main__":
    main()
