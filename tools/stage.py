#!/usr/bin/env python3
"""
Put an approved episode in the right spot: the archive, and the public media.

    ./tools/episode.py stage ep1                 # wraps this in `op run`
    ./tools/stage.py ep1 --dry-run               # what would go, no credentials needed

Two R2 buckets (infra/podcast/r2.tf). The archive is private and keeps the masters
off the workstation: the Riverside package, the edit as data, and every
deliverable. The media bucket is public at media.agentsee.work and holds
what the feed, the episode page and the platforms fetch. The publisher reads
release.json from the archive, so the workstation can be off.

  archive  sources/<slug>/riverside-timeline.zip
           episodes/<slug>/edit/{episode,manifest,chapters,clips,removals,edl,words}.json
           episodes/<slug>/out/**           everything but the wav and the variants
  media    podcast/<slug>/episode.mp3, episode-art.*, thumbnail.png,
           transcript.vtt, transcript.json, captions.srt, chapters.json,
           clips/*.{mp4,png,vtt}

It refuses an episode that is not approved, and one whose files have changed
since the approval (episode.py approve records every hash). Every object
carries its SHA-256 as metadata, so a re-run uploads only what changed, and
out/staged.json records what went where. Credentials come from
tools/stage.env through `op run`; nothing is read from disk.
"""

import argparse
import datetime as dt
import os
import re
import pathlib
import sys
import time
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from eplib import ROOT, die, ep_dir, load_json, save_json  # noqa: E402
from release import MIME  # noqa: E402

VENV = ROOT / "build" / "venv-stage" / "bin" / "python"
EDIT = ["episode.json", "manifest.json", "chapters.json", "clips.json", "removals.json",
        "edl.json", "transcript/words.json"]
SKIP_OUT = {"episode.wav", "variants", "staged.json"}
PUBLIC = {"episode.mp3", "episode-art.png", "episode-art.jpg", "thumbnail.png", "transcript.vtt",
          "transcript.json", "captions.srt", "chapters.json"}
PUBLIC_CLIP_SUFFIXES = {".mp4", ".png", ".vtt"}
CACHE = "public, max-age=86400"


def boto():
    try:
        import boto3  # noqa: F401
    except ImportError:
        # Compare prefixes, not executables: the venv's `python` is a symlink
        # to the system interpreter, so resolving it says "same" and nothing
        # would ever re-exec.
        venv = VENV.parent.parent
        if VENV.exists() and pathlib.Path(sys.prefix).resolve() != venv.resolve():
            sys.stdout.flush()
            os.execv(str(VENV), [str(VENV), __file__, *sys.argv[1:]])
        die("boto3 is missing: python3 -m venv build/venv-stage && build/venv-stage/bin/pip install boto3")
    import boto3
    return boto3


def sha256_of(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def plan(d, cfg, rel, warn):
    """Every object to put: (bucket, key, path, content type, public)."""
    out = d / "out"
    items = []
    pkg = pathlib.Path(cfg.get("package", ""))
    if pkg.exists():
        items.append(("archive", f"sources/{cfg['slug']}/riverside-timeline.zip", pkg, "application/zip"))
    else:
        warn(f"the Riverside package is not at {pkg}: the archive will have no source")
    for name in EDIT:
        p = d / name
        if p.exists():
            items.append(("archive", f"episodes/{cfg['slug']}/edit/{p.name}", p, "application/json"))
    for p in sorted(out.rglob("*")):
        if not p.is_file():
            continue
        relp = p.relative_to(out)
        if relp.parts[0] in SKIP_OUT:
            continue
        ctype = MIME.get(p.suffix, "application/octet-stream")
        if relp.as_posix() == "chapters.json":
            ctype = "application/json+chapters"
        items.append(("archive", f"episodes/{cfg['slug']}/out/{relp.as_posix()}", p, ctype))
        public = (relp.as_posix() in PUBLIC
                  or (relp.parts[0] == "clips" and p.suffix in PUBLIC_CLIP_SUFFIXES))
        if public:
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
    ap.add_argument("--dry-run", action="store_true", help="list what would go; no credentials needed")
    ap.add_argument("--no-source", action="store_true", help="stage without the Riverside package")
    a = ap.parse_args()
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

    ok = cfg.get("approved")
    if not ok and not a.dry_run:
        die("not approved: `episode.py approve <slug> --release YYYY-MM-DD` once the user has approved the assets")
    if ok:
        changed = [k for k, v in rel["files"].items() if ok["files"].get(k) != v["sha256"]]
        missing = [k for k in ok["files"] if k not in rel["files"]]
        if changed or missing:
            for k in changed:
                print(f"  changed since approval: {k}")
            for k in missing:
                print(f"  gone since approval: {k}")
            die("the deliverables are not the ones approved: re-run release and approve again")

    items = plan(d, cfg, rel, warn)
    if warnings and not a.no_source and not a.dry_run:
        die("fix the above, or --no-source to archive without the package")
    total = sum(p.stat().st_size for _, _, p, _ in items)
    by_bucket = {b: sum(1 for x in items if x[0] == b) for b in ("archive", "media")}
    print(f"{a.slug}: {len(items)} objects, {total / 1e9:.2f} GB  "
          f"(archive {by_bucket['archive']}, media {by_bucket['media']})")
    if a.dry_run:
        for b, key, p, ctype in items:
            print(f"  {b:<7} {key:<60} {p.stat().st_size / 1e6:9.1f} MB  {ctype}")
        return 0

    for var in ("R2_ACCOUNT_ID", "R2_ARCHIVE_BUCKET", "R2_MEDIA_BUCKET",
                "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        if not os.environ.get(var):
            die(f"{var} is not set: run under `op run --env-file=tools/stage.env`, "
                f"or `episode.py stage {a.slug}` which does that")
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
    s3 = boto3.client("s3", region_name="auto",
                      endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com")
    buckets = {"archive": os.environ["R2_ARCHIVE_BUCKET"], "media": os.environ["R2_MEDIA_BUCKET"]}

    def refused(e, what):
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

    # Cheap, before anything is uploaded: can the token read both buckets?
    for b, name in buckets.items():
        try:
            s3.list_objects_v2(Bucket=name, MaxKeys=1)
        except s3.exceptions.ClientError as e:
            die(refused(e, f"cannot read the {b} bucket {name}"))
    media_url = rel["episode"]["media_url"].rstrip("/")
    staged = {"slug": a.slug, "at": dt.datetime.now().isoformat(timespec="seconds"),
              "approved": {k: v for k, v in ok.items() if k != "files"},
              "archive": {}, "media": {}}
    hashes = {k: v["sha256"] for k, v in rel["files"].items()}
    put = skipped = 0
    for n, (b, key, p, ctype) in enumerate(items, 1):
        relp = p.relative_to(d / "out").as_posix() if (d / "out") in p.parents else None
        digest = hashes.get(relp) or sha256_of(p)
        try:
            there = s3.head_object(Bucket=buckets[b], Key=key).get("Metadata", {}).get("sha256")
        except s3.exceptions.ClientError as e:
            if e.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
                die(refused(e, f"cannot check {buckets[b]}/{key}"))
            there = None  # absent is the one answer that means "upload"
        if there == digest:
            skipped += 1
            record = "unchanged"
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
            staged["media"][relp] = {"url": f"{media_url}/{relp}", "sha256": digest, "state": record}
        else:
            staged["archive"][key] = {"sha256": digest, "state": record}
    save_json(d / "out" / "staged.json", staged)
    print(f"staged: {put} put, {skipped} unchanged; out/staged.json written; "
          f"mp3 at {staged['media'].get('episode.mp3', {}).get('url', '?')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
