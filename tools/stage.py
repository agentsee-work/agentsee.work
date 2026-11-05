#!/usr/bin/env python3
"""
Put an episode in the right spot: a review render to the archive for its
guest to see, or the approved episode to the archive and the public media.

    ./tools/episode.py review ep4                # the render as it stands, to the archive only
    ./tools/episode.py stage ep4                 # the approved episode; wraps this in `op run`
    ./tools/stage.py ep4 --dry-run               # what would go, no credentials needed
    ./tools/stage.py ep4 --review --dry-run

Two R2 buckets (infra/podcast/r2.tf). The archive is private and keeps the masters
off the workstation: the Riverside package, the edit as data, and every
deliverable. The media bucket is public at media.agentsee.work and holds
what the feed, the episode page and the platforms fetch. The publisher reads
release.json from the archive, so the workstation can be off.

  archive  sources/<slug>/riverside-timeline.zip
           episodes/<slug>/edit/{episode,manifest,chapters,clips,removals,edl,words}.json
           episodes/<slug>/review/**        the render a guest reviews, unnumbered; review.json
                                            says which round it is and what changed
           episodes/<slug>/out/**           the approved episode: everything but the wav and the variants
           guests/<id>/review.json          the same round, where the office finds it for the guest
  media    podcast/<slug>/episode.mp3, episode-art.*, thumbnail.png,
           transcript.vtt, transcript.json, captions.srt, chapters.json,
           clips/*.{mp4,png,vtt}

A review (--review) needs no approval and puts nothing on the media host,
whose paths are guessable by slug: a guest's episode is public to nobody
before the guest has approved it (AGE-112). The final run refuses an
episode that is not approved, one whose files have changed since the
approval (episode.py approve records every hash), and one whose guests
(episode.json "guests": their ids on the engine's Guests screen) have no
approval in the archive, approved another round, or whose approval changed
since approve read it. Every object carries its SHA-256 as metadata, so a
re-run uploads only what changed, and out/staged.json records what went
where, and goes to the archive last (episodes/<slug>/out/staged.json), so
the engine can ingest from there. Credentials come from tools/stage.env
through `op run`; nothing is read from disk. office.py shares the
connection and the guests' records.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from eplib import ROOT, die, ep_dir, load_json, save_json  # noqa: E402
from release import MIME  # noqa: E402

VENV = ROOT / "build" / "venv-stage" / "bin" / "python"
EDIT = ["episode.json", "manifest.json", "chapters.json", "clips.json", "removals.json",
        "edl.json", "transcript/words.json"]
SKIP_OUT = {"episode.wav", "variants", "staged.json", "review.json"}
PUBLIC = {"episode.mp3", "episode-art.png", "episode-art.jpg", "thumbnail.png", "transcript.vtt",
          "transcript.json", "captions.srt", "chapters.json"}
PUBLIC_CLIP_SUFFIXES = {".mp4", ".png", ".vtt"}
CACHE = "public, max-age=86400"
ENV = ("R2_ACCOUNT_ID", "R2_ARCHIVE_BUCKET", "R2_MEDIA_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")


def boto(script=__file__):
    """boto3, from build/venv-stage: the calling script is started over there when it is not
    already, before it has printed anything."""
    try:
        import boto3  # noqa: F401
    except ImportError:
        # Compare prefixes, not executables: the venv's `python` is a symlink
        # to the system interpreter, so resolving it says "same" and nothing
        # would ever re-exec.
        venv = VENV.parent.parent
        if VENV.exists() and pathlib.Path(sys.prefix).resolve() != venv.resolve():
            sys.stdout.flush()
            os.execv(str(VENV), [str(VENV), str(script), *sys.argv[1:]])
        die("boto3 is missing: python3 -m venv build/venv-stage && build/venv-stage/bin/pip install boto3")
    import boto3
    return boto3


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def refused(e, buckets, what):
    """One line that says which of the three usual causes it is."""
    err = e.response.get("Error", {})
    status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    code = err.get("Code") or status
    if status == 401:
        why = ("R2 does not know these credentials: check the vault item's username is the "
               "Access Key ID and its credential the Secret Access Key, and that the token "
               "has not expired")
    elif status == 403:
        why = ("the token is not allowed this: it must be Object Read & Write, scoped to "
               f"both {buckets['archive']} and {buckets['media']}")
    else:
        why = err.get("Message", "")
    return f"{what}: {code}. {why}"


def source_in_archive(s3, buckets, slug, relp, p):
    """The key of the same file under another episode's out/ in the archive, when it is there at the same
    size (--from-archive): the bytes are copied inside the bucket rather than sent again."""
    src = f"episodes/{slug}/out/{relp}"
    try:
        head = s3.head_object(Bucket=buckets["archive"], Key=src)
    except s3.exceptions.ClientError as e:
        if e.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
            die(refused(e, buckets, f"cannot check {buckets['archive']}/{src}"))
        return None
    return src if head.get("ContentLength") == p.stat().st_size else None


def connect(boto3, slug="<slug>"):
    """The S3 client for R2 and the two bucket names, from the environment `op run` filled. Refuses
    early, and says why, when a credential is missing, misfiled or not allowed to read."""
    for var in ENV:
        if not os.environ.get(var):
            die(f"{var} is not set: run under `op run --env-file=tools/stage.env`, "
                f"or `episode.py stage {slug}` which does that")
    # An R2 token for the S3 API is two hex strings: the Access Key ID (32)
    # and the Secret Access Key (64, the SHA-256 of the token value). The
    # dashboard shows all three on the day the token is minted and never
    # again, and filing the token value as the secret is the easy mistake.
    akid, secret = os.environ["AWS_ACCESS_KEY_ID"], os.environ["AWS_SECRET_ACCESS_KEY"]
    if not re.fullmatch(r"[0-9a-f]{32}", akid):
        print(f"  warning: AWS_ACCESS_KEY_ID is {len(akid)} chars, not the 32 hex of an R2 Access Key ID")
    if re.fullmatch(r"[0-9A-Za-z_-]{40}", secret):
        die("AWS_SECRET_ACCESS_KEY is 40 chars: that is the token VALUE. The S3 secret is its "
            "SHA-256, shown as 'Secret Access Key' when the token was minted; mint again if lost")
    if not re.fullmatch(r"[0-9a-f]{64}", secret):
        print(f"  warning: AWS_SECRET_ACCESS_KEY is {len(secret)} chars, not the 64 hex of an R2 Secret Access Key")
    # A copy inside the bucket (--from-archive) answers only when R2 has made it, and a 2.4 GB video took
    # longer than botocore's minute, so the client waits a quarter of an hour for an answer.
    s3 = boto3.client("s3", region_name="auto",
                      endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
                      config=boto3.session.Config(connect_timeout=30, read_timeout=900))
    buckets = {"archive": os.environ["R2_ARCHIVE_BUCKET"], "media": os.environ["R2_MEDIA_BUCKET"]}
    # Cheap, before anything is uploaded: can the token read both buckets?
    for b, name in buckets.items():
        try:
            s3.list_objects_v2(Bucket=name, MaxKeys=1)
        except s3.exceptions.ClientError as e:
            die(refused(e, buckets, f"cannot read the {b} bucket {name}"))
    return s3, buckets


def get_json(s3, bucket, key):
    """A JSON object from a bucket, with the SHA-256 of its bytes; (None, None) when it is not there."""
    try:
        r = s3.get_object(Bucket=bucket, Key=key)
    except s3.exceptions.ClientError as e:
        if e.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
            return None, None
        die(refused(e, {"archive": bucket, "media": bucket}, f"cannot read {bucket}/{key}"))
    body = r["Body"].read()
    try:
        return json.loads(body), hashlib.sha256(body).hexdigest()
    except ValueError:
        die(f"{bucket}/{key} is not JSON")


def put_json(s3, bucket, key, doc):
    body = json.dumps(doc, indent=1).encode("utf-8")
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json",
                  Metadata={"sha256": hashlib.sha256(body).hexdigest()})


def guests_of(cfg, s3, bucket):
    """The office's record of each guest episode.json names (their ids on the engine's Guests
    screen): the guest as they want to be shown, their review notes and their approval, if those
    exist, with the hash of the approval's bytes so a later change is seen."""
    out = []
    for gid in cfg.get("guests") or []:
        g, _ = get_json(s3, bucket, f"guests/{gid}/guest.json")
        if not g:
            die(f"guest {gid} is not in the archive (guests/{gid}/guest.json): the ids are on the engine's Guests screen")
        approval, sha = get_json(s3, bucket, f"guests/{gid}/approval.json")
        notes, _ = get_json(s3, bucket, f"guests/{gid}/notes.json")
        out.append({"id": gid, "name": g.get("shown_name") or g.get("name"), "guest": g,
                    "approval": approval, "approval_sha256": sha, "notes": notes})
    return out


def review_record(d):
    p = d / "out" / "review.json"
    return load_json(p) if p.exists() else None


def hosts_of(cfg, s3, bucket):
    """The hosts' approvals of the round, as the engine mirrors them (episodes/<slug>/review/hosts.json)."""
    doc, _ = get_json(s3, bucket, f"episodes/{cfg['slug']}/review/hosts.json")
    return doc


def host_names():
    """The hosts, by first name, from the engine's config.json; none without it."""
    p = ROOT / "engine" / "config.json"
    return list((load_json(p).get("hosts") or []) if p.exists() else [])


def unapproved(guests, d, cfg, hosts=None):
    """Why the office's records stand in the way, as lines; none when each guest has approved the
    round that went for review, each host has approved it on the engine, the edit is still that
    round's, and the approval is the one approve recorded."""
    why = []
    if not guests:
        return why
    rev = review_record(d)
    if rev is not None:
        sha = hashlib.sha256(json.dumps(rev, sort_keys=True).encode("utf-8")).hexdigest()
        ok = {a.get("host") for a in ((hosts or {}).get("approvals") or [])
              if (hosts or {}).get("round") == rev.get("round") and a.get("sha256") == sha}
        for h in host_names():
            if h not in ok:
                why.append(f"{h} has not approved round {rev.get('round')} on the engine "
                           f"(episodes/{cfg['slug']}/review/hosts.json)")
    recorded = {g["id"]: g for g in ((cfg.get("approved") or {}).get("guests") or [])}
    for g in guests:
        a = g["approval"]
        if not a:
            why.append(f"{g['name']} ({g['id']}) has not approved: no guests/{g['id']}/approval.json in the archive")
            continue
        got = (a.get("review") or {}).get("round")
        if rev is None:
            why.append(f"{g['name']} approved round {got}, but this episode has never gone for review "
                       "from here (no out/review.json)")
        elif got != rev.get("round"):
            why.append(f"{g['name']} approved round {got}; the render here is round {rev.get('round')}: "
                       "`episode.py review` and ask again")
        r = recorded.get(g["id"])
        if r and r.get("sha256") != g["approval_sha256"]:
            why.append(f"{g['name']}'s approval changed since approve read it: approve again")
    if rev:
        for name in ("edl.json", "chapters.json"):
            p = d / name
            was = (rev.get("edit") or {}).get(name)
            now = sha256_of(p) if p.exists() else None
            if was != now:
                why.append(f"{name} is not the one reviewed in round {rev.get('round')}: the guest saw "
                           "another cut. `episode.py review` and ask again")
    return why


def plan(d, cfg, warn, review=False):
    """Every object to put: (bucket, key, path, content type)."""
    out = d / "out"
    items = []
    pkg = pathlib.Path(cfg.get("package") or "/nonexistent")
    if review:
        pass  # the source waits for the final
    elif pkg.is_file():
        items.append(("archive", f"sources/{cfg['slug']}/riverside-timeline.zip", pkg, "application/zip"))
    else:
        warn(f"the Riverside package is not at {pkg}: the archive will have no source")
    for name in EDIT:
        p = d / name
        if p.exists():
            items.append(("archive", f"episodes/{cfg['slug']}/edit/{p.name}", p, "application/json"))
    where = "review" if review else "out"
    for p in sorted(out.rglob("*")):
        if not p.is_file():
            continue
        relp = p.relative_to(out)
        if relp.parts[0] in SKIP_OUT:
            continue
        ctype = MIME.get(p.suffix, "application/octet-stream")
        if relp.as_posix() == "chapters.json":
            ctype = "application/json+chapters"
        items.append(("archive", f"episodes/{cfg['slug']}/{where}/{relp.as_posix()}", p, ctype))
        public = (relp.as_posix() in PUBLIC
                  or (relp.parts[0] == "clips" and p.suffix in PUBLIC_CLIP_SUFFIXES))
        if public and not review:
            items.append(("media", f"podcast/{cfg['slug']}/{relp.as_posix()}", p, ctype))
    return items


class Progress:
    """boto3 calls this from its worker threads with each chunk's byte count.

    On a terminal the line is redrawn in place a few times a second. Through a
    pipe (op run) that would be invisible until the line ends, so it prints a
    full line every tenth of the file or every 15 s, whichever comes first."""

    def __init__(self, total):
        self.total, self.done = total, 0
        self.t0 = self.last_t = time.monotonic()
        self.last_pct = 0.0
        self.tty = sys.stdout.isatty()
        self.lock = threading.Lock()

    def __call__(self, n):
        with self.lock:
            self.done += n
            now = time.monotonic()
            pct = 100.0 * self.done / self.total if self.total else 100.0
            final = self.done >= self.total
            if self.tty:
                if now - self.last_t < 0.25 and not final:
                    return
            elif pct - self.last_pct < 10 and now - self.last_t < 15 and not final:
                return
            self.last_t, self.last_pct = now, pct
            rate = self.done / max(now - self.t0, 1e-6) / 1e6
            line = f"    {self.done / 1e6:.1f}/{self.total / 1e6:.1f} MB  {pct:3.0f}%  {rate:.1f} MB/s"
            if self.tty:
                print("\r" + line.ljust(60), end="\n" if final else "", flush=True)
            else:
                print(line, flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--review", action="store_true",
                    help="the render as it stands, to the archive for the guest's review: no approval, nothing public")
    ap.add_argument("--dry-run", action="store_true", help="list what would go; no credentials needed")
    ap.add_argument("--no-source", action="store_true", help="stage without the Riverside package")
    ap.add_argument("--from-archive", metavar="SLUG",
                    help="with --review: a file the archive already holds at episodes/SLUG/out/ with the same size "
                         "is copied there, not uploaded (a rehearsal from a staged episode, over a slow line)")
    a = ap.parse_args()
    if a.from_archive and not a.review:
        die("--from-archive goes with --review: the final run puts the episode's own files")
    # Under `op run` stdout is a pipe, so progress would otherwise arrive at exit.
    sys.stdout.reconfigure(line_buffering=True)
    # Before anything is printed: a re-exec into the venv starts main over.
    boto3 = None if a.dry_run else boto()

    d = ep_dir(a.slug)
    cfg = load_json(d / "episode.json")
    if not (d / "out" / "release.json").exists():
        die("no out/release.json: run the pipeline first")
    rel = load_json(d / "out" / "release.json")
    warnings = []

    def warn(msg):
        warnings.append(msg)
        print(f"  warning: {msg}")

    ok = None if a.review else cfg.get("approved")
    if not a.review:
        if not ok and not a.dry_run:
            die("not approved: `episode.py approve <slug> --release YYYY-MM-DD` once the guest and the user have "
                "approved the assets")
        if ok:
            changed = [k for k, v in rel["files"].items() if ok["files"].get(k) != v["sha256"]]
            missing = [k for k in ok["files"] if k not in rel["files"]]
            if changed or missing:
                for k in changed:
                    print(f"  changed since approval: {k}")
                for k in missing:
                    print(f"  gone since approval: {k}")
                die("the deliverables are not the ones approved: re-run release and approve again")
            if cfg.get("guests") and "guests" not in ok:
                die("the approval predates the guest gate: approve again, so the guests' records are read")

    items = plan(d, cfg, warn, review=a.review)
    if warnings and not a.no_source and not a.dry_run:
        die("fix the above, or --no-source to archive without the package")
    total = sum(p.stat().st_size for _, _, p, _ in items)
    by_bucket = {b: sum(1 for x in items if x[0] == b) for b in ("archive", "media")}
    print(f"{a.slug}{' for review' if a.review else ''}: {len(items)} objects, {total / 1e9:.2f} GB  "
          f"(archive {by_bucket['archive']}, media {by_bucket['media']})")
    if a.dry_run:
        for b, key, p, ctype in items:
            print(f"  {b:<7} {key:<60} {p.stat().st_size / 1e6:9.1f} MB  {ctype}")
        if a.from_archive:
            print(f"  a file the archive holds at episodes/{a.from_archive}/out/ at the same size is copied, not put")
        return 0

    s3, buckets = connect(boto3, a.slug)
    guests = guests_of(cfg, s3, buckets["archive"])
    if not a.review:
        why = unapproved(guests, d, cfg, hosts_of(cfg, s3, buckets["archive"]))
        if why:
            for w in why:
                print(f"  guest: {w}")
            die("the guests have not approved this render: it stays off the media host")

    hashes = {k: v["sha256"] for k, v in rel["files"].items()}
    put = skipped = 0
    archived, media = {}, {}
    media_url = rel["episode"]["media_url"].rstrip("/")
    for n, (b, key, p, ctype) in enumerate(items, 1):
        relp = p.relative_to(d / "out").as_posix() if (d / "out") in p.parents else None
        digest = hashes.get(relp) or sha256_of(p)
        try:
            there = s3.head_object(Bucket=buckets[b], Key=key).get("Metadata", {}).get("sha256")
        except s3.exceptions.ClientError as e:
            if e.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
                die(refused(e, buckets, f"cannot check {buckets[b]}/{key}"))
            there = None  # absent is the one answer that means "upload"
        src = source_in_archive(s3, buckets, a.from_archive, relp, p) if a.from_archive and b == "archive" and relp else None
        if there == digest:
            skipped += 1
            record = "unchanged"
        elif src:
            try:
                s3.copy_object(Bucket=buckets[b], Key=key, CopySource={"Bucket": buckets[b], "Key": src},
                               MetadataDirective="REPLACE", ContentType=ctype, Metadata={"sha256": digest})
            except s3.exceptions.ClientError as e:
                die(refused(e, buckets, f"the copy of {src} to {key} failed"))
            put += 1
            record = "copied"
            print(f"  {b:<7} {key}  copied from {src}  ({n}/{len(items)})", flush=True)
        else:
            extra = {"ContentType": ctype, "Metadata": {"sha256": digest}}
            if b == "media":
                extra["CacheControl"] = CACHE
            size = p.stat().st_size
            big = size >= 8_000_000  # boto3's multipart threshold; below it there is nothing to watch
            if big:
                print(f"  {b:<7} {key}  {size / 1e6:.1f} MB  ({n}/{len(items)})", flush=True)
            t0 = time.monotonic()
            try:
                s3.upload_file(str(p), buckets[b], key, ExtraArgs=extra,
                               Callback=Progress(size) if big else None)
            except (s3.exceptions.ClientError, boto3.exceptions.S3UploadFailedError) as e:
                die(f"upload of {buckets[b]}/{key} failed: {e}")
            except KeyboardInterrupt:
                die(f"interrupted during {buckets[b]}/{key}. Finished objects are kept; re-run to "
                    "continue. R2 drops the unfinished parts itself after seven days")
            put += 1
            record = "put"
            took = max(time.monotonic() - t0, 1e-6)
            if big:
                print(f"    done in {took:.0f} s, {size / 1e6 / took:.1f} MB/s", flush=True)
            else:
                print(f"  {b:<7} {key}  {size / 1e6:.1f} MB  ({n}/{len(items)})", flush=True)
        if b == "media":
            media[relp] = {"url": f"{media_url}/{relp}", "sha256": digest, "state": record}
        else:
            archived[key] = {"sha256": digest, "state": record}

    if a.review:
        # The round: one more than the last the archive has for this slug.
        # What changed is said file by file, so the office can tell the
        # guest what to look at again.
        prev, _ = get_json(s3, buckets["archive"], f"episodes/{a.slug}/review/review.json")
        before = (prev or {}).get("files") or {}
        files = {k.split("/review/", 1)[1]: v["sha256"] for k, v in archived.items() if "/review/" in k}
        review = {"slug": a.slug, "round": int((prev or {}).get("round") or 0) + 1,
                  "at": dt.datetime.now().isoformat(timespec="seconds"),
                  "number": cfg.get("number"), "title": cfg.get("title"),
                  "guests": [g["id"] for g in guests],
                  "edit": {name: sha256_of(d / name) for name in ("edl.json", "chapters.json", "removals.json")
                           if (d / name).exists()},
                  "files": files,
                  "changed": sorted(k for k, v in files.items() if before.get(k) != v) if prev else []}
        save_json(d / "out" / "review.json", review)
        try:
            put_json(s3, buckets["archive"], f"episodes/{a.slug}/review/review.json", review)
            for g in guests:
                put_json(s3, buckets["archive"], f"guests/{g['id']}/review.json", review)
        except s3.exceptions.ClientError as e:
            die(refused(e, buckets, "the review record did not go up"))
        who = ", ".join(g["name"] for g in guests) or "no guest named in episode.json"
        print(f"review round {review['round']}: {put} put, {skipped} unchanged; "
              f"{len(review['changed']) if prev else len(files)} file(s) new for {who}. Nothing is public.")
        return 0

    staged = {"slug": a.slug, "at": dt.datetime.now().isoformat(timespec="seconds"),
              "approved": {k: v for k, v in ok.items() if k != "files"},
              "archive": archived, "media": media}
    save_json(d / "out" / "staged.json", staged)
    # The record itself goes up last, so the engine can ingest an episode on
    # a machine without build/ (engine/archive.py reads release.json and
    # staged.json from here). Not hashed into the record it is: it describes
    # the rest.
    try:
        s3.upload_file(str(d / "out" / "staged.json"), buckets["archive"], f"episodes/{a.slug}/out/staged.json",
                       ExtraArgs={"ContentType": "application/json"})
    except (s3.exceptions.ClientError, boto3.exceptions.S3UploadFailedError) as e:
        die(f"upload of the staged record failed: {e}")
    print(f"staged: {put} put, {skipped} unchanged; out/staged.json written and archived; "
          f"mp3 at {staged['media'].get('episode.mp3', {}).get('url', '?')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
