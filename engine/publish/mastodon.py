"""
Mastodon: the post with the art or the clip, on the instance in the
account's handle, with an access token from an application the account
made itself (write:media and write:statuses).

The file goes up first, described for screen readers; an image is ready at
once, a video is accepted and processed, and the status is refused until
it is, so the media is polled until it is done. The status carries the
language, is public, and is sent with an idempotency key made from the
post and its attempt, so a retry after a lost reply cannot post twice
within the hour the instance remembers the key. mastodon.social takes 500
characters with every link counted as 23, and video to 99 MB.
"""

import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

import ledger
from publish import PublishError

SCHEDULES = False
NEEDS = "MASTODON_ACCESS_TOKEN"
LIMIT = 500
LINK = 23  # characters_reserved_per_url on mastodon.social
WAIT = 600
POLL = 3
TYPES = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def ready(env):
    return bool(env.get("MASTODON_ACCESS_TOKEN"))


def instance(account, env):
    """The instance's URL: MASTODON_INSTANCE, or the domain in the handle."""
    if env.get("MASTODON_INSTANCE"):
        return env["MASTODON_INSTANCE"].rstrip("/")
    m = re.fullmatch(r"@?[^@]+@([\w.-]+)", account.get("handle") or "")
    if not m:
        raise PublishError("no instance: the account's handle should read @user@instance, or set MASTODON_INSTANCE")
    return f"https://{m.group(1)}"


def length(text):
    return len(re.sub(r"https?://\S+", "x" * LINK, text))


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
        return f"{status}: {json.loads(body).get('error', '')}".strip()
    except (ValueError, AttributeError):
        return f"{status}: {body[:300]!r}"


def multipart(fields, name, filename, ctype, data):
    boundary = "agentsee" + secrets.token_hex(8)
    body = b""
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n"
             f"Content-Type: {ctype}\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def upload(path, description, base, tok, log):
    ctype = TYPES.get(path.suffix.lower())
    if not ctype:
        raise PublishError(f"Mastodon takes no {path.suffix} files here")
    body, ct = multipart({"description": description}, "file", path.name, ctype, path.read_bytes())
    st, h, b = request("POST", f"{base}/api/v2/media", body, {"Authorization": f"Bearer {tok}", "Content-Type": ct}, timeout=900)
    if st not in (200, 202):
        raise PublishError("the instance refused the file: " + said(st, b))
    mid = json.loads(b)["id"]
    deadline = time.time() + WAIT
    while st == 202:
        if time.time() > deadline:
            raise PublishError("the instance is still processing the file after ten minutes; try again later")
        time.sleep(POLL)
        st, h, b = request("GET", f"{base}/api/v1/media/{mid}", headers={"Authorization": f"Bearer {tok}"})
        if st == 206:
            st = 202
            log("    processing")
        elif st != 200:
            raise PublishError("the instance lost the file: " + said(st, b))
    return mid


def describe(post, episode, release):
    return f"post to Mastodon with {post['media'] or 'no media'}, {length(post['text'] or '')} of {LIMIT}: {post['title']}"


def publish(post, episode, release, files, env, account, log):
    text = (post["text"] or "").strip()
    n = length(text)
    if n > LIMIT:
        raise PublishError(f"the text is {n} characters as Mastodon counts them; the limit is {LIMIT}. Cut it on the post's page")
    if not text and not post["media"]:
        raise PublishError("nothing to post: no text and no media")
    base, tok = instance(account, env), env["MASTODON_ACCESS_TOKEN"]
    media = []
    if post["media"]:
        path = files / post["media"]
        if not path.exists():
            raise PublishError(f"{path} is not on this machine")
        clip = next((x for x in release.get("clips", []) if x["file"] == post["media"]), {})
        alt = clip.get("alt") or (post["title"] if clip else f"{episode['title']}: the episode art")
        log(f"  uploading {post['media']} ({path.stat().st_size:,} bytes)")
        media.append(upload(path, alt, base, tok, log))
    lang = (ledger.CONFIG.get("language") or ledger.CONFIG["youtube"]["language"])[:2]
    form = [("status", text), ("language", lang), ("visibility", "public")] + [("media_ids[]", m) for m in media]
    st, h, b = request("POST", f"{base}/api/v1/statuses", urllib.parse.urlencode(form).encode(), {
        "Authorization": f"Bearer {tok}", "Content-Type": "application/x-www-form-urlencoded",
        "Idempotency-Key": f"agentsee-post-{post['id']}-{post['attempts']}"})
    if st != 200:
        raise PublishError("the instance refused the post: " + said(st, b))
    d = json.loads(b)
    return {"status": "published", "remote_id": str(d["id"]), "permalink": d.get("url") or d.get("uri"),
            "published_at": ledger.now(), "note": f"{n} of {LIMIT} characters" + ("; " + post["media"] if media else "")}
