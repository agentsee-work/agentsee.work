#!/usr/bin/env python3
"""
Take a recording in from Riverside and write down what it is.

    ./tools/ingest.py ep4 ~/Downloads/ep4                 # a folder of track downloads
    ./tools/ingest.py ep4 ~/Downloads/ep4 --names jane_doe="Jane Doe" --order abrar,jane,james
    ./tools/ingest.py ep0 ~/Downloads/timeline.zip \
        --names 6ac4c0cd="James Hartt",6ac4c0ce="Abrar Mahmood"
    ./tools/ingest.py ep0 --show                          # what was recorded

The package is a folder of the tracks Riverside lets every plan download,
two files a person: their Aligned video (a CFR MP4 with their own audio
inside, padded at the front if they joined late) and their Raw audio (the
WAV). Nothing in a folder says where a WAV belongs on the shared timeline,
so it is measured: the WAV is found inside its own video's audio, which is
the same recording behind the same padding. The video sits at zero, the WAV
where it was found. Left to right is the show's seats (tools/show.json), or
--order.

Riverside's timeline export, a ZIP with a Premiere-style XML that says where
each track starts, how it laid them out and the chapters it guessed, went
behind the Business plan in November 2026. It is still read, for the
episodes made from one.

Either way the package is unpacked into build/episodes/<slug>/source/ and
reduced to one manifest.json that every later stage reads, so nothing
downstream has to parse XML or guess which file is whom.

Names are matched by id prefix and written into the manifest once. A folder's
files begin with the name each person typed at Riverside's door, so the hosts
are recognised from tools/show.json and only a guest needs --names. A
timeline's files are named by opaque ids, and every name is given.

What it checks, because each has bitten or will:
  - every audio track is 48 kHz, or is resampled to it (soxr) and said so
  - every video track is constant frame rate at the sequence's rate
  - audio and video durations agree per participant
  - offsets were measured or read from the XML, not assumed to be zero
"""

import argparse
import bisect
import pathlib
import re
import shutil
import statistics
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

from eplib import EPISODES, RATE, ROOT, die, ffprobe, load_json, need, run, save_json, video_offset

SHOW = ROOT / "tools" / "show.json"
MEDIA = {".wav", ".mp4", ".mov", ".m4a", ".mp3", ".xml"}
# Placing a WAV in its video's audio: the whole file at COARSE Hz finds the place, a minute at FINE Hz settles
# it, and a match counts when it stands SURE times above the next best. The real ones stand thirty to a hundred.
COARSE, FINE, SURE = 4000, 16000, 8.0


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
        # flattened like the zip: a browser saves the tracks side by side, a fetch keeps a folder a file
        found = sorted(p for p in src.rglob("*") if p.is_file() and p.suffix.lower() in MEDIA)
        twice = sorted({p.name for p in found if sum(1 for o in found if o.name == p.name) > 1})
        if twice:
            die(f"{src} holds more than one file called {', '.join(twice)}")
        for p in found:
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
    info = {
        "fps": fps,
        "frames": int(seq.find("duration").text),
        "width": int(sc.find("width").text),
        "height": int(sc.find("height").text),
        "clips": {},
        "chapters": [],
    }
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
                            "crop": [
                                float(p[k].find("value").text)
                                for k in ("leftcrop", "rightcrop", "topcrop", "bottomcrop")
                            ],
                        }
                    except KeyError, AttributeError:
                        pass
    for m in seq.findall("marker"):
        if (m.findtext("type") or "").lower() == "chapter":
            info["chapters"].append(
                {"title": m.findtext("name"), "t0": int(m.findtext("in")) / fps, "t1": int(m.findtext("out")) / fps}
            )
    return info


NOTES = []


def loose(source):
    """A folder of track downloads, each WAV paired with its video by the name Riverside gives a person's files.

    Riverside keeps a person's files in a folder of their own, <name>-<id>, and serves the video as
    <name>-<id>-CFR.mp4 and the WAV as <name>-<id>.wav. A browser saves the video under that name but the WAV as
    riverside_<name>_raw-audio_<studio>_<n>.wav, which shares only the name the person typed at the door."""
    ids = {}
    files = sorted(p for p in source.iterdir() if p.suffix.lower() in (".wav", ".mp4", ".mov"))
    for p in files:
        if p.suffix.lower() != ".wav":
            ids.setdefault(re.sub(r"-CFR$", "", p.stem, flags=re.I), {})["video"] = p.name
    for p in files:
        if p.suffix.lower() != ".wav" or p.stem.endswith(f"-{RATE}"):  # the latter is ingest's own resampled copy
            continue
        key = p.stem
        m = re.match(r"riverside_(.+?)_raw-audio_", p.name, re.I)
        if m and key not in ids:
            mine = [k for k in ids if k.lower().startswith(m.group(1).lower() + "-")]
            if len(mine) > 1:
                die(f"{p.name} could be the audio of {' or '.join(mine)}: rename it to one of those, .wav")
            key = mine[0] if mine else m.group(1)
        if "audio" in ids.get(key, {}):
            die(f"two WAVs for {key}: {ids[key]['audio']} and {p.name}")
        ids.setdefault(key, {})["audio"] = p.name
    return ids


def host_called(pid):
    """The host a loose track belongs to, by the name they typed at Riverside's door: the files are
    <that name>-<id>, and a host types their full name or their first."""
    said = re.sub(r"-[0-9a-f]{8}(-[0-9a-f]+)+$", "", pid.lower())
    said = re.sub(r"[^a-z0-9]+", " ", said).strip()
    for person in load_json(SHOW).get("people", {}).values():
        full = re.sub(r"[^a-z0-9]+", " ", person["name"].lower()).strip()
        if said in (full, full.split()[0]):
            return person["name"]
    return None


def pcm(path, rate):
    """A file's audio as mono floats at `rate`, sample k at time k / rate: the whole of it, decoded from the
    top, with silence where the stream starts late. A seek into a file's padding is not to be trusted to land
    where it was asked, and a stretch cut that way once came back two seconds short."""
    import numpy as np

    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-vn", "-ac", "1"]
    r = subprocess.run([*cmd, "-af", f"aresample={rate}:async=1:first_pts=0", "-f", "f32le", "-"], capture_output=True)
    if r.returncode != 0:
        die("ffmpeg failed:\n" + r.stderr.decode("utf-8", "replace")[-1500:])
    return np.frombuffer(r.stdout, dtype=np.float32)


def lag_of(track, ref, rate):
    """How many seconds into `ref` the start of `track` sits, and how far that answer stands above the next
    best. Cross-correlation with the phase transform: every frequency votes alike, so the peak is a sample
    wide even between a WAV and the AAC copy of it inside a video."""
    import numpy as np

    n = 1 << (len(track) + len(ref)).bit_length()
    x = np.fft.rfft(ref, n) * np.conj(np.fft.rfft(track, n))
    cc = np.fft.irfft(x / (np.abs(x) + 1e-12), n)
    k = int(np.argmax(cc))
    peak = float(cc[k])
    cc[max(0, k - 50) : k + 50] = 0
    return (k if k < n // 2 else k - n) / rate, peak / (float(np.abs(cc).max()) or 1e-12)


def place(wav, video):
    """Where a WAV sits in its own video's audio, and what the answer rests on.

    The whole file at a low rate finds the place wherever it is: a guest who joined at minute forty is forty
    minutes in. Three one-minute windows at a higher rate, near the start, the middle and the end, then settle
    it to a fraction of a millisecond and say whether it holds across the file. The two are one recording, so a
    place that moves means they are not what they are taken for."""
    coarse, peak = lag_of(pcm(wav, COARSE), pcm(video, COARSE), COARSE)
    fine = []
    if peak >= SURE:
        track, ref = pcm(wav, FINE), pcm(video, FINE)
        for at in (0.1, 0.5, 0.9):
            t = max(0, int(at * len(track)) - 30 * FINE)
            r0 = max(0, t + int((coarse - 2) * FINE))
            a, b = track[t : t + 60 * FINE], ref[r0 : r0 + 64 * FINE]
            if min(len(a), len(b)) < FINE:
                continue
            lag, sure = lag_of(a, b, FINE)
            if sure >= SURE:
                fine.append((r0 - t) / FINE + lag)
    return {
        "lag": statistics.median(fine) if fine else coarse,
        "peak": peak,
        "windows": len(fine),
        "spread": max(fine) - min(fine) if fine else 0.0,
    }


def settle(d, parts):
    """Loose tracks come with no timeline, so each WAV is placed by the audio inside its own video. An aligned
    video starts at the shared zero, behind black if the person joined late, and carries the same recording as
    the WAV behind the same wait: where the WAV is found in it is where the WAV belongs. The video stays at
    zero. What could not be measured is said, and stays at zero as an assumption."""
    notes = []
    for p in parts:
        if "audio" not in p or "video" not in p:
            notes.append(f"{p['short']}: one file, nothing to place it against: offset assumed 0")
            continue
        if not p["video"]["has_audio"]:
            notes.append(f"{p['short']}: the video has no audio inside to place the WAV against: offset assumed 0")
            continue
        found = place(d / p["audio"]["file"], d / p["video"]["file"])
        if found["peak"] < SURE:
            notes.append(
                f"{p['short']}: the WAV was not found in its video's audio (the best match stands x{found['peak']:.1f} "
                "above the rest): offset assumed 0. Are the two files the same person's?"
            )
            continue
        lag = 0.0 if abs(found["lag"]) < 0.001 else found["lag"]
        p["offset"], p["video"]["offset"] = round(lag, 4), 0.0
        p["placed"] = {
            "by": "the audio inside its video",
            "peak": round(found["peak"], 1),
            "windows": found["windows"],
            "spread_ms": round(found["spread"] * 1000, 2),
        }
        if found["spread"] > 0.005:
            notes.append(
                f"{p['short']}: the WAV's place in its video moves by {found['spread'] * 1000:.1f} ms across the "
                "file: they are not one recording"
            )
    base = min([p["offset"] for p in parts] + [p["video"]["offset"] for p in parts if "offset" in p.get("video", {})])
    for p in parts:
        p["offset"] = round(p["offset"] - base, 4)
        if "offset" in p.get("video", {}):
            p["video"]["offset"] = round(p["video"]["offset"] - base, 4)
    for p in parts:
        if "audio" in p and "video" in p:
            gap = abs(p["offset"] + p["audio"]["duration"] - video_offset(p) - p["video"]["duration"])
            if gap > 0.5:
                notes.append(f"{p['short']}: once the WAV is placed, audio and video end {gap:.2f}s apart")
    lengths = [p["video"]["duration"] for p in parts if "video" in p]
    if lengths and max(lengths) - min(lengths) > 1.0:
        notes.append(
            f"the videos differ in length by {max(lengths) - min(lengths):.1f}s: fine if someone left early. If "
            "someone joined late, theirs must be the Aligned video download, which carries the wait as black; "
            "a Raw video would put them at the start"
        )
    return notes


def seat(parts, order):
    """Left to right, for tracks no timeline laid out: --order, or the show's seats (tools/show.json) with the
    guest as the focus of the picture: in the middle when the hosts are both there, on the right of a host
    alone. Written as the centre a timeline's layout gave, which is all the picture reads."""
    if order:
        row = []
        for w in (x.strip() for x in order.split(",") if x.strip()):
            hit = [p for p in parts if p["short"] == w or p["id"].startswith(w)]
            if len(hit) != 1 or hit[0] in row:
                die(f"--order {order}: '{w}' is not one track of {', '.join(p['short'] for p in parts)}")
            row.append(hit[0])
        if len(row) != len(parts):
            die(f"--order {order} leaves out {', '.join(p['short'] for p in parts if p not in row)}")
    else:
        seats = load_json(SHOW).get("seats") or []
        hosts = sorted((p for p in parts if p["short"] in seats), key=lambda p: seats.index(p["short"]))
        guests = [p for p in parts if p["short"] not in seats]
        half = len(hosts) // 2 if len(hosts) > 1 else len(hosts)
        row = hosts[:half] + guests + hosts[half:]
    for i, p in enumerate(row):
        p["layout"] = {"centre": round((i + 0.5) / len(row) - 0.5, 4)}


def participants(source, xml):
    """Pair each audio file with its video by shared id."""
    ids = {}
    for p in source.iterdir():
        m = re.match(r"(.+?)[-_](audio|video)\.(wav|mp4|mov|m4a|mp3)$", p.name, re.I)
        if m:
            ids.setdefault(m.group(1), {})[m.group(2).lower()] = p.name
    if not ids:
        ids = loose(source)
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
            NOTES.append(
                f"the tracks' relative offsets vary by {drift:.3f}s across the "
                f"timeline's cuts; read at sequence {s_ref:.3f}s"
            )
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
            out.append(
                f"{short}: Riverside's kept stretches do not line up with {kept[0][0]}'s "
                f"({hit} of {len(edges)} edges within a frame): the track offsets are suspect"
            )
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("package", nargs="?", help="Riverside timeline zip, or a folder of tracks")
    ap.add_argument("--names", default="", help="id-prefix=name pairs, comma separated")
    ap.add_argument(
        "--order", help="left to right, for a folder of tracks: first names or id prefixes, comma separated"
    )
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
            print(
                f"  {p['short']:<8} {p['name']:<18} offset {p['offset']:+.3f}s  "
                f"{p['video']['width']}x{p['video']['height']}  audio {p['audio']['rate']} Hz"
            )
        for c in m["chapters"]:
            print(f"  chapter {c['t0']:8.2f}s  {c['title']}")
        return 0

    if not a.package:
        die("a package (zip or folder) is needed unless --show")
    if src.exists() and any(src.iterdir()) and not a.force:
        print(f"{src.relative_to(ROOT)} already unpacked; reading it (use --force to replace)")
        package = load_json(manifest_path).get("package") if manifest_path.exists() else pathlib.Path(a.package).name
    else:
        if src.exists():
            shutil.rmtree(src)
        package = unpack(a.package, src)

    xml_path = next(iter(sorted(src.glob("*.xml"))), None)
    xml = read_xml(xml_path) if xml_path else None
    if not xml:
        print("no timeline XML: loose tracks, each WAV placed by the audio inside its own video")

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
        if not name and not xml:
            name = host_called(p["id"])
        if not name:
            warnings.append(f'no name for {p["id"]} — pass --names {handle(p["id"])}="Full Name"')
            name = handle(p["id"])
        short = re.sub(r"[^a-z0-9]", "", name.split()[0].lower())
        entry = {"id": p["id"], "name": name, "short": short, "offset": p["offset"], "layout": p["layout"]}
        if p.get("edit") and len(p["edit"]) > 1:
            # Riverside's own cut, kept for the record and not applied: the
            # pipeline cuts from the raw track. Stretches of the raw track
            # that its edit kept, in this episode's time.
            kept = [(round(i_ + p["offset"], 3), round(o_ + p["offset"], 3)) for _, i_, o_ in p["edit"]]
            entry["riverside_edit"] = {
                "clips": len(kept),
                "kept_seconds": round(sum(b - a for a, b in kept), 1),
                "kept": kept,
            }
        if "audio" in p:
            info = ffprobe(src / p["audio"])
            au = info["audio"]
            path = p["audio"]
            if au["rate"] != RATE:
                out = src / (pathlib.Path(p["audio"]).stem + f"-{RATE}.wav")
                run(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-y",
                        "-i",
                        src / p["audio"],
                        "-ar",
                        RATE,
                        "-resampler",
                        "soxr",
                        "-precision",
                        "28",
                        "-c:a",
                        "pcm_s24le",
                        out,
                    ]
                )
                warnings.append(f"{short}: audio was {au['rate']} Hz, resampled to {RATE} -> {out.name}")
                path, info = out.name, ffprobe(out)
                au = info["audio"]
            entry["audio"] = {
                "file": f"source/{path}",
                "duration": info["duration"],
                "rate": au["rate"],
                "channels": au["channels"],
            }
        if "video" in p:
            info = ffprobe(src / p["video"])
            v = info["video"]
            entry["video"] = {
                "file": f"source/{p['video']}",
                "duration": info["duration"],
                "width": v["width"],
                "height": v["height"],
                "fps": v["fps"],
                "has_audio": info["audio"] is not None,
            }
            if not v["cfr"]:
                warnings.append(f"{short}: video is not constant frame rate")
            if xml and abs(v["fps"] - xml["fps"]) > 1e-3:
                warnings.append(f"{short}: video is {v['fps']} fps, sequence is {xml['fps']}")
        if xml and "audio" in entry and "video" in entry:
            gap = abs(entry["audio"]["duration"] - entry["video"]["duration"])
            if gap > 0.5:
                warnings.append(f"{short}: audio and video durations differ by {gap:.2f}s")
        parts.append(entry)

    if not parts:
        die(f"no tracks found in {src}")
    shorts = [p["short"] for p in parts]
    if len(set(shorts)) < len(shorts):
        die(
            f"two tracks are both '{next(x for x in shorts if shorts.count(x) > 1)}': "
            "give each a name of its own with --names"
        )
    if not xml:
        warnings += settle(d, parts)
        seat(parts, a.order)

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

        chapters = [
            {**c, "t0": round(to_src(c["t0"]) or 0.0, 3), "t1": round(to_src(c["t1"]) or duration, 3)} for c in chapters
        ]
    manifest = {
        "slug": a.slug,
        "package": package,
        "xml": f"source/{xml_path.name}" if xml_path else None,
        # what came from Riverside, as it came: the archive keeps these when there is no zip to keep
        "tracks": sorted(f"source/{p[k]}" for p in found for k in ("audio", "video") if k in p),
        "fps": fps,
        "duration": round(duration, 4),
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
        print(
            f"  {p['short']:<8} {p['name']:<18} offset {p['offset']:+.3f}s  "
            f"{v.get('width')}x{v.get('height')} {v.get('fps')}fps  "
            f"audio {au.get('rate')} Hz {au.get('duration', 0):.2f}s  "
            f"layout {p['layout']}"
        )
        if "placed" in p:
            pl = p["placed"]
            print(
                f"           placed by {pl['by']}: x{pl['peak']:.0f} above the next best, {pl['windows']} windows "
                f"within {pl['spread_ms']} ms; the video at {v['offset']:+.3f}s"
            )
    for c in manifest["chapters"]:
        print(f"  chapter {c['t0']:8.2f}s  {c['title']}")
    for w in warnings:
        print(f"  WARNING: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
