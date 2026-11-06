"""
Threads: the episode's art as an image post with its text, each clip as a
video post, at the post's hour by the engine (Threads does not schedule).
Meta fetches the file itself from a public address, so nothing is
uploaded: the media host already serves the art and the clips, and the
adapter hands over their URLs. Three requests make a post: a container
(the request to post, with the text, the file's URL and its alt text), a
poll until Meta has fetched and processed the file, then the publish; and
one read for the permalink. 500 characters and five links at most; the
plan cut the text to 500 already.

The token. A Threads user token lives sixty days and is refreshed, not
replaced: a refresh returns a new token good for sixty days from then,
allowed once the token is a day old and before it expires. The vault item
"Threads podcast token" is the live copy, as X's is: the consent (two
routes in web.py, served from here) writes the first token there, the
upkeep writes each refreshed one before it is used, and the adapter reads
it from the vault, not from the environment, which is a copy from when the
process started. X's guard applies: a token whose write fails is held in
this process and written on the next attempt, and the error says not to
restart before then.

The grant. Meta's consent is good for ninety days and renews with every
refresh while the profile is public; a private profile lets it lapse,
after which every call fails until the consent is given again. The upkeep
(once a day, from the worker; `engine.py threads` by hand) asks Meta for
both dates (debug_token) rather than counting, keeps them in the ledger's
`credential` row for Health, refreshes under thirty days, and mails hello@
under fourteen days of grant or when a refresh fails.

Never twice. The container's id goes into an event the moment Meta issues
it; a retry of a failed post reads that event first, and a container Meta
says is PUBLISHED is found among the account's recent posts by its text
rather than published again. The Files object the publisher hands over
carries the ledger connection, and that is how this adapter writes the
event and marks the credential when Meta refuses the token.
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
import mail
import vault
from publish import Hold, PublishError
from vault import VaultError

SCHEDULES = False
NEEDS = "THREADS_APP_ID / THREADS_APP_SECRET"
PLATFORM = "threads"
AUTH = "https://threads.com/oauth/authorize"
API = "https://graph.threads.com"
V = "v1.0"
SCOPES = ("threads_basic", "threads_content_publish", "threads_manage_insights", "threads_delete")
LIMIT = 500
LINKS = 5
ALT = 1000
WAIT = 600  # seconds to give Meta to fetch and process a file
POLL = 15
PENDING = "pending"  # what the vault item holds before the first consent (AGE-130, D7)
READ_AGAIN = 3600  # seconds a token read from the vault is trusted before it is read again
REFRESH_UNDER = dt.timedelta(days=30)
GRANT_UNDER = dt.timedelta(days=14)
MAIL_AGAIN = dt.timedelta(days=7)
REST = 3600  # seconds to rest when Meta names no time
NOT_NOW = (4, 17, 613)  # Meta's rate-limit codes
BAD_TOKEN = 190
TYPES = {".mp4": "VIDEO", ".jpg": "IMAGE", ".jpeg": "IMAGE", ".png": "IMAGE"}

_token = {"value": None, "read_at": 0.0, "user": None, "unsaved": None}


def ready(env):
    return all(env.get(k) for k in ("THREADS_APP_ID", "THREADS_APP_SECRET"))


def host():
    return (ledger.CONFIG.get("engine") or {}).get("host", "engine.agentsee.work")


def consent_url():
    return f"https://{host()}/meta/threads/consent"


def redirect_uri():
    return f"https://{host()}/meta/threads/callback"


def links(text):
    return re.findall(r"https?://\S+", text or "")


# ─── the token ───────────────────────────────────────────────────────────────
def item():
    cfg = ledger.CONFIG.get(PLATFORM)
    if not cfg:
        raise PublishError('config.json has no "threads" entry naming the vault item that holds the token')
    return cfg["vault"], cfg["item"], cfg.get("field", "credential")


def keep(value):
    """The token into the vault, or held for the next attempt."""
    _token["unsaved"] = value
    try:
        vault.write(*item(), value)
    except VaultError as e:
        raise PublishError(
            f"Meta issued a Threads token but the vault write failed ({e}). It is held in this process only and "
            "the write is retried on the next attempt. Do not restart the engine before that succeeds, or this "
            "token is lost and the vault keeps the older one, which expires on its own date"
        ) from e
    _token["unsaved"] = None
    _token.update(value=value, read_at=time.time(), user=None)


def token(log=print):
    """The sixty-day token, from the vault: read again every hour, since the upkeep may have refreshed it."""
    if _token["unsaved"]:
        keep(_token["unsaved"])
        log("  the held Threads token is in the vault now")
    if _token["value"] and time.time() < _token["read_at"] + READ_AGAIN:
        return _token["value"]
    try:
        value = (vault.read(*item()) or "").strip()
    except VaultError as e:
        raise PublishError(
            f"could not read the Threads token from the vault ({e}): the engine needs an op session (run it under op run)"
        ) from e
    if not value or value == PENDING:
        raise PublishError(f"no Threads token yet: give the consent at {consent_url()}")
    if value != _token["value"]:
        _token["user"] = None
    _token.update(value=value, read_at=time.time())
    return value


# ─── requests ────────────────────────────────────────────────────────────────
def call(method, path, params=None, timeout=60, version=V):
    """One request to Meta: the status, the headers and the body."""
    url = f"{API}/{version}/{path}" if version else f"{API}/{path}"
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


def error(body):
    try:
        e = json.loads(body).get("error")
        return e if isinstance(e, dict) else {}
    except ValueError, AttributeError:
        return {}


def said(status, body):
    e = error(body)
    if e:
        return f"{status}: {e.get('message') or e.get('type') or e}" + (f" (code {e['code']})" if "code" in e else "")
    return f"{status}: {body[:300]!r}"


def rest_until(headers):
    """When Meta says to try again: its estimate in minutes from the usage header; an hour when it gives none."""
    minutes = 0
    try:
        for entries in json.loads(headers.get("X-Business-Use-Case-Usage") or "{}").values():
            for e in entries:
                minutes = max(minutes, int(e.get("estimated_time_to_regain_access") or 0))
    except ValueError, AttributeError, TypeError:
        pass
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=minutes * 60 if minutes else REST)


def refuse(st, h, b, what, c=None):
    """Meta said no: a Hold when it means not now, a PublishError otherwise; a refused token marks the record."""
    e = error(b)
    if e.get("code") in NOT_NOW:
        raise Hold(f"Threads, {what}: {said(st, b)}", rest_until(h))
    if e.get("code") == BAD_TOKEN:
        if c is not None:
            record(c, state="invalid", checked_at=ledger.now(), note=said(st, b)[:300])
        _token.update(value=None, read_at=0.0)
        raise PublishError(f"Meta refused the Threads token ({said(st, b)}): give the consent again at {consent_url()}")
    raise PublishError(f"Threads, {what}: {said(st, b)}")


def me(tok, c=None):
    """Whose token: the user id and the handle, once per token in this process."""
    if _token["user"] and _token["user"][0] == tok:
        return _token["user"][1]
    st, h, b = call("GET", "me", {"fields": "id,username", "access_token": tok})
    if st != 200:
        refuse(st, h, b, "whose token this is", c)
    j = json.loads(b)
    _token["user"] = (tok, j)
    return j


# ─── the post ────────────────────────────────────────────────────────────────
def public_url(c, post, episode):
    """Where the media host serves the file: the ingest's record, or the episode's media address and the name."""
    row = c.execute(
        "SELECT url FROM deliverable WHERE episode=? AND name=?", (post["episode"], post["media"])
    ).fetchone()
    if row and row["url"]:
        return row["url"]
    base = (episode["media_url"] or "").rstrip("/")
    if not base:
        raise PublishError(f"no public address for {post['media']}: Meta fetches the file from the media host")
    return f"{base}/{post['media']}"


def alt_for(post, episode, release):
    if post["asset"] == "episode":
        return f"{episode['title']}: the episode art"
    clip = next((x for x in release.get("clips", []) if x["file"] == post["media"]), {})
    return clip.get("alt") or clip.get("title") or post["title"] or ""


def container(c, post, uid, tok, params):
    st, h, b = call("POST", f"{uid}/threads", {**params, "access_token": tok})
    if st != 200:
        refuse(st, h, b, "the container", c)
    cid = str(json.loads(b)["id"])
    ledger.event(
        c,
        "publish:container",
        "engine",
        post=post["id"],
        episode=post["episode"],
        detail={"id": cid, "media_type": params["media_type"], "media": post["media"]},
    )
    return cid


def state_of(cid, tok):
    st, h, b = call("GET", cid, {"fields": "status,error_message", "access_token": tok})
    return (st, h, b, json.loads(b) if st == 200 else {})


def wait_for(c, cid, tok):
    deadline = time.time() + WAIT
    while True:
        st, h, b, j = state_of(cid, tok)
        if st != 200:
            refuse(st, h, b, "the container's status", c)
        s = j.get("status")
        if s in ("FINISHED", "PUBLISHED"):
            return s
        if s == "ERROR":
            raise PublishError(f"Meta could not take the file: {j.get('error_message') or 'no reason given'}")
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


def find_post(c, uid, tok, text):
    """The account's recent posts, for the one with this text: a container published but never recorded."""
    st, h, b = call(
        "GET", f"{uid}/threads", {"fields": "id,text,permalink,timestamp", "limit": "25", "access_token": tok}
    )
    if st != 200:
        refuse(st, h, b, "the recent posts", c)
    return next((m for m in json.loads(b).get("data", []) if (m.get("text") or "").strip() == text), None)


def quota(c, uid, tok, log):
    """The day's publishing quota, before a container is made: full is a hold, not a failure."""
    st, h, b = call("GET", f"{uid}/threads_publishing_limit", {"fields": "quota_usage,config", "access_token": tok})
    if st != 200:
        if error(b).get("code") in (BAD_TOKEN, *NOT_NOW):
            refuse(st, h, b, "the publishing quota", c)
        log(f"  Threads would not say how much of the day's quota is used: {said(st, b)}")
        return None
    d = (json.loads(b).get("data") or [{}])[0]
    used, total = d.get("quota_usage"), (d.get("config") or {}).get("quota_total")
    if used is not None and total and used >= total:
        raise Hold(
            f"Threads: {used} of {total} posts in the last day, the limit",
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=REST),
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
    kind = TYPES.get(pathlib.Path(post["media"]).suffix.lower(), "?").lower() if post["media"] else "text"
    what = f"a Threads {kind} post of {post['media']} with its alt text" if post["media"] else "a Threads text post"
    return f"{what}, {len(text)} of {LIMIT} characters: {post['title']}"


def publish(post, episode, release, files, env, account, log):
    c = files.c
    text = (post["text"] or "").strip()
    if len(text) > LIMIT:
        raise PublishError(f"the text is {len(text)} characters; Threads takes {LIMIT}. Cut it on the post's page")
    if len(links(text)) > LINKS:
        raise PublishError(f"the text carries {len(links(text))} links; Threads takes {LINKS}")
    if not text and not post["media"]:
        raise PublishError("nothing to post: no text and no media")
    params = {"text": text} if text else {}
    if post["media"]:
        kind = TYPES.get(pathlib.Path(post["media"]).suffix.lower())
        if not kind:
            raise PublishError(f"Threads takes no {pathlib.Path(post['media']).suffix} files")
        params["media_type"] = kind
        params["image_url" if kind == "IMAGE" else "video_url"] = public_url(c, post, episode)
        params["alt_text"] = alt_for(post, episode, release)[:ALT]
    else:
        params["media_type"] = "TEXT"
    tok = token(log)
    uid = str(me(tok, c)["id"])
    notes = [f"{len(text)} of {LIMIT} characters", params["media_type"].lower()]
    cid = None
    prior = last_container(c, post)
    if prior:  # a failed attempt left a container: never twice
        st, h, b, j = state_of(prior["id"], tok)
        if st != 200 and error(b).get("code") in (BAD_TOKEN, *NOT_NOW):
            refuse(st, h, b, "the earlier container's status", c)
        s = j.get("status")
        if s == "PUBLISHED":
            m = find_post(c, uid, tok, text)
            if not m:
                raise PublishError(
                    f"Meta says the earlier container {prior['id']} was published, but no recent post carries this "
                    "text; look at the profile before a retry"
                )
            return found(m, "the earlier container had been published; the post found by its text")
        if s == "FINISHED":
            cid = prior["id"]
            notes.append("the earlier container, ready")
            log(f"  container {cid} from the earlier attempt is ready; publishing it")
        else:
            log(f"  the earlier container {prior['id']} is {s or said(st, b)}; starting again")
    if not cid:
        quota(c, uid, tok, log)
        cid = container(c, post, uid, tok, params)
        log(f"  container {cid}; waiting for Meta to fetch {post['media'] or 'the text'}")
    if wait_for(c, cid, tok) == "PUBLISHED":
        m = find_post(c, uid, tok, text)
        if not m:
            raise PublishError(f"container {cid} was published under us but no recent post carries this text")
        return found(m, "published under us; the post found by its text")
    st, h, b = call("POST", f"{uid}/threads_publish", {"creation_id": cid, "access_token": tok})
    if st != 200:
        refuse(st, h, b, "the publish", c)
    mid = str(json.loads(b)["id"])
    st, h, b = call("GET", mid, {"fields": "id,permalink,timestamp,shortcode", "access_token": tok})
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


# ─── the consent ─────────────────────────────────────────────────────────────
def authorize_url(env, state):
    return (
        AUTH
        + "?"
        + urllib.parse.urlencode(
            {
                "client_id": env["THREADS_APP_ID"],
                "redirect_uri": redirect_uri(),
                "scope": ",".join(SCOPES),
                "response_type": "code",
                "state": state,
            }
        )
    )


def exchange(env, code):
    """The code from the consent for a one-hour token, then that for the sixty-day one: (token, user id, seconds)."""
    code = code.strip().removesuffix("#_")  # Meta appends it to the redirect; not part of the code
    st, h, b = call(
        "POST",
        "oauth/access_token",
        {
            "client_id": env["THREADS_APP_ID"],
            "client_secret": env["THREADS_APP_SECRET"],
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri(),
            "code": code,
        },
        version=None,
    )
    if st != 200:
        raise PublishError("Meta would not exchange the code: " + said(st, b))
    j = json.loads(b)
    short, uid = j["access_token"], str(j.get("user_id") or "")
    st, h, b = call(
        "GET",
        "access_token",
        {"grant_type": "th_exchange_token", "client_secret": env["THREADS_APP_SECRET"], "access_token": short},
        version=None,
    )
    if st != 200:
        raise PublishError("Meta would not issue the long-lived token: " + said(st, b))
    j = json.loads(b)
    return j["access_token"], uid, int(j.get("expires_in") or 0)


def refresh(tok):
    st, h, b = call(
        "GET", "refresh_access_token", {"grant_type": "th_refresh_token", "access_token": tok}, version=None
    )
    if st != 200:
        raise PublishError("Meta would not refresh the token: " + said(st, b))
    j = json.loads(b)
    return j["access_token"], int(j.get("expires_in") or 0)


def inspect(tok):
    """Meta's word on the token: is_valid, expires_at, data_access_expires_at (the grant), issued_at, scopes, user_id."""
    st, h, b = call("GET", "debug_token", {"input_token": tok, "access_token": tok})
    if st != 200:
        raise PublishError("Meta would not describe the token: " + said(st, b))
    return json.loads(b).get("data") or {}


def stamp(unix):
    return dt.datetime.fromtimestamp(int(unix), dt.timezone.utc).replace(microsecond=0).isoformat() if unix else None


def parse(iso):
    return dt.datetime.fromisoformat(iso) if iso else None


def dates(info, expires_in=0):
    """The three instants from a debug_token answer; the expiry from the exchange's seconds when Meta says 0."""
    expires = stamp(info.get("expires_at"))
    if not expires and expires_in:
        expires = (
            (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=expires_in)).replace(microsecond=0).isoformat()
        )
    return {
        "issued_at": stamp(info.get("issued_at")) or ledger.now(),
        "expires_at": expires,
        "grant_expires_at": stamp(info.get("data_access_expires_at")),
    }


def record(c, **fields):
    """The credential row for Threads, made or updated."""
    fields["updated_at"] = ledger.now()
    cols, marks = ", ".join(fields), ", ".join("?" * len(fields))
    sets = ", ".join(f"{k}=excluded.{k}" for k in fields)
    c.execute(
        f"INSERT INTO credential (platform, {cols}) VALUES (?, {marks}) ON CONFLICT(platform) DO UPDATE SET {sets}",
        (PLATFORM, *fields.values()),
    )


def status(c):
    return c.execute("SELECT * FROM credential WHERE platform=?", (PLATFORM,)).fetchone()


def consent(c, env, code, who="engine", log=print):
    """The callback's work: the exchanges, the token into the vault, the record. Returns (row, profile)."""
    tok, uid, expires_in = exchange(env, code)
    keep(tok)
    v, name, _ = item()
    try:
        vault.write(v, name, "username", uid or "?")
    except VaultError as e:
        log(f"  the item's username was not updated with the user id: {e}")
    info = inspect(tok)
    who_ = str(info.get("user_id") or uid)
    record(
        c,
        who=who_,
        **dates(info, expires_in),
        checked_at=ledger.now(),
        refreshed_at=None,
        warned_at=None,
        state="valid",
        note=f"consent given by {who}; scopes {' '.join(info.get('scopes') or [])}",
    )
    ledger.event(c, "threads:consent", who, detail={"user": who_, "scopes": info.get("scopes")})
    return status(c), me(tok, c)


# ─── the upkeep ──────────────────────────────────────────────────────────────
def warn(c, env, text, log=print):
    """A word to hello@, at most once a week, and the row marked."""
    row = status(c)
    last = parse(row["warned_at"]) if row and row["warned_at"] else None
    if last and dt.datetime.now(dt.timezone.utc) - last < MAIL_AGAIN:
        return False
    state = row["state"] if row and row["state"] in ("failed", "invalid") else "warned"  # the worse word stays
    to, subject = mail.settings()["copy_to"], "The Threads token needs a look"
    if not mail.ready(env):
        log(f"  (no mail: {mail.NEEDS} not set) {text}")
        record(c, warned_at=ledger.now(), state=state, note=text[:300])
        return False
    try:
        msgid = mail.send(to, subject, text + f"\n\nHealth: https://{host()}/health", env)
        mail.record(c, "engine", to, subject, msgid)
    except mail.MailError as e:
        mail.record(c, "engine", to, subject, error=e)
        log(f"  the warning could not be mailed: {e}")
    record(c, warned_at=ledger.now(), state=state, note=text[:300])
    return True


def upkeep(c, env, log=print, now=None):
    """Once a day: Meta's dates for the token, a refresh under thirty days, a word to hello@ when the grant is
    lapsing or a refresh failed. Returns one line for the log."""
    now = now or dt.datetime.now(dt.timezone.utc)
    held = _token["unsaved"]
    try:
        tok = token(log)
    except PublishError as e:
        return str(e)
    if held:  # the refreshed token the last run could not file is in the vault now
        record(c, refreshed_at=ledger.now(), state="valid", note=None)
        ledger.event(c, "threads:refreshed", "engine", detail={"held": True})
    try:
        info = inspect(tok)
    except PublishError as e:
        record(c, checked_at=ledger.now(), state="failed", note=str(e)[:300])
        warn(c, env, f"Meta would not describe the Threads token: {e}", log)
        return f"failed: {e}"
    if info.get("is_valid") is False:
        record(c, checked_at=ledger.now(), state="invalid", note="Meta says the token is not valid")
        warn(c, env, f"The Threads token is no longer valid. Give the consent again at {consent_url()}", log)
        return "invalid: the consent must be given again"
    row = status(c)
    d = dates(info)
    record(c, who=str(info.get("user_id") or (row["who"] if row else "")), **d, checked_at=ledger.now(), state="valid")
    expires, grant, issued = parse(d["expires_at"]), parse(d["grant_expires_at"]), parse(d["issued_at"])
    words = [f"valid to {expires:%d %b}" if expires else "no expiry given"]
    if expires and expires - now < REFRESH_UNDER and (not issued or now - issued >= dt.timedelta(days=1)):
        try:
            new, expires_in = refresh(tok)
            keep(new)
            info = inspect(new)
            d = dates(info, expires_in)
            record(c, **d, refreshed_at=ledger.now(), state="valid", note=None)
            ledger.event(c, "threads:refreshed", "engine", detail={"expires_at": d["expires_at"]})
            expires, grant = parse(d["expires_at"]), parse(d["grant_expires_at"])
            words.append(f"refreshed, now to {expires:%d %b}" if expires else "refreshed")
        except PublishError as e:
            record(c, state="failed", note=str(e)[:300])
            warn(
                c, env, f"The Threads token could not be refreshed: {e}. Give the consent again at {consent_url()}", log
            )
            words.append(f"refresh failed: {e}")
    if grant:
        words.append(f"grant to {grant:%d %b}")
        if grant - now < GRANT_UNDER:
            warn(
                c,
                env,
                f"The Threads grant lapses on {grant:%d %B %Y}. A refresh renews it only while the profile is public; "
                f"otherwise the consent must be given again at {consent_url()}",
                log,
            )
    return ", ".join(words)
