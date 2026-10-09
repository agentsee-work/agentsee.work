"""
Shared pieces of the episode pipeline. Not a tool; the tools import it.

Paths, JSON, ffprobe, loudness measurement, and the edit-list arithmetic that
three different stages need to agree on. If two tools ever disagreed about
where a source second lands in the output, captions would drift from speech,
so the mapping lives here and nowhere else.
"""

import json
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
EPISODES = ROOT / "build" / "episodes"
CUES = ROOT / "build" / "audio" / "delivery"
RATE = 48000


def die(msg):
    sys.exit(f"error: {msg}")


def need(*tools):
    for t in tools:
        if not shutil.which(t):
            die(f"{t} not found on PATH")


def ep_dir(slug):
    d = EPISODES / slug
    if not d.exists():
        die(f"no episode at {d.relative_to(ROOT)} — run ingest first")
    return d


def load_json(path):
    return json.loads(pathlib.Path(path).read_text())


def save_json(path, data):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def run(cmd, **kw):
    """ffmpeg and friends. Fails loudly with the tail of stderr, which is the
    part that says why."""
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if r.returncode != 0:
        die(f"{cmd[0]} failed:\n" + r.stderr[-1500:])
    return r


def ffprobe(path):
    r = run(["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=index,codec_type,codec_name,sample_rate,"
             "channels,width,height,r_frame_rate,avg_frame_rate,nb_frames",
             "-of", "json", path])
    d = json.loads(r.stdout)
    out = {"duration": float(d["format"]["duration"]), "audio": None, "video": None}
    for s in d.get("streams", []):
        if s["codec_type"] == "audio" and out["audio"] is None:
            out["audio"] = {"codec": s["codec_name"], "rate": int(s["sample_rate"]),
                            "channels": int(s["channels"])}
        if s["codec_type"] == "video" and out["video"] is None:
            num, den = s["r_frame_rate"].split("/")
            anum, aden = s["avg_frame_rate"].split("/")
            fps = int(num) / int(den)
            afps = int(anum) / int(aden) if int(aden) else fps
            out["video"] = {"codec": s["codec_name"], "width": s["width"],
                            "height": s["height"], "fps": fps,
                            "cfr": abs(fps - afps) < 1e-3,
                            "frames": int(s.get("nb_frames") or 0)}
    return out


def measure(path, target=-16.0, tp=-1.0):
    """Integrated loudness, true peak and range of a file, by actually
    scanning it. loudnorm's first pass is the scanner; its forecast of the
    output is not used for anything, because it is pessimistic — see
    cues.py — and this pipeline only reports what it measured."""
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-i", str(path),
         "-af", f"loudnorm=I={target}:TP={tp}:LRA=11:print_format=json",
         "-f", "null", "-"], capture_output=True, text=True)
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not blocks:
        return None
    m = json.loads(blocks[-1])
    try:
        return {"lufs": float(m["input_i"]), "tp": float(m["input_tp"]),
                "lra": float(m["input_lra"]), "thresh": float(m["input_thresh"]),
                "offset": float(m["target_offset"])}
    except (ValueError, KeyError):
        return None


def loudnorm_filter(m, target=-16.0, tp=-1.0):
    """Second-pass, linear loudnorm from a first-pass measurement."""
    return (f"loudnorm=I={target}:TP={tp}:LRA=11:linear=true:"
            f"measured_I={m['lufs']}:measured_TP={m['tp']}:"
            f"measured_LRA={m['lra']}:measured_thresh={m['thresh']}:"
            f"offset={m['offset']}")


# Gentle levelling for cut dialogue, applied once before the limiter. A
# brickwall limiter alone, with raw speech peaking fifteen to twenty dB over
# its loudness, worked five or six dB on every loud syllable with a 60 ms
# release — heard as the level ducking and recovering at every onset, and
# most of all at a cut, where a quiet tail meets a loud onset. Two to one
# from -14 dBFS (RMS), 20 ms in and 400 ms out, moved the gain least from
# one 50 ms window to the next of the four chains measured on episode 0,
# and leaves the limiter the odd plosive. It runs on the dialogue *after*
# the cut, so its gain state flows across every join instead of being
# frozen at whatever it was on either side of the material removed.
COMPRESSOR = "acompressor=threshold=0.2:ratio=2:attack=20:release=400:knee=6:makeup=1:detection=rms"


def level_to(src, dst, target=-16.0):
    """A plain gain to the target loudness, into a float file. Nothing is
    limited or levelled here: peaks over full scale are kept as they are
    for the stage that cuts the dialogue and then controls them."""
    m = measure(src, target, -1.0)
    if m is None:
        die(f"could not measure {src}")
    run(["ffmpeg", "-nostdin", "-y", "-i", src, "-af", f"volume={target - m['lufs']:.2f}dB",
         "-ar", RATE, "-c:a", "pcm_f32le", dst])
    return measure(dst, target, -1.0)


def normalise(src, dst, target=-16.0, tp=-1.0, limiter=False, compress=False):
    """Loudness-normalise a file to a file, then measure the file.

    loudnorm's linear mode is the one that leaves dynamics alone, and it
    gives up — switching to its dynamic mode, which lands a dB or two short
    — whenever the gain it needs would push the true peak past the ceiling.
    Raw dialogue needs ten or twenty dB and its peaks would. So with
    `limiter`, the gain is applied first and the peaks caught at a dB below
    the ceiling in a pass of their own, and only the small residual is left
    for the linear pass, which then has the headroom to stay linear."""
    m = measure(src, target, tp)
    if m is None:
        die(f"could not measure {src}")
    work = src
    tmps = [pathlib.Path(dst).with_suffix(f".limited{i}.wav") for i in range(3)]
    if limiter:
        # alimiter's limit is linear and sample-peak: a "dB" suffix is not an
        # error, it is a limit of 1.0, and intersample peaks overshoot a
        # sample limit by up to a dB. So: linear, well under the true-peak
        # ceiling, no auto-levelling, and repeated until the residual gain
        # the linear pass needs will keep the true peak under the ceiling.
        lin = 10 ** ((tp - 2.5) / 20)
        for i in range(3):
            gain = target - m["lufs"]
            if work is not src and m["tp"] + gain <= tp - 0.05:
                break
            chain = f"volume={gain:.2f}dB"
            if compress and i == 0:
                chain += "," + COMPRESSOR
            chain += f",alimiter=limit={lin:.4f}:level=false:attack=5:release=150"
            run(["ffmpeg", "-nostdin", "-y", "-i", work, "-af", chain,
                 "-ar", RATE, "-c:a", "pcm_s24le", tmps[i]])
            m = measure(tmps[i], target, tp)
            work = tmps[i]
    run(["ffmpeg", "-nostdin", "-y", "-i", work, "-af", loudnorm_filter(m, target, tp),
         "-ar", RATE, "-c:a", "pcm_s24le", dst])
    for t_ in tmps:
        t_.unlink(missing_ok=True)
    return measure(dst, target, tp)


def hms(t, frac=True):
    t = max(0.0, float(t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    if frac:
        return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d}"


def srt_time(t):
    return hms(t).replace(".", ",")


# ── speech ────────────────────────────────────────────────────────────────────

def speech_intervals(words, merge_gap=0.3, pad=0.0, limit=None):
    """Merge word intervals into stretches of speech. `pad` widens each
    stretch so a cut never lands on the edge of a word."""
    ivs = sorted((w["t0"], w["t1"]) for w in words)
    out = []
    for a, b in ivs:
        if out and a - out[-1][1] <= merge_gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    res = []
    for a, b in out:
        a, b = a - pad, b + pad
        if limit:
            a, b = max(limit[0], a), min(limit[1], b)
        if res and a <= res[-1][1]:
            res[-1][1] = max(res[-1][1], b)
        else:
            res.append([a, b])
    return res


# ── edit list ─────────────────────────────────────────────────────────────────
#
# An EDL is a list of items in output order. A `src` item keeps source
# seconds t0..t1; a `card` item inserts `dur` seconds of something that is not
# the recording (a chapter card, with silence under it). Each item records
# `out`, the output second it starts at, which is derived — so it is written
# by cut.py once and read everywhere else rather than recomputed differently.

def edl_finalise(items):
    t = 0.0
    for it in items:
        it["out"] = round(t, 4)
        t += (it["t1"] - it["t0"]) if it["type"] == "src" else it["dur"]
    return round(t, 4)


def src_to_out(edl, t):
    """Where a source second lands in the output, or None if it was cut."""
    for it in edl["items"]:
        if it["type"] == "src" and it["t0"] <= t < it["t1"]:
            return it["out"] + (t - it["t0"])
    return None


def map_words(edl, words, shift=0.0):
    """Words that survive the edit, with output times. A word is judged by
    its middle: a removed filler whose span starts a few ms before the cut
    is still removed, and must not reach the captions. Edges that fell in
    a cut are pulled to the middle."""
    out = []
    for w in words:
        mid = (w["t0"] + w["t1"]) / 2
        om = src_to_out(edl, mid)
        if om is None:
            continue
        o0 = src_to_out(edl, w["t0"])
        o1 = src_to_out(edl, max(w["t0"], w["t1"] - 1e-3))
        if o0 is None or o0 > om:
            o0 = om - min(mid - w["t0"], 0.15)
        if o1 is None or o1 < om:
            o1 = om + min(w["t1"] - mid, 0.15)
        out.append({**w, "t0": round(o0 + shift, 3), "t1": round(o1 + shift, 3)})
    return out


# ── captions ──────────────────────────────────────────────────────────────────

CAPTION_FILLERS = {"um", "uh", "er", "erm", "ah", "hmm", "mm", "mhm", "huh"}


def caption_words(words):
    """The words worth captioning. Fillers are not. Nor is a brief
    interjection — three words at most, under 1.2 s — from the other person
    while a speaker holds the floor: captioned, it broke the speaker's
    sentence into one-word cues on either side of a one-word cue."""
    import re as _re
    ws = [w for w in words if _re.sub(r"[^a-z]", "", w["w"].lower()) not in CAPTION_FILLERS]
    runs = []
    for w in ws:
        if runs and runs[-1][0] == w.get("spk"):
            runs[-1][1].append(w)
        else:
            runs.append([w.get("spk"), [w]])
    out = []
    for k, (spk, run) in enumerate(runs):
        brief = len(run) <= 3 and run[-1]["t1"] - run[0]["t0"] < 1.2
        if brief and 0 < k < len(runs) - 1 and runs[k - 1][0] == runs[k + 1][0]:
            before, after = runs[k - 1][1][-1], runs[k + 1][1][0]
            if run[0]["t0"] - before["t1"] < 1.0 and after["t0"] - run[-1]["t1"] < 1.0:
                continue
        out.extend(run)
    return out


def caption_cues(words, max_chars=42, max_lines=2, max_dur=5.0, gap=0.7, fill=0.0):
    """Group words into caption cues. A cue never spans a speaker change, a
    long silence, or more than `max_lines` of `max_chars`. With `fill`, a
    sentence end only closes a cue that already holds that fraction of the
    room, so the cues come out two lines tall rather than one and a bit."""
    cues, cur = [], []

    def flush():
        if cur:
            cues.append(list(cur))
            cur.clear()

    for w in words:
        if cur:
            last = cur[-1]
            text = " ".join(x["w"] for x in cur) + " " + w["w"]
            # with `fill` the cue is meant for a balanced two-line wrap,
            # so it is full when that wrap would spill a line, not at a
            # character count the greedy wrap would then break into three
            full = (len(wrap_two(text, max_chars)) > 2 if fill and max_lines == 2
                    else len(text) > max_chars * max_lines)
            if (w.get("spk") != last.get("spk") or w["t0"] - last["t1"] > gap
                    or w["t1"] - cur[0]["t0"] > max_dur
                    or full
                    or re.search(r"[.?!]$", last["w"])
                    and len(text) > max(max_chars, fill * max_chars * max_lines)):
                flush()
        cur.append(w)
    flush()
    return cues


def wrap_two(text, max_chars=26):
    """Two lines as even as the words allow; one line for a single word."""
    words = text.split()
    if len(words) < 2:
        return [text] if text else []
    best = None
    for k in range(1, len(words)):
        a, b = " ".join(words[:k]), " ".join(words[k:])
        if best is None or abs(len(a) - len(b)) < best[0]:
            best = (abs(len(a) - len(b)), [a, b])
    if max(len(ln) for ln in best[1]) > max_chars:
        return wrap_lines(text, max_chars)
    return best[1]


def wrap_lines(text, max_chars=42):
    lines, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > max_chars:
            lines.append(cur)
            cur = word
        else:
            cur = (cur + " " + word).strip()
    if cur:
        lines.append(cur)
    return lines


def write_srt(cues, path, max_chars=42):
    lines = []
    for i, cue in enumerate(cues, 1):
        text = " ".join(w["w"] for w in cue)
        lines += [str(i), f"{srt_time(cue[0]['t0'])} --> {srt_time(cue[-1]['t1'])}",
                  "\n".join(wrap_lines(text, max_chars)), ""]
    pathlib.Path(path).write_text("\n".join(lines))


# ── audio, for cutting ────────────────────────────────────────────────────────
#
# Word timing from Whisper is good to about 50 ms, which is enough to know
# where a word is and not enough to cut next to one. These measure the audio
# itself: an energy envelope at 2 ms, and a cut that lands on the quietest
# moment near where the words said to cut.

ENV_HOP = 96   # samples at 48 kHz: 2 ms


def dialogue_sum(d, manifest, highpass_hz=80.0):
    """Every participant's track, offset onto the shared timeline and
    summed, mono. What the listener hears before any treatment."""
    import math
    import numpy as np
    import soundfile as sf
    from scipy.signal import butter, sosfiltfilt
    n = int(math.ceil(manifest["duration"] * RATE))
    bus = np.zeros(n)
    for p in manifest["participants"]:
        x, r = sf.read(str(d / p["audio"]["file"]), dtype="float64", always_2d=True)
        if r != RATE:
            die(f"{p['audio']['file']} is {r} Hz")
        x = x.mean(axis=1)
        if highpass_hz:
            x = sosfiltfilt(butter(2, highpass_hz, "hp", fs=RATE, output="sos"), x)
        off = int(round(p["offset"] * RATE))
        seg = x[:max(0, n - off)]
        bus[off:off + len(seg)] += seg
    return bus


def envelope_db(x, hop=ENV_HOP):
    """RMS per hop, in dBFS."""
    import numpy as np
    n = len(x) // hop
    frames = x[:n * hop].reshape(n, hop)
    rms = np.sqrt((frames ** 2).mean(axis=1)) + 1e-9
    return 20 * np.log10(rms)


def snap_quiet(env, t, before, after, hop=ENV_HOP):
    """The quietest moment in [t+before, t+after], as seconds. `before` is
    negative. Searching asymmetrically lets a cut prefer the inside of a
    filler to the outside of the word next to it."""
    import numpy as np
    i0 = max(0, int((t + before) * RATE / hop))
    i1 = min(len(env) - 1, int((t + after) * RATE / hop))
    if i1 <= i0:
        return t
    return (i0 + int(np.argmin(env[i0:i1 + 1]))) * hop / RATE


def cut_with_crossfades(x, items, xf=0.02):
    """Concatenate kept stretches of a mono track with a short equal-power
    crossfade at every join, so no cut clicks and none is heard as a cut.

    Each kept stretch is taken with half the fade extra on both sides and
    the extras overlap, so the result is exactly as long as the stretches
    add up to — which is what the video, cut hard at the same points,
    expects. A `card` item is silence of its length, with the fades on
    either side of it. An item may name its own fades, `xf_in` and
    `xf_out`: cut.py gives a join across a pause or a chapter seam, where
    both sides are quiet, a longer one than a join against a word."""
    import math
    import numpy as np

    def ramp(k):
        return np.sin(np.linspace(0, math.pi / 2, k)) ** 2          # equal-power

    total = sum((it["t1"] - it["t0"]) if it["type"] == "src" else it["dur"] for it in items)
    out = np.zeros(int(round(total * RATE)) + int(0.5 * RATE))
    pos = 0
    for it in items:
        if it["type"] == "src":
            n_in = int(float(it.get("xf_in", xf)) * RATE)
            n_out = int(float(it.get("xf_out", xf)) * RATE)
            a = int(round(it["t0"] * RATE)) - n_in // 2
            b = int(round(it["t1"] * RATE)) + n_out // 2
            seg = np.zeros(b - a)
            lo, hi = max(0, a), min(len(x), b)
            seg[lo - a:hi - a] = x[lo:hi]
            if n_in:
                seg[:n_in] *= ramp(n_in)
            if n_out:
                seg[-n_out:] *= ramp(n_out)[::-1]
            start = pos - n_in // 2
            if start < 0:
                seg, start = seg[-start:], 0
            out[start:start + len(seg)] += seg
            pos += int(round(it["t1"] * RATE)) - int(round(it["t0"] * RATE))
        else:
            pos += int(round(it["dur"] * RATE))
    return out[:int(round(total * RATE))]
