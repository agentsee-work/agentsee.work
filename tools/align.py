#!/usr/bin/env python3
"""
Forced alignment: exact word boundaries for the verbatim transcript.

    ./tools/align.py ep0
    ./tools/align.py ep0 --only james

Whisper's word timestamps are good to about 50 ms on an ordinary word and
far worse around a filler: it hands the um's sound to the word before, or
ends that word a quarter of a second early, and the two transcripts of the
same track disagree by 100-200 ms about where "absolutely" ends. A cut
placed from those times takes the end of the word and leaves the um, which
is what the third listen heard.

A forced aligner does not guess times from a transcript of its own; given
the words that were said, it finds where each one is, by Viterbi over a
character-level CTC model (wav2vec2-base-960h here, 20 ms frames). The
transcript is erm's verbatim one, which has the ums as words. This writes
build/episodes/<slug>/erm/<short>.aligned.json — the same words with
boundaries from the audio — and cut.py prefers it when it exists.

Runs in build/venv-align, a venv of its own with the GPU build of torch:

    python3 -m venv build/venv-align
    build/venv-align/bin/pip install torch transformers

On Linux a plain `pip install torch` is the CUDA build. The CPU build in
build/venv-erm is the fallback, at about ten times the time.
"""

import argparse
import os
import pathlib
import re
import subprocess
import sys

from eplib import ROOT, die, ep_dir, load_json, save_json

# a venv of its own with the GPU build of torch (`pip install torch transformers`
# gets the CUDA build on Linux); the CPU build in venv-erm is the fallback,
# at about ten times the time
VENVS = [ROOT / "build" / "venv-align" / "bin" / "python",
         ROOT / "build" / "venv-erm" / "bin" / "python"]
MODEL = "facebook/wav2vec2-base-960h"
SR = 16000


def ensure_venv():
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        return
    except ImportError:
        pass
    here = pathlib.Path(sys.prefix).resolve()
    for venv in VENVS:
        if venv.exists() and here != venv.parent.parent.resolve():
            os.execv(str(venv), [str(venv), *sys.argv])
    die("torch and transformers are not importable; see the docstring")


def decode(path):
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-ac", "1",
                          "-ar", str(SR), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def clean(word):
    """The model knows A-Z and apostrophe. 'agentsee.work' -> 'agenteework'
    is wrong but harmless: it is still aligned to the same sound."""
    return re.sub(r"[^A-Z']", "", word.upper())


HOP, RF = 320, 400   # wav2vec2-base: one frame per 320 samples, 400 in view


def viterbi(logp, tokens, blank=0, lo=None, hi=None):
    """CTC forced alignment over the whole track. logp: (T, V) log-
    probabilities; tokens: the target token ids. Returns, per token,
    (start_frame, end_frame). Only the previous frame's scores are kept —
    the full table for a ten-minute track would be gigabytes — and the
    back-pointers are one byte each. `lo`/`hi`, per token, are the frames
    it may occupy: a band around where Whisper heard the word, so a quiet
    word next to a long silence cannot wander thirty seconds along it,
    which it did."""
    import numpy as np
    T = logp.shape[0]
    ext = [blank]
    for t in tokens:
        ext += [t, blank]
    S = len(ext)
    NEG = -1e30
    ext_arr = np.array(ext)
    allow2 = np.zeros(S, dtype=bool)
    allow2[2:] = (ext_arr[2:] != blank) & (ext_arr[2:] != ext_arr[:-2])
    back = np.zeros((T, S), dtype=np.int8)
    if lo is not None:
        # a blank between two tokens may sit anywhere either of them may
        lo_t = np.array(lo)
        hi_t = np.array(hi)
        lo_e = np.zeros(S, dtype=np.int64)
        hi_e = np.full(S, T, dtype=np.int64)
        lo_e[1::2], hi_e[1::2] = lo_t, hi_t
        lo_e[2::2] = lo_t
        hi_e[0:-1:2] = hi_t
        lo_e[0], hi_e[-1] = 0, T
    else:
        lo_e = np.zeros(S, dtype=np.int64)
        hi_e = np.full(S, T, dtype=np.int64)
    prev = np.full(S, NEG)
    prev[0] = logp[0, ext[0]]
    if S > 1:
        prev[1] = logp[0, ext[1]]
    idx = np.arange(S)
    for t in range(1, T):
        c1 = np.concatenate(([NEG], prev[:-1]))
        c2 = np.concatenate(([NEG, NEG], prev[:-2]))
        c2[~allow2] = NEG
        stacked = np.stack([prev, c1, c2])
        arg = stacked.argmax(axis=0)
        prev = stacked[arg, idx] + logp[t, ext_arr]
        prev[(t < lo_e) | (t > hi_e)] = NEG
        back[t] = arg
    s = S - 1 if prev[S - 1] >= prev[S - 2] else S - 2
    path = []
    for t in range(T - 1, -1, -1):
        path.append(s)
        s = int(s) - int(back[t, s])
    path.reverse()
    spans = {}
    for t, s in enumerate(path):
        if ext[s] != blank:
            k = (s - 1) // 2
            a, b = spans.get(k, (t, t))
            spans[k] = (min(a, t), max(b, t))
    return [spans.get(k, (0, 0)) for k in range(len(tokens))]


def full_logp(model, proc, audio, device, window=24.0, step=22.0):
    """Log-probabilities for the whole track, one row per 20 ms frame. The
    model sees overlapping windows and each frame is taken from a window
    where it had a second of context on both sides, so no row depends on
    where the windows fell. Aligning the whole track at once is what makes
    the result independent of Whisper's timestamps: chunks cut by those
    timestamps put a word outside its own chunk when Whisper was a second
    out, and the aligner then had to put the word on the wrong sound."""
    import numpy as np
    import torch
    G = max(1, (len(audio) - RF) // HOP + 1)
    out = np.full((G, model.config.vocab_size), np.nan, dtype=np.float32)
    s = 0.0
    while True:
        c0 = (int(s * SR) // HOP) * HOP
        c1 = min(len(audio), c0 + int(window * SR))
        chunk = audio[c0:c1]
        if len(chunk) < RF:
            break
        inputs = proc(chunk, sampling_rate=SR, return_tensors="pt")
        with torch.inference_mode():
            logits = model(inputs.input_values.to(device)).logits[0]
        lp = torch.log_softmax(logits, dim=-1).cpu().numpy()
        g0, T = c0 // HOP, lp.shape[0]
        lo = 0 if c0 == 0 else 50
        hi = T if c1 >= len(audio) else T - 50
        hi = min(hi, G - g0)
        if hi > lo:
            out[g0 + lo:g0 + hi] = lp[lo:hi]
        if c1 >= len(audio):
            break
        s += step
    missing = np.isnan(out[:, 0])
    if missing.any():
        out[missing] = np.log(1e-6)
        out[missing, proc.tokenizer.pad_token_id] = 0.0
    return out


def align_words(logp, proc, words, band=4.0):
    """Where each word is, in seconds, from the whole-track log-probs.
    `words` are dicts with the text and Whisper's rough times; each is
    placed within `band` seconds of where Whisper heard it."""
    vocab = proc.tokenizer.get_vocab()
    blank, sep = vocab["<pad>"], vocab["|"]
    frame = HOP / SR
    T = logp.shape[0]
    seq, owner, lo, hi = [], [], [], []

    def window(i, j):
        """Frames between the band of word i and the band of word j."""
        a = max(0, int((words[i]["t0"] - band) / frame))
        b = min(T - 1, int((words[j]["t1"] + band) / frame))
        return a, b

    for i, w in enumerate(words):
        cw = clean(w["w"])
        if not cw:
            continue
        for ch in cw:
            if ch in vocab:
                seq.append(vocab[ch])
                owner.append(i)
                lo.append(window(i, i)[0])
                hi.append(window(i, i)[1])
        seq.append(sep)
        owner.append(-1)
        nxt = min(i + 1, len(words) - 1)
        lo.append(window(i, i)[0])
        hi.append(window(i, nxt)[1])
    if not seq:
        return [(None, None)] * len(words)
    spans = viterbi(logp, seq, blank, lo, hi)
    out = {}
    for (a, b), o in zip(spans, owner):
        if o < 0:
            continue
        s0, s1 = out.get(o, (a, b))
        out[o] = (min(s0, a), max(s1, b))
    return [(out[i][0] * frame, (out[i][1] + 1) * frame) if i in out else (None, None)
            for i in range(len(words))]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--only")
    ap.add_argument("--band", type=float, default=4.0,
                    help="seconds either side of Whisper's time a word may be placed")
    a = ap.parse_args()
    ensure_venv()
    import numpy as np
    import torch
    from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

    d = ep_dir(a.slug)
    m = load_json(d / "manifest.json")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    proc = Wav2Vec2Processor.from_pretrained(MODEL)
    model = Wav2Vec2ForCTC.from_pretrained(MODEL).to(device).eval()
    print(f"aligner {MODEL} on {device}")

    def envelope(audio):
        """The track's level in 10 ms hops, in dB."""
        hop = SR // 100
        nn = len(audio) // hop * hop
        return 20 * np.log10(np.sqrt((audio[:nn].reshape(-1, hop) ** 2).mean(1)) + 1e-9)

    def level(env, s0, s1):
        """The loudest hop under a span, with 50 ms of grace either side."""
        a_, b_ = max(0, int(s0 * 100) - 5), min(len(env), int(s1 * 100) + 5)
        return float(env[a_:b_].max()) if b_ > a_ else -99.0

    # Pass one places each track's words and takes its envelope and its
    # speaker's line. Pass two judges the ghosts, and one kind of ghost is
    # only visible against the other tracks, so every track's envelope is
    # read first; a track left out by --only is not aligned again, its line
    # comes from its last alignment, or from Whisper's times if it has none.
    tracks = []
    for p in m["participants"]:
        src = d / "erm" / f"{p['short']}.words.json"
        if not src.exists():
            die(f"{src.relative_to(ROOT)} missing — run fillers.py first")
        words = [w for w in load_json(src)["words"] if w["w"].strip()]
        audio = decode(d / p["audio"]["file"])
        env = envelope(audio)
        selected = not a.only or p["short"] == a.only
        if selected:
            logp = full_logp(model, proc, audio, device)
            spans = align_words(logp, proc, words, band=a.band)
        else:
            done = d / "erm" / f"{p['short']}.aligned.json"
            placed = load_json(done)["words"] if done.exists() else words
            spans = [(w["t0"], w["t1"]) if w.get("aligned", True) else (None, None) for w in placed]
        # the speaker's line: the quiet end of their words, each judged by
        # its loudest moment, so a word stretched over silence cannot
        # drag the line down with it
        peaks = [level(env, *sp) for sp in spans if sp[0] is not None]
        talk = float(np.percentile(peaks, 30)) - 8.0 if peaks else -40.0
        tracks.append({"p": p, "words": words, "spans": spans, "env": env, "talk": talk,
                       "selected": selected})

    for t in tracks:
        if not t["selected"]:
            continue
        p, words, spans, env, talk = t["p"], t["words"], t["spans"], t["env"], t["talk"]
        others = [o for o in tracks if o is not t]

        def talked_over(s0, s1, others=others):
            """Whether another speaker's sound over this span rises above their line."""
            return any(level(o["env"], s0, s1) > o["talk"] for o in others)

        # The words that are not there come in two kinds. Whisper writes
        # "Thank you." into silence, and the aligner, made to put it
        # somewhere, crams it into a few frames of nothing: the level rule
        # below sees that. It also writes "Thank you." into the other
        # person's turn, where a breath or a laugh gives it something to sit
        # on, and the level rule cannot see that. Whisper's own doubt can.
        # It scores every segment with the probability that it is not
        # speech at all, and erm keeps the score on each word as `nsp`:
        # 0.7 to 0.9 on these, 0.04 on a sentence. The score alone decides
        # nothing, since a quiet "Yeah." can score high too, so a segment
        # goes only when it scored above 0.6, every word of it sits within
        # 5 dB of the speaker's line, and the other speaker was talking
        # over it. Whole segments, so no half of one is left behind.
        segs, key = [], object()
        for i, w in enumerate(words):
            if w.get("nsp") != key:
                key = w.get("nsp")
                segs.append([])
            segs[-1].append(i)
        doubted = set()
        for seg in segs:
            if (words[seg[0]].get("nsp") or 0.0) <= 0.6:
                continue
            placed = [(i, spans[i]) for i in seg if spans[i][0] is not None]
            if placed and all(level(env, *sp) < talk + 5 for _, sp in placed) \
                    and any(talked_over(*sp) for _, sp in placed):
                doubted.update(i for i, _ in placed)

        out, moved, ghosts = [], [], []
        for i, (w, sp) in enumerate(zip(words, spans)):
            if sp[0] is None:
                out.append({**w, "aligned": False})
                continue
            lv = level(env, *sp)
            chars = len(clean(w["w"]))
            dur = max(sp[1] - sp[0], 1e-3)
            # a word with nothing under it is crammed into a few frames or
            # stretched across the silence; a spoken one is neither
            crammed = chars / dur > 25
            stretched = dur > max(0.5, chars * 0.12)
            rec = {"w": w["w"], "t0": round(sp[0], 3), "t1": round(sp[1], 3), "whisper_t0": w["t0"],
                   "whisper_t1": w["t1"], "aligned": True, "db": round(lv, 1)}
            if w.get("filled"):
                rec["filled"] = True
            # a real quiet word sits a few dB under the line; a made-up one
            # sits twelve or more under it, or well under and misshapen, or
            # Whisper doubted it and the other speaker was talking over it
            if lv < talk - 12 or (lv < talk - 4 and (crammed or stretched)):
                rec["ghost"] = True
                ghosts.append((w["w"], sp[0], lv, "no sound under it"))
            elif i in doubted:
                rec["ghost"] = True
                ghosts.append((w["w"], sp[0], lv, f"no-speech score {w['nsp']:.2f}, talked over"))
            else:
                moved.append(abs(sp[0] - w["t0"]))
            out.append(rec)
        save_json(d / "erm" / f"{p['short']}.aligned.json", {"model": MODEL, "words": out})
        moved = np.array(moved) if moved else np.zeros(1)
        print(f"  {p['short']:<8} {len(words)} words; start moved by median {np.median(moved):.3f}s, "
              f"p90 {np.percentile(moved, 90):.3f}s, max {moved.max():.2f}s; "
              f"{len(ghosts)} ghosts (no sound under them, or doubted by Whisper and talked over; "
              f"speech line {talk:.0f} dB)")
        for g in ghosts[:24]:
            print(f"     ghost {g[0]!r} at {g[1]:.2f}s, {g[2]:.0f} dB, {g[3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
