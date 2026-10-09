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
import pathlib
import re
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from eplib import (EPISODES, RATE, ROOT, die, ffprobe, load_json, need,  # noqa: E402
                   run, save_json)


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
                st = int(c.find("start").text)
                entry = info["clips"].setdefault(name, {})
                entry["start"] = st / fps
                entry["in"] = int(c.find("in").text) / fps
                entry["out"] = int(c.find("out").text) / fps
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
        clip = {}
        if xml:
            for name, c in xml["clips"].items():
                if name.startswith(pid):
                    clip.update(c)
        out.append({"id": pid, "offset": round(clip.get("start", 0.0), 4),
                    "layout": clip.get("layout"), **files})
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
        names = {p["id"][:8]: p["name"] for p in load_json(manifest_path)["participants"]}

    parts, warnings = [], []
    for p in participants(src, xml):
        name = next((v for k, v in names.items() if p["id"].startswith(k)), None)
        if not name:
            warnings.append(f"no name for {p['id']} — pass --names {p['id'][:8]}=\"Full Name\"")
            name = p["id"][:8]
        short = re.sub(r"[^a-z0-9]", "", name.split()[0].lower())
        entry = {"id": p["id"], "name": name, "short": short, "offset": p["offset"],
                 "layout": p["layout"]}
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
    duration = (xml["frames"] / fps if xml else
                max(p["offset"] + p["audio"]["duration"] for p in parts if "audio" in p))
    manifest = {
        "slug": a.slug, "package": package,
        "xml": f"source/{xml_path.name}" if xml_path else None,
        "fps": fps, "duration": round(duration, 4),
        "sequence": {"width": xml["width"], "height": xml["height"]} if xml else None,
        "participants": parts,
        "chapters": xml["chapters"] if xml else [],
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
