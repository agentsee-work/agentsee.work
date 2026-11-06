"""
Instagram: a clip as a Reel, shared to the feed, and the episode's art as
an image on the grid when the account takes the episode asset, at the
post's hour by the engine (the API does not schedule). As on Threads,
Meta fetches the file itself from the media host: a container with the
file's address and the caption (and, for a Reel, its cover: the clip's
JPEG poster when the pipeline made one, else the first frame), a poll
until Meta has processed it, the publish, one read for the permalink.
2,200 characters and thirty hashtags at most.

The token is a Business Integration System User token the dashboard
generated for the show's account (AGE-130, C7). It does not expire unless
the account leaves the business portfolio or the app is removed from it,
so there is no consent and no refresh here, and it comes from the
environment like Bluesky's. The account id is not typed anywhere: at first
use the adapter asks Meta which account the token serves and keeps the id
in the ledger's `credential` row. The daily upkeep asks debug_token (with
the app's own token, id|secret) whether the token still stands, for
Health, and mails hello@ when it does not: a new one is generated in the
dashboard and filed, and the engine recreated to read it.

Never twice: as Threads, the container's id in an event the moment Meta
issues it; a retry reads it first, and a PUBLISHED container is matched to
the post by its caption among the account's recent media.
"""

import datetime as dt
import json
import pathlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import ledger
from plan import ACCOUNTS
from publish import Hold, PublishError
from publish.meta import BAD_TOKEN, NOT_NOW, error, parse, public_url, rest_until, said, stamp, warn

SCHEDULES = False
NEEDS = "INSTAGRAM_APP_ID / INSTAGRAM_APP_SECRET / INSTAGRAM_TOKEN"
PLATFORM = "instagram"
API = "https://graph.facebook.com"
V = "v27.0"
LIMIT = 2200
HASHTAGS = 30
ALT = 1000
WAIT = 600  # seconds to give Meta to fetch and process a file
POLL = 15
QUOTA_REST = 24 * 3600  # the publishing quota is per rolling day
RESTRICTED = 2207050  # Meta's subcode: the account may not publish
TYPES = {".mp4": "REELS", ".jpg": "IMAGE", ".jpeg": "IMAGE"}
SUBJECT = "The Instagram token needs a look"
REGENERATE = (
    "generate a new token in the app dashboard (AGE-130, C7), file it as the credential of "
    '"Instagram podcast token", and recreate the engine'
)

_account = {}  # token → {"id", "username"}, in this process


def ready(env):
    return all(env.get(k) for k in ("INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET", "INSTAGRAM_TOKEN"))


def call(method, path, params=None, timeout=60):
    """One request to Meta: the status, the headers and the body."""
    url = f"{API}/{V}/{path}"
    data, headers = None, {}
    if method == "GET":
        if params:
            url += "?" + urllib.parse.urlencode(params)
    else:
        data = urllib.parse.urlencode(params or {}).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.headers, e.read()


def fatal(b):
    """An answer no retry of the same request will change: the token, the account's standing, or a rate limit."""
    e = error(b)
    return e.get("code") in (BAD_TOKEN, *NOT_NOW) or e.get("error_subcode") == RESTRICTED


def refuse(st, h, b, what, c=None):
    """Meta said no: a Hold when it means not now, a PublishError otherwise; a refused token marks the record."""
    e = error(b)
    if e.get("code") in NOT_NOW:
        raise Hold(f"Instagram, {what}: {said(st, b)}", rest_until(h))
    if e.get("code") == BAD_TOKEN or e.get("error_subcode") == RESTRICTED:
        if c is not None:
            ledger.set_credential(c, PLATFORM, state="invalid", checked_at=ledger.now(), note=said(st, b)[:300])
        raise PublishError(f"Meta refused the Instagram token ({said(st, b)}): {REGENERATE}")
    raise PublishError(f"Instagram, {what}: {said(st, b)}")


def account(c, env):
    """The account the token serves: its id and handle, from the credential row or from Meta once."""
    tok = env["INSTAGRAM_TOKEN"]
    if tok in _account:
        return _account[tok]
    row = ledger.credential(c, PLATFORM)
    if row and row["who"]:
        _account[tok] = {"id": row["who"], "username": (ACCOUNTS.get(PLATFORM) or {}).get("handle", "").lstrip("@")}
        return _account[tok]
    st, h, b = call("GET", "me/assigned_instagram_accounts", {"fields": "id,username", "access_token": tok})
    if st != 200:
        refuse(st, h, b, "which account the token serves", c)
    found_ = json.loads(b).get("data") or []
    if not found_:
        raise PublishError("the token serves no Instagram account: the login that generated it chose none")
    handle = (ACCOUNTS.get(PLATFORM) or {}).get("handle", "").lstrip("@")
    a = next((x for x in found_ if x.get("username") == handle), found_[0])
    ledger.set_credential(
        c, PLATFORM, who=str(a["id"]), state="valid", checked_at=ledger.now(), note=f"@{a.get('username')}"
    )
    ledger.event(c, "instagram:account", "engine", detail=a)
    _account[tok] = {"id": str(a["id"]), "username": a.get("username")}
    return _account[tok]


# ─── the post ────────────────────────────────────────────────────────────────
def cover_for(c, episode, release, media):
    """The clip's JPEG poster on the media host, when the pipeline made one (tools/clips.py since AGE-132)."""
    name = media.rsplit(".", 1)[0] + ".jpg"
    if name not in (release.get("files") or {}):
        return None
    return public_url(c, episode, name)


def alt_for(post, episode):
    return f"{episode['title']}: the episode art" if post["asset"] == "episode" else post["title"] or ""


def container(c, post, ig, tok, params):
    st, h, b = call("POST", f"{ig}/media", {**params, "access_token": tok})
    if st != 200:
        refuse(st, h, b, "the container", c)
    cid = str(json.loads(b)["id"])
    ledger.event(
        c,
        "publish:container",
        "engine",
        post=post["id"],
        episode=post["episode"],
        detail={"id": cid, "media_type": params.get("media_type", "IMAGE"), "media": post["media"]},
    )
    return cid


def state_of(cid, tok):
    st, h, b = call("GET", cid, {"fields": "status_code,status", "access_token": tok})
    return (st, h, b, json.loads(b) if st == 200 else {})


def wait_for(c, cid, tok):
    deadline = time.time() + WAIT
    while True:
        st, h, b, j = state_of(cid, tok)
        if st != 200:
            refuse(st, h, b, "the container's status", c)
        s = j.get("status_code")
        if s in ("FINISHED", "PUBLISHED"):
            return s
        if s == "ERROR":
            raise PublishError(f"Meta could not take the file: {j.get('status') or 'no reason given'}")
        if s == "EXPIRED":
            raise PublishError("the container expired before it was published; a retry starts again")
        if time.time() > deadline:
            raise PublishError(f"Meta had not finished with the file after {WAIT // 60} minutes (status {s})")
        time.sleep(POLL)


def last_container(c, post):
    row = c.execute(
        "SELECT detail FROM event WHERE what='publish:container' AND post=? ORDER BY id DESC LIMIT 1", (post["id"],)
    ).fetchone()
    return json.loads(row["detail"]) if row else None


def find_post(c, ig, tok, text):
    """The account's recent media, for the one with this caption: a container published but never recorded."""
    st, h, b = call(
        "GET", f"{ig}/media", {"fields": "id,caption,permalink,timestamp", "limit": "25", "access_token": tok}
    )
    if st != 200:
        refuse(st, h, b, "the recent media", c)
    return next((m for m in json.loads(b).get("data", []) if (m.get("caption") or "").strip() == text), None)


def quota(c, ig, tok, log):
    """The day's publishing quota, before a container is made: full is a hold of a day, not a failure."""
    st, h, b = call("GET", f"{ig}/content_publishing_limit", {"fields": "quota_usage,config", "access_token": tok})
    if st != 200:
        if fatal(b):
            refuse(st, h, b, "the publishing quota", c)
        log(f"  Instagram would not say how much of the day's quota is used: {said(st, b)}")
        return None
    d = (json.loads(b).get("data") or [{}])[0]
    used, total = d.get("quota_usage"), (d.get("config") or {}).get("quota_total")
    if used is not None and total and used >= total:
        raise Hold(
            f"Instagram: {used} of {total} posts in the last day, the limit",
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=QUOTA_REST),
        )
    return used, total


def found(m, note):
    return {
        "status": "published",
        "remote_id": str(m["id"]),
        "permalink": m.get("permalink"),
        "published_at": ledger.now(),
        "note": note,
    }


def describe(post, episode, release):
    text = post["text"] or ""
    kind = TYPES.get(pathlib.Path(post["media"] or "").suffix.lower())
    what = {"REELS": "an Instagram reel of", "IMAGE": "an Instagram image post of"}.get(
        kind, "nothing Instagram takes:"
    )
    return f"{what} {post['media'] or 'no file'}, {len(text)} of {LIMIT} characters: {post['title']}"


def publish(post, episode, release, files, env, account_, log):
    c = files.c
    text = (post["text"] or "").strip()
    if len(text) > LIMIT:
        raise PublishError(f"the caption is {len(text)} characters; Instagram takes {LIMIT}. Cut it on the post's page")
    tags = re.findall(r"#\w+", text)
    if len(tags) > HASHTAGS:
        raise PublishError(f"the caption carries {len(tags)} hashtags; Instagram takes {HASHTAGS}")
    if not post["media"]:
        raise PublishError("nothing to post: Instagram takes no post without a file")
    suffix = pathlib.Path(post["media"]).suffix.lower()
    kind = TYPES.get(suffix)
    if not kind:
        raise PublishError(f"Instagram takes no {suffix} files" + (": images must be JPEG" if suffix == ".png" else ""))
    url = public_url(c, episode, post["media"])
    if not url:
        raise PublishError(f"no public address for {post['media']}: Meta fetches the file from the media host")
    tok = env["INSTAGRAM_TOKEN"]
    ig = account(c, env)["id"]
    params = {"caption": text}
    notes = [f"{len(text)} of {LIMIT} characters"]
    if kind == "REELS":
        params.update(media_type="REELS", video_url=url, share_to_feed="true")
        cover = cover_for(c, episode, release, post["media"])
        if cover:
            params["cover_url"] = cover
            notes += ["reel", "with its cover"]
        else:
            params["thumb_offset"] = "0"
            notes += ["reel", "the first frame as its cover"]
    else:
        params.update(image_url=url, alt_text=alt_for(post, episode)[:ALT])
        notes.append("image")
    cid = None
    prior = last_container(c, post)
    if prior:  # a failed attempt left a container: never twice
        st, h, b, j = state_of(prior["id"], tok)
        if st != 200 and fatal(b):
            refuse(st, h, b, "the earlier container's status", c)
        s = j.get("status_code")
        if s == "PUBLISHED":
            m = find_post(c, ig, tok, text)
            if not m:
                raise PublishError(
                    f"Meta says the earlier container {prior['id']} was published, but no recent media carries this "
                    "caption; look at the account before a retry"
                )
            return found(m, "the earlier container had been published; the post found by its caption")
        if s == "FINISHED":
            cid = prior["id"]
            notes.append("the earlier container, ready")
            log(f"  container {cid} from the earlier attempt is ready; publishing it")
        else:
            log(f"  the earlier container {prior['id']} is {s or said(st, b)}; starting again")
    if not cid:
        quota(c, ig, tok, log)
        cid = container(c, post, ig, tok, params)
        log(f"  container {cid}; waiting for Meta to fetch {post['media']}")
    if wait_for(c, cid, tok) == "PUBLISHED":
        m = find_post(c, ig, tok, text)
        if not m:
            raise PublishError(f"container {cid} was published under us but no recent media carries this caption")
        return found(m, "published under us; the post found by its caption")
    st, h, b = call("POST", f"{ig}/media_publish", {"creation_id": cid, "access_token": tok})
    if st != 200:
        refuse(st, h, b, "the publish", c)
    mid = str(json.loads(b)["id"])
    st, h, b = call("GET", mid, {"fields": "id,permalink,timestamp,media_product_type", "access_token": tok})
    permalink = json.loads(b).get("permalink") if st == 200 else None
    if not permalink:
        notes.append("no permalink yet: " + said(st, b))
    return {
        "status": "published",
        "remote_id": mid,
        "permalink": permalink,
        "published_at": ledger.now(),
        "note": "; ".join(notes),
    }


# ─── the upkeep ──────────────────────────────────────────────────────────────
def upkeep(c, env, log=print):
    """Once a day: which account the token serves (once), and whether Meta still honours the token, for Health;
    a word to hello@ when it does not. Returns one line for the log."""
    if not ready(env):
        return f"{NEEDS} not set"
    try:
        a = account(c, env)
    except PublishError as e:
        if ledger.credential(c, PLATFORM) is None or ledger.credential(c, PLATFORM)["state"] != "invalid":
            ledger.set_credential(c, PLATFORM, checked_at=ledger.now(), state="failed", note=str(e)[:300])
        warn(c, env, PLATFORM, SUBJECT, f"Meta would not say which Instagram account the token serves: {e}", log)
        return f"failed: {e}"
    app = f"{env['INSTAGRAM_APP_ID']}|{env['INSTAGRAM_APP_SECRET']}"
    st, h, b = call("GET", "debug_token", {"input_token": env["INSTAGRAM_TOKEN"], "access_token": app})
    if st != 200:
        ledger.set_credential(c, PLATFORM, checked_at=ledger.now(), state="failed", note=said(st, b)[:300])
        warn(c, env, PLATFORM, SUBJECT, f"Meta would not describe the Instagram token: {said(st, b)}", log)
        return f"failed: {said(st, b)}"
    d = json.loads(b).get("data") or {}
    if d.get("is_valid") is False:
        ledger.set_credential(
            c, PLATFORM, checked_at=ledger.now(), state="invalid", note="Meta says the token is not valid"
        )
        warn(c, env, PLATFORM, SUBJECT, f"The Instagram token is no longer valid: {REGENERATE}", log)
        return "invalid: " + REGENERATE
    expires = stamp(d.get("expires_at"))
    ledger.set_credential(
        c,
        PLATFORM,
        who=a["id"],
        checked_at=ledger.now(),
        expires_at=expires,
        grant_expires_at=stamp(d.get("data_access_expires_at")),
        issued_at=stamp(d.get("issued_at")),
        state="valid",
        note=f"@{a.get('username') or '?'}",
    )
    return f"@{a.get('username') or '?'} (id {a['id']}), token valid" + (
        f" to {parse(expires):%d %b %Y}" if expires else ", no expiry"
    )
