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

Needs ffmpeg on PATH. Reads any format ffmpeg reads, writes 48 kHz 24-bit WAV.
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


def peak_dbfs(path):
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-i", str(path), "-af", "volumedetect",
         "-f", "null", "-"], capture_output=True, text=True)
    hit = re.search(r"max_volume:\s*(-?[\d.]+) dB", r.stderr)
    return float(hit.group(1)) if hit else None


def write(path, out, filt):
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-i", str(path), "-af", filt,
         "-ar", "48000", "-c:a", "pcm_s24le", str(out)],
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

    print(f"{'cue':28} {'role':5} {'secs':>5} {'in LUFS':>8} "
          f"{'target':>7} {'out':>7}  note")
    for f in files:
        role = "bed" if BED.search(f.name) else "fore"
        target = a.bed_lufs if role == "bed" else a.lufs
        secs = ffprobe_duration(f)
        out = out_dir / (f.stem + ".wav")

        if secs < MIN_DURATION:
            pk = peak_dbfs(f)
            gain = SHORT_PEAK_DBFS - pk if pk is not None else 0.0
            if not a.dry_run:
                write(f, out, f"volume={gain:+.2f}dB")
            print(f"{f.name:28} {role:5} {secs:>5.1f} {'peak':>8} "
                  f"{SHORT_PEAK_DBFS:>6.1f}dB {pk + gain:>6.1f}dB  "
                  f"too short to gate; peak-normalised")
            continue

        m = measure(f, target, a.tp)
        if m is None:
            print(f"{f.name:28} {role:5} {secs:>5.1f} {'-':>8} {'-':>7} {'-':>7}  "
                  f"SKIPPED: no measurable loudness (silent?)")
            continue
        filt = (f"loudnorm=I={target}:TP={a.tp}:LRA=11:linear=true:"
                f"measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
                f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
                f"offset={m['target_offset']}")
        if not a.dry_run:
            write(f, out, filt)
        print(f"{f.name:28} {role:5} {secs:>5.1f} {float(m['input_i']):>8.1f} "
              f"{target:>7.1f} {float(m['output_i']):>7.1f}  "
              f"TP {float(m['output_tp']):+.1f} dBTP")

    if a.dry_run:
        print("\n(dry run — nothing written)")
    else:
        print(f"\nwrote 48 kHz 24-bit WAV to {out_dir}/")


if __name__ == "__main__":
    main()
