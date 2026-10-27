#!/usr/bin/env python3
"""
Take a recording in from Riverside and write down what it is.

    ./tools/ingest.py ep0 ~/Downloads/timeline.zip \
        --names 6ac4c0cd="James Hartt",6ac4c0ce="Abrar Mahmood"
    ./tools/ingest.py ep0 path/to/folder --names ...     # loose per-track files
    ./tools/ingest.py ep0 --show                          # what was recorded

Riverside's timeline export is a ZIP: one WAV and one video-only MP4 per
participant, plus a Premiere-style XML that says where each starts on the
shared timeline, how Riverside laid them out, and the chapters it guessed.
This unpacks that into build/episodes/<slug>/source/ and reduces it to one
manifest.json that every later stage reads, so nothing downstream has to
parse XML or guess which file is whom.

Names are not in the package — the files are named by opaque ids — so they
are given here, matched by id prefix, and written into the manifest once.

What it checks, because each has bitten or will:
  - every audio track is 48 kHz, or is resampled to it (soxr) and said so
  - every video track is constant frame rate at the sequence's rate
  - audio and video durations agree per participant
  - offsets came from the XML, not from an assumption of zero
"""

import argparse
import bisect
import pathlib
import re
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from eplib import EPISODES, RATE, ROOT, die, ffprobe, load_json, need, run, save_json  # noqa: E402


def parse_names(s):
    out = {}
    for part in (s or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def unpack(src, dest):
    dest.mkdir(parents=True, exist_ok=True)
    src = pathlib.Path(src)
    if src.is_file() and src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            for n in names:
                # flatten: Riverside writes a flat zip, but never trust a zip's paths
                target = dest / pathlib.Path(n).name
                with z.open(n) as f, open(target, "wb") as g:
                    shutil.copyfileobj(f, g)
        return src.name
    if src.is_dir():
        for p in src.iterdir():
            if p.is_file():
                shutil.copy2(p, dest / p.name)
        return src.name
    die(f"{src} is neither a zip nor a folder")


def read_xml(path):
    """The sequence: fps, size, each clip's timeline start and layout, chapters.

    Premiere's xmeml puts the layout in a Basic Motion effect: scale, centre
    and crop per clip. Riverside uses it to express 'side by side', and we
    keep it so the assembly can reproduce the arrangement Riverside chose
    rather than inventing one.
    """
    root = ET.parse(path).getroot()
    seq = root.find("sequence")
    fps = int(seq.find("rate/timebase").text)
    sc = seq.find("media/video/format/samplecharacteristics")
    info = {"fps": fps, "frames": int(seq.find("duration").text),
            "width": int(sc.find("width").text), "height": int(sc.find("height").text),
            "clips": {}, "chapters": []}
    for kind in ("video", "audio"):
        for track in seq.findall(f"media/{kind}/track"):
            for c in track.findall("clipitem"):
                name = c.find("name").text
                st = int(c.find("start").text) / fps
                i_, o_ = int(c.find("in").text) / fps, int(c.find("out").text) / fps
                entry = info["clips"].setdefault(name, {"segments": []})
                # Riverside exports its own edit as many clips per track —
                # 580 on episode 1 — each a stretch of the source placed on
                # the timeline. The tracks' relative alignment is read from
                # clips at one sequence time they all cover (see align_at):
                # a clip's start less its in-point is where source time 0
                # would sit, minus whatever was cut out before that clip.
                entry["segments"].append((st, i_, o_))
                if "start" not in entry or st < entry["start"]:
                    entry["start"] = st
                eff = c.find("filter/effect")
                if eff is not None:
                    p = {x.find("parameterid").text: x for x in eff.findall("parameter")}
                    try:
                        entry["layout"] = {
                            "scale": float(p["scale"].find("value").text),
                            "centre": float(p["center"].find("value/horiz").text),
                            "crop": [float(p[k].find("value").text)
                                     for k in ("leftcrop", "rightcrop", "topcrop", "bottomcrop")],
                        }
                    except (KeyError, AttributeError):
                        pass
    for m in seq.findall("marker"):
        if (m.findtext("type") or "").lower() == "chapter":
            info["chapters"].append({"title": m.findtext("name"),
                                     "t0": int(m.findtext("in")) / fps,
                                     "t1": int(m.findtext("out")) / fps})
    return info


NOTES = []


def participants(source, xml):
    """Pair each audio file with its video by shared id."""
    ids = {}
    for p in source.iterdir():
        m = re.match(r"(.+?)[-_](audio|video)\.(wav|mp4|mov|m4a|mp3)$", p.name, re.I)
        if m:
            ids.setdefault(m.group(1), {})[m.group(2).lower()] = p.name
    if not ids:
        # loose downloads: riverside_<name>_... .wav / .mp4 — pair by stem
        for p in source.iterdir():
            if p.suffix.lower() in (".wav", ".mp4", ".mov"):
                kind = "audio" if p.suffix.lower() == ".wav" else "video"
                ids.setdefault(p.stem, {})[kind] = p.name
    out = []
    for pid, files in sorted(ids.items()):
        clip, edit = {}, None
        if xml:
            for name, c in xml["clips"].items():
                if name.startswith(pid):
                    clip.update({k: v for k, v in c.items() if k != "segments"})
                    # the audio track carries the content edit; the video
                    # track's clips are the camera switching
                    if name.endswith((".wav", ".m4a", ".mp3")) or edit is None:
                        edit = sorted(c["segments"])
        out.append({"id": pid, "offset": 0.0, "layout": clip.get("layout"), "edit": edit, **files})
    # Where source time 0 of each track sits on the sequence, read at one
    # sequence time every track covers. Reading it from each track's own
    # first clip is wrong on an edited timeline: Riverside trims a different
    # length of leading pause from each track, and on episode 1 that put one
    # voice 1.5 s late against the other. Offsets are then made relative to
    # the earliest track, never negative.
    edits = [p["edit"] for p in out if p.get("edit")]
    if edits:
        s_ref = max(e[0][0] for e in edits)
        for p in out:
            if p.get("edit"):
                p["offset"] = round(align_at(p["edit"], s_ref), 4)
        p0 = next(p for p in out if p.get("edit"))
        drift = 0.0
        # the same reading at every cut both tracks share: an edit that cut
        # the tracks at different points would make the offsets ambiguous
        for p in out[1:]:
            if not p.get("edit"):
                continue
            shared = sorted({st for st, _, _ in p["edit"]} & {st for st, _, _ in p0["edit"]})
            if len(shared) > 1:
                rel = [align_at(p["edit"], s) - align_at(p0["edit"], s) for s in shared]
                drift = max(drift, max(rel) - min(rel))
        if drift > 0.001:
            NOTES.append(f"the tracks' relative offsets vary by {drift:.3f}s across the "
                         f"timeline's cuts; read at sequence {s_ref:.3f}s")
    base = min(p["offset"] for p in out)
    for p in out:
        p["offset"] = round(p["offset"] - base, 4)
    return out


def align_at(segs, s):
    """Where this track's source time 0 would sit on the sequence, read from
    the clip under sequence time `s` (the nearest clip if none covers it):
    the clip's start less its in-point."""
    st, i_, _ = min(segs, key=lambda c: (not (c[0] <= s < c[0] + (c[2] - c[1])), abs(c[0] - s)))
    return st - i_


def kept_agree(parts, fps):
    """Riverside cuts every track of an edited timeline at the same sequence
    points, so once the offsets are applied the stretches it kept should
    coincide across tracks. When they do not, the offsets are wrong: this
    is the check that would have caught episode 1's voice 1.5 s late."""
    kept = [(p["short"], p["riverside_edit"]["kept"]) for p in parts if "riverside_edit" in p]
    if len(kept) < 2:
        return []
    tol = 1.0 / fps + 1e-3
    ref = sorted(x for a, b in kept[0][1] for x in (a, b))
    out = []
    for short, segs in kept[1:]:
        edges = [x for a, b in segs for x in (a, b)]
        hit = 0
        for x in edges:
            i = bisect.bisect_left(ref, x - tol)
            hit += i < len(ref) and ref[i] <= x + tol
        if hit < 0.8 * len(edges):
            out.append(f"{short}: Riverside's kept stretches do not line up with {kept[0][0]}'s "
                       f"({hit} of {len(edges)} edges within a frame): the track offsets are suspect")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("package", nargs="?", help="Riverside timeline zip, or a folder of tracks")
    ap.add_argument("--names", default="", help='id-prefix=name pairs, comma separated')
    ap.add_argument("--show", action="store_true", help="print the manifest and stop")
    ap.add_argument("--force", action="store_true", help="re-unpack over an existing source/")
    a = ap.parse_args()
    need("ffmpeg", "ffprobe")

    d = EPISODES / a.slug
    src = d / "source"
    manifest_path = d / "manifest.json"

    if a.show:
        m = load_json(manifest_path)
        print(f"{a.slug}: {m['duration']:.2f}s  {m['fps']} fps  {len(m['participants'])} participants")
        for p in m["participants"]:
            print(f"  {p['short']:<8} {p['name']:<18} offset {p['offset']:+.3f}s  "
                  f"{p['video']['width']}x{p['video']['height']}  audio {p['audio']['rate']} Hz")
        for c in m["chapters"]:
            print(f"  chapter {c['t0']:8.2f}s  {c['title']}")
        return 0

    if not a.package:
        die("a package (zip or folder) is needed unless --show")
    if src.exists() and any(src.iterdir()) and not a.force:
        print(f"{src.relative_to(ROOT)} already unpacked; reading it (use --force to replace)")
        package = (load_json(manifest_path).get("package") if manifest_path.exists()
                   else pathlib.Path(a.package).name)
    else:
        if src.exists():
            shutil.rmtree(src)
        package = unpack(a.package, src)

    xml_path = next(iter(sorted(src.glob("*.xml"))), None)
    xml = read_xml(xml_path) if xml_path else None
    if not xml:
        print("no timeline XML in the package: offsets assumed 0, no chapters, no layout")

    names = parse_names(a.names)
    if manifest_path.exists() and not names:
        names = {p["id"]: p["name"] for p in load_json(manifest_path)["participants"]}

    found = participants(src, xml)
    ids = [p["id"] for p in found]

    def handle(id_):
        """The shortest prefix of this id that no other track shares. Two
        tracks in one package began with the same eight characters, and
        eight was what the fallback used."""
        for n in range(8, len(id_) + 1):
            if sum(1 for o in ids if o.startswith(id_[:n])) == 1:
                return id_[:n]
        return id_

    parts, warnings = [], list(NOTES)
    for p in found:
        name = next((v for k, v in names.items() if p["id"].startswith(k)), None)
        if not name:
            warnings.append(f"no name for {p['id']} — pass --names {handle(p['id'])}=\"Full Name\"")
            name = handle(p["id"])
        short = re.sub(r"[^a-z0-9]", "", name.split()[0].lower())
        entry = {"id": p["id"], "name": name, "short": short, "offset": p["offset"],
                 "layout": p["layout"]}
        if p.get("edit") and len(p["edit"]) > 1:
            # Riverside's own cut, kept for the record and not applied: the
            # pipeline cuts from the raw track. Stretches of the raw track
            # that its edit kept, in this episode's time.
            kept = [(round(i_ + p["offset"], 3), round(o_ + p["offset"], 3)) for _, i_, o_ in p["edit"]]
            entry["riverside_edit"] = {"clips": len(kept), "kept_seconds": round(sum(b - a for a, b in kept), 1),
                                       "kept": kept}
        if "audio" in p:
            info = ffprobe(src / p["audio"])
            au = info["audio"]
            path = p["audio"]
            if au["rate"] != RATE:
                out = src / (pathlib.Path(p["audio"]).stem + f"-{RATE}.wav")
                run(["ffmpeg", "-nostdin", "-y", "-i", src / p["audio"], "-ar", RATE,
                     "-resampler", "soxr", "-precision", "28", "-c:a", "pcm_s24le", out])
                warnings.append(f"{short}: audio was {au['rate']} Hz, resampled to {RATE} "
                                f"-> {out.name}")
                path, info = out.name, ffprobe(out)
                au = info["audio"]
            entry["audio"] = {"file": f"source/{path}", "duration": info["duration"],
                              "rate": au["rate"], "channels": au["channels"]}
        if "video" in p:
            info = ffprobe(src / p["video"])
            v = info["video"]
            entry["video"] = {"file": f"source/{p['video']}", "duration": info["duration"],
                              "width": v["width"], "height": v["height"], "fps": v["fps"],
                              "has_audio": info["audio"] is not None}
            if not v["cfr"]:
                warnings.append(f"{short}: video is not constant frame rate")
            if xml and abs(v["fps"] - xml["fps"]) > 1e-3:
                warnings.append(f"{short}: video is {v['fps']} fps, sequence is {xml['fps']}")
        if "audio" in entry and "video" in entry:
            gap = abs(entry["audio"]["duration"] - entry["video"]["duration"])
            if gap > 0.5:
                warnings.append(f"{short}: audio and video durations differ by {gap:.2f}s")
        parts.append(entry)

    if not parts:
        die(f"no tracks found in {src}")

    fps = xml["fps"] if xml else parts[0].get("video", {}).get("fps", 24)
    warnings += kept_agree(parts, fps)
    # the raw tracks' length, not the XML sequence's: an edited timeline is
    # shorter than what was recorded
    duration = max(p["offset"] + p["audio"]["duration"] for p in parts if "audio" in p)
    chapters = xml["chapters"] if xml else []
    first = next((p for p in found if p.get("edit")), None)
    if first and len(first["edit"]) > 1 and chapters:
        # Riverside's chapter marks are in its edited sequence's time;
        # bring them back to this episode's time through its clip list
        segs = first["edit"]

        def to_src(t):
            for st, i_, o_ in segs:
                if st <= t < st + (o_ - i_):
                    return i_ + (t - st) + first["offset"]
            return None

        chapters = [{**c, "t0": round(to_src(c["t0"]) or 0.0, 3), "t1": round(to_src(c["t1"]) or duration, 3)}
                    for c in chapters]
    manifest = {
        "slug": a.slug, "package": package,
        "xml": f"source/{xml_path.name}" if xml_path else None,
        "fps": fps, "duration": round(duration, 4),
        "sequence": {"width": xml["width"], "height": xml["height"]} if xml else None,
        "participants": parts,
        "chapters": chapters,
        "warnings": warnings,
    }
    save_json(manifest_path, manifest)

    print(f"{manifest_path.relative_to(ROOT)}")
    print(f"  {duration:.2f}s  {fps} fps  package {package}")
    for p in parts:
        v = p.get("video", {})
        au = p.get("audio", {})
        print(f"  {p['short']:<8} {p['name']:<18} offset {p['offset']:+.3f}s  "
              f"{v.get('width')}x{v.get('height')} {v.get('fps')}fps  "
              f"audio {au.get('rate')} Hz {au.get('duration', 0):.2f}s  "
              f"layout {p['layout']}")
    for c in manifest["chapters"]:
        print(f"  chapter {c['t0']:8.2f}s  {c['title']}")
    for w in warnings:
        print(f"  WARNING: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
