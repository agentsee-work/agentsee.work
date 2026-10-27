"""
X: the episode's art with its text, each clip as a video, posted at the
post's hour by the engine (X does not schedule). The text was cut to 280 at
plan time; X counts any link as 23 characters whatever its length, so that
budget was honest, and the adapter counts the same way before it spends a
request. On pay-per-use a post with a link is the dear kind (20 cents
against a cent and a half without); every post here carries the short link.

The token. X's OAuth 2.0 refresh token is single use: each refresh returns
an access token good for two hours and a NEW refresh token, and retires the
old one. So the vault item is the live copy: the adapter reads it from the
vault at refresh time (not from the environment, a copy from when the
process started) and writes the replacement back BEFORE it uses the access
token. If that write fails, nothing is posted, the new refresh token is held
in this process and the write retried on the next attempt, and the error
says so: restarting the engine before it succeeds loses the token, and a
lost token means minting another (`tools/x.py auth`, or the Developer
Console). One refresher at a time: two processes refreshing from the same
item retire each other's tokens.

Media goes up through X's v2 chunked upload: initialize, append in segments
of at most 5 MB, finalize, then poll until X has processed a video.
"""

import base64
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

import ledger
import vault
from publish import PublishError
from vault import VaultError

SCHEDULES = False
NEEDS = "X_APP_ID / X_APP_SECRET / X_TOKEN_SECRET"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
API = "https://api.x.com/2"
CHUNK = 4 * 1024 * 1024  # X: keep each segment at or below 5 MB
LIMIT = 280
LINK = 23  # what X counts any URL as, whatever its length
WAIT = 600  # seconds to give X to process a video
RETRY = (500, 502, 503, 504)
TYPES = {".mp4": ("video/mp4", "tweet_video"), ".jpg": ("image/jpeg", "tweet_image"),
         ".jpeg": ("image/jpeg", "tweet_image"), ".png": ("image/png", "tweet_image")}

_token = {"access": None, "until": 0.0, "unsaved": None}


def ready(env):
    return all(env.get(k) for k in ("X_APP_ID", "X_APP_SECRET", "X_TOKEN_SECRET"))


def item():
    cfg = ledger.CONFIG.get("x")
    if not cfg:
        raise PublishError('config.json has no "x" entry naming the vault item that holds the refresh token')
    return cfg["vault"], cfg["item"], cfg.get("field", "credential")


def keep(value):
    """The new refresh token into the vault, or held for the next attempt."""
    _token["unsaved"] = value
    try:
        vault.write(*item(), value)
    except VaultError as e:
        raise PublishError(
            f"X issued a new refresh token but the vault write failed ({e}). The old token is retired; "
            "the new one is held in this process only and the write is retried on the next attempt. "
            "Do not restart the engine before that succeeds, or the token is lost and another must be minted") from e
    _token["unsaved"] = None


def token(env, log=print):
    if _token["unsaved"]:
        keep(_token["unsaved"])
        log("  the held refresh token is in the vault now")
    if _token["access"] and time.time() < _token["until"] - 120:
        return _token["access"]
    try:
        current = vault.read(*item())
    except VaultError as e:
        raise PublishError(f"could not read the X refresh token from the vault ({e}): the engine needs an op "
                           "session (run it under op run) to refresh the token and write the replacement back") from e
    data = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": current,
                                   "client_id": env["X_APP_ID"]}).encode()
    auth = "Basic " + base64.b64encode(f"{env['X_APP_ID']}:{env['X_APP_SECRET']}".encode()).decode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "Authorization": auth})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.load(r)
    except urllib.error.HTTPError as e:
        with e:
            why = e.read().decode(errors="replace")[:300]
        raise PublishError("X would not refresh the token: " + why
                           + ". If something else refreshed it first, the vault holds a retired token: "
                           "mint another with tools/x.py auth, or in the Developer Console, and file it") from e
    new = j.get("refresh_token")
    if not new:
        raise PublishError("X refreshed but sent no new refresh token: offline.access is missing from the consent")
    _token["access"], _token["until"] = j["access_token"], time.time() + int(j.get("expires_in", 7200))
    keep(new)
    scopes = (j.get("scope") or "").split()
    missing = [s for s in ("tweet.write", "media.write") if s not in scopes] if scopes else []
    if missing:
        raise PublishError(f"the token lacks {' '.join(missing)}: consent again with tools/x.py auth")
    return _token["access"]


def request(method, url, tok, body=None, headers=None, timeout=120):
    h = {"Authorization": f"Bearer {tok}"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.headers, e.read()


def said(status, body):
    try:
        j = json.loads(body)
        if "detail" in j:
            return f"{status}: {j.get('title', '')}: {j['detail']}".replace(": :", ":")
        if "errors" in j:
            return f"{status}: " + "; ".join(str(e.get("message") or e) for e in j["errors"])
        if "error" in j:
            return f"{status}: {j['error']}: {j.get('error_description', '')}".strip(": ")
    except (ValueError, TypeError, AttributeError):
        pass
    return f"{status}: {body[:300]!r}"


def multipart(fields, name, data):
    boundary = "agentsee" + secrets.token_hex(8)
    body = b""
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"blob\"\r\n"
             "Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def length(text):
    """Characters as X counts them: every link is 23."""
    return len(re.sub(r"https?://\S+", "x" * LINK, text))


def upload(path, env, log):
    if path.suffix.lower() not in TYPES:
        raise PublishError(f"X takes no {path.suffix} files")
    media_type, category = TYPES[path.suffix.lower()]
    size = path.stat().st_size
    st, h, b = request("POST", f"{API}/media/upload/initialize", token(env, log), json.dumps({
        "media_type": media_type, "total_bytes": size, "media_category": category}).encode(),
        {"Content-Type": "application/json"})
    if st not in (200, 201, 202):
        raise PublishError("X would not start the upload: " + said(st, b))
    mid = json.loads(b)["data"]["id"]
    sent, i, shown = 0, 0, -1
    with path.open("rb") as f:
        while sent < size:
            chunk = f.read(CHUNK)
            body, ctype = multipart({"segment_index": str(i)}, "media", chunk)
            for attempt in range(4):
                st, h, b = request("POST", f"{API}/media/upload/{mid}/append", token(env, log), body,
                                   {"Content-Type": ctype}, timeout=600)
                if st not in RETRY:
                    break
                time.sleep(2 ** attempt)
            if st not in (200, 201, 204):
                raise PublishError(f"X refused segment {i}: " + said(st, b))
            sent += len(chunk)
            i += 1
            pct = sent * 100 // size
            if pct // 10 != shown:
                shown = pct // 10
                log(f"    {pct}%")
    st, h, b = request("POST", f"{API}/media/upload/{mid}/finalize", token(env, log), b"",
                       {"Content-Type": "application/json"})
    if st not in (200, 201):
        raise PublishError("X would not finalize the upload: " + said(st, b))
    info = json.loads(b)["data"].get("processing_info")
    deadline = time.time() + WAIT
    while info and info.get("state") in ("pending", "in_progress"):
        if time.time() > deadline:
            raise PublishError(f"X had not finished processing {path.name} after {WAIT // 60} minutes")
        time.sleep(min(max(int(info.get("check_after_secs") or 1), 0), 30))
        st, h, b = request("GET", f"{API}/media/upload?" + urllib.parse.urlencode(
            {"command": "STATUS", "media_id": mid}), token(env, log))
        if st != 200:
            raise PublishError("X would not say how the processing goes: " + said(st, b))
        info = json.loads(b)["data"].get("processing_info")
    if info and info.get("state") == "failed":
        err = info.get("error") or {}
        raise PublishError(f"X could not process {path.name}: {err.get('message') or err or info}")
    return mid


def describe(post, episode, release):
    return f"post to X with {post['media'] or 'no media'}, {length(post['text'] or '')} of {LIMIT}: {post['title']}"


def publish(post, episode, release, files, env, account, log):
    text = (post["text"] or "").strip()
    n = length(text)
    if n > LIMIT:
        raise PublishError(f"the text is {n} characters as X counts them; the limit is {LIMIT}. Cut it on the post's page")
    if not text and not post["media"]:
        raise PublishError("nothing to post: no text and no media")
    mid = None
    if post["media"]:
        path = files / post["media"]
        if not path.exists():
            raise PublishError(f"{path} is not on this machine")
        log(f"  uploading {post['media']} ({path.stat().st_size:,} bytes)")
        mid = upload(path, env, log)
    body = {"text": text}
    if mid:
        body["media"] = {"media_ids": [mid]}
    st, h, b = request("POST", f"{API}/tweets", token(env, log), json.dumps(body).encode(),
                       {"Content-Type": "application/json"})
    if st not in (200, 201):
        raise PublishError("X refused the post: " + said(st, b))
    pid = json.loads(b)["data"]["id"]
    handle = (account.get("handle") or "").lstrip("@") or "i"
    note = f"{n} of {LIMIT} characters" + (f"; {post['media']}" if mid else "") + ("; with a link" if "http" in text else "")
    return {"status": "published", "remote_id": pid, "permalink": f"https://x.com/{handle}/status/{pid}",
            "published_at": ledger.now(), "note": note}
