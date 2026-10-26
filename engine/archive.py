"""
The archive bucket, from the engine's side. Stage puts an episode's release
record and every deliverable in `episodes/<slug>/out/` (tools/stage.py);
this is how the engine reads them when they are not on the machine it runs
on, and where it keeps the nightly copy of its own ledger.

  Files(c, episode)      a directory of the episode's deliverables that
                         fetches from the archive what is not here, checked
                         against the sha256 the ledger holds; adapters see a
                         path, as before
  fetch_release(slug)    release.json and staged.json into the data dir, for
                         an ingest on a machine without build/
  snapshot(c)            a consistent copy of the ledger to
                         engine/ledger-<day>.sqlite and engine/ledger-latest.sqlite
  restore(path)          the latest copy back, for a rebuild

R2 speaks S3, so this is SigV4 over urllib and nothing else: no boto3 on the
box, no venv. The credential is the R2 token scoped to the two podcast
buckets, the same vault item tools/stage.env resolves (engine.env names it).
The signer is checked against botocore's in the tests, with a vector fixed
on 15 October 2026.
"""

import datetime as dt
import hashlib
import hmac
import json
import os
import pathlib
import sqlite3
import tempfile
import urllib.error
import urllib.parse
import urllib.request

import ledger

NEED = ("R2_ACCOUNT_ID", "R2_ARCHIVE_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
LATEST = "engine/ledger-latest.sqlite"
EMPTY = hashlib.sha256(b"").hexdigest()


class ArchiveError(Exception):
    pass


class Missing(ArchiveError):
    """The archive has no such object."""


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _hmac(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def quote(s, safe):
    return urllib.parse.quote(s, safe=safe)


def sign(method, host, path, query, headers, payload_hash, key, secret, region="auto", when=None):
    """AWS Signature Version 4 for S3: the headers to send, Authorization
    among them. `path` is the raw path (`/bucket/key`), encoded here once."""
    when = when or dt.datetime.now(dt.timezone.utc)
    amz, day = when.strftime("%Y%m%dT%H%M%SZ"), when.strftime("%Y%m%d")
    h = {k.lower(): " ".join(str(v).split()) for k, v in headers.items()}
    h.update({"host": host, "x-amz-date": amz, "x-amz-content-sha256": payload_hash})
    signed = ";".join(sorted(h))
    canonical = "\n".join([
        method, quote(path, "/-_.~"),
        "&".join(f"{quote(k, '-_.~')}={quote(v, '-_.~')}" for k, v in sorted(query.items())),
        "".join(f"{k}:{h[k]}\n" for k in sorted(h)), signed, payload_hash])
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz, scope, _sha(canonical.encode())])
    k = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret).encode(), day), region), "s3"), "aws4_request")
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    h["authorization"] = f"AWS4-HMAC-SHA256 Credential={key}/{scope}, SignedHeaders={signed}, Signature={sig}"
    return h


def file_sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Client:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        missing = [k for k in NEED if not env.get(k)]
        if missing:
            raise ArchiveError(" / ".join(missing) + " not set: run under `op run --env-file=engine/engine.env`")
        self.bucket = env["R2_ARCHIVE_BUCKET"]
        self.key, self.secret = env["AWS_ACCESS_KEY_ID"], env["AWS_SECRET_ACCESS_KEY"]
        self.endpoint = (env.get("R2_ENDPOINT") or f"https://{env['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com").rstrip("/")
        self.host = urllib.parse.urlparse(self.endpoint).netloc

    def request(self, method, key, body=b"", headers=None, query=None, timeout=120):
        """One signed request. The body is bytes, or a (file, size, sha256)
        triple streamed as it is. Returns (status, headers, response), the
        response still open for the caller to read."""
        path = f"/{self.bucket}/{key}"
        h = dict(headers or {})
        if isinstance(body, tuple):
            data, size, digest = body
            h["content-length"] = str(size)
        else:
            data, digest = body, _sha(body)
        h = sign(method, self.host, path, query or {}, h, digest, self.key, self.secret)
        url = self.endpoint + quote(path, "/-_.~") + ("?" + urllib.parse.urlencode(query) if query else "")
        req = urllib.request.Request(url, data=data if method in ("PUT", "POST") else None, method=method, headers=h)
        try:
            r = urllib.request.urlopen(req, timeout=timeout)
            return r.status, r.headers, r
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e

    def said(self, status, resp):
        with resp:
            text = resp.read(600).decode(errors="replace")
        why = {401: "R2 does not know these credentials", 403: "the token is not allowed this (Object Read & Write on the podcast buckets)"}.get(status, "")
        return f"{status}{': ' + why if why else ''} {text.strip()}".strip()

    def head(self, key):
        st, h, r = self.request("HEAD", key)
        with r:
            pass
        if st == 404:
            return None
        if st != 200:
            raise ArchiveError(f"HEAD {key}: {st}")
        return h

    def get(self, key, dest, sha256=None, log=None):
        """The object to `dest`, through a sibling temp file, checked
        against `sha256` when given. Returns dest."""
        dest = pathlib.Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        st, h, r = self.request("GET", key, timeout=3600)
        if st == 404:
            with r:
                pass
            raise Missing(f"{key} is not in the archive")
        if st != 200:
            raise ArchiveError(f"GET {key}: " + self.said(st, r))
        digest = hashlib.sha256()
        tmp = dest.with_name(dest.name + ".part")
        size = int(h.get("Content-Length") or 0)
        got, shown = 0, -1
        with r, tmp.open("wb") as f:
            for chunk in iter(lambda: r.read(1 << 20), b""):
                f.write(chunk)
                digest.update(chunk)
                got += len(chunk)
                if log and size:
                    pct = got * 100 // size
                    if pct // 10 != shown:
                        shown = pct // 10
                        log(f"    {pct}%")
        if sha256 and digest.hexdigest() != sha256:
            tmp.unlink()
            raise ArchiveError(f"{key} does not match the sha256 the ledger holds; not kept")
        tmp.replace(dest)
        return dest

    def get_json(self, key):
        st, h, r = self.request("GET", key)
        if st == 404:
            with r:
                pass
            return None
        if st != 200:
            raise ArchiveError(f"GET {key}: " + self.said(st, r))
        with r:
            return json.load(r)

    def put(self, key, path, content_type="application/octet-stream", metadata=None):
        path = pathlib.Path(path)
        size, digest = path.stat().st_size, file_sha256(path)
        h = {"content-type": content_type}
        for k, v in (metadata or {}).items():
            h[f"x-amz-meta-{k}"] = v
        with path.open("rb") as f:
            st, rh, r = self.request("PUT", key, (f, size, digest), h, timeout=3600)
        with r:
            if st not in (200, 201):
                raise ArchiveError(f"PUT {key}: " + self.said(st, r))
        return digest


# ─── the engine's uses of it ─────────────────────────────────────────────────
def out_dir(slug):
    return ledger.data_dir() / "episodes" / slug / "out"


class Files:
    """An episode's deliverables as a directory: `files / name` is a path,
    fetched from the archive first when it is not on this machine and the
    ledger says the archive has it. Without the archive's credentials the
    path is simply absent, and the adapter says so."""

    def __init__(self, c, episode, client=None):
        self.c, self.slug, self.client = c, episode["slug"], client
        src = pathlib.Path(episode["source_dir"] or "")
        self.local = src if (src / "release.json").exists() else out_dir(self.slug)

    def __truediv__(self, name):
        p = self.local / name
        if p.exists():
            return p
        row = self.c.execute("SELECT sha256, archived FROM deliverable WHERE episode=? AND name=?",
                             (self.slug, name)).fetchone()
        if not row or not row["archived"]:
            return p
        try:
            client = self.client or Client()
        except ArchiveError:
            return p
        try:
            client.get(f"episodes/{self.slug}/out/{name}", p, sha256=row["sha256"])
        except Missing:
            pass  # the ledger was told otherwise, but an absent file is the adapter's to judge
        return p

    def __str__(self):
        return str(self.local)


def fetch_release(slug, client=None):
    """release.json and staged.json from the archive into the data dir, so
    an ingest can run where build/ does not exist. Returns the directory."""
    client = client or Client()
    d = out_dir(slug)
    for name in ("release.json", "staged.json"):
        j = client.get_json(f"episodes/{slug}/out/{name}")
        if j is None:
            raise ArchiveError(f"episodes/{slug}/out/{name} is not in the archive" + (
                ": stage puts staged.json there last; re-run `episode.py stage` for this episode" if name == "staged.json" else ""))
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(json.dumps(j, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return d


def snapshot(c, client=None, who="engine", log=print):
    """A consistent copy of the ledger (SQLite's own backup API, so the
    worker need not stop) to the archive under today's date and as latest.

    Refuses an empty ledger. The data lives on the root disk, so a rebuilt
    box starts with none and the engine silently makes a fresh one; without
    this guard the nightly snapshot would then overwrite the archive's good
    copy — and `latest` with it — with that empty ledger, which is how two
    days of posts were nearly lost on 24 October 2026. An empty ledger is a
    box that has not had `engine.py restore` run yet, never a thing to back
    up; so a count of zero episodes AND zero posts is a loud failure here,
    caught by `cmd_snapshot` and recorded `snapshot:failed` by the worker."""
    episodes = c.execute("SELECT COUNT(*) FROM episode").fetchone()[0]
    posts = c.execute("SELECT COUNT(*) FROM post").fetchone()[0]
    if not episodes and not posts:
        raise ArchiveError(
            "the ledger has no episodes and no posts: refusing to snapshot, "
            "because it would overwrite the archive's good copy (and latest) "
            "with an empty one. On a rebuilt box run `engine.py restore` first.")
    client = client or Client()
    day = dt.datetime.now(ledger.ZONE).strftime("%Y-%m-%d")
    fd, tmp = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        dst = sqlite3.connect(tmp)
        with dst:
            c.backup(dst)
        dst.close()
        size = os.path.getsize(tmp)
        keys = [f"engine/ledger-{day}.sqlite", LATEST]
        for key in keys:
            digest = client.put(key, tmp, "application/vnd.sqlite3", {"sha256": file_sha256(pathlib.Path(tmp))})
    finally:
        os.unlink(tmp)
    ledger.event(c, "snapshot", who, detail={"bytes": size, "keys": keys, "sha256": digest})
    log(f"  snapshot: {size:,} bytes to {keys[0]} and {LATEST}")
    return keys[0]


def restore(path, client=None, key=LATEST, force=False):
    """The ledger back from the archive, for a rebuilt box. Refuses to
    overwrite one that exists unless told."""
    path = pathlib.Path(path)
    if path.exists() and not force:
        raise ArchiveError(f"{path} exists; use --force to replace it with the archive's copy")
    client = client or Client()
    h = client.head(key)
    if h is None:
        raise Missing(f"{key} is not in the archive: nothing to restore")
    client.get(key, path, sha256=h.get("x-amz-meta-sha256"))
    for sidecar in ("-wal", "-shm"):
        side = path.with_name(path.name + sidecar)
        if side.exists():
            side.unlink()
    return path
