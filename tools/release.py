#!/usr/bin/env python3
"""
Everything a publisher needs, from what the render made: release.json.

    ./tools/release.py ep1
    ./tools/release.py ep1 --no-art           # skip the episode artwork render

Reads build/episodes/<slug>/{episode,edl,clips,manifest}.json, the words,
out/qc.json and the files in out/, plus tools/show.json for the show's own
words (name, people, URLs, footer), and writes to out/:

  release.json        one manifest: the show, the episode, its people, the
                      chapters, every file with its size and hash, the clips
                      with their place in the episode, the notes in every
                      shape, the post text per clip, a day-by-day plan
  chapters.json       Podcasting 2.0 chapters (application/json+chapters)
  transcript.vtt      WebVTT with <v Speaker> voice tags: the feed, the site
  transcript.json     Podcasting 2.0 JSON transcript, for the episode page
  notes.md            the show notes, readable
  description.html    the notes as the feed's <description>, Apple's tag set
  description.txt     the same, plain
  youtube.txt         the YouTube title, then the description with the
                      chapter lines YouTube turns into timestamp links
  episode-art.png     3000x3000 episode artwork, and episode-art.jpg at 1400
  clips/NN-slug.vtt   captions per clip, in the clip's time

Nothing here is audio or video; it runs in seconds and is safe to re-run.
The free text — a summary, links worth giving, a guest — comes from a
`notes` block in episode.json, written by a person or by Claude from the
transcript:

  "notes": {"summary": "Two or three sentences.",
            "links": [{"title": "Stalwart", "url": "https://stalw.art"}],
            "guests": [{"name": "...", "who": "one line", "href": "https://..."}],
            "questions": [4, 7],
            "tags": ["email", "self-hosting"]}

Without a summary the notes still build, from the chapter list, and the
warning says so. The episode's guid is minted here the first time and kept
in episode.json: a directory treats a changed guid as a new episode.

Times. Every time in release.json is in the output's time (the mp3 and the
mp4 agree), so a chapter's `start` is where YouTube, Apple and the site all
jump to, and a clip's `at` is where its first word lands in the episode.
"""

import argparse
import datetime as dt
import hashlib
import html
import json
import pathlib
import re
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from eplib import (ROOT, cap_first, caption_cues, caption_words, die, ep_dir, ffprobe,  # noqa: E402
                   hms, load_json, map_words, save_json, sub_edl, tidy_words)

SHOW = ROOT / "tools" / "show.json"
PI_NAMESPACE = uuid.UUID("ead4c236-bf58-58c6-a2c6-a6b28d128cb6")   # podcast:guid, per the spec
MIME = {".mp3": "audio/mpeg", ".mp4": "video/mp4", ".png": "image/png", ".jpg": "image/jpeg",
        ".srt": "application/x-subrip", ".vtt": "text/vtt", ".json": "application/json",
        ".txt": "text/plain", ".md": "text/markdown"}
YT_TITLE, YT_DESC, YT_TAGS = 100, 5000, 500


def mmss(t):
    """Chapter time the way YouTube's description wants it: 02:15, or 1:02:15."""
    s = hms(t, False)
    return s[3:] if t < 3600 else s.lstrip("0")


def esc_vtt(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def show_config():
    if not SHOW.exists():
        die(f"{SHOW.relative_to(ROOT)} missing: the show's name, people and URLs live there")
    show = load_json(SHOW)
    if not show.get("guid"):
        bare = re.sub(r"^[a-z]+://", "", show["feed_url"])
        show["guid"] = str(uuid.uuid5(PI_NAMESPACE, bare))
    return show


def episode_guid(d, cfg):
    """Minted once, kept in episode.json. Apple and every app key episodes
    on it; a guid that changes is a duplicate episode in every listener's
    feed, so it is never derived from anything that might be edited."""
    if cfg.get("guid"):
        return cfg["guid"], False
    cfg["guid"] = str(uuid.uuid4())
    save_json(d / "episode.json", cfg)
    return cfg["guid"], True


def the_questions(show):
    """The ten questions, from the site, so notes can cite them by number
    without the text being typed a second time."""
    src = ROOT / show.get("questions_from", "public/index.html")
    if not src.exists():
        return []
    m = re.search(r'<ol class="questions">(.*?)</ol>', src.read_text(), re.S)
    if not m:
        return []
    items = re.findall(r"<li>(.*?)</li>", m.group(1), re.S)
    return [html.unescape(re.sub(r"<[^>]+>", "", q)).strip() for q in items]


def people(m, show, notes, warn):
    """Who is on the episode. The hosts are in tools/show.json; anyone on a
    track who is not is a guest, with the line and the link the notes give
    them. A guest in the notes with no track (a name read out) is listed too."""
    guests = {g["name"]: g for g in notes.get("guests", [])}
    out = []
    for p in m["participants"]:
        known = show.get("people", {}).get(p["short"]) or next(
            (v for v in show.get("people", {}).values() if v.get("name") == p["name"]), None)
        g = guests.pop(p["name"], None)
        if known:
            out.append({"name": p["name"], "short": p["short"], "role": known.get("role", "host"),
                        "href": known.get("href"), "group": "cast"})
            continue
        if g is None:
            warn(f"{p['name']} is on a track but not in tools/show.json or notes.guests: "
                 f"listed as a guest with no line and no link")
        out.append({"name": p["name"], "short": p["short"], "role": "guest",
                    "href": (g or {}).get("href"), "who": (g or {}).get("who"), "group": "cast"})
    for g in guests.values():
        out.append({"name": g["name"], "role": "guest", "href": g.get("href"),
                    "who": g.get("who"), "group": "cast"})
    return out


def file_info(path, prev, media=True):
    st = path.stat()
    key = str(path.name if path.parent.name == "out" else f"{path.parent.name}/{path.name}")
    old = (prev or {}).get(key)
    info = {"bytes": st.st_size, "type": MIME.get(path.suffix, "application/octet-stream")}
    if old and old.get("bytes") == st.st_size and old.get("mtime") == int(st.st_mtime):
        info["sha256"] = old["sha256"]
    else:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        info["sha256"] = h.hexdigest()
    info["mtime"] = int(st.st_mtime)
    if media and path.suffix in (".mp3", ".mp4"):
        pr = ffprobe(path)
        info["duration"] = round(pr["duration"], 3)
        if pr["video"]:
            info["width"], info["height"] = pr["video"]["width"], pr["video"]["height"]
    return key, info


def transcript_files(out, mapped, names, show_name):
    """The transcript as cues: a cue never spans a speaker change, a long
    silence, or more than two lines of ninety characters, and closes at a
    sentence end once it has a line's worth. Apple shows the speaker's name
    from the VTT voice tag whenever it changes."""
    cues = caption_cues(mapped, max_chars=90, max_lines=2, max_dur=8.0, gap=1.0)
    vtt = ["WEBVTT", "", f"NOTE {show_name} transcript, one speaker per cue", ""]
    segs = []
    for cue in cues:
        text = cap_first(" ".join(w["w"] for w in cue))
        spk = names.get(cue[0].get("spk"), cue[0].get("spk", ""))
        t0, t1 = cue[0]["t0"], cue[-1]["t1"]
        vtt += [f"{hms(t0)} --> {hms(t1)}", f"<v {esc_vtt(spk)}>{esc_vtt(text)}", ""]
        segs.append({"speaker": spk, "startTime": t0, "endTime": t1, "body": text})
    (out / "transcript.vtt").write_text("\n".join(vtt))
    save_json(out / "transcript.json", {"version": "1.0.0", "segments": segs})
    return len(cues)


def clip_vtt(path, ws):
    cues = caption_cues(caption_words(ws), max_chars=26, max_lines=2, max_dur=5.0, gap=1.0, fill=0.7)
    lines = ["WEBVTT", ""]
    for cue in cues:
        lines += [f"{hms(cue[0]['t0'])} --> {hms(cue[-1]['t1'])}",
                  esc_vtt(" ".join(w["w"] for w in cue)), ""]
    path.write_text("\n".join(lines))
    return len(cues)


def clips_out(d, out, edl, words, din, clips, show, ep, names, prev, warn):
    res = []
    for i, c in enumerate(clips, 1):
        slug = c.get("slug") or f"clip-{i:02d}"
        base = f"{i:02d}-{slug}"
        mp4 = out / "clips" / f"{base}.mp4"
        if not mp4.exists():
            warn(f"clip {base}: not rendered (out/clips/{base}.mp4 missing); run clips.py")
            continue
        t0, t1 = c["t0"], c["t1"]
        cw = [w for w in words if t0 <= w["t0"] < t1]
        local = sub_edl(edl, t0, t1)
        ws = tidy_words(map_words(local, cw))
        in_ep = map_words(edl, cw, shift=din)
        at = in_ep[0]["t0"] if in_ep else None
        end = in_ep[-1]["t1"] if in_ep else None
        if at is None:
            warn(f"clip {base}: none of its words survive the episode's cut; no episode time")
        text = " ".join(w["w"] for w in ws)
        _, finfo = file_info(mp4, prev)
        dur = finfo.get("duration", local["duration"])
        n_cues = clip_vtt(mp4.with_suffix(".vtt"), ws)
        cast = " and ".join(n for n in names.values())
        alt = f"{cast} on the {show['name']}, episode {ep['number']}: {text}"
        if len(alt) > 1000:
            alt = alt[:997].rsplit(" ", 1)[0] + "..."
        hook = c.get("hook") or c["title"]
        tags = list(dict.fromkeys(show.get("hashtags", []) + c.get("tags", [])))
        entry = {"index": i, "slug": slug, "title": c["title"], "hook": hook,
                 "file": f"clips/{base}.mp4", "poster": f"clips/{base}.png",
                 "captions": f"clips/{base}.vtt", "caption_cues": n_cues,
                 "source": {"t0": t0, "t1": t1}, "at": at, "end": end,
                 "at_hms": mmss(at) if at is not None else None, "duration": round(dur, 3),
                 "text": text, "alt": alt, "hashtags": tags,
                 "post": {"text": f"{hook}\n\nFrom {show['name']} episode {ep['number']}, "
                                  f"“{ep['title']}”.",
                          "link": ep["url"], "link_at": f"{ep['url']}#t={int(at)}" if at is not None else ep["url"],
                          "at_hms": mmss(at) if at is not None else None},
                 **{k: v for k, v in finfo.items() if k != "mtime"}}
        if not 15 <= dur <= 120:
            warn(f"clip {base}: {dur:.1f}s is outside the 15-120s a podcast:soundbite allows")
        res.append(entry)
    return res


def build_notes(ep, show, chapters, links, questions_text, people_, warn):
    """The notes in the AGE-61 shape: who, what, the chapters with times,
    what was mentioned, where to find the guest, the standing footer."""
    hosts = [p for p in people_ if p["role"] == "host"]
    guests = [p for p in people_ if p["role"] == "guest"]
    summary = ep.get("summary")
    if not summary:
        warn("notes.summary missing from episode.json: the notes open with the chapter list only")
        summary = "In this episode: " + "; ".join(c["title"] for c in chapters if c["title"] != "Outro") + "."
    guest_line = None
    if guests:
        guest_line = "With " + ", ".join(
            f"{g['name']}" + (f", {g['who']}" if g.get("who") else "") for g in guests) + "."
    chap_lines = [f"{c['hms']} {c['title']}" for c in chapters]
    q_lines = []
    for n in ep.get("questions", []):
        if 1 <= n <= len(questions_text):
            q_lines.append(f"{n}. {questions_text[n - 1]}")
        else:
            warn(f"notes.questions: no question {n} on the site")
    footer = [ln.format(site=show["site"], feed_url=show["feed_url"]) for ln in show.get("footer", [])]
    cast_lines = [f"{p['name']}" + (f" — {p['href']}" if p.get("href") else "") for p in hosts + guests]

    # markdown, for reading
    md = [f"# {ep['title']}", "",
          f"{show['name']}, episode {ep['number']} · {ep['date']} · {ep['duration_hms']}", "",
          summary, ""]
    if guest_line:
        md += [guest_line, ""]
    md += ["## Chapters", ""] + chap_lines + [""]
    if links:
        md += ["## Mentioned", ""] + [f"- [{l['title']}]({l['url']})" for l in links] + [""]
    if q_lines:
        md += ["## Of the ten questions, this one touches", ""] + q_lines + [""]
    md += ["## Who", ""] + cast_lines + ["", "---", ""] + footer
    markdown = "\n".join(md) + "\n"

    # plain text: the feed's fallback and most of the socials
    txt = [summary]
    if guest_line:
        txt.append(guest_line)
    txt += ["", "Chapters:"] + chap_lines
    if links:
        txt += ["", "Mentioned:"] + [f"{l['title']}: {l['url']}" for l in links]
    if q_lines:
        txt += ["", "Of the ten questions, this one touches:"] + q_lines
    txt += [""] + footer
    text = "\n".join(txt) + "\n"

    # html, in the tag set Apple's description allows: p, ol, ul, li, a
    h = [f"<p>{html.escape(summary)}</p>"]
    if guest_line:
        h.append(f"<p>{html.escape(guest_line)}</p>")
    h.append("<p>Chapters:</p><ul>" + "".join(f"<li>{html.escape(ln)}</li>" for ln in chap_lines) + "</ul>")
    if links:
        h.append("<p>Mentioned:</p><ul>" + "".join(
            f'<li><a href="{html.escape(l["url"], quote=True)}">{html.escape(l["title"])}</a></li>' for l in links) + "</ul>")
    if q_lines:
        h.append("<p>Of the ten questions, this one touches:</p><ul>" + "".join(f"<li>{html.escape(q)}</li>" for q in q_lines) + "</ul>")
    for ln in footer:
        ln_h = html.escape(ln)
        for url in re.findall(r"https?://\S+", ln):
            ln_h = ln_h.replace(html.escape(url), f'<a href="{html.escape(url, quote=True)}">{html.escape(url)}</a>')
        h.append(f"<p>{ln_h}</p>")
    htm = "\n".join(h) + "\n"

    # youtube: the title line, a blank, then the description. Chapter lines
    # starting at 0:00, three or more, ten seconds apart, become the chapter
    # bar and timestamp links; the rules are checked below.
    yt_title = show["youtube"]["title"].format(title=ep["title"], show=show["name"], number=ep["number"])
    yt = [summary]
    if guest_line:
        yt.append(guest_line)
    yt += ["", "Chapters:"] + chap_lines
    if links:
        yt += ["", "Mentioned:"] + [f"{l['title']}: {l['url']}" for l in links]
    if q_lines:
        yt += ["", "Of the ten questions, this one touches:"] + q_lines
    yt += ["", f"Episode page and transcript: {ep['url']}"] + footer
    yt += ["", " ".join("#" + t for t in show.get("hashtags", []))]
    yt_desc = "\n".join(yt) + "\n"
    if len(yt_title) > YT_TITLE:
        warn(f"YouTube title is {len(yt_title)} characters; the limit is {YT_TITLE}")
    if len(yt_desc) > YT_DESC:
        warn(f"YouTube description is {len(yt_desc)} characters; the limit is {YT_DESC}")
    tags = show["youtube"].get("tags", []) + ep.get("tags", [])
    if len(",".join(tags)) > YT_TAGS:
        warn(f"YouTube tags run to {len(','.join(tags))} characters; the limit is {YT_TAGS}")
    if len(chapters) < 3:
        warn("fewer than three chapters: YouTube shows no chapter bar")
    for a, b in zip(chapters, chapters[1:]):
        if b["start"] - a["start"] < 10:
            warn(f"chapters {a['title']!r} and {b['title']!r} are under 10s apart: YouTube drops the bar")
    if chapters and chapters[0]["start"] != 0:
        warn("the first chapter does not start at 0:00: YouTube shows no chapter bar")
    return {"markdown": markdown, "text": text, "html": htm, "summary": summary,
            "youtube": {"title": yt_title, "description": yt_desc, "tags": tags,
                        "category": show["youtube"].get("category"),
                        "playlist": show["youtube"].get("playlist")}}


def episode_art(out, ep, theme, prev, force):
    sig = {"title": ep["title"], "number": ep["number"], "theme": theme}
    png, jpg = out / "episode-art.png", out / "episode-art.jpg"
    if not force and png.exists() and jpg.exists() and (prev or {}).get("art") == sig:
        return sig, False
    from banner import render
    from show import build
    html_, w, h = build("episodeart", theme, ep["title"], None, number=ep["number"])
    render(html_, png, w, h)
    from PIL import Image
    Image.open(png).convert("RGB").resize((1400, 1400), Image.LANCZOS).save(jpg, quality=90)
    return sig, True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--no-art", action="store_true", help="do not render the episode artwork")
    ap.add_argument("--art", action="store_true", help="render the artwork even if it is current")
    ap.add_argument("--theme", default="noir", help="noir (default) or newsprint")
    a = ap.parse_args()

    d = ep_dir(a.slug)
    out = d / "out"
    for f in ("episode.json", "edl.json", "manifest.json", "transcript/words.json", "out/qc.json"):
        if not (d / f).exists():
            die(f"{f} missing: release comes after assemble")
    cfg, edl, m = load_json(d / "episode.json"), load_json(d / "edl.json"), load_json(d / "manifest.json")
    words, qc = load_json(d / "transcript/words.json"), load_json(out / "qc.json")
    clips = load_json(d / "clips.json") if (d / "clips.json").exists() else []
    show = show_config()
    prev = load_json(out / "release.json") if (out / "release.json").exists() else {}
    notes = cfg.get("notes", {})
    warnings = []

    def warn(msg):
        warnings.append(msg)
        print(f"  warning: {msg}")

    if any(v.startswith("TO BE WRITTEN") for v in show.get("description", {}).values()):
        warn("the show description in tools/show.json is a placeholder (AGE-59)")

    guid, minted = episode_guid(d, cfg)
    if minted:
        print(f"  guid minted and kept in episode.json: {guid}")
    din = qc["timings"]["dialogue_in"]
    names = words.get("speakers", {})
    total = qc.get("episode_mp4_duration") or qc["timings"]["total"]
    ep = {"number": cfg["number"], "season": cfg.get("season"), "type": cfg.get("type", "full"),
          "title": cfg["title"], "slug": a.slug, "guid": guid, "date": cfg["date"],
          "release": cfg.get("release") or cfg["date"], "explicit": bool(cfg.get("explicit", show.get("explicit", False))),
          "duration": round(total, 3), "duration_hms": mmss(total),
          "url": show["episode_url"].format(slug=a.slug), "media_url": show["media_url"].format(slug=a.slug),
          "summary": notes.get("summary"), "questions": notes.get("questions", []),
          "tags": notes.get("tags", []),
          "approved": {k: v for k, v in (cfg.get("approved") or {}).items() if k != "files"} or None}

    # chapters, from the render's own list, so the mp3's ID3 chapters, the
    # video's chapter cards, YouTube and the feed all say the same times
    chapters = [{"title": c["title"], "start": c["t"], "hms": mmss(c["t"])} for c in qc["chapters"]]
    save_json(out / "chapters.json", {
        "version": "1.2.0", "title": ep["title"], "podcastName": show["name"],
        "chapters": [{"startTime": c["start"], "title": c["title"]} for c in chapters]})

    print("transcript")
    mapped = tidy_words(map_words(edl, words["words"], shift=din))
    n_cues = transcript_files(out, mapped, names, show['name'])
    print(f"  {n_cues} cues, {len(mapped)} words, {len(names)} speakers")

    print("clips")
    people_ = people(m, show, notes, warn)
    clip_entries = clips_out(d, out, edl, words["words"], din, clips, show, ep, names, prev.get("files"), warn)
    for c in clip_entries:
        print(f"  {c['index']:02d} {c['slug']:<28} at {c['at_hms'] or '--:--':>6}  {c['duration']:.1f}s  {c['caption_cues']} cues")

    print("notes")
    links = notes.get("links", [])
    notes_out = build_notes(ep, show, chapters, links, the_questions(show), people_, warn)
    (out / "notes.md").write_text(notes_out["markdown"])
    (out / "description.txt").write_text(notes_out["text"])
    (out / "description.html").write_text(notes_out["html"])
    (out / "youtube.txt").write_text(notes_out["youtube"]["title"] + "\n\n" + notes_out["youtube"]["description"])
    print(f"  youtube title {len(notes_out['youtube']['title'])} chars, description {len(notes_out['youtube']['description'])}; "
          f"{len(chapters)} chapters, {len(links)} links")

    art = prev.get("art")
    if not a.no_art:
        art, made = episode_art(out, ep, a.theme, prev, a.art)
        print(f"  episode-art.png {'rendered' if made else 'current'}")

    print("files")
    files = {}
    for name in ("episode.mp3", "episode.mp4", "captions.srt", "transcript.vtt", "transcript.json",
                 "chapters.json", "thumbnail.png", "episode-art.png", "episode-art.jpg"):
        p = out / name
        if p.exists():
            k, info = file_info(p, prev.get("files"))
            files[k] = info
        elif name in ("episode.mp3", "episode.mp4"):
            warn(f"{name} missing: assemble has not rendered it")
    for c in clip_entries:
        for key in ("file", "poster", "captions"):
            p = out / c[key]
            if p.exists():
                k, info = file_info(p, prev.get("files"))
                files[k] = info
    files["chapters.json"]["type"] = "application/json+chapters"
    for k in ("episode.mp3", "episode.mp4"):
        if k in files:
            print(f"  {k:<14} {files[k]['bytes'] / 1e6:8.1f} MB  {files[k].get('duration', 0):.1f}s  {files[k]['sha256'][:12]}")
    print(f"  {len(files)} files listed")

    # a day-by-day plan the publisher can follow or override: the episode on
    # the release day, then one clip a day
    day0 = dt.date.fromisoformat(ep["release"])
    plan = [{"asset": "episode", "day": 0, "date": day0.isoformat()}]
    for i, c in enumerate(clip_entries, 1):
        plan.append({"asset": f"clip:{c['index']:02d}-{c['slug']}", "day": i, "date": (day0 + dt.timedelta(days=i)).isoformat()})

    rel = {"slug": a.slug, "generated": dt.datetime.now().isoformat(timespec="seconds"),
           "show": {k: show.get(k) for k in ("name", "subtitle", "guid", "site", "feed_url", "language",
                                             "categories", "explicit", "owner", "description", "hashtags")},
           "episode": ep, "people": people_,
           "chapters": chapters,
           "soundbites": [{"startTime": c["at"], "duration": round(c["duration"], 1), "title": c["title"]}
                          for c in clip_entries if c["at"] is not None],
           "transcript": {"vtt": "transcript.vtt", "json": "transcript.json", "srt": "captions.srt",
                          "txt": "transcript.txt", "cues": n_cues, "words": len(mapped)},
           "notes": {"summary": notes_out["summary"], "markdown": "notes.md", "html": "description.html",
                     "text": "description.txt", "youtube": "youtube.txt", "links": links},
           "youtube": notes_out["youtube"],
           "art": art, "files": files, "clips": clip_entries, "plan": plan, "warnings": warnings}
    save_json(out / "release.json", rel)
    print(f"{(out / 'release.json').relative_to(ROOT)}  {len(warnings)} warning{'s' if len(warnings) != 1 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
