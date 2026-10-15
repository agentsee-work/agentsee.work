"""
YouTube: the episode as a video, each clip as a Short, scheduled by YouTube
itself. The engine uploads as soon as a post exists (SCHEDULES), private,
with publishAt set to the post's due time, and YouTube makes it public then;
a post whose time has already passed goes up public at once. After the
upload: the captions (the episode's SRT, a clip's VTT), the thumbnail (the
episode's, or the clip's poster; refused, not failed, while the channel is
not verified for custom thumbnails), and the episode into the show's
playlist, created if missing. The permalink is the watch URL, or the shorts
URL for a clip.

Quota, as of October 2026: uploads have their own bucket of 100 a day, so an
episode and its clips go up together; captions cost 400 of the 10,000
general units each, thumbnails and playlist items 50. The channel's refresh
token comes from the vault through engine.env; access tokens are minted
from it as needed, once an hour, and again per chunk on a long upload.

The upload is Google's resumable protocol: one POST opens a session, the
file goes up in 8 MiB chunks, a 308 says how far it got, and a dropped
connection resumes from there rather than from the start.
"""

import datetime as dt
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

import ledger
from publish import PublishError

SCHEDULES = True
NEEDS = "YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
CHUNK = 8 * 1024 * 1024  # a multiple of 256 KiB, as the protocol wants
GRACE = dt.timedelta(minutes=15)  # closer than this to its time, a post goes public at once
RETRY = (500, 502, 503, 504)

_token = {"value": None, "until": 0.0}


def ready(env):
    return all(env.get(k) for k in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"))


def token(env):
    if _token["value"] and time.time() < _token["until"] - 120:
        return _token["value"]
    data = urllib.parse.urlencode({
        "client_id": env["YOUTUBE_CLIENT_ID"], "client_secret": env["YOUTUBE_CLIENT_SECRET"],
        "refresh_token": env["YOUTUBE_REFRESH_TOKEN"], "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.load(r)
    except urllib.error.HTTPError as e:
        raise PublishError("Google would not refresh the token: " + e.read().decode(errors="replace")[:300])
    _token["value"], _token["until"] = j["access_token"], time.time() + int(j.get("expires_in", 3600))
    return _token["value"]


def request(method, url, tok, body=None, headers=None, timeout=120):
    h = {"Authorization": f"Bearer {tok}"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def said(status, body):
    try:
        err = json.loads(body)["error"]
        reason = (err.get("errors") or [{}])[0].get("reason")
        return f"{status}: {err.get('message')}" + (f" ({reason})" if reason else "")
    except (ValueError, KeyError, TypeError, AttributeError):
        return f"{status}: {body[:300]!r}"


def upload(path, meta, env, log, content_type, notify):
    size = path.stat().st_size
    url = f"{UPLOAD}/videos?" + urllib.parse.urlencode({
        "uploadType": "resumable", "part": "snippet,status", "notifySubscribers": "true" if notify else "false"})
    st, h, body = request("POST", url, token(env), json.dumps(meta).encode(), {
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": content_type, "X-Upload-Content-Length": str(size)})
    if st != 200:
        raise PublishError("could not open an upload session: " + said(st, body))
    session = h["Location"]
    sent, backoff, shown = 0, 2, -1
    with path.open("rb") as f:
        while True:
            f.seek(sent)
            chunk = f.read(CHUNK)
            end = sent + len(chunk) - 1
            st, h, body = request("PUT", session, token(env), chunk, {
                "Content-Type": content_type, "Content-Range": f"bytes {sent}-{end}/{size}"}, timeout=900)
            if st in (200, 201):
                return json.loads(body)
            if st == 308:
                rng = h.get("Range")
                sent = int(rng.split("-")[1]) + 1 if rng else end + 1
                backoff = 2
                pct = sent * 100 // size
                if pct // 10 != shown:
                    shown = pct // 10
                    log(f"    {pct}%")
                continue
            if st in RETRY:
                time.sleep(backoff)
                backoff = min(backoff * 2, 64)
                st2, h2, body2 = request("PUT", session, token(env), b"", {"Content-Range": f"bytes */{size}"}, timeout=60)
                if st2 in (200, 201):
                    return json.loads(body2)
                if st2 == 404:
                    raise PublishError("the upload session expired; run again")
                rng = h2.get("Range") if st2 == 308 else None
                sent = int(rng.split("-")[1]) + 1 if rng else 0
                continue
            raise PublishError("upload refused: " + said(st, body))


def captions(video_id, path, env, language, name):
    boundary = "agentsee" + secrets.token_hex(8)
    meta = json.dumps({"snippet": {"videoId": video_id, "language": language, "name": name, "isDraft": False}})
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
            f"--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n").encode() + path.read_bytes() \
        + f"\r\n--{boundary}--\r\n".encode()
    st, h, b = request("POST", f"{UPLOAD}/captions?uploadType=multipart&part=snippet", token(env), body,
                       {"Content-Type": f"multipart/related; boundary={boundary}"})
    if st not in (200, 201):
        raise PublishError("captions refused: " + said(st, b))
    return json.loads(b)["id"]


def thumbnail(video_id, path, env):
    ctype = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    st, h, b = request("POST", f"{UPLOAD}/thumbnails/set?videoId={video_id}", token(env), path.read_bytes(),
                       {"Content-Type": ctype})
    if st == 403:
        return "thumbnail refused: the channel is not verified for custom thumbnails"
    if st not in (200, 201):
        raise PublishError("thumbnail refused: " + said(st, b))
    return "thumbnail set"


def playlist_id(title, env):
    page = None
    while True:
        q = {"part": "snippet", "mine": "true", "maxResults": "50"}
        if page:
            q["pageToken"] = page
        st, h, b = request("GET", f"{API}/playlists?" + urllib.parse.urlencode(q), token(env))
        if st != 200:
            raise PublishError("could not list playlists: " + said(st, b))
        j = json.loads(b)
        for it in j.get("items", []):
            if it["snippet"]["title"] == title:
                return it["id"], False
        page = j.get("nextPageToken")
        if not page:
            break
    st, h, b = request("POST", f"{API}/playlists?part=snippet,status", token(env),
                       json.dumps({"snippet": {"title": title}, "status": {"privacyStatus": "public"}}).encode(),
                       {"Content-Type": "application/json"})
    if st not in (200, 201):
        raise PublishError("could not create the playlist: " + said(st, b))
    return json.loads(b)["id"], True


def add_to_playlist(pid, video_id, env):
    st, h, b = request("POST", f"{API}/playlistItems?part=snippet", token(env), json.dumps({
        "snippet": {"playlistId": pid, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).encode(),
        {"Content-Type": "application/json"})
    if st not in (200, 201):
        raise PublishError("could not add to the playlist: " + said(st, b))


def tags_within(tags, limit=500):
    """YouTube's 500 characters of tags: a comma between tags counts, and a
    tag with a space in it is counted as if quoted."""
    out, used = [], 0
    for t in tags:
        cost = len(t) + (2 if " " in t else 0) + (1 if out else 0)
        if used + cost > limit:
            break
        out.append(t)
        used += cost
    return out


def describe(post, episode, release):
    kind = "the episode" if post["asset"] == "episode" else "a Short"
    return f"upload {post['media']} as {kind}, private, public at {post['scheduled_at']}: {post['title']}"


def publish(post, episode, release, files, env, account, log):
    cfg = ledger.CONFIG["youtube"]
    kind = "episode" if post["asset"] == "episode" else "clip"
    path = files / post["media"]
    if not path.exists():
        raise PublishError(f"{path} is not on this machine")
    when = dt.datetime.fromisoformat(post["scheduled_at"])
    now = dt.datetime.now(dt.timezone.utc)
    status = {"privacyStatus": "private", "selfDeclaredMadeForKids": False, "embeddable": True, "license": "youtube"}
    if when - now > GRACE:
        status["publishAt"] = when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        status["privacyStatus"] = "public"
    yt = release.get("youtube", {})
    snippet = {
        "title": (post["title"] or "")[:100],
        "description": (post["text"] or "")[:5000],
        "tags": tags_within(yt.get("tags", [])),
        "categoryId": str(yt.get("category", 28)),
        "defaultLanguage": cfg["language"],
        "defaultAudioLanguage": cfg["language"],
    }
    ctype = (release["files"].get(post["media"]) or {}).get("type") or "video/mp4"
    log(f"  uploading {post['media']} ({path.stat().st_size:,} bytes)")
    video = upload(path, {"snippet": snippet, "status": status}, env, log, ctype, cfg["notify_subscribers"][kind])
    vid = video["id"]
    notes = []
    if kind == "episode":
        cap = files / "captions.srt"
        thumb = files / "thumbnail.png"
    else:
        clip = next((x for x in release["clips"] if x["file"] == post["media"]), {})
        cap = files / clip["captions"] if clip.get("captions") else None
        thumb = files / clip["poster"] if clip.get("poster") else None
    if cap and cap.exists():
        try:
            captions(vid, cap, env, cfg["language"], "English")
            notes.append("captions")
        except PublishError as e:
            notes.append(str(e))
    if thumb and thumb.exists():
        try:
            notes.append(thumbnail(vid, thumb, env))
        except PublishError as e:
            notes.append(str(e))
    if kind == "episode":
        pid, made = playlist_id(cfg["playlist"], env)
        add_to_playlist(pid, vid, env)
        notes.append("playlist" + (" (created)" if made else ""))
    permalink = f"https://www.youtube.com/watch?v={vid}" if kind == "episode" else f"https://www.youtube.com/shorts/{vid}"
    return {
        "status": "scheduled" if "publishAt" in status else "published",
        "remote_id": vid, "permalink": permalink,
        "published_at": when.isoformat() if "publishAt" in status else ledger.now(),
        "note": "; ".join(notes),
    }
