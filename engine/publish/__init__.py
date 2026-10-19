"""
The publisher: what is due, to the platform that wants it, through one
adapter per platform. An adapter is a module in this package with:

    SCHEDULES   True when the platform itself holds a post until its time
                (YouTube's publishAt), so the post is due the moment it
                exists; False when the engine has to wait for the day.
    NEEDS       the environment variables it cannot run without, as a string
                for the message when they are missing.
    ready(env)  whether those are set.
    describe(post, episode, release) → one line for --dry-run.
    publish(post, episode, release, files, env, account, log) → a dict of
                post columns to set (status, remote_id, permalink,
                published_at) and an optional "note" for the event. Raises
                PublishError, or anything, when the platform said no; the
                ledger records it either way.

A post is never run twice at once: the run marks it `publishing` first, and
`due` ignores that state. A failure leaves `failed` with the error, and a
person retries it from the Posts screen.

Holds. When the platform says "not now" rather than "no" (YouTube's daily
upload limit), the adapter raises Hold with a time: the post keeps its
state, the rest of that account is left for later in the same run, and
`due` leaves the account alone until the time has passed. HELD remembers
that for the life of the process, so a worker tries again by itself and a
`publish` run by hand tries at once.

Late posts. An adapter that sends (SENDS, the default; the hand card sets it
False) is not given a post whose time passed more than `late_hours` ago
(config.json): if the engine was off on the morning, that morning's posts
wait on the By hand screen for a person to publish now, move or skip,
rather than going out in a bunch at the next start. A post a person marked
`due` goes regardless.
"""

import datetime as dt
import importlib
import os

import archive
import ledger
from plan import ACCOUNTS


class PublishError(Exception):
    pass


class Hold(PublishError):
    """Not now: try the account again after `until` (aware datetime)."""

    def __init__(self, msg, until):
        super().__init__(msg)
        self.until = until


HELD = {}  # account → (until, why), in this process


def adapter(account):
    return importlib.import_module("publish." + ACCOUNTS[account]["adapter"])


def parse(iso):
    return dt.datetime.fromisoformat(iso) if iso else None


def late_after():
    return dt.timedelta(hours=float(ledger.CONFIG.get("late_hours", 6)))


def split(c, as_of=None):
    """Planned posts in two lists: those the next run takes (marked due by a
    person, on a platform that schedules for us, or past their time by less
    than late_hours) and those that wait for a person."""
    t = parse(as_of) if as_of else dt.datetime.now(dt.timezone.utc)
    now, late = [], []
    for p in c.execute("SELECT * FROM post WHERE status IN ('planned', 'due') ORDER BY scheduled_at, id"):
        if p["account"] not in ACCOUNTS:
            continue
        held = HELD.get(p["account"])
        if held and held[0] > t:
            continue
        if held:
            HELD.pop(p["account"], None)
        mod = adapter(p["account"])
        when = parse(p["scheduled_at"])
        if p["status"] == "due" or getattr(mod, "SCHEDULES", False):
            now.append(p)
        elif when and when <= t:
            if getattr(mod, "SENDS", True) and t - when > late_after():
                late.append(p)
            else:
                now.append(p)
    return now, late


def due(c, as_of=None, include_late=False):
    now, late = split(c, as_of)
    return now + late if include_late else now


def late(c, as_of=None):
    return split(c, as_of)[1]


def run(c, posts, env=None, dry_run=False, who="engine", log=print):
    env = os.environ if env is None else env
    results = []
    resting = set()  # accounts a Hold stopped in this run
    for p in posts:
        if p["account"] in resting:
            results.append((p["id"], "held"))
            continue
        account = ACCOUNTS[p["account"]]
        mod = adapter(p["account"])
        ep = ledger.episode(c, p["episode"])
        r = ledger.release(ep)
        files = archive.Files(c, ep)  # a path per deliverable, fetched from the archive when not here
        tag = f"post {p['id']} ({p['account']}, {p['asset']})"
        if not mod.ready(env):
            log(f"  {tag}: skipped, {mod.NEEDS} not set")
            results.append((p["id"], "skipped-no-credentials"))
            continue
        if dry_run:
            log(f"  {tag}: " + mod.describe(p, ep, r))
            results.append((p["id"], "dry-run"))
            continue
        ledger.update_post(c, p["id"], status="publishing", attempts=p["attempts"] + 1)
        ledger.event(c, "publish:start", who, post=p["id"], episode=p["episode"])
        try:
            res = mod.publish(p, ep, r, files, env, account, log)
        except Hold as e:
            why = str(e)[:1000]
            ledger.update_post(c, p["id"], status=p["status"], last_error=why)
            ledger.event(c, "publish:held", who, post=p["id"], episode=p["episode"], detail={"until": e.until.isoformat(), "why": why})
            HELD[p["account"]] = (e.until, why)
            resting.add(p["account"])
            log(f"  {tag}: held until {e.until.astimezone(ledger.ZONE).strftime('%H:%M')}: {why}")
            results.append((p["id"], "held"))
            continue
        except Exception as e:  # noqa: BLE001 — whatever the platform or the network threw, the ledger must say
            err = f"{type(e).__name__}: {e}"[:1000]
            ledger.update_post(c, p["id"], status="failed", last_error=err)
            ledger.event(c, "publish:failed", who, post=p["id"], episode=p["episode"], detail=err)
            log(f"  {tag}: failed: {err}")
            results.append((p["id"], "failed"))
            continue
        note = res.pop("note", None)
        ledger.update_post(c, p["id"], last_error=None, **res)
        ledger.event(c, "publish:" + res["status"], who, post=p["id"], episode=p["episode"],
                     detail={**res, "note": note})
        log(f"  {tag}: {res['status']}" + (f" ({note})" if note else "") + (f" {res.get('permalink')}" if res.get("permalink") else ""))
        results.append((p["id"], res["status"]))
    return results
