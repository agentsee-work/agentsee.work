#!/usr/bin/env python3
"""
Find the ums Whisper left out, one track at a time, with erm.

    ./tools/fillers.py ep0
    ./tools/fillers.py ep0 --only james

Whisper transcribes readable text and leaves most disfluencies out, folding
the "um" into the word next to it. erm (MIT, github.com/dougcalobrisi/erm)
runs Whisper again with a prompt that keeps fillers, then three audio
passes for the ones it still missed: voiced sound in gaps, a second burst
inside an over-long word, and a trailing vowel confirmed by pitch. It writes
a cut list per track to build/episodes/<slug>/erm/<short>.json and its own
verbatim transcript to <short>.words.json, both in that track's own time
(see verbatim_words.py: any speech-level sound the transcript has no word
for is transcribed again on its own, so a reply Whisper dropped comes back).
cut.py uses the verbatim words — an um as a token with the words either
side of it timed — to place each filler cut, and the cut list's gap
detections for sound outside any word.

erm lives in its own venv, like transcription:

    python3 -m venv build/venv-erm
    build/venv-erm/bin/pip install erm "av<16" nvidia-cublas-cu12 nvidia-cudnn-cu12

(`av<16`: faster-whisper's decoder still passes an argument PyAV 16 dropped.)
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from eplib import ROOT, die, ep_dir, load_json  # noqa: E402

VENV = ROOT / "build" / "venv-erm"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--only", help="one participant's short name")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--gap-min-ms", type=int, default=350)
    ap.add_argument("--no-gaps", action="store_true", help="skip erm's gap detector")
    ap.add_argument("--no-pitch", action="store_true", help="skip pitch confirmation (more aggressive)")
    ap.add_argument("--no-fill", action="store_true", help="do not transcribe sounds the transcript has no word for")
    ap.add_argument("--jobs", type=int, default=2, help="tracks transcribed at once on the GPU")
    a = ap.parse_args()

    erm = VENV / "bin" / "erm"
    if not erm.exists():
        die(f"{erm.relative_to(ROOT)} not found; see the docstring for the two commands that make it")
    d = ep_dir(a.slug)
    m = load_json(d / "manifest.json")
    out_dir = d / "erm"
    out_dir.mkdir(exist_ok=True)
    env = dict(os.environ)
    libs = [str(p) for p in VENV.glob("lib/python3*/site-packages/nvidia/*/lib")]
    env["LD_LIBRARY_PATH"] = ":".join(libs + [env.get("LD_LIBRARY_PATH", "")])

    def one(p):
        out = out_dir / f"{p['short']}.json"
        cmd = [str(erm), str(d / p["audio"]["file"]), "--dry-run", "--json", str(out),
               "--model", a.model, "--gap-min-ms", str(a.gap_min_ms)]
        if a.no_gaps:
            cmd.append("--no-detect-gaps")
        if a.no_pitch:
            cmd.append("--no-confirm-pitch")
        r = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if r.returncode != 0 or not out.exists():
            die(f"erm failed on {p['short']}:\n" + (r.stderr or r.stdout)[-1500:])
        # erm's own transcript, with the fillers as words: the better source
        # for where an um is and where the words either side of it are
        words_out = out_dir / f"{p['short']}.words.json"
        cmd2 = [str(VENV / "bin" / "python"), str(ROOT / "tools" / "verbatim_words.py"),
                str(d / p["audio"]["file"]), a.model, str(words_out)]
        if a.no_fill:
            cmd2.append("--no-fill")
        r2 = subprocess.run(cmd2, env=env, capture_output=True, text=True)
        if r2.returncode != 0 or not words_out.exists():
            die(f"verbatim transcription failed on {p['short']}:\n" + (r2.stderr or r2.stdout)[-1500:])
        summary = json.loads(r2.stdout.strip().splitlines()[-1])
        vw = load_json(words_out)["words"]
        cuts = load_json(out)["cuts"]
        kinds = {}
        for c in cuts:
            k = ("gap" if c["word"].startswith("<gap") else "in" if c["word"].startswith("<in:")
                 else "long" if c["word"].startswith("<long:") else "word")
            kinds[k] = kinds.get(k, 0) + 1
        secs = sum(c["end"] - c["start"] for c in cuts)
        return (f"  {p['short']:<8} {len(vw)} verbatim words ({summary['holes']} sounds with no word, "
                f"{summary['filled']} words recovered{': ' + summary['filled_words'] if summary['filled'] else ''}), "
                f"{len(cuts)} cuts, {secs:.1f}s  {kinds}")

    # both tracks at once: two large-v3 instances fit on an 11 GB card, and
    # the pass is the slowest in the pipeline
    from concurrent.futures import ThreadPoolExecutor
    parts = [p for p in m["participants"] if not a.only or p["short"] == a.only]
    with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        for line in ex.map(one, parts):
            print(line)
    print(f"{out_dir.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
