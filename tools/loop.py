#!/usr/bin/env python3
"""
Find a rendered loop's true length, and cut it, to the sample.

Calculating the length from the score's tempo does not work. MuseScore's
audio export does not always come out at the written tempo, so a bed written
at 132 can render at 126 and every figure derived from the score is then
wrong. This measures the audio instead, which is the only thing that is
actually true.

Given a render of N identical cycles — the ×8 bed — it finds the period by
autocorrelating the signal's envelope, refines it to the sample by correlating
one cycle against the next, then scores every candidate cycle boundary by how
cleanly the loop's end meets its own start.

Note that length / N is *not* the period: the file ends with the last note
still decaying, and that tail belongs to no cycle. It is only an upper bound,
which is how it is used here. That last part matters because sample
libraries rotate between several recordings of the same note, so some joins
are quieter than others and the best one cannot be predicted, only measured.

Usage:
    python3 tools/loop.py bed-x8.wav --cycles 8
    python3 tools/loop.py bed-x8.wav --cycles 8 --cut bed-loop.wav
    python3 tools/loop.py bed-x8.wav --cycles 8 --beats 8   # for the bpm figure

It prints an ffmpeg atrim command as well as cutting the file, because that
is sample-exact and repeatable where typing figures into a selection toolbar
is neither — Audacity reads those in the *project* rate, not the clip's, so
a 44.1 kHz clip in a 48 kHz project silently disagrees with you.
"""

import argparse
import shutil
import subprocess
import sys

import numpy as np

SILENCE = 1e-4          # below this is the lead-in or the tail, not the music
SEAM_WINDOW = 2048      # samples either side of the join to judge it by


def decode(path):
    """The file as mono float32, at its own sample rate."""
    rate = int(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=sample_rate", "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, check=True).stdout.strip())
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", path,
         "-ac", "1", "-f", "f32le", "-"],
        capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).astype(np.float64), rate


def music_span(x):
    """First and last sample that is actually sound, not padding."""
    loud = np.abs(x) > SILENCE
    if not loud.any():
        sys.exit("the file is silent")
    idx = np.flatnonzero(loud)
    return int(idx[0]), int(idx[-1]) + 1


HOP = 256               # envelope resolution for the coarse search


def coarse_period(x, first, cycles):
    """Period from envelope autocorrelation, bounded above by length / cycles.

    cycles*period + tail = span, so span/cycles is an upper bound and never
    the answer. The lower bound keeps the search off the period's own
    multiples and sub-multiples.
    """
    span = len(x) - first
    upper = span / cycles
    lower = upper * 0.55

    n = span // HOP
    env = np.sqrt(np.mean(
        x[first:first + n * HOP].reshape(n, HOP) ** 2, axis=1))
    env -= env.mean()

    size = 1 << int(np.ceil(np.log2(max(len(env) * 2, 2))))
    f = np.fft.rfft(env, size)
    ac = np.fft.irfft(f * np.conj(f), size)[:len(env)]

    lo, hi = max(1, int(lower / HOP)), min(int(upper / HOP), len(ac) - 1)
    if hi <= lo:
        sys.exit("file too short for the number of cycles given")
    return (lo + int(np.argmax(ac[lo:hi + 1]))) * HOP


def refine_period(x, start, coarse, search):
    """The lag that best maps one cycle onto the next, to the sample."""
    length = min(coarse * 3, len(x) - start - coarse - search)
    if length < coarse:
        length = max(coarse // 2, min(coarse, len(x) - start - coarse - search))
    a = x[start:start + length]
    na = np.linalg.norm(a)
    best, best_score = coarse, -np.inf
    for p in range(coarse - search, coarse + search + 1):
        b = x[start + p:start + p + length]
        if len(b) < length or na == 0:
            continue
        nb = np.linalg.norm(b)
        if nb == 0:
            continue
        score = float(a @ b) / (na * nb)
        if score > best_score:
            best, best_score = p, score
    return best, best_score


def seam_error(x, start, period):
    """How badly the loop's end meets its start, in dB relative to the music.

    Looped, the sample after start+period-1 is start itself. The signal's own
    continuation there is start+period, so the mismatch between the two is the
    click you will hear.
    """
    w = SEAM_WINDOW
    if start + period + w > len(x):
        return None
    head = x[start:start + w]
    wrap = x[start + period:start + period + w]
    rms = np.sqrt(np.mean(x[start:start + period] ** 2))
    if rms == 0:
        return None
    err = np.sqrt(np.mean((head - wrap) ** 2))
    return 20 * np.log10(max(err, 1e-12) / rms)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("--cycles", type=int, required=True,
                    help="how many identical repeats the render contains")
    ap.add_argument("--beats", type=int, default=8,
                    help="beats per cycle, for the bpm figure (default 8 = 2 bars of 4/4)")
    ap.add_argument("--cut", metavar="OUT", help="write the best loop here")
    ap.add_argument("--search", type=int, default=HOP * 2,
                    help=f"± samples to refine around the estimate (default {HOP*2})")
    a = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found on PATH")

    x, rate = decode(a.src)
    first, last = music_span(x)
    span = last - first
    coarse = coarse_period(x, first, a.cycles)
    period, conf = refine_period(x, first, coarse, a.search)

    print(f"{a.src}  {rate} Hz, {len(x)/rate:.3f} s")
    print(f"  music runs {first:,} .. {last:,}  "
          f"({span:,} samples, {span/rate:.3f} s)")
    print(f"  lead-in {first/rate*1000:.0f} ms, tail after the music "
          f"{(len(x)-last)/rate*1000:.0f} ms")
    print(f"  length / {a.cycles} (upper bound only): {span // a.cycles:,}")
    print(f"  envelope autocorrelation:          {coarse:,}")
    print(f"  refined to the sample:             {period:,}  "
          f"(match {conf:.4f})")
    print(f"  = {period/rate:.6f} s  ->  {60*a.beats*rate/period:.2f} bpm "
          f"for {a.beats} beats")

    print(f"\n  {'cycle':>5} {'start':>12} {'seam':>8}")
    rows = []
    for c in range(a.cycles):
        s = first + c * period
        e = seam_error(x, s, period)
        if e is None:
            continue
        rows.append((e, c, s))
        print(f"  {c+1:>5} {s:>12,} {e:>7.1f} dB")
    if not rows:
        sys.exit("no candidate cycle had room for a full loop plus its wrap")
    rows.sort()
    err, cyc, start = rows[0]
    print(f"\n  cleanest join: cycle {cyc+1} at {start:,}, seam {err:.1f} dB "
          f"below the music")

    cmd = (f"ffmpeg -i {a.src} -af "
           f"atrim=start_sample={start}:end_sample={start+period} "
           f"-c:a pcm_s24le LOOP.wav")
    print(f"\n  {cmd}")

    if a.cut:
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", a.src, "-af",
             f"atrim=start_sample={start}:end_sample={start+period}",
             "-c:a", "pcm_s24le", a.cut], check=True)
        print(f"  wrote {a.cut}")


if __name__ == "__main__":
    main()
