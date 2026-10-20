"""
Bluesky: the post with the art or the clip, through the account's own data
server, on an app password.

A session is opened with the identifier and the app password at the
entryway; the DID document it returns names the account's PDS, and the
rest goes there. The art is a blob on the PDS (at most a million bytes) in
an images embed; a clip goes to Bluesky's video service first, which
processes it and stores the result on the PDS on our behalf under a service
token the PDS issues for that one purpose, then the job is polled until it
returns the blob, which goes in a video embed with its aspect ratio. The
link and the hashtags in the text are facets, with byte offsets over the
UTF-8, or they would be plain text. The permalink is the app's URL built
from the record key.

Bluesky takes 300 graphemes; the plan cuts the text to that. Video needs
the account's email verified, and there is a daily number of video posts
per account that the service enforces.
"""

import datetime as dt
import json
import re
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

import ledger
from publish import PublishError

SCHEDULES = False
NEEDS = "BLUESKY_IDENTIFIER / BLUESKY_APP_PASSWORD"
ENTRY = "https://bsky.social"  # createSession, and the PDS when the DID document names none
VIDEO = "https://video.bsky.app"
LIMIT = 300
IMAGE_MAX = 1_000_000
WAIT = 600  # seconds to give the video service
POLL = 3
TYPES = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}

_session = {"jwt": None, "did": None, "pds": None, "until": 0.0}


def ready(env):
    return bool(env.get("BLUESKY_IDENTIFIER") and env.get("BLUESKY_APP_PASSWORD"))


def language():
    return ledger.CONFIG.get("language") or ledger.CONFIG["youtube"]["language"]


def request(method, url, body=None, headers=None, timeout=120):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.headers, e.read()


def said(status, body):
    try:
        d = json.loads(body)
        return f"{status}: {d.get('error', '')} {d.get('message', '')}".strip()
    except (ValueError, AttributeError):
        return f"{status}: {body[:300]!r}"


def session(env):
    if _session["jwt"] and time.time() < _session["until"]:
        return _session
    st, h, b = request("POST", f"{ENTRY}/xrpc/com.atproto.server.createSession", json.dumps({
        "identifier": env["BLUESKY_IDENTIFIER"], "password": env["BLUESKY_APP_PASSWORD"]}).encode(),
        {"Content-Type": "application/json"})
    if st != 200:
        raise PublishError("Bluesky would not open a session: " + said(st, b))
    d = json.loads(b)
    pds = ENTRY
    for s in (d.get("didDoc") or {}).get("service") or []:
        if s.get("type") == "AtprotoPersonalDataServer" and s.get("serviceEndpoint"):
            pds = s["serviceEndpoint"].rstrip("/")
    _session.update(jwt=d["accessJwt"], did=d["did"], pds=pds, until=time.time() + 3600)
    return _session


def facets(text):
    """Links and hashtags as facets, indexed in bytes of the UTF-8."""
    b, out = text.encode("utf-8"), []
    for m in re.finditer(rb"https?://[^\s<>\"'\])]+", b):
        s, e = m.start(), m.end()
        while e > s and b[e - 1:e] in (b".", b",", b";", b":", b"!", b"?"):
            e -= 1
        out.append({"index": {"byteStart": s, "byteEnd": e},
                    "features": [{"$type": "app.bsky.richtext.facet#link", "uri": b[s:e].decode()}]})
    for m in re.finditer(rb"(?<![\w/#])#([A-Za-z]\w*)", b):
        out.append({"index": {"byteStart": m.start(), "byteEnd": m.end()},
                    "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": m.group(1).decode()}]})
    return sorted(out, key=lambda f: f["index"]["byteStart"])


def dimensions(path):
    """(width, height) of a PNG or a JPEG from its header, or None."""
    with path.open("rb") as f:
        head = f.read(32)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", head[16:24])
        if head[:2] == b"\xff\xd8":
            f.seek(2)
            while True:
                marker = f.read(2)
                if len(marker) < 2 or marker[0] != 0xFF:
                    return None
                if marker[1] in (0xC0, 0xC1, 0xC2):
                    f.read(3)
                    h, w = struct.unpack(">HH", f.read(4))
                    return w, h
                (n,) = struct.unpack(">H", f.read(2))
                f.seek(n - 2, 1)
    return None


def upload_blob(path, ctype, s):
    st, h, b = request("POST", f"{s['pds']}/xrpc/com.atproto.repo.uploadBlob", path.read_bytes(),
                       {"Authorization": f"Bearer {s['jwt']}", "Content-Type": ctype}, timeout=300)
    if st != 200:
        raise PublishError("the PDS refused the file: " + said(st, b))
    return json.loads(b)["blob"]


def upload_video(path, s, log):
    host = urllib.parse.urlparse(s["pds"]).hostname
    q = urllib.parse.urlencode({"aud": f"did:web:{host}", "lxm": "com.atproto.repo.uploadBlob", "exp": int(time.time()) + 1800})
    st, h, b = request("GET", f"{s['pds']}/xrpc/com.atproto.server.getServiceAuth?{q}", headers={"Authorization": f"Bearer {s['jwt']}"})
    if st != 200:
        raise PublishError("the PDS would not issue a service token: " + said(st, b))
    tok = json.loads(b)["token"]
    q = urllib.parse.urlencode({"did": s["did"], "name": path.name})
    st, h, b = request("POST", f"{VIDEO}/xrpc/app.bsky.video.uploadVideo?{q}", path.read_bytes(),
                       {"Authorization": f"Bearer {tok}", "Content-Type": "video/mp4"}, timeout=900)
    try:
        job = json.loads(b)
    except ValueError:
        job = {}
    job = job.get("jobStatus", job)
    if st != 200 and not job.get("blob"):  # a video seen before comes back as an error carrying its blob
        raise PublishError("the video service refused the clip: " + said(st, b))
    deadline = time.time() + WAIT
    while not job.get("blob"):
        if job.get("state") == "JOB_STATE_FAILED":
            raise PublishError(f"the video service could not process the clip: {job.get('error') or job.get('message') or 'no reason'}")
        if time.time() > deadline:
            raise PublishError("the video service is still processing the clip after ten minutes; try again later")
        time.sleep(POLL)
        st, h, b = request("GET", f"{VIDEO}/xrpc/app.bsky.video.getJobStatus?" + urllib.parse.urlencode({"jobId": job.get("jobId", "")}))
        d = json.loads(b) if b else {}
        job = d.get("jobStatus", d)
        if job.get("progress") is not None:
            log(f"    processing: {job['progress']}%")
    return job["blob"]


def describe(post, episode, release):
    return f"post to Bluesky with {post['media'] or 'no media'}, {len(post['text'] or '')} of {LIMIT}: {post['title']}"


def publish(post, episode, release, files, env, account, log):
    text = (post["text"] or "").strip()
    if len(text) > LIMIT:
        raise PublishError(f"the text is {len(text)} characters; Bluesky takes {LIMIT}. Cut it on the post's page")
    if not text and not post["media"]:
        raise PublishError("nothing to post: no text and no media")
    s = session(env)
    record = {"$type": "app.bsky.feed.post", "text": text,
              "createdAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z", "langs": [language()]}
    f = facets(text)
    if f:
        record["facets"] = f
    notes = [f"{len(text)} of {LIMIT} characters", f"{len(f)} facets"]
    if post["media"]:
        path = files / post["media"]
        if not path.exists():
            raise PublishError(f"{path} is not on this machine")
        ctype = TYPES.get(path.suffix.lower())
        if not ctype:
            raise PublishError(f"Bluesky takes no {path.suffix} files")
        size = path.stat().st_size
        meta = (release.get("files") or {}).get(post["media"]) or {}
        if ctype == "video/mp4":
            log(f"  uploading {post['media']} ({size:,} bytes) to the video service")
            clip = next((x for x in release.get("clips", []) if x["file"] == post["media"]), {})
            embed = {"$type": "app.bsky.embed.video", "video": upload_video(path, s, log), "alt": clip.get("alt") or post["title"] or ""}
            if meta.get("width") and meta.get("height"):
                embed["aspectRatio"] = {"width": int(meta["width"]), "height": int(meta["height"])}
            notes.append("video")
        else:
            if size > IMAGE_MAX:
                raise PublishError(f"{post['media']} is {size:,} bytes; Bluesky takes images up to {IMAGE_MAX:,}")
            log(f"  uploading {post['media']} ({size:,} bytes)")
            img = {"image": upload_blob(path, ctype, s), "alt": f"{episode['title']}: the episode art"}
            dims = dimensions(path)
            if dims:
                img["aspectRatio"] = {"width": dims[0], "height": dims[1]}
            embed = {"$type": "app.bsky.embed.images", "images": [img]}
            notes.append("image")
        record["embed"] = embed
    st, h, b = request("POST", f"{s['pds']}/xrpc/com.atproto.repo.createRecord", json.dumps({
        "repo": s["did"], "collection": "app.bsky.feed.post", "record": record}).encode(),
        {"Authorization": f"Bearer {s['jwt']}", "Content-Type": "application/json"})
    if st != 200:
        raise PublishError("Bluesky refused the post: " + said(st, b))
    uri = json.loads(b)["uri"]
    handle = (account.get("handle") or "").lstrip("@") or s["did"]
    return {"status": "published", "remote_id": uri, "permalink": f"https://bsky.app/profile/{handle}/post/{uri.rsplit('/', 1)[1]}",
            "published_at": ledger.now(), "note": "; ".join(notes)}
