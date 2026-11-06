"""
What the two Meta adapters share (publish/threads.py, publish/instagram.py):
the shape of Meta's errors, its "not now" with the wait it names, the
instants its token endpoints speak in, where the media host serves a file,
and the word to hello@ when a token needs a person. The ledger's
`credential` row per platform is kept through ledger.py.
"""

import datetime as dt
import json

import ledger
import mail

REST = 3600  # seconds to rest when Meta names no time
NOT_NOW = (4, 17, 613)  # Meta's rate-limit codes
BAD_TOKEN = 190
MAIL_AGAIN = dt.timedelta(days=7)


def error(body):
    try:
        e = json.loads(body).get("error")
        return e if isinstance(e, dict) else {}
    except ValueError, AttributeError:
        return {}


def said(status, body):
    e = error(body)
    if e:
        tag = ", ".join(f"{k} {e[j]}" for k, j in (("code", "code"), ("subcode", "error_subcode")) if j in e)
        return f"{status}: {e.get('message') or e.get('type') or e}" + (f" ({tag})" if tag else "")
    return f"{status}: {body[:300]!r}"


def rest_until(headers):
    """When Meta says to try again: its estimate in minutes from the usage headers; an hour when it gives none."""
    minutes = 0
    for name in ("X-Business-Use-Case-Usage", "X-App-Usage"):
        try:
            usage = json.loads(headers.get(name) or "{}")  # X-App-Usage is one object; the other a list per app
            entries = [usage] + [e for v in usage.values() if isinstance(v, list) for e in v if isinstance(e, dict)]
            for e in entries:
                minutes = max(minutes, int(e.get("estimated_time_to_regain_access") or 0))
        except ValueError, AttributeError, TypeError:
            pass
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=minutes * 60 if minutes else REST)


def stamp(unix):
    """A Unix time from Meta as an ISO instant; its 0 ("never") as None."""
    return dt.datetime.fromtimestamp(int(unix), dt.timezone.utc).replace(microsecond=0).isoformat() if unix else None


def parse(iso):
    return dt.datetime.fromisoformat(iso) if iso else None


def public_url(c, episode, name):
    """Where the media host serves the file: the ingest's record, or the episode's media address and the name."""
    row = c.execute("SELECT url FROM deliverable WHERE episode=? AND name=?", (episode["slug"], name)).fetchone()
    if row and row["url"]:
        return row["url"]
    base = (episode["media_url"] or "").rstrip("/")
    return f"{base}/{name}" if base else None


def warn(c, env, platform, subject, text, log=print):
    """A word to hello@ about a platform's token, at most once a week, and its row marked; a failed or
    invalid state stays, since it is the worse word."""
    row = ledger.credential(c, platform)
    last = parse(row["warned_at"]) if row and row["warned_at"] else None
    if last and dt.datetime.now(dt.timezone.utc) - last < MAIL_AGAIN:
        return False
    state = row["state"] if row and row["state"] in ("failed", "invalid") else "warned"
    to = mail.settings()["copy_to"]
    if not mail.ready(env):
        log(f"  (no mail: {mail.NEEDS} not set) {text}")
        ledger.set_credential(c, platform, warned_at=ledger.now(), state=state, note=text[:300])
        return False
    host = (ledger.CONFIG.get("engine") or {}).get("host", "engine.agentsee.work")
    try:
        msgid = mail.send(to, subject, text + f"\n\nHealth: https://{host}/health", env)
        mail.record(c, "engine", to, subject, msgid)
    except mail.MailError as e:
        mail.record(c, "engine", to, subject, error=e)
        log(f"  the warning could not be mailed: {e}")
    ledger.set_credential(c, platform, warned_at=ledger.now(), state=state, note=text[:300])
    return True
