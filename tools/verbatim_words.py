#!/usr/bin/env python3
"""
erm's verbatim transcript of one track, then a second look at any
speech-level sound it has no word for. Run by fillers.py inside venv-erm:

    build/venv-erm/bin/python tools/verbatim_words.py TRACK MODEL OUT.json [--no-fill]

Whisper drops the odd short utterance altogether — a reply of a second and
a half in the middle of the other person's story went missing from episode
0 — and everything downstream that trusts the transcript then treats that
sound as nothing: the gate closes on it, the cut takes it. So any run of
speech-level sound of 0.3 s or more with no word over it is transcribed
again on its own, with a little context, and whatever comes back on the
sound is added, marked `filled`. Laughs come back as nothing, which is
right.
"""

import json
import re
import subprocess
import sys

import numpy as np

SR = 16000
FILLERS = {"um", "uh", "er", "erm", "ah", "hmm", "mm", "mhm", "huh"}
STOCK = {"thank", "thanks", "you", "for", "watching", "subscribe", "subscribing", "bye", "ha", "oh"}


def norm(w):
    return re.sub(r"[^a-z']", "", w.lower())



def decode(path, ss=None, t=None):
    cmd = ["ffmpeg", "-nostdin", "-v", "error"]
    if ss is not None:
        cmd += ["-ss", f"{ss:.3f}"]
    if t is not None:
        cmd += ["-t", f"{t:.3f}"]
    cmd += ["-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, dtype=np.float32)


def words_of(model, audio, prompt):
    segments, info = model.transcribe(audio, word_timestamps=True, initial_prompt=prompt,
                                      condition_on_previous_text=False)
    out = []
    for seg in segments:
        for w in (seg.words or []):
            if w.start is None or w.end is None:
                continue
            out.append({"w": w.word.strip(), "t0": round(float(w.start), 3), "t1": round(float(w.end), 3),
                        "p": round(float(w.probability), 3), "nsp": round(float(seg.no_speech_prob), 3)})
    return out, float(info.duration)


def main():
    track, model_name, out = sys.argv[1:4]
    fill = "--no-fill" not in sys.argv
    from erm.asr import VERBATIM_PROMPT
    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, device="auto", compute_type="auto")
    ws, dur = words_of(model, track, VERBATIM_PROMPT)
    filled, holes = [], []
    if fill and ws:
        x = decode(track)
        hop = SR // 100
        n = len(x) // hop * hop
        env = 20 * np.log10(np.sqrt((x[:n].reshape(-1, hop) ** 2).mean(1)) + 1e-9)
        inw = np.zeros(len(env), dtype=bool)
        for w in ws:
            inw[max(0, int((w["t0"] - 0.15) * 100)):int((w["t1"] + 0.15) * 100) + 1] = True
        level = float(np.percentile(env[inw], 30)) - 8.0
        loud = np.append((env > level) & ~inw, False)
        start = None
        for i, v in enumerate(loud):
            if v and start is None:
                start = i
            elif not v and start is not None:
                if (i - start) / 100 >= 0.3:
                    if holes and start / 100 - holes[-1][1] < 0.3:
                        holes[-1][1] = i / 100
                    else:
                        holes.append([start / 100, i / 100])
                start = None
        for h0, h1 in holes:
            a, b = max(0.0, h0 - 0.4), min(dur, h1 + 0.4)
            seg = decode(track, a, b - a)

            def on_sound(ws_):
                return [{**w, "t0": round(a + w["t0"], 3), "t1": round(a + w["t1"], 3), "filled": True}
                        for w in ws_
                        if h0 - 0.15 <= a + (w["t0"] + w["t1"]) / 2 <= h1 + 0.15
                        and w["p"] >= 0.4 and w["nsp"] < 0.6]

            got = on_sound(words_of(model, seg, VERBATIM_PROMPT)[0])
            plain = on_sound(words_of(model, seg, None)[0])
            # Whisper makes words up on a laugh or a breath — "you for
            # watching", "thank you" — and what it makes up changes with
            # the prompt, while what was said does not. So the words must
            # come back the same with and without the prompt (fillers
            # aside, which only the prompt keeps), and must not be the
            # stock phrases it hallucinates.
            said = [w for w in got if norm(w["w"]) not in FILLERS]
            if [norm(w["w"]) for w in said] != [norm(w["w"]) for w in plain if norm(w["w"]) not in FILLERS]:
                continue
            if not got or {norm(w["w"]) for w in got} <= STOCK or len(got) > 6 * (h1 - h0) + 2:
                continue
            filled += got
        ws = sorted(ws + filled, key=lambda w: w["t0"])
    json.dump({"duration": dur, "words": ws, "filled": len(filled),
               "holes": [[round(h0, 2), round(h1, 2)] for h0, h1 in holes]}, open(out, "w"), indent=1)
    print(json.dumps({"words": len(ws), "holes": len(holes), "filled": len(filled),
                      "filled_words": " ".join(w["w"] for w in filled)}))


if __name__ == "__main__":
    main()
