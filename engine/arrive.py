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
date are frozen. YouTube takes its uploads within a minute of the plan
(publish.split), so in practice that is at once.
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
from pages import button, esc, link, section, table, when

SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,60}")
GUEST = re.compile(r"[0-9a-f]{8}")
ENGINE = (ledger.CONFIG.get("engine") or {}).get("host", "engine.agentsee.work")
OUT = ("publishing", "scheduled", "published")  # a platform has it, or is being handed it
FAILING = ("failed", "unlinked")  # tried again every pass: nothing came in, or all but the short links did
QUIET = dt.timedelta(minutes=30)  # a failure younger than this is not worth a mail: the next pass may clear it
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
            {k: x.get(k) for k in ("slug", "state", "why", "number", "title", "release", "staged_at", "quiet")}
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
        lines += ["", "The site's pull request is still yours to merge on the morning.", "", f"{page}/{slug}"]
        return (f"{name_of(x)} was staged again" if x.get("again") else f"{name_of(x)} is in"), "\n".join(lines)
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
            f"A post that goes out before they are pushed carries a link that does not resolve. {again}"
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
    if not pushed or pushed < newest:
        return '<span class="error">planned, links not pushed</span>'
    return "planned, links pushed"


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
            said.append(f"{r['slug']} is in ({r['posts']} posts planned, {r['links']} links pushed)")
        else:
            verb = {"refused": " was refused", "waiting": " is waiting", "failed": " failed"}.get(r["state"], "")
            said.append(f"{r['slug']}{verb}: {r['why']}")
    return "; ".join(said)
