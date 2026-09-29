#!/usr/bin/env python3
"""
Cut bar ranges out of a MusicXML score into standalone excerpts.

The theme is written once, as one 26-bar piece. The show needs it as six
separate sounds — a sting, an outro, a loopable bed, a button — and every one
of those is a range of bars out of the same score. This cuts them.

The cutting is trivial; the bookkeeping is not. MusicXML states divisions,
key, time signature, clef and instrument transposition once, in bar 1. An
excerpt starting at bar 19 carries none of them, so it opens without
complaint and plays back at the wrong speed, in the wrong key, an octave out.
Dynamics have the same shape of problem: a range starting mid-piece inherits
whatever mark was last written, which is nowhere in the bars you kept.

So this walks the score from bar 1 to the start of the range, accumulating
that state, and injects it into the first bar it keeps.

Usage:
    python3 tools/score.py theme.mxl sting.musicxml --bars 5-7 --truncate 1
    python3 tools/score.py theme.mxl bed.musicxml   --bars 1-2 --parts 'Acoustic Bass'
    python3 tools/score.py theme.mxl outro.musicxml --bars 19-26 --title 'AgentSee Outro'

Reads .mxl (compressed) or .musicxml, writes .musicxml. Standard library
only. Output opens in MuseScore, and in anything else that reads MusicXML.
"""

import argparse
import copy
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

# Attributes MusicXML declares once and then assumes. Every one of these has
# to be carried forward into an excerpt or the excerpt is silently wrong.
CARRIED = ("divisions", "key", "time", "clef", "transpose", "staff-details")

# Tone-colour and playing-technique marks persist until cancelled, so an
# excerpt starting after one has to inherit it — same problem as dynamics.
TECHNIQUE = {"sul tasto", "tasto", "dolce", "sul ponticello", "sul pont.",
             "ponticello", "pont.", "pont", "metallico", "metal.",
             "pizz.", "pizzicato", "arco", "con sordino", "senza sordino"}
CANCEL = {"ord.", "ordinario", "modo ordinario", "nat.", "naturale", "natural",
          "normale", "pos. nat.", "sonido natural"}

# Rest durations in quarter notes, longest first — used to pad a truncated bar.
REST_TYPES = (("whole", 4), ("half", 2), ("quarter", 1),
              ("eighth", .5), ("16th", .25), ("32nd", .125))


def load(path):
    """Parse .mxl or .musicxml. An .mxl is a zip; container.xml names the score."""
    if str(path).endswith(".mxl"):
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist()
                        if n.endswith(".xml") and "META-INF" not in n)
            return ET.fromstring(z.read(name))
    return ET.parse(path).getroot()


def rests_for(ticks, div):
    """Standard rest types summing to `ticks`, longest first."""
    out = []
    for name, beats in REST_TYPES:
        n = int(ticks // (beats * div))
        out += [(name, int(beats * div))] * n
        ticks -= n * beats * div
    return out


def single_type(ticks, div):
    """The rest/note type for exactly `ticks`, or None if it isn't a plain one."""
    for name, beats in REST_TYPES:
        if ticks == beats * div:
            return name
    return None


def classify_first(measure):
    """Whether this measure already opens with a technique mark of its own."""
    for d in measure.findall("direction"):
        if classify(d) in ("technique", "cancel"):
            return "technique"
    return None


def classify(direction):
    """What kind of persistent state this direction sets, if any."""
    if direction.find(".//dynamics") is not None:
        return "dynamics"
    if direction.find(".//metronome") is not None or \
       direction.find("sound[@tempo]") is not None:
        return "tempo"
    words = " ".join(w.text or "" for w in direction.iter("words")).strip().lower()
    if words in CANCEL:
        return "cancel"
    if words in TECHNIQUE:
        return "technique"
    return None


def tempo_before(root, first):
    """The last tempo direction at or before bar `first`, from any part.

    MuseScore writes tempo into the first part only, so a bass-only excerpt
    has to look outside the part it keeps or it renders at the default 120.
    """
    found = None
    for part in root.findall("part"):
        for m in part.findall("measure"):
            if int(m.get("number")) > first:
                break
            for d in m.findall("direction"):
                if classify(d) == "tempo":
                    found = copy.deepcopy(d)
    return found


def tempo_of(root):
    """The score's written tempo, or None. MusicXML puts it in <sound tempo>."""
    for s in root.iter("sound"):
        if s.get("tempo"):
            return float(s.get("tempo"))
    return None


def metre_of(root):
    """(beats, beat-type) from the first time signature. Defaults to 4/4."""
    t = root.find(".//attributes/time")
    if t is None:
        return 4, 4
    return int(t.findtext("beats", 4)), int(t.findtext("beat-type", 4))


def truncate_bar(measure, beat, div, beats, beat_type):
    """Drop everything in `measure` after `beat`, and pad the rest with rests.

    A sting has to land on a downbeat and then get out of the way. That means
    keeping the first beat of a bar and throwing away the other three — but a
    bar has to stay a bar, so what's thrown away comes back as rests.
    """
    limit = beat * div
    bar_ticks = int(beats * div * 4 / beat_type)
    pos = start = 0
    drop, straddler = [], None

    for n in measure.findall("note"):
        if n.find("chord") is None:      # a chord's members share a start
            start = pos
            pos += int(n.findtext("duration") or 0)
        if start >= limit:
            drop.append(n)
        elif pos > limit:
            straddler = n

    for n in drop:
        measure.remove(n)

    if straddler is not None:
        kept = limit - sum(int(x.findtext("duration") or 0)
                           for x in measure.findall("note")
                           if x is not straddler and x.find("chord") is None)
        name = single_type(kept, div)
        if name:
            straddler.find("duration").text = str(int(kept))
            straddler.find("type").text = name
        else:
            print(f"  note: the cut at beat {beat} falls inside a note that "
                  f"can't be shortened to a plain duration; left it long",
                  file=sys.stderr)

    used = sum(int(n.findtext("duration") or 0)
               for n in measure.findall("note") if n.find("chord") is None)
    for name, ticks in rests_for(max(0, bar_ticks - used), div):
        r = ET.SubElement(measure, "note")
        ET.SubElement(r, "rest")
        ET.SubElement(r, "duration").text = str(ticks)
        ET.SubElement(r, "type").text = name


def excerpt(root, first, last, keep_parts=None, truncate_beat=None, title=None):
    out = copy.deepcopy(root)
    beats, beat_type = metre_of(out)
    # Always, even from bar 1: MuseScore writes tempo into the first part only,
    # so a bass-only excerpt needs it injected however early it starts.
    tempo = tempo_before(out, first)
    tempo_placed = False

    if title:
        # The title lives in two places: work-title metadata, and engraved
        # text on the page. The engraved one is found by its credit-type, not
        # by guessing from its size or position — every guess that looked
        # reasonable also rewrote the composer's name.
        for w in out.iter("work-title"):
            w.text = title
        for c in out.findall("credit"):
            if c.findtext("credit-type") == "title":
                for words in c.findall("credit-words"):
                    words.text = title

    part_list = out.find("part-list")
    wanted = []
    for sp in list(part_list.findall("score-part")):
        name = (sp.findtext("part-name") or "").strip()
        if keep_parts and name not in keep_parts:
            part_list.remove(sp)
        else:
            wanted.append(sp.get("id"))
    if keep_parts and not wanted:
        sys.exit(f"no part matched {keep_parts}; the score has: " + ", ".join(
            (sp.findtext("part-name") or "?").strip()
            for sp in root.find("part-list").findall("score-part")))

    for part in list(out.findall("part")):
        if part.get("id") not in wanted:
            out.remove(part)
            continue

        # Walk everything before the range, remembering what it established.
        state, dynamics, technique, div = {}, None, None, 1
        for m in part.findall("measure"):
            if int(m.get("number")) >= first:
                break
            for a in m.findall("attributes"):
                for tag in CARRIED:
                    el = a.find(tag)
                    if el is not None:
                        state[tag] = copy.deepcopy(el)
                if a.findtext("divisions"):
                    div = int(a.findtext("divisions"))
            for d in m.findall("direction"):
                kind = classify(d)
                if kind == "dynamics":
                    dynamics = copy.deepcopy(d)
                elif kind == "technique":
                    technique = copy.deepcopy(d)
                elif kind == "cancel":
                    technique = None          # back to ordinario; nothing to carry
        for m in part.findall("measure"):    # divisions may only appear later
            if m.findtext(".//divisions"):
                div = int(m.findtext(".//divisions"))

        kept = [m for m in part.findall("measure")
                if first <= int(m.get("number")) <= last]
        for m in list(part.findall("measure")):
            part.remove(m)
        for i, m in enumerate(kept):
            m.set("number", str(i + 1))
            part.append(m)
        if not kept:
            sys.exit(f"no bars in range {first}-{last}")

        attrs = kept[0].find("attributes")
        if attrs is None:
            attrs = ET.Element("attributes")
            kept[0].insert(0, attrs)
        for tag in CARRIED:
            if tag in state and attrs.find(tag) is None:
                attrs.append(state[tag])
        if any(classify(d) == "tempo" for d in kept[0].findall("direction")):
            tempo_placed = True               # this part already states it
        elif tempo is not None and not tempo_placed:
            kept[0].insert(0, tempo)          # once only, or both staves show it
            tempo_placed = True
        if technique is not None and classify_first(kept[0]) != "technique":
            kept[0].insert(1, technique)
        if dynamics is not None and kept[0].find(".//dynamics") is None:
            kept[0].insert(1, dynamics)

        if truncate_beat:
            truncate_bar(kept[-1], truncate_beat, div, beats, beat_type)

    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help=".mxl or .musicxml")
    ap.add_argument("out", help=".musicxml to write")
    ap.add_argument("--bars", required=True, metavar="FIRST-LAST",
                    help="inclusive bar range, e.g. 5-7, or a single bar, 18")
    ap.add_argument("--parts", metavar="NAMES",
                    help="comma-separated part names to keep; default is all")
    ap.add_argument("--truncate", type=float, metavar="BEAT",
                    help="keep only up to this beat of the last bar, padding "
                         "the rest with rests")
    ap.add_argument("--title", help="rewrite the work title on the excerpt")
    a = ap.parse_args()

    if not re.fullmatch(r"\d+(-\d+)?", a.bars):
        sys.exit(f"--bars wants 5-7 or 18, not {a.bars!r}")
    first, _, tail = a.bars.partition("-")
    first, last = int(first), int(tail or first)
    if last < first:
        sys.exit(f"--bars {a.bars}: the range runs backwards")
    root = load(a.src)
    ET.ElementTree(excerpt(root, first, last,
                           a.parts.split(",") if a.parts else None,
                           a.truncate, a.title)).write(
        a.out, encoding="UTF-8", xml_declaration=True)

    beats, beat_type = metre_of(root)
    tempo = tempo_of(root)
    bars = last - first + 1
    length = (bars - (1 - a.truncate / beats if a.truncate else 0)) \
        * beats * (4 / beat_type) * 60 / (tempo or 120)
    print(f"{a.out}: bars {first}-{last} ({bars} bar{'s' * (bars > 1)}, "
          f"~{length:.1f}s at {tempo or 120:.0f}"
          f"{'' if tempo else ' — assumed, the score has no tempo marking'})"
          + (f", cut at beat {a.truncate}" if a.truncate else "")
          + (f", {a.parts} only" if a.parts else ""))


if __name__ == "__main__":
    main()
