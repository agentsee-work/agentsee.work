#!/usr/bin/env python3
"""
Transcribe an episode, one track at a time, with word timing.

    ./tools/transcribe.py ep0
    ./tools/transcribe.py ep0 --model medium.en --cpu

Riverside's transcript has no word-level timestamps, and the pipeline needs
them for everything after this: where to cut, which panel is speaking, what
the captions highlight. So this runs Whisper locally, on each participant's
own track. Separate tracks make speaker labels free — there is nobody else on
the file — and make voice activity detection honest, because a track that is
silent while the other person talks is silent, not quiet crosstalk.

Output, in build/episodes/<slug>/transcript/:
  words.json       every word, with speaker and timeline seconds (offset applied)
  <short>.json     the raw per-track result, for reprocessing without the GPU
  transcript.txt   speaker turns with timestamps, for reading
  transcript.srt   captions in timeline time (assemble.py writes the edited one)

Runs faster-whisper. It lives in its own venv because its CUDA wheels do not
belong in the system Python:

    python3 -m venv build/venv-transcribe
    build/venv-transcribe/bin/pip install faster-whisper nvidia-cublas-cu12 nvidia-cudnn-cu12

The script re-executes itself under that venv if it finds one.
"""

import argparse
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from eplib import ROOT, caption_cues, die, ep_dir, hms, load_json, save_json, write_srt  # noqa: E402

VENV = ROOT / "build" / "venv-transcribe" / "bin" / "python"


def ensure_engine():
    try:
        import faster_whisper  # noqa: F401
        return
    except ImportError:
        pass
    # Compare prefixes, not executables: the venv's python is a symlink to the
    # system one, so resolved paths are equal and the re-exec never happened.
    if VENV.exists() and pathlib.Path(sys.prefix).resolve() != VENV.parent.parent.resolve():
        os.execv(str(VENV), [str(VENV), *sys.argv])
    die("faster-whisper is not importable and build/venv-transcribe does not exist; "
        "see the docstring for the two commands that make it")


def cuda_libs():
    """ctranslate2 finds cuBLAS and cuDNN through the loader, not pip, so the
    wheel-installed copies have to be on LD_LIBRARY_PATH. Add them and re-exec
    once; the variable is read at process start."""
    if os.environ.get("_EP_CUDA_LIBS"):
        return
    try:
        import nvidia  # noqa: F401
    except ImportError:
        return
    base = pathlib.Path(sys.prefix) / "lib"
    libs = [str(p) for p in base.glob("python3*/site-packages/nvidia/*/lib")]
    if libs:
        os.environ["LD_LIBRARY_PATH"] = ":".join(libs + [os.environ.get("LD_LIBRARY_PATH", "")])
        os.environ["_EP_CUDA_LIBS"] = "1"
        os.execv(sys.executable, [sys.executable, *sys.argv])


def apply_fixes(words, fixes):
    """Deterministic spelling, after the model: 'Abra=Abrar' or
    'the bra=Abrar' or 'Agency Podcast=AgentSee podcast'. Multi-word matches
    are replaced by the replacement's words spread over the same span, so
    the timing and the captions survive the edit."""
    out = list(words)
    for rule in fixes:
        if "=" not in rule:
            continue
        src, dst = rule.split("=", 1)
        src_t = [re.sub(r"[,.?!]+$", "", t.lower()) for t in src.split()]
        dst_t = dst.split()
        i = 0
        while i <= len(out) - len(src_t):
            win = [re.sub(r"[^a-z0-9'@.]", "", re.sub(r"[,.?!]+$", "", w["w"].lower()))
                   for w in out[i:i + len(src_t)]]
            if win == src_t:
                t0, t1 = out[i]["t0"], out[i + len(src_t) - 1]["t1"]
                # the matched words' own punctuation survives; the rule's does not
                trailing = re.search(r"[,.?!]+$", out[i + len(src_t) - 1]["w"])
                dst_t = dst_t[:-1] + [re.sub(r"[,.?!]+$", "", dst_t[-1])]
                step = (t1 - t0) / max(1, len(dst_t))
                new = []
                for k, tok in enumerate(dst_t):
                    w = dict(out[i])
                    w["w"] = tok + (trailing.group(0) if trailing and k == len(dst_t) - 1 else "")
                    w["t0"] = round(t0 + k * step, 3)
                    w["t1"] = round(t0 + (k + 1) * step, 3)
                    new.append(w)
                out[i:i + len(src_t)] = new
                i += len(new)
            else:
                i += 1
    return out


HALLUCINATIONS = re.compile(r"^(thank you\.?|thanks for watching\.?|you\.?|\.+)$", re.I)


def decode(path):
    """16 kHz mono float32, through ffmpeg. faster-whisper would do this
    itself through PyAV, but the PyAV it finds is not the one it was written
    against (`metadata_errors` came and went), and ffmpeg is already a
    requirement of everything else here."""
    import subprocess
    import numpy as np
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-ac", "1",
         "-ar", "16000", "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def transcribe_track(model, path, language, beam, prompt="", hotwords=""):
    segments, info = model.transcribe(
        decode(path), language=language, beam_size=beam, word_timestamps=True,
        vad_filter=True, vad_parameters=dict(min_silence_duration_ms=500),
        condition_on_previous_text=False,
        # The names the model will otherwise guess at. A prompt in the show's
        # own voice steers spelling; hotwords bias the decoder towards them.
        initial_prompt=prompt or None, hotwords=hotwords or None)
    segs, dropped = [], []
    for s in segments:
        text = s.text.strip()
        # Whisper's failure mode on silence is confident, polite nonsense.
        if s.no_speech_prob > 0.6 or s.avg_logprob < -1.2 or HALLUCINATIONS.match(text):
            dropped.append((round(s.start, 1), round(s.end, 1), text[:40]))
            continue
        segs.append({"t0": round(s.start, 3), "t1": round(s.end, 3), "text": text,
                     "no_speech": round(s.no_speech_prob, 3),
                     "logprob": round(s.avg_logprob, 3),
                     "words": [{"w": w.word.strip(), "t0": round(w.start, 3),
                                "t1": round(w.end, 3), "p": round(w.probability, 3)}
                               for w in (s.words or []) if w.word.strip()]})
    return segs, {"language": info.language, "probability": round(info.language_probability, 3),
                  "dropped": dropped}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--language", default="en")
    ap.add_argument("--beam", type=int, default=5)
    ap.add_argument("--cpu", action="store_true", help="do not try the GPU")
    ap.add_argument("--only", help="one participant's short name")
    ap.add_argument("--prompt", default=None,
                    help="a sentence in the show's voice with the names spelled right; "
                         "default is built from episode.json")
    ap.add_argument("--hotwords", default=None, help="words to bias towards")
    ap.add_argument("--fix", action="append", default=[],
                    help="spelling fix 'heard=Meant', repeatable; episode.json's 'fixes' too")
    ap.add_argument("--dir", default="transcript",
                    help="output folder name under the episode (default transcript); "
                         "use another to try settings without losing the last run")
    ap.add_argument("--merge-only", action="store_true",
                    help="rebuild words.json and the text files from the per-track JSON")
    ap.add_argument("--from-aligned", action="store_true",
                    help="build words.json from erm/<short>.aligned.json instead: the verbatim "
                         "transcript, with the fillers as words, timed by forced alignment")
    a = ap.parse_args()

    d = ep_dir(a.slug)
    m = load_json(d / "manifest.json")
    tdir = d / a.dir
    tdir.mkdir(exist_ok=True)

    if not a.merge_only:
        ensure_engine()
        if not a.cpu:
            cuda_libs()
        from faster_whisper import WhisperModel
        model, device = None, None
        for dev, ct in ([] if a.cpu else [("cuda", "float16")]) + [("cpu", "int8")]:
            try:
                model = WhisperModel(a.model, device=dev, compute_type=ct)
                device = f"{dev}/{ct}"
                break
            except Exception as e:  # noqa: BLE001
                print(f"  {dev} unavailable: {str(e).splitlines()[0][:120]}")
        if model is None:
            die("no device could load the model")
        print(f"model {a.model} on {device}")
        cfg = load_json(d / "episode.json") if (d / "episode.json").exists() else {}
        names = ", ".join(p["name"] for p in m["participants"])
        # No initial_prompt and no hotwords by default. On episode 0 a one-line
        # prompt with the names cost 40% of the words — whole chunks skipped,
        # and the prompt itself transcribed as speech — and hotwords alone cost
        # a third, and varied run to run. Spelling is fixed deterministically
        # at merge time instead (--fix, or "fixes" in episode.json).
        prompt = a.prompt or ""
        hotwords = a.hotwords or ""
        if prompt:
            print(f"prompt: {prompt}")
        if hotwords:
            print(f"hotwords: {hotwords}")
        for p in m["participants"]:
            if a.only and p["short"] != a.only:
                continue
            path = d / p["audio"]["file"]
            print(f"  {p['short']:<8} {path.name} ...", end="", flush=True)
            segs, info = transcribe_track(model, path, a.language, a.beam, prompt, hotwords)
            n = sum(len(s["words"]) for s in segs)
            print(f" {len(segs)} segments, {n} words, {len(info['dropped'])} dropped as noise")
            for t0, t1, txt in info["dropped"]:
                print(f"      dropped {t0}-{t1}s: {txt!r}")
            save_json(tdir / f"{p['short']}.json",
                      {"participant": p["short"], "file": p["audio"]["file"],
                       "model": a.model, "device": device, "offset": p["offset"],
                       "info": info, "segments": segs})

    # merge: apply each track's timeline offset, label the speaker, sort
    words, segments = [], []
    aligned_src = False
    for p in m["participants"]:
        off = p["offset"]
        fa = d / "erm" / f"{p['short']}.aligned.json"
        if a.from_aligned and fa.exists():
            # Whisper's own timestamps on this recording ran up to half a
            # second early around fillers; the aligner's are within a frame
            # or two. Sentences are cut at punctuation or a second's gap.
            aligned_src = True
            aw = [w for w in load_json(fa)["words"]
                  if w.get("aligned") and w["w"].strip() and not w.get("ghost")]
            cur = []
            for w in aw:
                words.append({"w": w["w"], "t0": round(w["t0"] + off, 3), "t1": round(w["t1"] + off, 3),
                              "p": 1.0, "spk": p["short"]})
                if cur and (w["t0"] - cur[-1]["t1"] > 1.0 or re.search(r"[.?!]$", cur[-1]["w"])):
                    segments.append({"spk": p["short"], "t0": round(cur[0]["t0"] + off, 3),
                                     "t1": round(cur[-1]["t1"] + off, 3), "text": " ".join(x["w"] for x in cur)})
                    cur = []
                cur.append(w)
            if cur:
                segments.append({"spk": p["short"], "t0": round(cur[0]["t0"] + off, 3),
                                 "t1": round(cur[-1]["t1"] + off, 3), "text": " ".join(x["w"] for x in cur)})
            continue
        f = tdir / f"{p['short']}.json"
        if not f.exists():
            print(f"  no transcript for {p['short']} yet")
            continue
        t = load_json(f)
        for i, s in enumerate(t["segments"]):
            segments.append({"spk": p["short"], "t0": round(s["t0"] + off, 3),
                             "t1": round(s["t1"] + off, 3), "text": s["text"]})
            for w in s["words"]:
                words.append({"w": w["w"], "t0": round(w["t0"] + off, 3),
                              "t1": round(w["t1"] + off, 3), "p": w["p"], "spk": p["short"]})
    words.sort(key=lambda w: (w["t0"], w["t1"]))
    segments.sort(key=lambda s: s["t0"])
    cfg = load_json(d / "episode.json") if (d / "episode.json").exists() else {}
    fixes = list(cfg.get("fixes", [])) + list(a.fix)
    if fixes:
        before = " ".join(w["w"] for w in words)
        words = apply_fixes(words, fixes)
        after = " ".join(w["w"] for w in words)
        print(f"  fixes applied: {len(fixes)} rules, text {'changed' if before != after else 'unchanged'}")
    # segments carry text for reading; rebuild it from the fixed words
    for seg in segments:
        ws = [w["w"] for w in words if w["spk"] == seg["spk"] and seg["t0"] - 0.01 <= w["t0"] < seg["t1"]]
        if ws:
            seg["text"] = " ".join(ws)
    save_json(tdir / "words.json", {"slug": a.slug, "words": words, "segments": segments,
                                    "aligned": aligned_src,
                                    "speakers": {p["short"]: p["name"] for p in m["participants"]}})

    # readable: one paragraph per speaker turn
    lines, cur_spk, cur = [], None, []
    for s in segments:
        if s["spk"] != cur_spk:
            if cur:
                lines.append(f"[{hms(cur[0]['t0'], False)}] {m_name(m, cur_spk)}: "
                             + " ".join(x["text"] for x in cur) + "\n")
            cur_spk, cur = s["spk"], []
        cur.append(s)
    if cur:
        lines.append(f"[{hms(cur[0]['t0'], False)}] {m_name(m, cur_spk)}: "
                     + " ".join(x["text"] for x in cur) + "\n")
    (tdir / "transcript.txt").write_text("\n".join(lines))
    write_srt(caption_cues(words), tdir / "transcript.srt")

    print(f"{(tdir / 'words.json').relative_to(ROOT)}  {len(words)} words, "
          f"{len(segments)} segments, {len(lines)} turns")
    if words:
        print(f"  first word {hms(words[0]['t0'])}  last word {hms(words[-1]['t1'])}")
    return 0


def m_name(m, short):
    return next(p["name"] for p in m["participants"] if p["short"] == short)


if __name__ == "__main__":
    sys.exit(main())
