"""
An approved, staged episode into the ledger: the episode row and one
deliverable per file in release.json, with the media-host URL for the public
ones and whether the archive holds it, both from staged.json. Re-running
refreshes what is there and touches no post.

Where the two records come from: build/episodes/<slug>/out on a workstation
that rendered the episode; otherwise (the VPS) the archive bucket, into the
data dir, since stage puts both there. `--archive` asks for the latter even
when build/ exists.
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
import archive  # noqa: E402
import ledger  # noqa: E402
from eplib import EPISODES, die, load_json  # noqa: E402

KINDS = (
    ("episode.mp3", "audio"), ("episode.mp4", "video"), ("captions.srt", "captions"),
    ("chapters.json", "chapters"), ("thumbnail.png", "thumbnail"),
)


def kind_of(name):
    for exact, kind in KINDS:
        if name == exact:
            return kind
    if name.startswith("transcript."):
        return "transcript"
    if name.startswith("episode-art."):
        return "art"
    if name.startswith("clips/"):
        return {"mp4": "clip", "png": "poster", "vtt": "clip-captions"}.get(name.rsplit(".", 1)[-1], "other")
    return "other"


def ingest(c, slug, src=None, who="engine", from_archive=False):
    d = pathlib.Path(src) if src else EPISODES / slug / "out"
    if from_archive or (not src and not (d / "release.json").exists()):
        try:
            d = archive.fetch_release(slug)
        except archive.ArchiveError as e:
            die(f"{slug}: no release.json in {d}, and the archive could not provide one: {e}")
    if not (d / "release.json").exists():
        die(f"no release.json in {d}: run release and approve first")
    if not (d / "staged.json").exists():
        die(f"{slug} is not staged ({d / 'staged.json'} missing): the engine publishes from R2, not from a workstation")
    r = load_json(d / "release.json")
    s = load_json(d / "staged.json")
    ep = r["episode"]
    if not ep.get("approved"):
        die(f"{slug} is not approved")
    release_at = ledger.at(ep["release"], ledger.CONFIG["release_time"])
    c.execute("BEGIN")
    c.execute("""INSERT INTO episode (slug, number, title, release, release_at, guid, url, media_url,
                   approved_by, approved_at, staged_at, release_json, source_dir, ingested_at)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(slug) DO UPDATE SET number=excluded.number, title=excluded.title,
                   release=excluded.release, release_at=excluded.release_at, guid=excluded.guid,
                   url=excluded.url, media_url=excluded.media_url, approved_by=excluded.approved_by,
                   approved_at=excluded.approved_at, staged_at=excluded.staged_at,
                   release_json=excluded.release_json, source_dir=excluded.source_dir,
                   ingested_at=excluded.ingested_at""",
              (slug, str(ep["number"]), ep["title"], ep["release"], release_at, ep["guid"], ep["url"],
               ep["media_url"], ep["approved"]["by"], ep["approved"]["at"], s.get("at"),
               json.dumps(r, ensure_ascii=False), str(d), ledger.now()))
    n = 0
    for name, f in r["files"].items():
        url = (s.get("media", {}).get(name) or {}).get("url")
        archived = 1 if f"episodes/{slug}/out/{name}" in s.get("archive", {}) else 0
        c.execute("""INSERT INTO deliverable (episode, name, kind, sha256, bytes, type, duration, width, height, url, archived)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(episode, name) DO UPDATE SET kind=excluded.kind, sha256=excluded.sha256,
                       bytes=excluded.bytes, type=excluded.type, duration=excluded.duration, width=excluded.width,
                       height=excluded.height, url=excluded.url, archived=excluded.archived""",
                  (slug, name, kind_of(name), f.get("sha256"), f.get("bytes"), f.get("type"), f.get("duration"),
                   f.get("width"), f.get("height"), url, archived))
        n += 1
    ledger.event(c, "ingest", who, episode=slug, detail={"files": n, "release": ep["release"], "staged_at": s.get("at")})
    c.execute("COMMIT")
    return n
