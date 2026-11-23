"""
A staged episode into the ledger with nobody at a shell. Stage writes
`episodes/<slug>/out/staged.json` to the archive last (tools/stage.py), so
that one object is the signal: the worker lists the archive every few
minutes, and an episode whose staged record the ledger has not taken is
checked, ingested, planned and given its short links here, in one pass. What
used to be `engine.py ingest` and `engine.py plan` over SSH.

What a pass will not do by itself, and says instead (the Episodes screen, a
mail to the hosts, a red row on Health):

  refused   the record is not approved; a guest's approval is not the one
            this engine recorded; the number is another episode's; or
            something has gone out and the staged number or date is no longer
            the one it went out under. Nothing changes until the episode is
            staged again.
  waiting   automatic arrival is off (it starts off: the first passes only
            say what they would do, and a host turns it on once); the release
            time has passed; or the episode was approved with the guests'
            gate waived. A host's button brings that one staging in.
  failed    the archive or the ledger said no, or release.json is not the one
            staged.json names (a stage is under way). Tried again every pass.
  unlinked  it is in the ledger and planned, and KV did not take its short
            links. Only the links are tried again, whatever the switch says.

A number or a date that changes after an arrival (a guest episode moved in
the queue) is followed while nothing has gone out: every post takes the new
short link, and its new day when the plan's day moved; a post nobody edited
is written again from the record, an edited one keeps its words. Two
episodes swapping numbers go in one transaction with the link ids cleared
first, so e5… is never two posts'. Once a post is out the number and the
date are frozen.

Confirming the plan. An episode that arrives is planned and nothing more:
no upload, no post and no card is made until a host has confirmed its plan
(`confirm`, the button on the episode's page, or `engine.py confirm`), and
the publisher leaves every post of an unconfirmed episode alone
(publish.split). So the number and the date freeze when the first post goes
out after that, not as the episode arrives. `hold` takes the confirmation
back; a staging taken again while none of the episode's posts is out takes
it back too, because what was confirmed is no longer what is staged, and
the hosts are asked again. A plan still unconfirmed a day before its
release gets one more note (`chase`) and a red row on Health.
"""

import collections
import datetime as dt
import hashlib
import json
import os
import re
import threading
import time

import archive
import ingest
import ledger
import linear
import mail
import plan
import publish
from pages import button, esc, link, section, table, when

SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,60}")
GUEST = re.compile(r"[0-9a-f]{8}")
ENGINE = (ledger.CONFIG.get("engine") or {}).get("host", "engine.agentsee.work")
OUT = ("publishing", "scheduled", "published")  # a platform has it, or is being handed it
FAILING = ("failed", "unlinked")  # tried again every pass: nothing came in, or all but the short links did
QUIET = dt.timedelta(minutes=30)  # a failure younger than this is not worth a mail: the next pass may clear it
SOON = dt.timedelta(hours=24)  # a plan nobody has confirmed this close to its release is said again, and is red
LOCK = threading.Lock()  # one pass at a time: the worker's, or a screen's "look now"
LAST = {}  # the last pass in this process, for the screens


class Verdict(Exception):
    """Why this staging is not brought in now."""

    state = "refused"


class Wait(Verdict):
    state = "waiting"


class Trouble(Exception):
    """One episode stopped the transaction; the rest are tried without it."""

    def __init__(self, cand, why):
        super().__init__(why)
        self.cand = cand


def every():
    return dt.timedelta(minutes=float((ledger.CONFIG.get("arrive") or {}).get("every_minutes", 10)))


def sha(b):
    return hashlib.sha256(b).hexdigest()


# ─── the switch, and a host's word on one staging ────────────────────────────
def on(c):
    row = c.execute("SELECT what FROM event WHERE what IN ('arrive:on', 'arrive:off') ORDER BY id DESC LIMIT 1")
    row = row.fetchone()
    return bool(row and row["what"] == "arrive:on")


def switch(c, to, who):
    if to != on(c):
        ledger.event(c, "arrive:on" if to else "arrive:off", who)


def arrival(c, slug):
    return c.execute("SELECT * FROM arrival WHERE slug = ?", (slug,)).fetchone()


def allow(c, slug, who):
    """A host brings this staging in whatever the pass was waiting for. Only
    a waiting one: a refusal is answered by staging again, not by a button."""
    row = arrival(c, slug)
    if not row or row["state"] not in ("waiting", "aside"):
        raise ValueError(f"{slug} is not waiting for anyone")
    c.execute(
        "UPDATE arrival SET state='waiting', allowed_for=?, allowed_by=?, allowed_at=? WHERE slug=?",
        (row["staged_at"], who, ledger.now(), slug),
    )
    ledger.event(c, "arrive:allowed", who, episode=slug, detail={"staged_at": row["staged_at"]})


def set_aside(c, slug, who, aside=True):
    """Not this one: off the red list until it is staged again, or put back."""
    row = arrival(c, slug)
    if not row or row["state"] not in (("waiting", "refused") if aside else ("aside",)):
        raise ValueError(f"{slug} is not waiting for anyone")
    c.execute("UPDATE arrival SET state=?, since=? WHERE slug=?", ("aside" if aside else "waiting", ledger.now(), slug))
    ledger.event(c, "arrive:aside" if aside else "arrive:back", who, episode=slug)


def act(c, what, slug, who, **kw):
    """One button on the Episodes screen. Returns the sentence it shows."""
    if what == "auto/off":
        switch(c, False, who)
        return "Automatic arrival is paused: the engine still looks, and brings nothing in without a host's word"
    if what == "auto/on":
        switch(c, True, who)
        return "Automatic arrival is on. " + sentence(attempt(c, who=who, **kw))
    if what == "look":
        return sentence(attempt(c, who=who, **kw))
    if what == "bring":
        allow(c, slug, who)
        return sentence(attempt(c, who=who, **kw), slug)
    set_aside(c, slug, who, what == "aside")
    return f"{slug} is set aside until it is staged again" if what == "aside" else f"{slug} is back on the list"


# ─── the plan, confirmed by a host ───────────────────────────────────────────
def confirm(c, slug, who, staged=None):
    """A host has read the plan and says send it: the publisher takes the
    episode's posts from its next minute. `staged` is the staging the host was
    shown; when the ledger has taken another since, the word is not for this
    plan. Returns the sentence for whoever asked."""
    e = ledger.episode(c, slug)
    if not e:
        raise ValueError(f"{slug} is not in the ledger")
    if e["confirmed_at"]:
        return f"The plan of {slug} was confirmed already, by {e['confirmed_by']}"
    n = c.execute("SELECT COUNT(*) FROM post WHERE episode=?", (slug,)).fetchone()[0]
    if not n:
        raise ValueError(f"{slug} has no posts to confirm: plan it first")
    if staged is not None and staged != (e["staged_at"] or ""):
        raise ValueError(f"{slug} was staged again since that page was made: read the plan as it is now")
    c.execute("UPDATE episode SET confirmed_at=?, confirmed_by=? WHERE slug=?", (ledger.now(), who, slug))
    ledger.event(c, "confirm", who, episode=slug, detail={"posts": n, "staged_at": e["staged_at"]})
    return f"The plan of {slug} is confirmed: the publisher takes its posts from the next minute"


def hold(c, slug, who, why=None):
    """The confirmation taken back: nothing more of the episode is sent until
    a host confirms again. What a platform already has stays there."""
    e = ledger.episode(c, slug)
    if not e:
        raise ValueError(f"{slug} is not in the ledger")
    if not e["confirmed_at"]:
        return f"The plan of {slug} is not confirmed: nothing of it is being sent"
    c.execute("UPDATE episode SET confirmed_at=NULL, confirmed_by=NULL WHERE slug=?", (slug,))
    was = {"confirmed_by": e["confirmed_by"], "confirmed_at": e["confirmed_at"]}
    ledger.event(c, "confirm:held", who, episode=slug, detail={**was, "why": why})
    return (
        f"The plan of {slug} is held: nothing more is sent until it is confirmed again. "
        "What a platform already has stays there"
    )


def unconfirmed(c):
    """Episodes with posts the publisher would take and no host's word to take
    them, soonest release first."""
    return c.execute(
        "SELECT * FROM episode WHERE confirmed_at IS NULL AND slug IN "
        "(SELECT episode FROM post WHERE status IN ('planned', 'due')) ORDER BY release_at, slug"
    ).fetchall()


def soon(e):
    """Its release is under a day away, or has passed."""
    at = e["release_at"] and dt.datetime.fromisoformat(e["release_at"])
    return bool(at and at - dt.datetime.now(dt.timezone.utc) < SOON)


def chase(c, env=None, log=print):
    """One more note to the hosts about a plan nobody has confirmed with its
    release under a day away. Once per wait (a hold begins a new one), and not
    within QUIET of the arrival or the hold, which has just said the same.
    Returns the episodes it wrote about."""
    env = os.environ if env is None else env
    chased = []
    for e in unconfirmed(c):
        slug = e["slug"]
        if not soon(e):
            continue
        began = c.execute(
            "SELECT COALESCE(MAX(id), 0) FROM event WHERE episode=? AND what='confirm:held'", (slug,)
        ).fetchone()[0]
        if c.execute(
            "SELECT 1 FROM event WHERE episode=? AND what='confirm:reminder' AND id > ?", (slug, began)
        ).fetchone():
            continue
        last = c.execute(
            "SELECT MAX(at) FROM event WHERE episode=? AND what IN ('ingest', 'confirm:held')", (slug,)
        ).fetchone()[0]
        if last and dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(last) < QUIET:
            continue
        passed = dt.datetime.fromisoformat(e["release_at"]) <= dt.datetime.now(dt.timezone.utc)
        name = f"Episode {e['number']}, {e['title']}"
        subject = f"{name} is not confirmed, and its release {'has passed' if passed else 'is under a day away'}"
        text = (
            f"{slug} is in the ledger and planned, and no host has confirmed its plan. "
            f"Its release {'was' if passed else 'is'} {when(e['release_at'])}.\n\n"
            "Nothing of it is sent until one of you confirms: no upload, no post, no card. "
            "The page has the plan and the button. This is the only reminder.\n\n"
            f"https://{ENGINE}/episodes/{slug}"
        )
        ledger.event(c, "confirm:reminder", "engine", episode=slug, detail={"release_at": e["release_at"]})
        to = mail.settings().get("copy_to")
        if to and mail.ready(env):
            try:
                mail.record(c, "engine", to, subject, mail.send(to, subject, text, env))
            except mail.MailError as err:
                mail.record(c, "engine", to, subject, error=err)
                log(f"arrive: the reminder to confirm {slug} was not sent: {err}")
            time.sleep(mail.PACE)
        chased.append(slug)
    return chased


# ─── one pass ────────────────────────────────────────────────────────────────
def scan(c, client):
    """The staged episodes the ledger has not settled: one listing, and one
    HEAD of each staged.json, whose ETag says whether it is the one a pass
    dealt with before. Returns (candidates, how many are staged)."""
    folders, _ = client.list("episodes/")
    cands, staged = [], set()
    for folder in sorted(folders):
        slug = folder[len("episodes/") :].rstrip("/")
        if not SLUG.fullmatch(slug):
            continue
        h = client.head(f"episodes/{slug}/out/staged.json", timeout=20)
        if h is None:
            continue
        staged.add(slug)
        etag = (h.get("ETag") or "").strip('"')
        row = arrival(c, slug)
        if row and row["state"] in ("in", "aside") and etag and row["etag"] == etag:
            continue
        cands.append({"slug": slug, "etag": etag, "row": row, "state": None, "why": None})
    return cands, staged


def released(date):
    return dt.datetime.fromisoformat(ledger.at(date, ledger.CONFIG["release_time"])) <= dt.datetime.now(dt.timezone.utc)


def out(p):
    return bool(p["status"] in OUT or p["published_at"] or p["remote_id"] or p["permalink"])


def posts(c, slug):
    return c.execute("SELECT * FROM post WHERE episode=? ORDER BY id", (slug,)).fetchall()


def judge(c, client, cand):
    """The two records read and checked; raises the Verdict, or returns with
    the candidate ready to bring in ('ok') or already here ('in')."""
    slug, row = cand["slug"], cand["row"]
    base = f"episodes/{slug}/out/"
    sraw = client.get_bytes(base + "staged.json", timeout=20)
    rraw = client.get_bytes(base + "release.json", timeout=20)
    if sraw is None:
        raise archive.ArchiveError("staged.json went away while it was being read")
    s = json.loads(sraw)
    cand.update(s=s, staged_at=s.get("at"))
    if rraw is None:
        raise Verdict("staged.json is in the archive and release.json is not")
    listed = (s.get("archive") or {}).get(base + "release.json") or {}
    if listed.get("sha256") and listed["sha256"] != sha(rraw):
        raise archive.ArchiveError(
            "release.json in the archive is not the one staged.json names: a stage is under way, or did not finish"
        )
    r = json.loads(rraw)
    ep = r["episode"]
    cand.update(
        r=r, raw={"release.json": rraw, "staged.json": sraw}, number=str(ep["number"]), title=ep["title"],
        release=ep["release"],
    )  # fmt: skip
    ap = ep.get("approved")
    if not ap:
        raise Verdict("the staged record is not approved")
    old = ledger.episode(c, slug)
    mine = posts(c, slug) if old else []
    if old and old["staged_at"] == cand["staged_at"] and mine:
        if row and row["state"] == "unlinked":  # a pass brought it in and its links did not go: only those are left
            cand.update(state="ok", resume=True, files=len(r["files"]), made=[], changes={}, again=False)
        else:  # ingested and planned by hand, at this staging: nothing to do, and nothing to say
            cand.update(state="in", quiet=True)
        return
    for g in ap.get("guests") or []:
        check_guest(c, client, slug, g)
    gone = [p for p in mine if out(p)]
    if gone and (str(old["number"]) != cand["number"] or old["release"] != cand["release"]):
        raise Verdict(
            f"{len(gone)} of its posts {'is' if len(gone) == 1 else 'are'} out as episode {old['number']} for "
            f"{old['release']}, and the staged record now says episode {cand['number']} for {cand['release']}. "
            "The number and the date are frozen once anything is out: stage it again with the old ones, or take "
            "those posts down by hand first"
        )
    if not (row and row["allowed_for"] and row["allowed_for"] == cand["staged_at"]):
        if ap.get("waived"):
            raise Wait(f"it was approved with the guests' approval waived ({ap['waived']}), so a host brings it in")
        if released(cand["release"]):
            raise Wait(f"its release time ({cand['release']}) has passed, so a host brings it in")
        if not on(c):
            raise Wait("automatic arrival is off")
    cand["state"] = "ok"


def check_guest(c, client, slug, g):
    """The approval the episode was approved with is the file in the archive,
    and that file is the approval this engine took from the guest."""
    gid, name = str(g.get("id") or ""), g.get("name") or "a guest"
    if not GUEST.fullmatch(gid):
        raise Verdict(f"the approved record names a guest this engine cannot know ({gid!r})")
    key = f"guests/{gid}/approval.json"
    data = client.get_bytes(key, timeout=20)
    if data is None:
        raise Verdict(f"{name}'s approval ({key}) is not in the archive")
    if sha(data) != g.get("sha256"):
        raise Verdict(f"{name}'s approval in the archive is not the one the episode was approved with")
    a = json.loads(data)
    rev, sig = a.get("review") or {}, a.get("signature") or {}
    mine = c.execute(
        "SELECT * FROM approval WHERE guest=? AND slug=? AND round=?", (gid, slug, rev.get("round"))
    ).fetchone()
    if not mine:
        raise Verdict(f"this engine holds no approval by {name} of round {rev.get('round')} of {slug}")
    if (
        rev.get("slug") != slug
        or mine["at"] != a.get("at")
        or mine["sha256"] != sig.get("sha256")
        or mine["name"] != sig.get("name")
    ):
        raise Verdict(f"{name}'s approval in the archive does not match the one this engine recorded")
    later = c.execute("SELECT round FROM review WHERE guest=? AND slug=?", (gid, slug)).fetchone()
    if later and later["round"] > mine["round"]:
        raise Verdict(f"{name} approved round {mine['round']}, and the review is at round {later['round']} now")


def settle(c, ok, live="ok", sure=()):
    """Numbers. An episode may take a number only when no other episode ends
    this pass holding it: the other is leaving it in the same pass (a swap),
    or never had it. Whoever would collide is refused; the rest stand. The
    ones that wait (`live` is 'waiting') are held to the same rule after the
    ones coming in (`sure`), so a wait is never for something that would be
    refused the moment the switch, or a host, let it through."""
    held = {e["slug"]: str(e["number"]) for e in c.execute("SELECT slug, number FROM episode")}
    held.update({x["slug"]: x["number"] for x in sure})
    moved = True
    while moved:
        moved = False
        final = dict(held)
        final.update({x["slug"]: x["number"] for x in ok if x["state"] == live})
        for x in ok:
            if x["state"] != live:
                continue
            clash = sorted(s for s, n in final.items() if s != x["slug"] and n == x["number"])
            if clash:
                x.update(state="refused", why=f"episode number {x['number']} is {', '.join(clash)}'s")
                moved = True


def bring(c, ok, who):
    """Every one of these into the ledger, followed and planned, in one
    transaction; a Trouble names the one that stopped it."""
    before = {}
    ok = [x for x in ok if not x.get("resume")]
    for x in ok:
        old = ledger.episode(c, x["slug"])
        before[x["slug"]] = {"episode": old, "r": plan.release_of(old) if old else None, "posts": posts(c, x["slug"])}
    c.execute("BEGIN")
    x = None
    try:
        for x in ok:
            old = before[x["slug"]]["episode"]
            if old and str(old["number"]) != x["number"]:  # its link ids go first, so a swap never collides
                c.execute("UPDATE post SET link_id=NULL WHERE episode=?", (x["slug"],))
        for x in ok:
            slug, was = x["slug"], before[x["slug"]]
            d = archive.out_dir(slug)
            d.mkdir(parents=True, exist_ok=True)
            for name, data in x["raw"].items():  # as the archive holds them, where plan and the adapters look
                (d / name).write_bytes(data)
            x["files"] = ingest.store(c, slug, d, x["r"], x["s"], who)
            x["changes"] = follow(c, slug, was, who) if was["episode"] else {}
            x["held"] = bool(was["episode"] and was["episode"]["confirmed_at"] and not any(map(out, was["posts"])))
            if x["held"]:  # what a host confirmed is not what is staged now, and none of it has gone: ask again
                hold(c, slug, who, "staged again with none of its posts out")
            late = bool(was["posts"]) and released(x["release"])
            x["made"] = [] if late else plan.draft(c, slug, who)
            x.update(late=late, again=bool(was["posts"]))
            ledger.event(
                c,
                "arrive",
                who,
                episode=slug,
                detail={"staged_at": x["staged_at"], "posts": len(x["made"]), **x["changes"]},
            )
        c.execute("COMMIT")
    except Exception as e:
        c.execute("ROLLBACK")
        raise Trouble(x, f"{type(e).__name__}: {e}") from e
    except BaseException:
        c.execute("ROLLBACK")
        raise


def follow(c, slug, was, who):
    """The posts that have not gone out, after the record under them changed:
    the link of the number it has now, the plan's day if that moved, and the
    words again unless a person wrote them. Returns what changed."""
    old, old_r = was["episode"], was["r"]
    row = ledger.episode(c, slug)
    r = plan.release_of(row)
    days = {e["asset"]: e["date"] for e in r["plan"]}
    old_days = {e["asset"]: e["date"] for e in old_r["plan"]}
    n = collections.Counter()
    for p in was["posts"]:
        if out(p):
            continue
        a, asset = plan.ACCOUNTS.get(p["account"]), p["asset"]
        lid = plan.link_id(row["number"], p["account"], asset, r) if a and asset in days else None
        title, text, media = plan.compose(r, asset, p["account"], lid) if lid else (None, None, None)
        if text is None:  # the plan no longer has this asset, or this account, or nothing to say on it
            if p["status"] in ("planned", "due", "hand"):
                ledger.update_post(c, p["id"], who, status="skipped")
                ledger.event(
                    c, "follow:dropped", who, post=p["id"], episode=slug, detail="the staged plan no longer makes it"
                )
                n["dropped"] += 1
            continue
        fields = {"link_id": lid, "link_url": plan.link_url(r, asset), "media": media}
        hhmm = a.get("at", "09:00")
        new_at = ledger.at(days[asset], hhmm)
        if asset in old_days and ledger.at(old_days[asset], hhmm) != new_at:
            fields["scheduled_at"] = new_at
        edited = c.execute("SELECT 1 FROM event WHERE post=? AND what='edit:text' LIMIT 1", (p["id"],)).fetchone()
        if not edited:
            fields.update(title=title, text=text)
        elif p["link_id"] and p["link_id"] != lid:
            fields["text"] = (p["text"] or "").replace(plan.short(p["link_id"]), plan.short(lid))
        fields = {k: v for k, v in fields.items() if p[k] != v}
        if not fields:
            continue
        ledger.update_post(c, p["id"], who, **fields)
        detail = {k: [p[k], v] for k, v in fields.items() if k not in ("text", "title")}
        if edited:
            n["kept"] += 1
            detail["words"] = "kept as a person wrote them"
        elif "text" in fields or "title" in fields:
            n["rewritten"] += 1
            detail["words"] = "written again from the record"
        n["retimed"] += "scheduled_at" in fields
        ledger.event(c, "follow:kept" if edited else "follow", who, post=p["id"], episode=slug, detail=detail)
    changes = {k: v for k, v in n.items() if v}
    if str(old["number"]) != str(row["number"]):
        changes["number"] = [old["number"], row["number"]]
    if old["release"] != row["release"]:
        changes["release"] = [old["release"], row["release"]]
    return changes


def look(c, client=None, who="worker", put=None, env=None, dry_run=False, log=print):
    """One pass over the archive. Returns what it found: when, how many
    episodes are staged, and a result for each one it had to think about."""
    env = os.environ if env is None else env
    put = put or plan.kv_put
    found = {"at": ledger.now(), "on": on(c), "staged": 0, "results": []}
    try:
        client = client or archive.Client(env)
    except archive.ArchiveError as e:
        found["skipped"] = str(e)
        return remember(found, dry_run)
    try:
        cands, staged = scan(c, client)
    except (archive.ArchiveError, OSError) as e:
        found["error"] = f"the archive could not be listed: {e}"
        log(f"arrive: {found['error']}")
        return remember(found, dry_run)
    found["staged"] = len(staged)
    for x in cands:
        try:
            judge(c, client, x)
        except Verdict as v:
            x.update(state=v.state, why=str(v))
        except (archive.ArchiveError, OSError, ValueError, KeyError, TypeError) as e:
            x.update(state="failed", why=f"{type(e).__name__}: {e}")
    ok = [x for x in cands if x["state"] == "ok"]
    settle(c, ok)
    ok = [x for x in ok if x["state"] == "ok"]
    settle(c, [x for x in cands if x["state"] == "waiting"], "waiting", ok)
    if not dry_run:
        while ok:
            try:
                bring(c, ok, who)
                break
            except Trouble as t:
                t.cand.update(state="failed", why=str(t))
                ok = [x for x in ok if x is not t.cand]
                settle(c, ok)
                ok = [x for x in ok if x["state"] == "ok"]
        for x in ok:
            x["confirmed"] = bool(ledger.episode(c, x["slug"])["confirmed_at"])
            try:
                x["planned"] = c.execute("SELECT COUNT(*) FROM post WHERE episode=?", (x["slug"],)).fetchone()[0]
                x["links"] = plan.links(c, x["slug"], put, who)
                x["state"] = "in"
            except (RuntimeError, OSError) as e:
                x.update(state="unlinked", why=f"it is in the ledger and planned, and its short links did not go: {e}")
        stale = [
            r["slug"] for r in c.execute("SELECT slug FROM arrival WHERE state != 'in'") if r["slug"] not in staged
        ]
        for slug in stale:  # its staged record left the archive: nothing to wait for
            c.execute("DELETE FROM arrival WHERE slug=?", (slug,))
        for x in cands:
            save(c, x, who)
            tell(c, x, env, log)
    for x in cands:
        state = "would be brought in" if x["state"] == "ok" else x["state"]
        log(f"arrive: {x['slug']}: {state}" + (f": {x['why']}" if x.get("why") else ""))
        found["results"].append(
            {
                k: x.get(k)
                for k in ("slug", "state", "why", "number", "title", "release", "staged_at", "quiet", "confirmed")
            }
            | {"posts": len(x.get("made") or []), "links": x.get("links")}
        )
    return remember(found, dry_run)


def remember(found, dry_run=False):
    if not dry_run:
        LAST.clear()
        LAST.update(found)
    return found


def attempt(c, wait=False, **kw):
    """A pass, unless one is running: None then (a screen says so)."""
    if not LOCK.acquire(blocking=wait):
        return None
    try:
        return look(c, **kw)
    finally:
        LOCK.release()


def due(asked=False):
    if asked or not LAST.get("at"):
        return True
    return dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(LAST["at"]) >= every()


def save(c, x, who):
    """The pass's answer on the episode's row, with when the answer began."""
    row, t = x["row"], ledger.now()
    same = row and row["state"] == x["state"] and (x["state"] in FAILING or (row["why"] or "") == (x.get("why") or ""))
    since = row["since"] if same else t
    c.execute(
        """INSERT INTO arrival (slug, etag, staged_at, number, title, release, state, why, seen_at, checked_at,
                   since, done_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(slug) DO UPDATE SET etag=excluded.etag, state=excluded.state, why=excluded.why,
                   staged_at=COALESCE(excluded.staged_at, staged_at), number=COALESCE(excluded.number, number),
                   title=COALESCE(excluded.title, title), release=COALESCE(excluded.release, release),
                   checked_at=excluded.checked_at, since=excluded.since, done_at=excluded.done_at""",
        (
            x["slug"],
            x["etag"] if x["state"] in ("in", "refused", "waiting") else None,
            x.get("staged_at"),
            x.get("number"),
            x.get("title"),
            x.get("release"),
            x["state"],
            x.get("why"),
            t,
            t,
            since,
            t if x["state"] == "in" else None,
        ),
    )
    if not same and x["state"] != "in":
        ledger.event(c, f"arrive:{x['state']}", who, episode=x["slug"], detail=x.get("why"))
    x["since"] = since


# ─── telling someone ─────────────────────────────────────────────────────────
def name_of(x):
    return f"Episode {x['number']}, {x['title']}" if x.get("number") else x["slug"]


def words(x):
    """The subject and the body of the note for this answer."""
    slug, page = x["slug"], f"https://{ENGINE}/episodes"
    if x["state"] == "in":
        ch = x.get("changes") or {}
        lines = [f"The engine found {slug} staged in the archive and brought it in by itself.", ""]
        lines.append(f"  Release   {when(ledger.at(x['release'], ledger.CONFIG['release_time']))}")
        lines.append(f"  Files     {x.get('files')}")
        new = f", {len(x.get('made') or [])} of them new" if x.get("again") else ""
        lines.append(f"  Posts     {x.get('planned')} planned{new}")
        lines.append(f"  Links     {x.get('links')} short links pushed")
        if "number" in ch:
            lines.append(f"  Number    {ch['number'][0]} became {ch['number'][1]}")
        if "release" in ch:
            lines.append(f"  Date      {ch['release'][0]} became {ch['release'][1]}")
        for key, text in (
            ("rewritten", "post(s) written again from the new record"),
            ("kept", "edited post(s) kept their words and took the new link or time: read them"),
            ("retimed", "post(s) moved to the plan's new day"),
            ("dropped", "post(s) skipped, because the staged plan no longer makes them"),
        ):
            if ch.get(key):
                lines.append(f"  {ch[key]} {text}")
        if x.get("late"):
            lines += ["", "It is already released, so no new posts were planned; `engine.py plan` adds them."]
        lines += ["", "The site's pull request is still yours to merge on the morning.", ""]
        if x.get("held"):
            ask = ": confirm its plan again"
            lines.append(
                "Its plan had been confirmed. It was staged again with none of its posts out, so that word is "
                "gone: nothing of it is sent until a host confirms the plan again."
            )
        elif not x.get("confirmed"):
            ask = ": confirm its plan"
            lines.append(
                "Nothing of it is sent anywhere until a host confirms its plan: no upload, no post, no card. "
                "The page has the plan and the button."
            )
        else:
            ask = ""
            lines.append("Its plan stays confirmed, so the publisher goes on with what has not gone out.")
        lines.append(f"{page}/{slug}")
        return f"{name_of(x)} {'was staged again' if x.get('again') else 'is in'}{ask}", "\n".join(lines)
    if x["state"] == "waiting":
        return f"{name_of(x)} is staged and waiting for you", (
            f"{slug} is staged in the archive and has not been brought in: {x['why']}.\n\n"
            f"The Episodes screen has the button:\n{page}"
        )
    if x["state"] == "refused":
        return f"{name_of(x)} was not brought in", (
            f"{slug} is staged in the archive and the engine refused it: {x['why']}.\n\n"
            f"Nothing here has changed. It is looked at again when it is staged again.\n{page}"
        )
    again = f"The engine tries again every {every().seconds // 60} minutes; this is the only note about it.\n{page}"
    if x["state"] == "unlinked":
        return f"{name_of(x)} is in without its short links", (
            f"{slug} was brought in and its posts are planned, but the short links were not pushed: {x['why']}.\n\n"
            f"A post that goes out before they are pushed carries a link that does not resolve. "
            + ("" if x.get("confirmed") else f"None goes out until a host confirms its plan ({page}/{slug}). ")
            + again
        )
    return f"{name_of(x)} could not be brought in yet", (
        f"{slug} is staged in the archive and bringing it in failed: {x['why']}.\n\n{again}"
    )


def tell(c, x, env, log=print):
    """One note to the hosts per answer per staging, and a comment on each
    guest's issue when the episode has guests. A hosts-only episode has no
    issue the engine knows, so the mail is all it gets."""
    if x.get("quiet") or x["state"] in ("ok", None):
        return
    if x["state"] in FAILING and dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(x["since"]) < QUIET:
        return
    key = f"{x['state']}|{x.get('staged_at')}|{'' if x['state'] in ('in', *FAILING) else x.get('why')}"
    row = arrival(c, x["slug"])
    if not row or row["told"] == key:
        return
    c.execute("UPDATE arrival SET told=? WHERE slug=?", (key, x["slug"]))
    subject, text = words(x)
    to = mail.settings().get("copy_to")
    if to and mail.ready(env):
        try:
            mail.record(c, "engine", to, subject, mail.send(to, subject, text, env))
        except mail.MailError as e:
            mail.record(c, "engine", to, subject, error=e)
            log(f"arrive: the note about {x['slug']} was not sent: {e}")
        time.sleep(mail.PACE)  # a pass with several answers keeps under the mail server's throttle
    if x["state"] not in ("in", "refused"):
        return
    approved = ((x.get("r") or {}).get("episode") or {}).get("approved") or {}
    for g in approved.get("guests") or []:
        known = c.execute("SELECT id, issue FROM guest WHERE id=?", (str(g.get("id")),)).fetchone()
        if known and known["issue"]:
            linear.enqueue(c, "arrived", {"issue": known["issue"], "text": f"{subject}.\n\n{text}"}, guest=known["id"])


# ─── the screens ─────────────────────────────────────────────────────────────
def stage(c, e):
    """Where an episode in the ledger has got to, in a few words."""
    n, newest = c.execute(
        "SELECT COUNT(*), MAX(created_at) FROM post WHERE episode=? AND link_id IS NOT NULL", (e["slug"],)
    ).fetchone()
    if not n:
        return '<span class="muted">ingested, no posts</span>'
    pushed = c.execute(
        "SELECT MAX(at) FROM event WHERE what='links' AND (episode=? OR episode IS NULL)", (e["slug"],)
    ).fetchone()[0]
    ask = "" if e["confirmed_at"] else link(f"/episodes/{e['slug']}", "to confirm")
    if not pushed or pushed < newest:
        return '<span class="error">planned, links not pushed</span>' + (", " + ask if ask else "")
    return ask or "planned, links pushed"


def last_look():
    if not LAST.get("at"):
        return f"not yet in this process; the worker looks every {every().seconds // 60} minutes"
    if LAST.get("skipped"):
        return f"{esc(when(LAST['at']))}: not looked, {esc(LAST['skipped'])}"
    if LAST.get("error"):
        return f'{esc(when(LAST["at"]))} <span class="error">{esc(LAST["error"])}</span>'
    return f"{esc(when(LAST['at']))}: {LAST['staged']} staged in the archive"


def panel(c, csrf):
    """The arrivals on the Episodes screen: the switch, the last look, and
    every staged episode that is not simply in."""
    is_on = on(c)
    rows = c.execute(
        "SELECT * FROM arrival WHERE state != 'in' ORDER BY state = 'aside', release, slug"
    ).fetchall()  # fmt: skip
    would = [r for r in rows if r["state"] == "waiting" and r["why"] == "automatic arrival is off"]
    minutes = every().seconds // 60
    if is_on:
        head = (
            f"<p>Automatic arrival is <b>on</b>: every {minutes} minutes the engine looks in the archive and brings "
            "in what has been approved and staged, plans its posts and pushes its short links.</p>"
            + button("/episodes/auto/off", "Pause it", csrf)
        )
    else:
        names = ", ".join(esc(f"{r['slug']} ({r['title']})") for r in would)
        head = (
            f"<p>Automatic arrival is <b>off</b>: the engine looks every {minutes} minutes and only says what it "
            "would do. "
            + (f"Turned on, it would bring in: <b>{names}</b>." if would else "Nothing is waiting on the switch.")
            + "</p>"
            + button("/episodes/auto/on", "Turn it on", csrf, confirm=True)
        )
    head += f"<p>Last looked {last_look()}.</p>" + button("/episodes/look", "Look now", csrf)
    if not rows:
        return section("Arrivals", "Nothing waiting", head)
    trs = []
    for r in rows:
        label = {
            "refused": '<span class="error">refused</span>',
            "failed": '<span class="error">failed, trying again</span>',
            "unlinked": '<span class="error">in, links not pushed, trying again</span>',
            "aside": '<span class="muted">set aside</span>',
        }.get(r["state"], "waiting")
        acts = ""
        if r["state"] in ("waiting", "aside"):
            acts += button(f"/episodes/{r['slug']}/bring", "Bring it in", csrf, confirm=True)
        if r["state"] == "aside":
            acts += button(f"/episodes/{r['slug']}/back", "Put it back", csrf)
        elif r["state"] not in FAILING:  # a failure is tried again every pass; there is nothing to set aside
            acts += button(f"/episodes/{r['slug']}/aside", "Set aside", csrf)
        known = ledger.episode(c, r["slug"])
        trs.append(
            [
                link(f"/episodes/{r['slug']}", r["slug"]) if known else esc(r["slug"]),
                esc(r["number"] or ""),
                esc(r["title"] or ""),
                esc(r["release"] or ""),
                esc(when(r["staged_at"]) or ""),
                label,
                esc(r["why"] or ""),
                acts,
            ]
        )
    return section(
        "Arrivals",
        f"{len(rows)} staged and not brought in",
        head + table(["Episode", "No.", "Title", "Release", "Staged", "State", "Why", ""], trs),
    )


def plan_panel(c, e, csrf):
    """The plan on an episode's page. Until a host confirms it: what
    confirming sends, counted as the publisher will count it
    (publish.fate_of), and the button. After: who confirmed, and the button
    that takes it back."""
    slug = e["slug"]
    if e["confirmed_at"]:
        return section(
            "Plan",
            "Confirmed",
            f"<p>Confirmed by {esc(e['confirmed_by'])}, {esc(when(e['confirmed_at']))}. The publisher takes this "
            "episode's posts as their times come.</p>"
            "<p>Holding the plan stops anything more being sent until it is confirmed again. What a platform "
            "already has stays there: a video YouTube holds still goes public at its time, and a post that is "
            "out stays out.</p>" + button(f"/episodes/{slug}/hold", "Hold the plan", csrf),
        )
    ps = posts(c, slug)
    if not ps:
        return section(
            "Plan",
            "Nothing to confirm yet",
            "<p>No posts are planned. Once they are, none is sent until a host confirms the plan here.</p>",
        )
    t = dt.datetime.now(dt.timezone.utc)
    hours = f"{publish.late_after().total_seconds() / 3600:g}"
    left = [p for p in ps if p["status"] in ("planned", "due") and p["account"] in plan.ACCOUNTS]
    up, public, later, at_once, late, cards = [], [], [], [], [], []
    for p in left:
        mod, at = publish.adapter(p["account"]), publish.parse(p["scheduled_at"])
        fate = publish.fate_of(p, t)
        if getattr(mod, "SCHEDULES", False):
            up.append(p)
            if not at or at - t <= getattr(mod, "GRACE", dt.timedelta(0)):
                public.append(p)
        elif not getattr(mod, "SENDS", True):
            cards.append(p)
        else:
            {None: later, "now": at_once, "late": late}[fate].append(p)

    def s(some):
        return "" if len(some) == 1 else "s"

    does, warns = [], []
    if up:
        names = " and ".join(sorted({"YouTube" if p["platform"] == "youtube" else p["platform"] for p in up}))
        private = len(up) - len(public)
        does.append(
            f"{names} takes {len(up)} upload{s(up)} now, "
            + (f"{private} of them private until their times." if public else "private until their times.")
        )
    if later:
        day = when(min(later, key=lambda p: publish.parse(p["scheduled_at"]))["scheduled_at"], "%A %-d %B")
        does.append(
            f"{len(later)} post{s(later)} go{'es' if len(later) == 1 else ''} out by "
            f"{'itself' if len(later) == 1 else 'themselves'} from {day}."
        )
    if cards:
        does.append(f"{len(cards)} card{s(cards)} for a person to post come up on By hand, each on its day.")
    if not left:
        does.append("Nothing is left for the publisher to send.")
    if e["release_at"] and publish.parse(e["release_at"]) <= t:
        warns.append("The release time has passed.")
    if public:
        grace = int(getattr(publish.adapter(public[0]["account"]), "GRACE", dt.timedelta(0)).total_seconds() // 60)
        warns.append(
            f"Public at once, not private: {len(public)} upload{s(public)} at or past the time set, or within "
            f"{grace} minutes of it. The late rule does not hold an upload back."
        )
    if at_once:
        warns.append(
            f"Sent within a minute of confirming: {len(at_once)} post{s(at_once)} past the time set by less than "
            f"{hours} hours."
        )
    if late:
        warns.append(
            f"Not sent: {len(late)} post{s(late)} more than {hours} hours past the time set. Each waits under "
            "Late on By hand, to be published now, moved or skipped."
        )
    gone = [p for p in ps if out(p)]
    if gone:
        warns.append(f"Already out or with a platform, and staying there: {len(gone)} post{s(gone)}.")
    per = collections.Counter(p["account"] for p in left)
    days = sorted(publish.parse(p["scheduled_at"]) for p in left if p["scheduled_at"])
    facts = [
        ("Episode", esc(f"{e['number']}, {e['title']}")),
        ("Release", esc(when(e["release_at"]))),
        ("Page", link(e["url"], external=True)),
        ("Posts to send", esc(", ".join(f"{a} {n}" for a, n in per.items()) or "none")),
    ]
    if days:
        first, last = (when(d.isoformat(), "%A %-d %B %Y") for d in (days[0], days[-1]))
        facts.append(("Days", esc(first if first == last else f"{first} to {last}")))
    form = (
        f'<form method="post" action="/episodes/{esc(slug)}/confirm" class="inline">'
        f'<input type="hidden" name="_t" value="{esc(csrf)}">'
        f'<input type="hidden" name="staged" value="{esc(e["staged_at"] or "")}">'
        '<button type="submit" class="signal">Confirm the plan</button></form>'
    )
    return section(
        "Plan",
        "Nothing is sent until a host confirms it",
        '<dl class="facts">'
        + "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in facts)
        + "</dl><p>"
        + " ".join(esc(d) for d in does)
        + "</p>"
        + "".join(f'<p class="error">{esc(w)}</p>' for w in warns)
        + "<p>Each post's text, time and skip can still be changed below before confirming.</p>"
        + form,
    )


def confirm_health(c):
    """The Health row for plans waiting on a host: red when one's release is
    under a day away or has passed."""
    rows = unconfirmed(c)
    if not rows:
        return "none waiting"
    names = ", ".join(link(f"/episodes/{e['slug']}", e["slug"]) for e in rows)
    close = [e for e in rows if soon(e)]
    if not close:
        return f"{len(rows)} waiting for a host: {names}"
    urgent = ", ".join(link(f"/episodes/{e['slug']}", e["slug"]) for e in close)
    return (
        f'<span class="error">{len(close)} not confirmed with the release under 24 hours away or passed: {urgent}'
        f"</span>; {len(rows)} waiting in all"
    )


def health(c):
    counts = dict(c.execute("SELECT state, COUNT(*) FROM arrival GROUP BY state").fetchall())
    bad = counts.get("refused", 0) + sum(counts.get(s, 0) for s in FAILING)
    text = f"{'on' if on(c) else 'off: it only says what it would do'}; last looked {last_look()}; " + (
        f"{counts.get('in', 0)} in, {counts.get('waiting', 0)} waiting"
    )
    if bad:
        text += f'; <span class="error">{bad} refused or failing: see {link("/episodes", "Episodes")}</span>'
    return text


def sentence(found, slug=None):
    """What a pass did, for the screen that asked for it."""
    if found is None:
        return "A look is running already; reload in a moment"
    if found.get("skipped"):
        return f"Not looked: {found['skipped']}"
    if found.get("error"):
        return found["error"]
    loud = [r for r in found["results"] if not r.get("quiet") and (slug is None or r["slug"] == slug)]
    if not loud:
        return f"Looked: {found['staged']} staged in the archive, nothing new"
    said = []
    for r in loud:
        if r["state"] == "in":
            ask = "" if r.get("confirmed") else ", and nothing of it is sent until its plan is confirmed"
            said.append(f"{r['slug']} is in ({r['posts']} posts planned, {r['links']} links pushed){ask}")
        else:
            verb = {"refused": " was refused", "waiting": " is waiting", "failed": " failed"}.get(r["state"], "")
            said.append(f"{r['slug']}{verb}: {r['why']}")
    return "; ".join(said)
