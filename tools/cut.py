#!/usr/bin/env python3
"""
Decide the edit, as data.

    ./tools/cut.py ep0
    ./tools/cut.py ep0 --chapters build/episodes/ep0/chapters.json
    ./tools/cut.py ep0 --start 12.4 --end 540.2          # trim by hand
    ./tools/cut.py ep0 --slate-in "rolling" --slate-out "that's a wrap"
    ./tools/cut.py ep0 --no-fillers --max-pause 1.5 --pause 0.6   # the loose cut
    ./tools/cut.py ep0 --show                              # print the EDL

Reads the transcript and the audio, and writes build/episodes/<slug>/edl.json:
the list of source stretches that survive, in order, with chapter cards
between them. Nothing here touches media. assemble.py renders what this
decides, so the decision can be read, argued with and edited before anything
is rendered, and re-rendered from the same file afterwards.

What it removes, and how it decides:

  Fillers.   um, uh, erm and their spellings, as Whisper heard them. A
             filler is cut out with the silence around it closed to --pause.
  Stammers.  A word said twice in a row, or a word that is the start of the
             next one ("start started", "that that's"). The first goes; the
             speaker lands on the last.
  Pauses.    Silence longer than --max-pause, closed to --pause. Silence is
             measured on the audio, not inferred from word times, because
             Whisper stretches a word's end across the pause that follows it.
  Head/tail. A spoken slate, or the first and last word, or --start/--end.

Every cut lands on the quietest moment near where the words said to cut,
found on a 2 ms energy envelope of the summed dialogue, and assemble.py
crossfades 20 ms across each join. That is what makes a cut inaudible: not
where the transcript thinks a word ends, but where the sound actually does.

Chapters come from a chapters.json you write (or Riverside's guesses in the
manifest) and become cards in the nearest seam after the chapter time; with
no seam within --chapter-window the chapter is a marker only.
"""

import argparse
import pathlib
import re
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from eplib import (ENV_HOP, RATE, ROOT, dialogue_sum, die, edl_finalise, ep_dir,  # noqa: E402
                   envelope_db, hms, load_json, save_json, snap_quiet, src_to_out)

BUTTON_LEN = 2.7   # 04-button's first phrase; see AGE-62 for why not 5.0
FILLERS = {"um", "uh", "erm", "er", "hmm", "mm", "mhm", "ah", "uhm", "umm", "uhh",
           "ehm", "hm", "mmm", "huh"}
HOP = ENV_HOP / RATE
REASON = {1: "filler", 2: "stammer", 3: "pause", 4: "chapter", 5: "manual", 7: "um (verbatim)"}


def tok(w):
    return re.sub(r"[^a-z0-9']", "", w.lower())


ELONGATED = re.compile(r"^(u+m+|u+h+|e+r+m*|a+h+|h+m+|m+h*m+|e+h+m*)$")


def is_filler(w):
    x = tok(w)
    return x in FILLERS or bool(ELONGATED.match(x))


def find_phrase(words, phrase, from_end=False):
    target = [tok(t) for t in phrase.split()]
    toks = [tok(w["w"]) for w in words]
    n = len(target)
    rng = range(len(toks) - n, -1, -1) if from_end else range(0, len(toks) - n + 1)
    for i in rng:
        if toks[i:i + n] == target:
            return i, i + n - 1
    return None


def place_chapter(t, words, window_before=6.0, window_after=25.0, min_gap=0.25):
    """A seam near the chapter time: a gap between words with nobody talking
    across it. Prefers one after a sentence end, then the longest."""
    ws = sorted(words, key=lambda w: w["t0"])
    cands = []
    for a, b in zip(ws, ws[1:]):
        g0, g1 = a["t1"], b["t0"]
        if g1 - g0 < min_gap or not (t - window_before <= g0 <= t + window_after):
            continue
        if any(o["t0"] < g1 and o["t1"] > g0 for o in ws if o is not a and o is not b):
            continue
        sentence = bool(re.search(r"[.?!]$", a["w"]))
        cands.append((sentence, min(g1 - g0, 1.0), -abs(g0 - t), g0, g1))
    if not cands:
        return None
    best = max(cands)
    return best[3], best[4]


def segment_of(words, segments):
    """Which transcript segment each word came from. Whisper sometimes emits
    the last word of one segment again as the first of the next — one spoken
    word, two tokens — and that must not read as a stammer."""
    segs = sorted(segments, key=lambda s: s["t0"])
    ids = []
    for w in words:
        mid = (w["t0"] + w["t1"]) / 2
        # by the middle of the word, with no tolerance: a tolerance put the
        # second half of a split word into the first word's segment
        hit = next((k for k, s in enumerate(segs)
                    if s["spk"] == w["spk"] and s["t0"] <= mid < s["t1"]), None)
        ids.append(hit)
    return ids


def fillers_and_stammers(words, segments, extra, do_fillers, do_stammers):
    """Word indexes to remove, with a reason code."""
    out = {}
    toks = [tok(w["w"]) for w in words]
    seg = segment_of(words, segments)
    if do_fillers:
        for i, (w, t) in enumerate(zip(words, toks)):
            if (t in FILLERS or t in extra) and 0.05 <= w["t1"] - w["t0"] <= 1.5:
                out[i] = 1
    if do_stammers:
        for i in range(len(words) - 1):
            a = toks[i]
            w = words[i]
            if not a or a in FILLERS:
                continue
            # the repeat may come after a filler: "quite, um, quite"
            j = i + 1
            while j < len(words) and words[j]["spk"] == w["spk"] and toks[j] in FILLERS:
                j += 1
            if j >= len(words):
                continue
            b, nxt = toks[j], words[j]
            if not b or w["spk"] != nxt["spk"] or nxt["t0"] - w["t1"] > 1.2:
                continue
            if seg[i] is None or seg[i] != seg[j]:
                continue
            if a == b or (len(a) >= 3 and b.startswith(a)):
                out.setdefault(i, 2)
    return out


def runs(mask):
    """(start, end) index pairs of True runs."""
    if not mask.any():
        return []
    d = np.diff(mask.astype(np.int8), prepend=0, append=0)
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--start", type=float, help="source second the show starts (override)")
    ap.add_argument("--end", type=float, help="source second the show ends (override)")
    ap.add_argument("--slate-in", help="phrase spoken just before the show starts")
    ap.add_argument("--slate-out", help="phrase spoken just after the show ends")
    ap.add_argument("--pad", type=float, default=0.25, help="seconds kept before the first and after the last word")
    ap.add_argument("--max-pause", type=float, default=0.7, help="silence longer than this is closed")
    ap.add_argument("--pause", type=float, default=0.4, help="what a closed silence becomes")
    ap.add_argument("--fillers", choices=["all", "clean", "off"], default="all",
                    help="clean: only where both edges of the cut are quiet")
    ap.add_argument("--stammers", choices=["all", "clean", "off"], default="all",
                    help="clean: only where both edges of the cut are quiet")
    ap.add_argument("--no-fillers", action="store_true", help="same as --fillers off")
    ap.add_argument("--no-stammers", action="store_true", help="same as --stammers off")
    ap.add_argument("--removals", help="removals.json: [{phrase, near} | {t0, t1}], editorial cuts; "
                         "default is the episode's removals.json if it exists")
    ap.add_argument("--out", help="where to write the EDL (default edl.json)")
    ap.add_argument("--quiet-join-db", type=float, default=8.0,
                    help="an edge counts as quiet within this many dB of the silence threshold")
    ap.add_argument("--no-erm", action="store_true", help="ignore erm/<short>.json even if present")
    ap.add_argument("--erm-passes", default="gap,in,long",
                    help="which of erm's acoustic detectors to take, outside any transcribed word: "
                         "gap, in (inside a word), long (trailing vowel)")
    ap.add_argument("--gap-clear", type=float, default=0.12,
                    help="an untokened sound between two words must sit this far (s) from both; "
                         "smaller catches a quick um before a vowel, at some risk to the vowel")
    ap.add_argument("--clean-ms", type=float, default=40.0,
                    help="clean mode: silence needed at each edge of a cut, in ms")
    ap.add_argument("--fillers-extra", default="", help="more filler words, comma separated")
    ap.add_argument("--silence-db", type=float, default=28.0,
                    help="how far below the speech level counts as silence")
    ap.add_argument("--silence-above-floor", type=float, default=14.0,
                    help="how far above the noise floor still counts as silence")
    ap.add_argument("--chapters", help="chapters.json: [{title, at: 'first words of the section'"
                         "[, after: 'last words of the one before'][, card: false]}]; "
                         "or {title, t0} in source seconds for the seam-finder")
    ap.add_argument("--no-chapters", action="store_true")
    ap.add_argument("--chapter-window", type=float, default=25.0)
    ap.add_argument("--card-dur", type=float, default=BUTTON_LEN)
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--debug", nargs=2, type=float, metavar=("FROM", "TO"),
                    help="print, per 10 ms of this source span, the reason code, protection, "
                         "silence and level — for working out why a cut landed where it did")
    a = ap.parse_args()

    d = ep_dir(a.slug)
    # the episode's chosen settings (variants.py --choose) stand in for the
    # defaults, so a plain `cut.py <slug>` reproduces the edit that was picked
    chosen = (load_json(d / "episode.json").get("cut") or {}) if (d / "episode.json").exists() else {}
    for key, val in chosen.items():
        if getattr(a, key, None) == ap.get_default(key):
            setattr(a, key, val)
    if chosen:
        print("  settings from episode.json: " + ", ".join(f"{k}={v}" for k, v in chosen.items()))
    if a.no_fillers:
        a.fillers = "off"
    if a.no_stammers:
        a.stammers = "off"
    edl_path = pathlib.Path(a.out) if a.out else d / "edl.json"
    if a.show:
        edl = load_json(edl_path)
        for it in edl["items"]:
            if it["type"] == "src":
                print(f"  {hms(it['out'])}  src {hms(it['t0'])} - {hms(it['t1'])}  "
                      f"({it['t1'] - it['t0']:.2f}s)")
            else:
                print(f"  {hms(it['out'])}  card {it['dur']}s  {it['title']}")
        s = edl["stats"]
        print(f"  {hms(edl['duration'])}  end  ({edl['duration']:.1f}s from "
              f"{edl['source_seconds']:.1f}s: {s['fillers']} fillers, {s['stammers']} stammers, "
              f"{s['pauses']} pauses, {edl['removed_seconds']:.1f}s removed in {len(edl['removed'])} cuts)")
        return 0

    m = load_json(d / "manifest.json")
    t = load_json(d / "transcript" / "words.json")
    words, segments = t["words"], t["segments"]
    if not words:
        die("no words in the transcript")

    # ── the audio, as an envelope ──────────────────────────────────────────
    print("  measuring the audio", flush=True)
    env = envelope_db(dialogue_sum(d, m))
    n = len(env)
    # each speaker alone, for "is the other person actually making a sound here"
    sig_spk = {p["short"]: dialogue_sum(d, {**m, "participants": [p]}) for p in m["participants"]}
    env_spk = {k: envelope_db(v)[:n] for k, v in sig_spk.items()}
    in_word = np.zeros(n, dtype=bool)
    for w in words:
        in_word[int(w["t0"] / HOP):int(w["t1"] / HOP) + 1] = True
    speech_level = float(np.percentile(env[in_word], 70)) if in_word.any() else -20.0
    noise = float(np.percentile(env, 10))
    # Two mics' room tone and breathing sit well above the floor, so "silence"
    # is relative to the floor by a margin, not to the speech by a bigger one.
    # On episode 0: speech -37, floor -62, pauses around -48.
    thr = max(noise + a.silence_above_floor, speech_level - a.silence_db)
    silent = env < thr
    # each speaker's own idea of "sound", for their own track: a soft
    # interjection over the other person sits far below the summed track's
    # threshold and is still a sound on its own track
    # The line sits halfway between the quiet end of their speech (30th
    # percentile inside their words) and the loud end of their silence
    # (97th percentile of their track while the other person talks).
    # Episode 0: James -52, Abrar -63; the summed track's line is -48.
    loud_spk = {}
    for p in m["participants"]:
        e = env_spk[p["short"]]
        mine = np.zeros(n, dtype=bool)
        theirs = np.zeros(n, dtype=bool)
        for w in words:
            (mine if w["spk"] == p["short"] else theirs)[int(w["t0"] / HOP):int(w["t1"] / HOP) + 1] = True
        quiet = theirs & ~mine
        if mine.any() and quiet.any():
            loud_spk[p["short"]] = (float(np.percentile(e[mine], 30)) + float(np.percentile(e[quiet], 97))) / 2
        else:
            loud_spk[p["short"]] = thr + 4.0

    # Speech-level sound a speaker makes with no word for it — a laugh, a
    # reply Whisper dropped — counts as their speech for every guard below.
    # Trusting the transcript alone cut a whole reply out from under the
    # other person's um.
    talk_spk = {}
    for p in m["participants"]:
        e = env_spk[p["short"]]
        mine = np.zeros(n, dtype=bool)
        for w in words:
            if w["spk"] == p["short"]:
                mine[int(w["t0"] / HOP):int(w["t1"] / HOP) + 1] = True
        peaks = [float(e[int(w["t0"] / HOP):int(w["t1"] / HOP) + 1].max()) for w in words
                 if w["spk"] == p["short"] and int(w["t1"] / HOP) + 1 > int(w["t0"] / HOP)]
        line = (float(np.percentile(peaks, 30)) - 8.0) if peaks else thr
        arr = np.zeros(n, dtype=bool)
        for a0, a1 in runs(e > line):
            if (a1 - a0) * HOP >= 0.3:
                arr[max(0, a0 - int(0.1 / HOP)):a1 + int(0.1 / HOP)] = True
        talk_spk[p["short"]] = arr
    other_talk = {k: (np.any([v for o, v in talk_spk.items() if o != k], axis=0)
                      if len(talk_spk) > 1 else np.zeros(n, dtype=bool)) for k in talk_spk}
    other_busy = {}
    for k in talk_spk:
        busy = other_talk[k].copy()
        for w in words:
            if w["spk"] != k and tok(w["w"]) not in FILLERS:
                busy[int(w["t0"] / HOP):int(w["t1"] / HOP) + 1] = True
        other_busy[k] = busy

    # ── head and tail ─────────────────────────────────────────────────────
    head, tail = words[0]["t0"], words[-1]["t1"]
    how_head, how_tail = "first word", "last word"
    if a.slate_in:
        hit = find_phrase(words, a.slate_in)
        if hit:
            nxt = next((w for w in words[hit[1] + 1:]), None)
            head, how_head = (nxt["t0"] if nxt else words[hit[1]]["t1"]), f"slate '{a.slate_in}'"
        else:
            print(f"  slate-in '{a.slate_in}' not found")
    if a.slate_out:
        hit = find_phrase(words, a.slate_out, from_end=True)
        if hit:
            prv = words[hit[0] - 1] if hit[0] > 0 else None
            tail, how_tail = (prv["t1"] if prv else words[hit[0]]["t0"]), f"slate '{a.slate_out}'"
        else:
            print(f"  slate-out '{a.slate_out}' not found")
    if a.start is not None:
        head, how_head = a.start, "--start"
    if a.end is not None:
        tail, how_tail = a.end, "--end"
    head = max(0.0, head - a.pad)
    tail = min(m["duration"], tail + a.pad)
    H, T = int(head / HOP), min(n, int(tail / HOP))
    kept = [i for i, w in enumerate(words) if head <= w["t0"] and w["t1"] <= tail]

    # ── fillers and stammers: removed, edges snapped to the quiet ─────────
    extra = {tok(x) for x in a.fillers_extra.split(",") if x.strip()}
    drop = fillers_and_stammers(words, segments, extra, a.fillers != "off", a.stammers != "off")
    reason = np.zeros(n, dtype=np.int8)

    # Words that stay are protected: no rule may remove a hop inside one.
    # Not their spans — Whisper hands the pause on either side of a word to
    # the word, so spans cover most of the silence — but the sound inside
    # the span, found by a lower threshold so soft words count, widened by
    # 30 ms. This is what stops the pause rule eating "them" or "the".
    from scipy.ndimage import maximum_filter1d
    in_span = np.zeros(n, dtype=bool)
    # just under the silence threshold: on episode 0 only two words have
    # their loudest moment below that, and room tone mostly sits under it
    sound = env > thr - 2.0
    for i, w in enumerate(words):
        if i in drop or i not in kept:
            continue
        in_span[max(0, int((w["t0"] - 0.03) / HOP)):min(n, int((w["t1"] + 0.03) / HOP))] = True

    def word_sound(mask):
        """Contiguous sound of 40 ms or more, widened by 30 ms each side. A
        stray hop of room tone is not a word, and must not split a pause."""
        keep_ = np.zeros(n, dtype=bool)
        for r0, r1 in runs(mask):
            if (r1 - r0) * HOP >= 0.04:
                keep_[r0:r1] = True
        return maximum_filter1d(keep_.astype(np.int8), int(0.06 / HOP)) > 0

    protected = word_sound(in_span & sound)

    def plausible(w):
        return 0.15 + 0.08 * max(1, len(tok(w["w"])))

    skipped = []
    removed_words = {}
    quiet_edge = thr + a.quiet_join_db
    quiet_hops = max(2, int(0.03 / HOP))

    def edge_db(tt):
        i = int(tt / HOP)
        return float(env[max(0, i - 3):i + 4].mean())

    from scipy.ndimage import uniform_filter1d
    env_spk_smooth = {k: uniform_filter1d(v, 10) for k, v in env_spk.items()}

    def valley(spk, t_lo, t_hi, depth=4.0):
        """The boundary between two sounds that never fall silent: the
        lowest point of the smoothed envelope in [t_lo, t_hi], if it sits
        at least `depth` dB below the peaks on both sides. A vowel running
        into an um dips at the transition even when nothing goes quiet."""
        sm = env_spk_smooth[spk]
        j0, j1 = max(0, int(t_lo / HOP)), min(n, int(t_hi / HOP))
        if j1 - j0 < 10:
            return None
        k = j0 + int(np.argmin(sm[j0:j1]))
        if sm[j0:k + 1].max() - sm[k] >= depth and sm[k:j1].max() - sm[k] >= depth:
            return k * HOP
        return None

    def other_word(spk, t0_, t1_):
        """Does anyone else have a word inside [t0_, t1_]? With aligned words
        this is the honest guard: a backchannel hum under an um is sound,
        and not worth keeping; a word is."""
        return bool(other_busy[spk][max(0, int(t0_ / HOP)):int(t1_ / HOP)].any())

    def clear_of_others(spk, i0, i1, margin=0.05):
        """The part of [i0, i1) the other person is not talking over, if
        the speaker's own sound has stopped by its edge: an um that ends
        just before the other person comes in can still go, in full."""
        busy = other_busy[spk][i0:i1]
        if not busy.any():
            return i0, i1
        e = env_spk[spk]
        quiet = sound_line(spk, i0 * HOP, i1 * HOP)
        m_ = int(margin / HOP)
        b0 = i0 + int(np.argmax(busy))
        b1 = i1 - int(np.argmax(busy[::-1]))
        best = None
        if (b0 - m_) - i0 >= int(0.12 / HOP):
            end = int(snap_quiet(e, (b0 - m_) * HOP, -0.08, 0.0) / HOP)
            if end > i0 and e[max(i0, end - 2):end + 1].max() < quiet:
                best = (i0, end)
        if i1 - (b1 + m_) >= int(0.12 / HOP) and (best is None or i1 - (b1 + m_) > best[1] - best[0]):
            start = int(snap_quiet(e, (b1 + m_) * HOP, 0.0, 0.08) / HOP)
            if start < i1 and e[start:min(i1, start + 3)].max() < quiet:
                best = (start, i1)
        return best

    def other_sound(spk, t0_, t1_):
        """Does anyone else make 40 ms of sound inside [t0_, t1_]?"""
        i0, i1 = max(0, int(t0_ / HOP)), min(n, int(t1_ / HOP))
        for other, e in env_spk.items():
            if other != spk and (e[i0:i1] > loud_spk[other]).sum() * HOP >= 0.04:
                return True
        return False

    def in_silence(i):
        """A cut point counts as quiet if at least --clean-ms of real
        silence — below the threshold itself, not merely near it — sits
        within 60 ms of it. A momentary dip between two voiced words
        ("focus was") is not silence, and a cut there shows."""
        need, reach = max(1, int(a.clean_ms / 1000 / HOP)), int(0.06 / HOP)
        lo, hi = max(0, i - reach), min(n, i + reach + 1)
        return int(silent[lo:hi].sum()) >= need

    # editorial removals: a phrase near a time, or explicit seconds
    rem_path = pathlib.Path(a.removals) if a.removals else d / "removals.json"
    manual = []
    keeps = []
    if rem_path.exists():
        for r in load_json(rem_path):
            if r.get("keep"):
                # the editor says this must not be touched by any rule
                keeps.append((float(r["t0"]), float(r["t1"]), r.get("why", "keep")))
                continue
            if "phrase" in r:
                best = None
                for i in range(len(words)):
                    hit = find_phrase(words[i:], r["phrase"])
                    if hit and hit[0] == 0:
                        t0_ = words[i]["t0"]
                        if best is None or abs(t0_ - r.get("near", t0_)) < abs(best[0] - r.get("near", t0_)):
                            best = (t0_, words[i + hit[1]]["t1"], r["phrase"])
                if best:
                    manual.append(best)
                else:
                    print(f"  removal '{r['phrase']}' not found")
            else:
                manual.append((float(r["t0"]), float(r["t1"]), r.get("why", "manual")))
    for t0_, t1_, what in manual:
        i0, i1 = int(snap_quiet(env, t0_, -0.06, 0.06) / HOP), int(snap_quiet(env, t1_, -0.06, 0.06) / HOP)
        if i1 > i0:
            reason[i0:i1] = 5
            print(f"  removing '{what}' {hms(t0_)} - {hms(t1_)} by request")
    for t0_, t1_, what in keeps:
        protected[max(0, int(t0_ / HOP)):min(n, int(t1_ / HOP))] = True
        for i in [i for i, w in enumerate(words) if i in drop and w["t0"] < t1_ and w["t1"] > t0_]:
            drop.pop(i, None)
        print(f"  keeping '{what}' {hms(t0_)} - {hms(t1_)} by request")

    words_aligned = bool(t.get("aligned"))

    def sound_line(spk, lo, hi):
        """What counts as sound on a speaker's track between `lo` and `hi`:
        ten dB under their line, since a soft um is still an um — but never
        under their local floor, which on a track gated at the source rises
        and falls, and once put the whole pause after an um inside it."""
        e = env_spk[spk]
        c0, c1 = max(0, int((lo - 1.0) / HOP)), min(n, int((hi + 1.0) / HOP))
        floor = float(np.percentile(e[c0:c1], 10)) if c1 > c0 else -99.0
        return max(loud_spk[spk] - 10.0, min(loud_spk[spk] - 3.0, floor + 6.0))

    def between_neighbours(spk, lo, hi):
        """The sound on `spk`'s track strictly between two aligned words,
        as a (hop, hop) cut, or None. The aligner's word edges are within a
        frame or two of the truth, so what lies between two neighbours is
        the thing to remove: a filler, or a stammered first try."""
        e = env_spk[spk]
        # the aligner's spans are character spikes: a word's sound runs on
        # past its last spike and starts before its first by up to 80 ms,
        # so that much on either side belongs to the neighbours
        r0, r1 = lo + 0.08, hi - 0.08
        w0, w1 = max(0, int(r0 / HOP)), min(n, int(r1 / HOP))
        if w1 - w0 < 10:
            return None
        loud = e[w0:w1] > sound_line(spk, lo, hi)
        st = [(a0 + w0, a1 + w0) for a0, a1 in runs(loud) if (a1 - a0) * HOP >= 0.04]
        if not st:
            return None
        i0 = max(w0, int(snap_quiet(e, st[0][0] * HOP, -0.04, 0.01) / HOP))
        i1 = min(w1, int(snap_quiet(e, st[-1][1] * HOP, -0.01, 0.04) / HOP))
        return (i0, i1) if (i1 - i0) * HOP >= 0.05 else None

    # ── verbatim fillers ──────────────────────────────────────────────────
    # erm's transcript hears the ums as words and times the words either
    # side. Each filler token becomes a cut between its neighbours: start
    # and end snapped to the quietest moment within 60 ms, never past the
    # previous word's end or the next word's start (erm's own rule), taken
    # only if the other speaker makes no sound there. This is the better
    # evidence, so it overrides the neighbours' protection inside the cut.
    filler_how = {}
    verbatim_regions = []
    erm_dir = d / "erm"
    if (erm_dir.exists() or words_aligned) and not a.no_erm:
        for p in m["participants"]:
            if words_aligned:
                # words.json is the aligned verbatim transcript already
                aligned = True
                vw = [w for w in words if w["spk"] == p["short"]]
            else:
                f = erm_dir / f"{p['short']}.aligned.json"
                aligned = f.exists()
                if not aligned:
                    f = erm_dir / f"{p['short']}.words.json"
                if not f.exists():
                    continue
                vw = [{"w": x["w"], "t0": x["t0"] + p["offset"], "t1": x["t1"] + p["offset"]}
                      for x in load_json(f)["words"] if x["w"].strip() and x.get("aligned", True)]
            k = 0
            while k < len(vw):
                if not is_filler(vw[k]["w"]):
                    k += 1
                    continue
                j = k
                while j + 1 < len(vw) and is_filler(vw[j + 1]["w"]) and vw[j + 1]["t0"] - vw[j]["t1"] < 0.4:
                    j += 1
                ts, te = vw[k]["t0"], vw[j]["t1"]
                n_tokens = j - k + 1
                lo = vw[k - 1]["t1"] if k > 0 and ts - vw[k - 1]["t1"] < 2.0 else ts - 0.35
                hi = vw[j + 1]["t0"] if j + 1 < len(vw) else m["duration"]
                k = j + 1
                if not (head <= ts and te <= tail) or te - ts > 2.5:
                    filler_how["too long or outside"] = filler_how.get("too long or outside", 0) + 1
                    continue
                e = env_spk[p["short"]]
                if aligned:
                    # The aligner's spans are character spikes, not acoustic
                    # extents, so the filler token itself is tiny. What it
                    # fixes is the neighbours: the previous word's last spike
                    # and the next word's first spike are within a frame or
                    # two of the truth, where Whisper was half a second out.
                    # The um is the sound between them.
                    cut_ = between_neighbours(p["short"], lo, hi)
                    if cut_ is None:
                        filler_how["no sound"] = filler_how.get("no sound", 0) + 1
                        continue
                    i0, i1 = cut_
                    trimmed = clear_of_others(p["short"], i0, i1)
                    if trimmed is None:
                        filler_how["other speaker talking"] = filler_how.get("other speaker talking", 0) + 1
                        skipped.append((ts, vw[k - n_tokens]["w"] if k - n_tokens >= 0 else "um", "filler",
                                        f"the other speaker is talking over it ({hms(i0 * HOP)} - {hms(i1 * HOP)})"))
                        continue
                    was = (i0, i1)
                    i0, i1 = trimmed
                    if a.fillers == "clean" and not (in_silence(i0) and in_silence(i1)):
                        filler_how["join on sound"] = filler_how.get("join on sound", 0) + 1
                        continue
                    protected[i0:i1] = False
                    reason[i0:i1] = np.where(reason[i0:i1] > 0, reason[i0:i1], 7)
                    verbatim_regions.append((i0 * HOP, i1 * HOP))
                    key = "cut" if trimmed == was else "cut, trimmed clear of the other speaker"
                    filler_how[key] = filler_how.get(key, 0) + 1
                    continue
                # Whisper gives the um's sound to the word before and parks
                # the token on the pause after it, so the token span holds
                # mostly silence. The um is the sound between the previous
                # token's end and the um token's end: the first stretch of
                # sound there, and any further stretch that starts well
                # before the token ends — one starting near the end is the
                # next word arriving early.
                # the um often begins a little before the previous token
                # ends — the token's end is late, or the pause between them
                # is short — so look back 150 ms past it for the onset
                w0, w1 = max(0, int((lo - 0.15) / HOP)), min(n, int(te / HOP))
                loud = e[w0:w1] > loud_spk[p["short"]]
                stretches = []
                for r0, r1 in runs(loud):
                    if stretches and (r0 - stretches[-1][1]) * HOP < 0.03:
                        stretches[-1] = (stretches[-1][0], r1)
                    else:
                        stretches.append((r0, r1))
                # every stretch that starts before the token ends and well
                # before the next word begins: a long um has dips inside it,
                # and the next word's onset often arrives a quarter of a
                # second before its own token, and must not count
                bound = min(te - 0.1, hi - 0.25)
                if bound <= lo + 0.04:
                    # the next word follows too closely for the margin; the
                    # token's own end is the only bound left
                    bound = max(lo + 0.04, min(te - 0.05, hi - 0.05))
                stretches = [(r0 + w0, r1 + w0) for r0, r1 in stretches
                             if (r1 - r0) * HOP >= 0.04 and (r0 + w0) * HOP < bound]
                if not stretches:
                    filler_how["no sound"] = filler_how.get("no sound", 0) + 1
                    if a.debug:
                        print(f"  debug: no sound for '{vw[k - 1]['w']}' token {ts:.2f}-{te:.2f} lo {lo:.2f} hi {hi:.2f} "
                              f"bound {bound:.2f} window {w0 * HOP:.2f}-{w1 * HOP:.2f} max dB {float(e[w0:w1].max()) if w1 > w0 else -99:.0f} vs {loud_spk[p['short']]:.0f} "
                              f"raw runs {[(round((r0 + w0) * HOP, 2), round((r1 + w0) * HOP, 2)) for r0, r1 in runs(loud)]}")
                    continue
                if stretches[0][0] <= w0 + 2:
                    # continuous with the previous word. Whisper's boundary
                    # between them is late by up to a quarter of a second,
                    # which leaves the front of the um in. Look for the dip
                    # at the transition; failing that, if the previous
                    # token is longer than its word could be, the overrun
                    # is the um; failing that, lean 40 ms early.
                    v = valley(p["short"], lo - 0.25, lo + 0.15)
                    prev = vw[k - 2] if k >= 2 else None
                    if v is not None:
                        i0 = int(v / HOP)
                    elif prev and (prev["t1"] - prev["t0"]) - plausible(prev) >= 0.15:
                        i0 = int(max(lo - 0.3, prev["t0"] + plausible(prev) * 1.1) / HOP)
                    else:
                        i0 = int((lo - 0.04) / HOP)
                else:
                    i0 = max(w0, int(snap_quiet(e, stretches[0][0] * HOP, -0.04, 0.01) / HOP))
                end_cap = max(lo + 0.06, min(hi - 0.15, te + 0.05))
                if stretches[-1][1] * HOP > end_cap:
                    # the um's tail touches the next word's onset: end the cut
                    # at the quietest moment in the 200 ms before the word
                    i1 = int(snap_quiet(e, end_cap, -0.2, 0.0) / HOP)
                else:
                    i1 = min(int(end_cap / HOP),
                             int(snap_quiet(e, stretches[-1][1] * HOP, -0.01, 0.04) / HOP))
                if (i1 - i0) * HOP < 0.06:
                    filler_how["no sound"] = filler_how.get("no sound", 0) + 1
                    continue
                if other_sound(p["short"], i0 * HOP, i1 * HOP):
                    filler_how["other speaker talking"] = filler_how.get("other speaker talking", 0) + 1
                    continue
                if a.fillers == "clean" and not (in_silence(i0) and in_silence(i1)):
                    filler_how["join on sound"] = filler_how.get("join on sound", 0) + 1
                    continue
                if a.debug and i0 * HOP < a.debug[1] and i1 * HOP > a.debug[0]:
                    print(f"  debug: verbatim '{vw[k - 1]['w']}' token {ts:.2f}-{te:.2f} lo {lo:.2f} hi {hi:.2f} "
                          f"stretches {[(round(x * HOP, 2), round(y * HOP, 2)) for x, y in stretches]} "
                          f"-> cut {i0 * HOP:.2f}-{i1 * HOP:.2f}")
                protected[i0:i1] = False
                reason[i0:i1] = np.where(reason[i0:i1] > 0, reason[i0:i1], 7)
                verbatim_regions.append((i0 * HOP, i1 * HOP))
                filler_how["cut"] = filler_how.get("cut", 0) + 1
        if filler_how:
            print("  verbatim fillers: " + ", ".join(f"{v} {k}" for k, v in filler_how.items()))

    # ── sound between two aligned words with no token for it ─────────────
    # The verbatim transcript misses some ums outright. Between two aligned
    # words of one speaker, a quarter-second gap that contains sound and no
    # word of anyone else holds something that was said and not written
    # down — an um, a voiced breath, a laugh — and it goes.
    n_gap = 0
    if words_aligned and a.fillers != "off":
        for p in m["participants"]:
            vw = [w for w in words if w["spk"] == p["short"]]
            for a_w, b_w in zip(vw, vw[1:]):
                g0, g1 = a_w["t1"], b_w["t0"]
                if g1 - g0 < 0.4 or g1 - g0 > 2.0 or not (head <= g0 and g1 <= tail):
                    continue
                if reason[int(g0 / HOP):int(g1 / HOP)].any():
                    continue
                # an isolated burst in the middle of the gap, clear of both
                # words by 120 ms, 150-800 ms long, with quiet either side
                cut_ = between_neighbours(p["short"], g0 + 0.04, g1 - 0.04)
                if cut_ is None:
                    continue
                i0, i1 = cut_
                if not (0.15 <= (i1 - i0) * HOP <= 0.8):
                    continue
                if i0 * HOP < g0 + a.gap_clear or i1 * HOP > g1 - a.gap_clear:
                    continue
                e_ = env_spk[p["short"]]
                if not (e_[max(0, i0 - 15):i0].max() < loud_spk[p["short"]] - 10.0
                        and e_[i1:i1 + 15].max() < loud_spk[p["short"]] - 10.0):
                    continue
                if other_word(p["short"], i0 * HOP, i1 * HOP):
                    continue
                if a.fillers == "clean" and not (in_silence(i0) and in_silence(i1)):
                    continue
                protected[i0:i1] = False
                reason[i0:i1] = np.where(reason[i0:i1] > 0, reason[i0:i1], 7)
                n_gap += 1
        if n_gap:
            print(f"  sound between words with no token: {n_gap} cut")

    for i, why in drop.items():
        if i not in kept:
            continue
        w = words[i]
        if why == 1 and (words_aligned or any(r0 < w["t1"] and r1 > w["t0"] for r0, r1 in verbatim_regions)):
            continue   # placed from the aligned words above
        if why == 2 and words_aligned:
            # a stammer with aligned neighbours: the first try is the sound
            # between the word before it and the repeat that follows
            prev_w = next((x for x in reversed(words[:i]) if x["spk"] == w["spk"]), None)
            j = i + 1
            while j + 1 < len(words) and words[j]["spk"] == w["spk"] and tok(words[j]["w"]) in FILLERS:
                j += 1
            nxt_w = words[j]
            lo_ = prev_w["t1"] if prev_w and w["t0"] - prev_w["t1"] < 1.5 else w["t0"] - 0.05
            cut_ = between_neighbours(w["spk"], lo_, nxt_w["t0"])
            if cut_ is None:
                skipped.append((w["t0"], w["w"], REASON[why], "no sound between its neighbours"))
                continue
            i0, i1 = cut_
            if a.stammers == "clean" and not (in_silence(i0) and in_silence(i1)):
                skipped.append((w["t0"], w["w"], REASON[why], "the join would be on sound"))
                continue
            if other_word(w["spk"], i0 * HOP, i1 * HOP):
                skipped.append((w["t0"], w["w"], REASON[why], "the other speaker is talking"))
                continue
            protected[i0:i1] = False
            reason[i0:i1] = np.where(reason[i0:i1] > 0, reason[i0:i1], 2)
            removed_words[2] = removed_words.get(2, 0) + 1
            continue
        # inside the filler rather than outside the neighbour: search forward
        # from its start and backward from its end
        x0 = snap_quiet(env, w["t0"], -0.03, 0.12)
        x1 = snap_quiet(env, w["t1"], -0.12, 0.03)
        i0, i1 = int(x0 / HOP), int(x1 / HOP)
        if i1 - i0 < 2:
            continue
        # Whisper stretches a word across the pause after it (and sometimes
        # before). Only the sound is the word; the silent edges of its span
        # are pause, and stay free for the pause rule to keep some of.
        while i1 > i0 and silent[i1 - 1]:
            i1 -= 1
        while i0 < i1 and silent[i0]:
            i0 += 1
        # A word cannot be longer than a word. When Whisper's span for a
        # stammered "that" is over a second, the span is wrong, not the
        # speaker, and removing it would take the neighbour. Skip it: a
        # missed stammer is invisible, a chopped word is not.
        cap = 0.9 if why == 1 else 0.15 + 0.08 * len(tok(w["w"]))
        if (i1 - i0) * HOP > cap:
            skipped.append((w["t0"], w["w"], REASON[why], "span too long to be the word"))
            continue
        # clean: the cut must land in quiet at both ends, or the join will
        # be heard. A filler left in is less wrong than a cut that shows.
        mode = a.fillers if why == 1 else a.stammers
        if mode == "clean" and not (in_silence(i0) and in_silence(i1)):
            skipped.append((w["t0"], w["w"], REASON[why], "the join would be on sound"))
            continue
        # never into a neighbour that stays
        while i0 < i1 and protected[i0]:
            i0 += 1
        while i1 > i0 and protected[i1 - 1]:
            i1 -= 1
        if i1 - i0 >= 2:
            reason[i0:i1] = why
            removed_words[why] = removed_words.get(why, 0) + 1

    # a filler or stammer the rules decided to leave is a word that stays
    for t0_, w_, why_, because in skipped:
        w = next(x for x in words if x["t0"] == t0_ and x["w"] == w_)
        p0, p1 = max(0, int((w["t0"] - 0.03) / HOP)), min(n, int((w["t1"] + 0.03) / HOP))
        span_sound = np.zeros(n, dtype=bool)
        span_sound[p0:p1] = sound[p0:p1]
        protected |= word_sound(span_sound)

    n_untr = 0

    # ── erm's fillers ─────────────────────────────────────────────────────
    # Per track, in that track's time. A cut is only taken where the other
    # speaker has no word, because the edit is one shared timeline. Where
    # erm's own Whisper heard the filler as a word ("um,"), that is better
    # evidence than our transcript, which folded it into the neighbour, so
    # the neighbour's protection yields inside the cut. The acoustic passes
    # (gap, in, long) get no such trust and stay under full protection.
    erm_stats = {}
    erm_dir = d / "erm"
    if erm_dir.exists() and not a.no_erm:
        passes = {x.strip() for x in a.erm_passes.split(",") if x.strip()}
        for p in m["participants"]:
            f = erm_dir / f"{p['short']}.json"
            if not f.exists():
                continue
            for c in load_json(f)["cuts"]:
                lab = c["word"]
                kind = ("gap" if lab.startswith("<gap") else "in" if lab.startswith("<in:")
                        else "long" if lab.startswith("<long:") else "word")
                if kind not in passes:
                    continue
                t0_, t1_ = c["start"] + p["offset"], c["end"] + p["offset"]
                if t1_ - t0_ > 1.2 or not (head <= t0_ and t1_ <= tail):
                    erm_stats["too long or outside"] = erm_stats.get("too long or outside", 0) + 1
                    continue
                if other_sound(p["short"], t0_, t1_):
                    erm_stats["other speaker talking"] = erm_stats.get("other speaker talking", 0) + 1
                    continue
                if kind == "word":
                    continue   # placed from the verbatim words above
                # the acoustic passes are heuristics about the shape of a
                # word, and the words they are least wrong about are the ones
                # our transcript has nothing for at all. Inside one of our
                # words — where "in" and "long" live by definition — a quiet
                # tail like "-ing" reads as a gap and the cut clips the word.
                if any(w["t0"] < t1_ and w["t1"] > t0_ and tok(w["w"]) not in FILLERS
                       for w in words):
                    erm_stats["inside one of our words"] = erm_stats.get("inside one of our words", 0) + 1
                    continue
                i0 = int(snap_quiet(env, t0_, -0.03, 0.03) / HOP)
                i1 = int(snap_quiet(env, t1_, -0.03, 0.03) / HOP)
                while i1 > i0 and silent[i1 - 1]:
                    i1 -= 1
                while i0 < i1 and silent[i0]:
                    i0 += 1
                while i0 < i1 and protected[i0]:
                    i0 += 1
                while i1 > i0 and protected[i1 - 1]:
                    i1 -= 1
                if a.fillers == "clean" and not (in_silence(i0) and in_silence(i1)):
                    erm_stats["join on sound"] = erm_stats.get("join on sound", 0) + 1
                    continue
                if (i1 - i0) * HOP >= 0.06 and not (reason[i0:i1] > 0).all():
                    if a.debug and i0 * HOP < a.debug[1] and i1 * HOP > a.debug[0]:
                        print(f"  debug: erm {kind} '{lab}' {t0_:.2f}-{t1_:.2f} -> cut {i0 * HOP:.2f}-{i1 * HOP:.2f}")
                    reason[i0:i1] = np.where(reason[i0:i1] > 0, reason[i0:i1], 7)
                    erm_stats[kind] = erm_stats.get(kind, 0) + 1
        if erm_stats:
            print("  erm: " + ", ".join(f"{v} {k}" for k, v in erm_stats.items()))

    # ── pauses: silence, with the removed words counted as silence ────────
    removed = reason > 0
    sil = (silent & ~protected) | removed
    sil[:H] = False
    sil[T:] = False
    max_h, pause_h = int(a.max_pause / HOP), int(a.pause / HOP)
    n_pauses = 0
    for r0, r1 in runs(sil):
        has_cut = removed[r0:r1].any()
        if not has_cut and (r1 - r0) <= max_h:
            continue
        free = np.flatnonzero(~removed[r0:r1]) + r0     # silence that is not a removed word
        k = min(len(free), pause_h)
        keep = set(free[:k // 2].tolist() + free[len(free) - (k - k // 2):].tolist()) if k else set()
        seg = reason[r0:r1]
        newly = np.flatnonzero(seg == 0) + r0
        newly = np.array([i for i in newly if i not in keep], dtype=int)
        if len(newly):
            reason[newly] = 3
            if not has_cut:
                n_pauses += 1

    # ── tidy: no sliver kept, no blink removed ────────────────────────────
    removed = reason > 0
    removed[:H] = True
    removed[T:] = True
    for r0, r1 in runs(~removed):
        if (r1 - r0) * HOP < 0.10 and r0 > H and r1 < T and not protected[r0:r1].any():
            removed[r0:r1] = True
            reason[r0:r1] = 3
    for r0, r1 in runs(removed):
        if (r1 - r0) * HOP < 0.04 and r0 >= H and r1 <= T:
            removed[r0:r1] = False
            reason[r0:r1] = 0

    # ── chapters ──────────────────────────────────────────────────────────
    chapters, seams = [], []
    if not a.no_chapters:
        src = load_json(a.chapters) if a.chapters else m.get("chapters", [])
        kw = [words[i] for i in kept]

        def find_phrase(phrase, near, what):
            """The word indexes (first, last) of a quoted phrase, matched on
            one speaker's own run of words so a backchannel from the other
            person in the middle of it does not break the match."""
            want = [tok(x) for x in phrase.split() if tok(x)]
            hits = []
            for p in m["participants"]:
                idx = [k for k, w in enumerate(words) if w["spk"] == p["short"]]
                toks_ = [tok(words[k]["w"]) for k in idx]
                for k in range(len(toks_) - len(want) + 1):
                    if toks_[k:k + len(want)] == want:
                        hits.append((idx[k], idx[k + len(want) - 1]))
            if not hits:
                die(f"{what}: {phrase!r} is not in the transcript (check transcript/transcript.txt)")
            if len(hits) > 1:
                if near is None:
                    die(f"{what}: {phrase!r} is said {len(hits)} times, at "
                        + ", ".join(hms(words[h[0]]["t0"]) for h in hits) + "; quote more of it, or add t0")
                hits.sort(key=lambda h: abs(words[h[0]]["t0"] - near))
            return hits[0]

        def quietest(t_a, t_b):
            i_a, i_b = max(0, int(t_a / HOP)), min(n, int(t_b / HOP))
            if i_b <= i_a:
                return t_a
            return (i_a + int(np.argmin(env[i_a:i_b]))) * HOP

        for i, c in enumerate(src):
            if i == 0 and c.get("t0", 0) <= head + 5 and not c.get("at"):
                chapters.append({"title": c["title"], "t0": head, "card": False})
                continue
            if c.get("at"):
                # the editor quotes the first words of the section, and,
                # with `after`, the last words of the one before: the card
                # goes between, and whatever was said between goes
                i_at, _ = find_phrase(c["at"], c.get("t0"), f"chapter {c['title']!r} start")
                w_at = words[i_at]
                if c.get("after"):
                    _, i_af = find_phrase(c["after"], c.get("t0"), f"chapter {c['title']!r} end of the previous")
                    w_af = words[i_af]
                    if w_af["t1"] >= w_at["t0"]:
                        die(f"chapter {c['title']!r}: 'after' {c['after']!r} ({hms(w_af['t1'])}) "
                            f"is not before 'at' {c['at']!r} ({hms(w_at['t0'])})")
                    lo = w_af["t1"] + 0.1
                else:
                    prev_w = max((w for w in words if w is not w_at and w["t1"] <= w_at["t0"] + 0.02),
                                 key=lambda w: w["t1"], default=None)
                    lo = (prev_w["t1"] + 0.08) if prev_w else head
                hi = w_at["t0"] - 0.05
                anchor = w_af if c.get("after") else None
                chatter = [w for w in words if w is not w_at and w is not anchor
                           and w["t0"] > (anchor["t0"] if anchor else lo - 0.1) and w["t1"] <= w_at["t0"] + 0.02]
                if chatter:
                    # the seam opens just after the anchor and closes just
                    # before the section starts; the chatter is inside it
                    s0 = quietest(lo, max(lo + 0.02, min(lo + 0.5, chatter[0]["t0"] - 0.02)))
                    s1 = quietest(min(hi - 0.02, max(chatter[-1]["t1"] + 0.02, hi - 0.5)), hi)
                elif hi - lo >= 0.1:
                    s0 = quietest(lo, min(lo + 0.5, hi))
                    s1 = quietest(max(s0, hi - 0.5), hi)
                else:
                    s0 = s1 = quietest(lo - 0.08, hi + 0.06)
                i0, i1 = int(s0 / HOP), int(s1 / HOP)
                if i1 > i0:
                    removed[i0:i1] = True
                    protected[i0:i1] = False
                    reason[i0:i1] = 4
                card = c.get("card", True) is not False
                chapters.append({"title": c["title"], "t0": round(w_at["t0"], 3), "card": card,
                                 "seam": (round(s0, 3), round(s1, 3))})
                print(f"  chapter {c['title']!r}: starts at {c['at']!r} ({hms(w_at['t0'])}); "
                      f"{'card' if card else 'marker'} in the seam {hms(s0)} - {hms(s1)}"
                      + (f", dropping {s1 - s0:.2f}s after {c['after']!r}" if c.get("after") else "")
                      + (": " + " ".join(w["w"] for w in chatter) if chatter else ""))
                continue
            if c.get("card") is False:
                chapters.append({"title": c["title"], "t0": c["t0"], "card": False})
                continue
            if c.get("exact"):
                # the editor said here: the quietest moment within 0.3s of it
                x = snap_quiet(env, c["t0"], -0.3, 0.3)
                chapters.append({"title": c["title"], "t0": c["t0"], "card": True,
                                 "seam": (round(x - 0.01, 3), round(x + 0.01, 3))})
                print(f"  chapter '{c['title']}' at {hms(c['t0'])}: card exactly there ({hms(x)})")
                continue
            spot = place_chapter(c["t0"], kw, window_after=a.chapter_window)
            if spot:
                chapters.append({"title": c["title"], "t0": c["t0"], "card": True, "seam": spot})
                print(f"  chapter '{c['title']}' at {hms(c['t0'])}: card in the seam "
                      f"{hms(spot[0])} - {hms(spot[1])} ({spot[1] - spot[0]:.2f}s)")
            else:
                chapters.append({"title": c["title"], "t0": c["t0"], "card": False})
                print(f"  chapter '{c['title']}' at {hms(c['t0'])}: no seam within "
                      f"{a.chapter_window}s, marker only")

    if a.debug:
        t_a, t_b = a.debug
        print(f"  debug {t_a:.2f}-{t_b:.2f}: t  reason prot silent dB")
        for t_ in np.arange(t_a, t_b, 0.01):
            i = int(t_ / HOP)
            print(f"    {t_:7.2f}  {REASON.get(int(reason[i]), '-') if reason[i] else '-':<10} "
                  f"{'P' if protected[i] else '.'}   {'s' if silent[i] else '.'}   {env[i]:5.0f}")

    # ── items ─────────────────────────────────────────────────────────────
    items = [{"type": "src", "t0": round(r0 * HOP, 3), "t1": round(r1 * HOP, 3)}
             for r0, r1 in runs(~removed)]
    for c in chapters:
        if not c.get("card"):
            continue
        g0, g1 = c["seam"]
        mid = snap_quiet(env, (g0 + g1) / 2, -(g1 - g0) / 2 + 0.02, (g1 - g0) / 2 - 0.02)
        card = {"type": "card", "name": None, "number": None, "title": c["title"],
                "dur": a.card_dur}
        placed = False
        for k in range(len(items) - 1):
            if items[k]["type"] == "src" and items[k + 1]["type"] == "src" \
                    and items[k]["t1"] <= g1 + 0.1 and items[k + 1]["t0"] >= g0 - 0.1:
                items.insert(k + 1, card)
                placed = True
                break
        if not placed:
            for k, it in enumerate(items):
                if it["type"] == "src" and it["t0"] < mid < it["t1"]:
                    left = {"type": "src", "t0": it["t0"], "t1": round(mid, 3)}
                    right = {"type": "src", "t0": round(mid, 3), "t1": it["t1"]}
                    items[k:k + 1] = [left, card, right]
                    placed = True
                    break
        if not placed:
            c["card"] = False
    num = 1
    for it in items:
        if it["type"] == "card":
            num += 1
            it["number"], it["name"] = num, f"chapter-{num:02d}"
    # crossfade per join: across a pause, a chapter seam or an editorial
    # cut both sides are quiet and a longer fade hides the change of room
    # tone; a filler cut sits right against a word and gets the short one
    for k in range(len(items) - 1):
        p_, q_ = items[k], items[k + 1]
        if p_["type"] == "src" and q_["type"] == "src":
            r0, r1 = int(p_["t1"] / HOP), int(q_["t0"] / HOP)
            why = (int(np.bincount(reason[r0:r1]).argmax()) or 3) if r1 > r0 else 3
            xf = 0.06 if why in (3, 4, 5) else 0.02
        else:
            xf = 0.06
        if p_["type"] == "src":
            p_["xf_out"] = xf
        if q_["type"] == "src":
            # out of a card's silence the speech starts at once: a short fade
            q_["xf_in"] = 0.02 if p_["type"] == "card" else xf
    duration = edl_finalise(items)

    out_chapters = [{"title": chapters[0]["title"] if chapters else "Start", "out": 0.0}]
    for it in items:
        if it["type"] == "card":
            out_chapters.append({"title": it["title"], "out": it["out"]})
    for c in chapters[1:]:
        if not c.get("card"):
            o = src_to_out({"items": items}, c["t0"])
            if o is None:
                o = next((it["out"] for it in items if it["type"] == "src" and it["t0"] >= c["t0"]), None)
            if o is not None:
                out_chapters.append({"title": c["title"], "out": round(o, 3)})
    out_chapters.sort(key=lambda c: c["out"])
    for i, c in enumerate(out_chapters, 1):
        c["number"] = i

    removed_list = [{"t0": round(r0 * HOP, 3), "t1": round(r1 * HOP, 3),
                     "why": REASON.get(int(np.bincount(reason[r0:r1]).argmax()) or 3, "pause")}
                    for r0, r1 in runs(removed) if r0 >= H and r1 <= T]
    joins = np.array([env[max(0, int(tt / HOP) - 1):int(tt / HOP) + 2].mean()
                      for it in items if it["type"] == "src" for tt in (it["t0"], it["t1"])])
    stats = {"joins": len(joins), "joins_median_db": round(float(np.median(joins)), 1),
             "joins_loud": int((joins > thr + 8).sum()),
             "fillers": removed_words.get(1, 0), "stammers": removed_words.get(2, 0),
             "fillers_found": sum(1 for i, why in drop.items() if why == 1 and i in kept),
             "stammers_found": sum(1 for i, why in drop.items() if why == 2 and i in kept),
             "pauses": n_pauses, "skipped": len(skipped), "manual": len(manual),
             "erm": erm_stats, "verbatim": filler_how, "gap_sound": n_gap,
             "threshold_db": round(thr, 1),
             "speech_level_db": round(speech_level, 1), "noise_db": round(noise, 1)}
    edl = {"slug": a.slug, "head": round(head, 3), "tail": round(tail, 3),
           "how": {"head": how_head, "tail": how_tail},
           "params": {"pad": a.pad, "max_pause": a.max_pause, "pause": a.pause,
                      "fillers": a.fillers, "stammers": a.stammers, "clean_ms": a.clean_ms,
                      "card_dur": a.card_dur},
           "items": items, "duration": duration, "chapters": out_chapters,
           "removed": removed_list,
           "removed_seconds": round(sum(r["t1"] - r["t0"] for r in removed_list), 3),
           "source_seconds": round(tail - head, 3), "stats": stats}
    save_json(edl_path, edl)

    print(f"{edl_path.relative_to(ROOT) if edl_path.is_relative_to(ROOT) else edl_path}")
    print(f"  head {hms(head)} ({how_head})   tail {hms(tail)} ({how_tail})")
    print(f"  speech {speech_level:.0f} dB, noise {noise:.0f} dB, silence below {thr:.0f} dB; "
          + ", ".join(f"{k} sounds above {v:.0f}" for k, v in loud_spk.items()))
    n_erm = sum(v for k, v in erm_stats.items() if k in ("gap", "in", "long")) + filler_how.get("cut", 0)
    print(f"  {tail - head:.1f}s of source -> {duration:.1f}s: {stats['fillers']} fillers, "
          f"{n_erm} more from erm, {stats['stammers']} stammers, {stats['pauses']} pauses; "
          f"{edl['removed_seconds']:.1f}s removed in {len(removed_list)} cuts; "
          f"{sum(1 for it in items if it['type'] == 'src')} stretches kept")
    print(f"  joins: {stats['joins']}, median {stats['joins_median_db']} dB, "
          f"{stats['joins_loud']} on sound rather than silence")
    for t0_, w_, why_, because in skipped:
        print(f"  left alone: {why_} '{w_}' at {hms(t0_)}, {because}")
    for c in out_chapters:
        print(f"  chapter {c['number']}  {hms(c['out'])}  {c['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
