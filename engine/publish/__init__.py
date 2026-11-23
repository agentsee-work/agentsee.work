"""
The publisher: what is due, to the platform that wants it, through one
adapter per platform. An adapter is a module in this package with:

    SCHEDULES   True when the platform itself holds a post until its time
                (YouTube's publishAt), so the post is due the moment its
                plan is confirmed; False when the engine has to wait for
                the day.
    NEEDS       the environment variables it cannot run without, as a string
                for the message when they are missing.
    ready(env)  whether those are set.
    describe(post, episode, release) → one line for --dry-run.
    publish(post, episode, release, files, env, account, log) → a dict of
                post columns to set (status, remote_id, permalink,
                published_at) and an optional "note" for the event. Raises
                PublishError, or anything, when the platform said no; the
                ledger records it either way.

Confirmed plans only. Nothing of an episode is handed to an adapter until a
host has confirmed its plan (episode.confirmed_at; arrive.confirm sets it,
arrive.hold clears it): `split` selects the posts of confirmed episodes, so
`due`, `late`, the worker and the command line never see the others, and
`run` refuses a post of an unconfirmed episode however it was named.

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
    """Planned posts of confirmed episodes in two lists: those the next run
    takes (marked due by a person, on a platform that schedules for us, or
    past their time by less than late_hours) and those that wait for a
    person. A post of an episode whose plan is not confirmed is in neither."""
    t = parse(as_of) if as_of else dt.datetime.now(dt.timezone.utc)
    now, late = [], []
    for p in c.execute(
        "SELECT * FROM post WHERE status IN ('planned', 'due') "
        "AND episode IN (SELECT slug FROM episode WHERE confirmed_at IS NOT NULL) ORDER BY scheduled_at, id"
    ):
        if p["account"] not in ACCOUNTS:
            continue
        held = HELD.get(p["account"])
        if held and held[0] > t:
            continue
        if held:
            HELD.pop(p["account"], None)
        fate = fate_of(p, t)
        if fate:
            (now if fate == "now" else late).append(p)
    return now, late


def fate_of(p, t):
    """What a run at time t does with a planned post once its episode is
    confirmed: "now" (it takes it), "late" (it leaves it for a person), or
    None (its time has not come). The episode's page counts with this before
    a host confirms, so what it promises is what split then does."""
    if p["account"] not in ACCOUNTS:
        return None
    mod = adapter(p["account"])
    when = parse(p["scheduled_at"])
    if p["status"] == "due" or getattr(mod, "SCHEDULES", False):
        return "now"
    if when and when <= t:
        return "late" if getattr(mod, "SENDS", True) and t - when > late_after() else "now"
    return None


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
        if not ep["confirmed_at"]:  # named by hand (publish --post), or the plan was held after the list was made
            log(f"  {tag}: left, the plan of {p['episode']} is not confirmed")
            results.append((p["id"], "unconfirmed"))
            continue
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
            ledger.event(
                c,
                "publish:held",
                who,
                post=p["id"],
                episode=p["episode"],
                detail={"until": e.until.isoformat(), "why": why},
            )
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
        ledger.event(
            c, "publish:" + res["status"], who, post=p["id"], episode=p["episode"], detail={**res, "note": note}
        )
        log(
            f"  {tag}: {res['status']}"
            + (f" ({note})" if note else "")
            + (f" {res.get('permalink')}" if res.get("permalink") else "")
        )
        results.append((p["id"], res["status"]))
    return results
