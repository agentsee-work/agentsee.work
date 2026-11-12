"""
The review (AGE-111): the guest watches the episode and the clips before
anyone else, says what should change, and approves what may go out. Nothing
is published before that: the pipeline refuses (tools/office.py, stage.py).

What the pipeline writes for this module (`episode.py review`, stage.py
--review): the render as it stands under `episodes/<slug>/review/`, and the
round's record at `guests/<id>/review.json`, which `fetch` reads into the
ledger's review table whenever the guest looks. The files reach the guest
through signed links the archive client mints (archive.presign), good for
an hour, so the bucket stays private and no byte passes through here.

What this module writes for the pipeline, under `guests/<id>/`: `notes.json`,
every note of every round with the moment in the episode it is about where
there is one; `approval.json`, the approval with its signature's record; and
`approval.html`, the signed copy with the image in it, which goes to the guest
by mail as well. The hosts hear of each note as it is made, and of the
approval, by mail and on the guest's issue (linear.py's jobs `noted` and
`approved`, the label `episode-approved`).

The second signature is made and kept the way the release's is (release.py):
a PNG from the pad, the name as typed, the address the code verified, the
time, the IP and the device, and the hash of what was signed, here the
review record's, so an approval is of one round and of no other. A newer
round asks to be approved again.
"""

import base64
import datetime as dt
import hashlib
import json
import os
import re

import archive
import idea
import ledger
import linear
import mail
import release
from pages import esc, go, link, section, when

LINK_TTL = 3600  # a signed link's life
REFRESH = 60  # seconds before the office looks in the archive again for a new round
NOTE_MAX = 2000
WANT = ("episode.mp4", "episode.mp3", "thumbnail.png", "episode-art.png")
CUT_WINDOW = 240  # seconds of transcript either side of a note's moment that the model reads

CUT_SYSTEM = """You help edit an episode of a podcast. You are given a guest's review note and the episode's transcript, with
speakers and times. Decide whether the note asks for words to be removed from the episode. If it does, find the
passage of the transcript the note means and answer with JSON only:
{"cut": true, "phrase": "<the passage, copied from the transcript word for word with its punctuation, as one
contiguous run; whole sentences where you can; long enough to occur only once>", "why": "<a few words on what it is>"}
If the note does not ask for a removal, or the passage cannot be found, answer {"cut": false}.
Never invent, shorten or paraphrase the words: the phrase must appear in the transcript exactly as you give it.
No prose around the JSON, no code fences."""

NOTES_MAIL = """Hello,

{name} left a note on round {round} of "{title}" through {host}:

{notes}

It is on the issue as well, and in the archive at guests/{gid}/notes.json; `episode.py notes {slug}` brings the
confirmed cuts in."""

APPROVAL_MAIL = """Hello {first},

You approved "{title}", round {round}, on {when}, through {host}. {clips}

The copy attached is your record of it: what you approved, your signature, and what was recorded with it. The same
copy is in AgentSee's archive. James and Abrar now have what they need to put the episode out, and the published
episode carries the number they give it."""


class ReviewError(Exception):
    pass


def office():
    return os.environ.get("ENGINE_GUEST_HOST") or (ledger.CONFIG.get("guest") or {}).get("host", "see.agentsee.work")


def archive_origin():
    """Where the signed links point, for the page's content security policy; none without the credential."""
    try:
        return archive.Client().endpoint
    except archive.ArchiveError:
        return None


def mmss(t):
    t = int(round(t or 0))
    h, m, s = t // 3600, t // 60 % 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_t(s):
    """A moment typed as m:ss, h:mm:ss or seconds; None for nothing."""
    s = (s or "").strip()
    if not s:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.(\d+))?", s)
    if not m:
        raise ReviewError("A moment is m:ss, like 12:34, or seconds")
    h, mi, se, frac = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(se) + (float("0." + frac) if frac else 0.0)


def _age(iso):
    t = dt.datetime.fromisoformat(iso)
    if t.tzinfo is None:
        t = t.replace(tzinfo=ledger.ZONE)
    return (dt.datetime.now(ledger.ZONE) - t).total_seconds()


# ─── the round, from the archive ─────────────────────────────────────────────
def current(c, gid):
    return c.execute("SELECT * FROM review WHERE guest=?", (gid,)).fetchone()


def fetch(c, g, client=None, force=True):
    """`guests/<id>/review.json` and the round's release.json from the archive
    into the ledger: the row, or None while there is nothing. A look inside
    REFRESH seconds of the last is skipped unless forced; an archive failure
    is an event and the ledger's copy stands."""
    gid = g["id"]
    row = current(c, gid)
    if row and not force and _age(row["fetched_at"]) < REFRESH:
        return row
    try:
        client = client or archive.Client()
        doc = client.get_json(f"guests/{gid}/review.json")
        rel = client.get_json(f"episodes/{doc['slug']}/review/release.json") if doc and doc.get("slug") else None
    except archive.ArchiveError as e:
        ledger.event(c, "review:fetch:failed", "engine", detail=str(e)[:300], guest=gid)
        return row
    if not doc or not doc.get("slug"):
        return row
    now = ledger.now()
    slug, rnd, rec = str(doc["slug"]), int(doc.get("round") or 0), json.dumps(doc, sort_keys=True)
    release_json = json.dumps(rel) if rel else (row["release"] if row else None)
    if row and (row["slug"], row["round"], row["record"]) == (slug, rnd, rec):
        c.execute("UPDATE review SET fetched_at=?, release=? WHERE guest=?", (now, release_json, gid))
    else:
        c.execute(
            "INSERT INTO review (guest, slug, round, record, release, seen_at, fetched_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(guest) DO UPDATE SET slug=excluded.slug, round=excluded.round, record=excluded.record, "
            "release=excluded.release, seen_at=excluded.seen_at, fetched_at=excluded.fetched_at",
            (gid, slug, rnd, rec, release_json, now, now),
        )
        ledger.event(
            c,
            "review:round",
            "engine",
            detail={"slug": slug, "round": rnd, "changed": doc.get("changed") or []},
            guest=gid,
        )
    return current(c, gid)


def record(row):
    return json.loads(row["record"])


def release_of(row):
    return json.loads(row["release"]) if row["release"] else {}


def title_of(row):
    return record(row).get("title") or (release_of(row).get("episode") or {}).get("title") or row["slug"]


def clips_of(row):
    """The clips as the review's release.json names them (index, slug, title,
    file), which is how a veto names one for the pipeline; the poster beside
    each where the round has it."""
    files = record(row).get("files") or {}
    out = []
    for i, clip in enumerate(release_of(row).get("clips") or [], 1):
        slug = clip.get("slug") or f"clip-{i:02d}"
        f = clip.get("file") or f"clips/{i:02d}-{slug}.mp4"
        poster = f[:-4] + ".png" if f.endswith(".mp4") else None
        out.append(
            {
                "index": clip.get("index") or i,
                "slug": slug,
                "title": clip.get("title") or f"Clip {i}",
                "file": f,
                "poster": poster if poster in files else None,
            }
        )
    return out


def links(row, names, client=None):
    """Signed links for the named files of the round, good for LINK_TTL; none without the credential."""
    try:
        client = client or archive.Client()
    except archive.ArchiveError:
        return {}
    files = record(row).get("files") or {}
    return {n: client.url(f"episodes/{row['slug']}/review/{n}", LINK_TTL) for n in names if n in files}


# ─── notes ───────────────────────────────────────────────────────────────────
def notes(c, gid, slug=None, rnd=None):
    if slug is None:
        return c.execute("SELECT * FROM review_note WHERE guest=? ORDER BY id", (gid,)).fetchall()
    return c.execute(
        "SELECT * FROM review_note WHERE guest=? AND slug=? AND round=? ORDER BY id", (gid, slug, rnd)
    ).fetchall()


def add_note(c, g, text, at_text, env=None, client=None, by=None):
    """A note, sent as it is made: kept, read for a cut, mirrored to the
    archive, the hosts told by mail when it is the guest's. `by` is a host's
    name when a host leaves it from the engine; the guest's notes carry none.
    Returns what the issue's job needs (guests.tell)."""
    row = current(c, g["id"])
    if not row:
        raise ReviewError("Nothing to note yet")
    text = "\n".join(" ".join(ln.split()) for ln in (text or "").strip().splitlines()).strip()[:NOTE_MAX]
    if len(text) < 2:
        raise ReviewError("Write the note first")
    t = parse_t(at_text)
    at = ledger.now()
    c.execute(
        "INSERT INTO review_note (guest, slug, round, t, text, at, sent_at, author) VALUES (?,?,?,?,?,?,?,?)",
        (g["id"], row["slug"], row["round"], t, text, at, at, by),
    )
    nid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    ledger.event(
        c,
        "host:noted" if by else "guest:noted",
        by or g["address"],
        detail={"slug": row["slug"], "round": row["round"], "t": t, "text": text[:200]},
        guest=g["id"],
    )
    cut = find_cut(c, g, row, text, t, os.environ if env is None else env, client, who="host" if by else "guest")
    if cut and cut.get("phrase"):
        c.execute(
            "UPDATE review_note SET remove=? WHERE id=?",
            (json.dumps({"phrase": cut["phrase"], "why": cut.get("why")}), nid),
        )
    mirror_notes(c, g, client)
    title = title_of(row)
    mailed = False
    if not by:
        line = f"- {'at ' + mmss(t) + ': ' if t is not None else ''}{text}"
        if cut and cut.get("phrase"):
            line += f'\n\nThey asked for a cut. The words found in the transcript, awaiting their confirmation: "{cut["phrase"]}"'
        elif cut:
            line += "\n\nThey asked for a cut, but the words could not be found in the transcript; read the note."
        body = NOTES_MAIL.format(
            name=g["name"], round=row["round"], title=title, host=office(), notes=line, gid=g["id"], slug=row["slug"]
        )
        mailed = _mail_hosts(c, g, f"{g['name']}'s note on round {row['round']} of {title}", body)
    return {
        "slug": row["slug"],
        "round": row["round"],
        "title": title,
        "count": 1,
        "notes": [{"t": t, "text": text}],
        "cut": (cut or {}).get("phrase"),
        "missed": bool(cut and not cut.get("phrase")),
        "by": by,
        "mailed": mailed,
    }


# ─── a cut, found in the transcript ──────────────────────────────────────────
def tok(w):
    """A word as tools/cut.py compares it: lower case, letters, digits and apostrophes only."""
    return re.sub(r"[^a-z0-9']", "", w.lower())


def transcript_of(row, client=None):
    """The round's transcript: (start, speaker, body) per segment, from the archive; none without it."""
    try:
        doc = (client or archive.Client()).get_json(f"episodes/{row['slug']}/review/transcript.json")
    except archive.ArchiveError:
        return []
    segs = (doc or {}).get("segments") or []
    return [(float(s.get("startTime") or 0), s.get("speaker") or "", s.get("body") or "") for s in segs]


def occurrences(segs, phrase):
    """How many times the phrase's words occur in the transcript, compared the way tools/cut.py compares them."""
    target = [tok(w) for w in phrase.split()]
    target = [w for w in target if w]
    toks = [tok(w) for _, _, body in segs for w in body.split()]
    toks = [w for w in toks if w]
    if not target or len(target) > len(toks):
        return 0
    return sum(1 for i in range(len(toks) - len(target) + 1) if toks[i : i + len(target)] == target)


def find_cut(c, g, row, text, t, env, client=None, who="guest"):
    """What the note asks to remove, as the words of the transcript, found by
    the model and checked here: None when the note is not a cut or the model
    is not set up; {"phrase", "why"} when the words occur in the transcript
    exactly once; {"phrase": None} when the model named words that do not."""
    if not idea.ready(env):
        return None
    segs = transcript_of(row, client)
    if not segs:
        return None
    if t is None:
        shown = segs
    else:
        shown = [s for s in segs if t - CUT_WINDOW <= s[0] <= t + CUT_WINDOW] or segs
    lines = "\n".join(f"[{mmss(s)}] {spk}: {body}" for s, spk, body in shown)
    moment = f", about the moment {mmss(t)}" if t is not None else ""
    user = f'The {who}\'s note{moment}:\n"{text}"\n\nThe transcript{", around that moment" if t is not None else ""}:\n{lines}'
    try:
        answer, usage, stop, model = idea.request(env, CUT_SYSTEM, [{"role": "user", "content": user}])
        m = re.search(r"\{.*\}", answer, re.S)
        got = json.loads(m.group(0)) if m else {}
    except (idea.IdeaError, ValueError) as e:
        ledger.event(c, "review:cut:failed", "engine", detail=str(e)[:300], guest=g["id"])
        return None
    if not got.get("cut"):
        return None
    phrase = " ".join(str(got.get("phrase") or "").split())
    if phrase and occurrences(segs, phrase) == 1:
        ledger.event(c, "review:cut:found", "engine", detail={"phrase": phrase[:200], "model": model}, guest=g["id"])
        return {"phrase": phrase, "why": str(got.get("why") or "")[:120]}
    ledger.event(c, "review:cut:missed", "engine", detail={"phrase": phrase[:200], "model": model}, guest=g["id"])
    return {"phrase": None}


def answer_cut(c, g, nid, yes, client=None, by=None):
    """The word on the words found, by whoever left the note (the guest on the
    office, a host on the engine): confirmed, which the pipeline takes into
    removals.json, or not."""
    n = c.execute("SELECT * FROM review_note WHERE id=? AND guest=?", (nid, g["id"])).fetchone()
    if not n or not n["remove"]:
        raise ReviewError("No cut to answer")
    if bool(n["author"]) != bool(by):
        raise ReviewError("That note is not yours to answer")
    if n["confirmed"] is not None:
        raise ReviewError("You answered that one already")
    c.execute("UPDATE review_note SET confirmed=? WHERE id=?", (1 if yes else 0, nid))
    phrase = json.loads(n["remove"])["phrase"]
    ledger.event(
        c,
        f"{'host' if by else 'guest'}:cut:{'confirmed' if yes else 'declined'}",
        by or g["address"],
        detail={"slug": n["slug"], "round": n["round"], "phrase": phrase[:200]},
        guest=g["id"],
    )
    mirror_notes(c, g, client)
    row = current(c, g["id"])
    title = title_of(row) if row else n["slug"]
    what = "confirmed the cut" if yes else "decided against the cut"
    if not by:
        _mail_hosts(
            c,
            g,
            f"{g['name']} {what} on round {n['round']} of {title}",
            f'Hello,\n\n{g["name"]} {what} on round {n["round"]} of "{title}" through {office()}:\n\n"{phrase}"\n\n'
            + (
                f"guests/{g['id']}/notes.json carries it as confirmed; `episode.py notes {n['slug']}` takes it into "
                "removals.json."
                if yes
                else "The words stay; the note itself stands."
            ),
        )
    return {
        "slug": n["slug"],
        "round": n["round"],
        "title": title,
        "text": n["text"],
        "phrase": phrase,
        "confirmed": yes,
        "by": by,
    }


# ─── the hosts' approvals ────────────────────────────────────────────────────
def hosts():
    return list(ledger.CONFIG.get("hosts") or [])


def host_approvals(c, gid, row):
    return c.execute(
        "SELECT * FROM host_approval WHERE guest=? AND slug=? AND round=? ORDER BY id", (gid, row["slug"], row["round"])
    ).fetchall()


def approve_as_host(c, g, host, who, ip, ua, client=None):
    """One host's approval of the round in hand, from the engine: recorded
    like the release's host signatures (who, when, from where, the hash of
    the round's record), mirrored for the pipeline. Returns what the issue's
    job needs."""
    row = current(c, g["id"])
    if not row:
        raise ReviewError("Nothing to approve yet")
    if host not in hosts():
        raise ReviewError(f"This engine's hosts are {' and '.join(hosts())}; you are {who}")
    if any(r["host"] == host for r in host_approvals(c, g["id"], row)):
        raise ReviewError(f"You approved round {row['round']} already")
    at, sha = ledger.now(), hashlib.sha256(row["record"].encode("utf-8")).hexdigest()
    c.execute(
        "INSERT INTO host_approval (guest, slug, round, host, who, ip, ua, at, sha256) VALUES (?,?,?,?,?,?,?,?,?)",
        (g["id"], row["slug"], row["round"], host, who, ip, (ua or "")[:300], at, sha),
    )
    ledger.event(
        c,
        "host:approved",
        who,
        detail={"host": host, "slug": row["slug"], "round": row["round"], "ip": ip},
        guest=g["id"],
    )
    mirror_hosts(c, g, row, client)
    done = [r["host"] for r in host_approvals(c, g["id"], row)]
    return {
        "host": host,
        "slug": row["slug"],
        "round": row["round"],
        "title": title_of(row),
        "approved": done,
        "waiting": [h for h in hosts() if h not in done],
        "guest_approved": bool(approval(c, g["id"], row)),
    }


def hosts_record(c, g, row):
    """The hosts' approvals of the round, the way `episodes/<slug>/review/hosts.json` holds them for the pipeline."""
    return {
        "slug": row["slug"],
        "round": row["round"],
        "sha256": hashlib.sha256(row["record"].encode("utf-8")).hexdigest(),
        "hosts": hosts(),
        "approvals": [
            {"host": r["host"], "who": r["who"], "at": r["at"], "ip": r["ip"], "ua": r["ua"], "sha256": r["sha256"]}
            for r in host_approvals(c, g["id"], row)
        ],
    }


def mirror_hosts(c, g, row, client=None):
    key = f"episodes/{row['slug']}/review/hosts.json"
    try:
        (client or archive.Client()).put_bytes(
            key, json.dumps(hosts_record(c, g, row), indent=1).encode("utf-8"), "application/json"
        )
    except archive.ArchiveError as e:
        ledger.event(c, "review:mirror:failed", "engine", detail={"key": key, "error": str(e)[:300]}, guest=g["id"])
        return None
    return key


def notes_record(c, gid):
    """Every round's notes, the way `guests/<id>/notes.json` holds them for the pipeline (tools/office.py)."""
    rounds = {}
    for n in notes(c, gid):
        r = rounds.setdefault(
            (n["slug"], n["round"]), {"n": n["round"], "slug": n["slug"], "at": n["at"], "notes": [], "sent": None}
        )
        entry = {"at": n["at"], "t": n["t"], "text": n["text"], "sent": n["sent_at"]}
        if n["author"]:
            entry["by"] = n["author"]
        if n["remove"]:
            entry["remove"] = {"phrase": json.loads(n["remove"])["phrase"]}
            entry["confirmed"] = bool(n["confirmed"])
        r["notes"].append(entry)
        if n["sent_at"] and (not r["sent"] or n["sent_at"] > r["sent"]):
            r["sent"] = n["sent_at"]
    return {"guest": gid, "rounds": list(rounds.values())}


def mirror_notes(c, g, client=None):
    key = f"guests/{g['id']}/notes.json"
    try:
        (client or archive.Client()).put_bytes(
            key, json.dumps(notes_record(c, g["id"]), indent=1).encode("utf-8"), "application/json"
        )
    except archive.ArchiveError as e:
        ledger.event(c, "review:mirror:failed", "engine", detail={"key": key, "error": str(e)[:300]}, guest=g["id"])
        return None
    return key


def _mail_hosts(c, g, subject, text):
    to = mail.settings().get("copy_to")
    if not to:
        return False
    try:
        msgid = mail.send(to, subject, text)
    except mail.MailError as e:
        mail.record(c, "see", to, subject, error=e, guest=g["id"])
        return False
    mail.record(c, "see", to, subject, msgid, guest=g["id"])
    return True


# ─── the approval ────────────────────────────────────────────────────────────
def approval(c, gid, row=None):
    """The approval of the round in hand, or the latest one."""
    if row:
        return c.execute(
            "SELECT * FROM approval WHERE guest=? AND slug=? AND round=?", (gid, row["slug"], row["round"])
        ).fetchone()
    return c.execute("SELECT * FROM approval WHERE guest=? ORDER BY id DESC LIMIT 1", (gid,)).fetchone()


def approve(c, g, form, ip, ua, client=None):
    """The guest's approval of the round in hand, with the clips they vetoed and
    their signature; then the copies and the mail. Returns the approval row,
    the archive keys the copies got, and whether the mail went."""
    row = current(c, g["id"])
    if not row:
        raise ReviewError("Nothing to approve yet")
    if str(form.get("round") or "") != str(row["round"]):
        raise ReviewError(f"Round {row['round']} is the one here now; look at it, then approve")
    if approval(c, g["id"], row):
        raise ReviewError(f"You approved round {row['round']} already")
    name = " ".join((form.get("name") or "").split())[:80]
    if len(name) < 2:
        raise ReviewError("Type your full name")
    if form.get("agree") != "yes":
        raise ReviewError("Tick the box to approve the episode")
    try:
        png = release.normalise_png(release.check_png(form.get("signature")))
    except release.ReleaseError as e:
        raise ReviewError(str(e)) from None
    vetoed = [cl["file"] for cl in clips_of(row) if form.get(f"use-{cl['index']}") != "yes"]
    at, sha = ledger.now(), hashlib.sha256(row["record"].encode("utf-8")).hexdigest()
    c.execute(
        "INSERT INTO approval (guest, slug, round, vetoed, name, address, png, ip, ua, at, sha256) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            g["id"],
            row["slug"],
            row["round"],
            json.dumps(vetoed),
            name,
            g["address"],
            png,
            ip,
            (ua or "")[:300],
            at,
            sha,
        ),
    )
    ledger.event(
        c,
        "guest:approved",
        g["address"],
        detail={"slug": row["slug"], "round": row["round"], "vetoed": vetoed, "name": name, "ip": ip},
        guest=g["id"],
    )
    a = approval(c, g["id"], row)
    c.execute("UPDATE approval SET html=? WHERE id=?", (html_copy(c, g, row, a), a["id"]))  # the copy, as signed
    a = approval(c, g["id"], row)
    keys = copies(c, g, row, a, client)
    sent = send_copy(c, g, row, a)
    return a, keys, sent


def approval_record(c, g, row, a):
    doc, vetoed = record(row), json.loads(a["vetoed"])
    clips = clips_of(row)
    return {
        "at": a["at"],
        "guest": {"id": g["id"], "name": g["name"], "address": g["address"], "issue": g["issue"]},
        "review": {
            "slug": row["slug"],
            "round": row["round"],
            "at": doc.get("at"),
            "title": title_of(row),
            "sha256": a["sha256"],
            "files": doc.get("files") or {},
        },
        "episode": True,
        "clips": {"vetoed": vetoed, "kept": [cl["file"] for cl in clips if cl["file"] not in vetoed]},
        "signature": {
            "name": a["name"],
            "address": a["address"],
            "at": a["at"],
            "ip": a["ip"],
            "ua": a["ua"],
            "sha256": a["sha256"],
        },
        "office": office(),
        "copies": {k: f"guests/{g['id']}/approval.{k}" for k in ("json", "html")},
    }


def record_rows(c, g, row, a):
    """The record as people read it, on the copy."""
    doc, vetoed = record(row), json.loads(a["vetoed"])
    clips = clips_of(row)
    rows = [
        ("Episode", f"{title_of(row)}, review round {row['round']} of {doc.get('at') or '?'}"),
        ("Review record's sha256", a["sha256"]),
        ("The episode", "approved"),
        ("Clips kept", ", ".join(cl["title"] for cl in clips if cl["file"] not in vetoed) or "none"),
        ("Clips vetoed", ", ".join(cl["title"] for cl in clips if cl["file"] in vetoed) or "none"),
        ("Guest", a["name"]),
        ("Email address", f"{a['address']} (verified by a code emailed to it)"),
        ("Signed at", release.stamp(a["at"])),
        ("From", a["ip"] or ""),
        ("Device", (a["ua"] or "")[:200]),
        ("Copies", f"guests/{g['id']}/approval.json and .html in AgentSee's archive"),
    ]
    return rows


def html_copy(c, g, row, a):
    """The signed copy as one HTML file: what was approved, the signature as an image, the record."""
    vetoed = json.loads(a["vetoed"])
    clips = clips_of(row)
    kept = "".join(f"<li>{esc(cl['title'])}</li>" for cl in clips if cl["file"] not in vetoed) or "<li>none</li>"
    out = "".join(f"<li>{esc(cl['title'])}</li>" for cl in clips if cl["file"] in vetoed) or "<li>none</li>"
    src = "data:image/png;base64," + base64.b64encode(a["png"]).decode()
    sig = (
        f'<figure class="signature"><img src="{src}" alt="{esc(a["name"])}&#8217;s signature" width="320" height="120">'
        f"<figcaption><b>{esc(a['name'])}</b><br>signed on {esc(when(a['at']))}</figcaption></figure>"
    )
    rec = "".join(f"<tr><td>{esc(k)}</td><td>{esc(val)}</td></tr>" for k, val in record_rows(c, g, row, a))
    style = (
        "body{font:16px/1.55 Georgia,'Times New Roman',serif;color:#1a1a1a;background:#fff;margin:0;padding:2rem 1.2rem}"
        "main{max-width:40rem;margin:0 auto}h1{font-size:1.7rem}h2{font-size:1.05rem;margin:1.3rem 0 .3rem}"
        ".signature{margin:1rem 0;max-width:15rem}.signature img{display:block;width:100%;height:auto;border-bottom:1px solid #333}"
        "figcaption{font-size:.9rem;margin-top:.3rem}table{border-collapse:collapse;font-size:.9rem}"
        "td{padding:.2rem .8rem .2rem 0;vertical-align:top;overflow-wrap:anywhere}td:first-child{color:#555;white-space:nowrap}"
    )
    title = title_of(row)
    return (
        '<!doctype html>\n<html lang="en-GB"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        f"<title>Approval of {esc(title)}, round {row['round']}, by {esc(a['name'])}</title><style>{style}</style></head>"
        f"<body><main><h1>Approval of &#8220;{esc(title)}&#8221;</h1>"
        f"<p>{esc(a['name'])} watched review round {row['round']} of this episode of the AgentSee podcast through "
        f"{esc(office())} and approved it for publication as it is in that round, on {esc(when(a['at']))}.</p>"
        f"<h2>The episode</h2><p>Approved.</p><h2>Clips to use</h2><ul>{kept}</ul><h2>Clips not to use</h2><ul>{out}</ul>"
        f"<h2>Signed</h2>{sig}<h2>The record</h2><table>{rec}</table></main></body></html>\n"
    )


def copies(c, g, row, a, client=None):
    """The two copies into the archive; `approval.json` is the one the pipeline reads."""
    keys = {}
    rec = json.dumps(approval_record(c, g, row, a), indent=1).encode("utf-8")
    keys["json"] = release._put(c, client, f"guests/{g['id']}/approval.json", rec, "application/json", g["id"])
    page = (a["html"] or html_copy(c, g, row, a)).encode("utf-8")
    keys["html"] = release._put(c, client, f"guests/{g['id']}/approval.html", page, "text/html; charset=utf-8", g["id"])
    return keys


def send_copy(c, g, row, a):
    vetoed = json.loads(a["vetoed"])
    clips = clips_of(row)
    if not clips:
        clipline = "There are no clips with it."
    elif not vetoed:
        clipline = f"All {len(clips)} clips may be used."
    else:
        kept = len(clips) - len(vetoed)
        clipline = (
            f"{kept} clip{'s' if kept != 1 else ''} may be used; "
            f"{len(vetoed)} ({', '.join(cl['title'] for cl in clips if cl['file'] in vetoed)}) will not be."
        )
    title = title_of(row)
    text = APPROVAL_MAIL.format(
        first=g["name"].split()[0], title=title, round=row["round"], when=when(a["at"]), host=office(), clips=clipline
    )
    page = (a["html"] or html_copy(c, g, row, a)).encode("utf-8")
    att = [(f"agentsee-approval-round-{row['round']}.html", page, "text/html")]
    subject = f"Your approval of {title}"
    try:
        msgid = mail.send(g["address"], subject, text, attachments=att, copy=True)
    except mail.MailError as e:
        mail.record(c, "see", g["address"], "approval copy", error=e, guest=g["id"])
        return False
    mail.record(c, "see", g["address"], "approval copy", msgid, guest=g["id"])
    return True


def signature_png(c, gid):
    a = approval(c, gid)
    return a["png"] if a else None


# ─── the issue ───────────────────────────────────────────────────────────────
def job_noted(env, a, c=None):
    """The note on the guest's issue: quoted as the guest's words, with the moment it is about; a host's plain."""
    lines = "\n".join(
        f"{'At ' + mmss(n['t']) + ': ' if n.get('t') is not None else ''}{n['text']}" for n in a.get("notes") or []
    )
    if a.get("by"):
        body = (
            f'{a["by"]} left a note on review round {a["round"]} of "{a["title"]}" ({a["slug"]}) on the engine on '
            f"{a['when']}: {lines}"
        )
    else:
        head = (
            f'{a["name"]} left a note on review round {a["round"]} of "{a["title"]}" ({a["slug"]}) on {a["when"]}. '
            f"`episode.py notes {a['slug']}` brings the confirmed cuts in.\n\n"
        )
        body = head + linear.quote(a["name"], lines)
    if a.get("cut"):
        body += f'\n\nThe words found in the transcript for the cut, awaiting their confirmation: "{a["cut"]}"'
    elif a.get("missed"):
        body += "\n\nThey asked for a cut, but the words could not be found in the transcript; read the note."
    cm = linear.comment(env, a["issue"], body)
    return {"issue": a["issue"], "comment": cm["url"]}


def job_cut(env, a, c=None):
    """The word on a cut, on the issue: the guest's, or a host's."""
    name = a.get("by") or a["name"]
    if a["confirmed"]:
        text = (
            f'{name} confirmed the cut on review round {a["round"]} of "{a["title"]}" ({a["slug"]}) on '
            f'{a["when"]}: "{a["phrase"]}". `episode.py notes {a["slug"]}` takes it into removals.json.'
        )
    else:
        text = (
            f'{name} decided against the cut on review round {a["round"]} of "{a["title"]}" ({a["slug"]}) on '
            f'{a["when"]}: "{a["phrase"]}" stays; the note itself stands.'
        )
    cm = linear.comment(env, a["issue"], text)
    return {"issue": a["issue"], "comment": cm["url"]}


def job_host_approved(env, a, c=None):
    """A host's approval on the guest's issue, and whether the pipeline now has what it needs."""
    text = (
        f'{a["host"]} approved review round {a["round"]} of "{a["title"]}" ({a["slug"]}) on the engine on {a["when"]}'
    )
    if a.get("waiting"):
        text += f"; waiting for {', '.join(a['waiting'])}."
    else:
        text += f", the last of the hosts ({', '.join(a['approved'])})."
        if a.get("guest_approved"):
            text += f" The guest has approved too: `episode.py approve {a['slug']}` has what it needs."
        else:
            text += " The guest has not approved yet."
    cm = linear.comment(env, a["issue"], text)
    return {"issue": a["issue"], "comment": cm["url"]}


def said(text, bad=False):
    """The word beside a form (idea.word), for the engine's routes."""
    return idea.word(text, bad)


def job_approved(env, a, c=None):
    """The approval on the guest's issue: which round, when, from where, the clips vetoed; and the label."""
    key = a["issue"]
    text = (
        f'{a["name"]} approved review round {a["round"]} of "{a["title"]}" ({a["slug"]}) through {office()} '
        f"on {a['when']}, from {a['address']}."
    )
    if a.get("vetoed"):
        text += f" Clips vetoed: {', '.join(a['vetoed'])}."
    else:
        text += " Every clip may be used."
    text += f" The record: guests/{a['gid']}/approval.json in the archive; `episode.py approve {a['slug']}` reads it."
    cm = linear.comment(env, key, text)
    return {"issue": key, "comment": cm["url"], "label": linear.label_softly(env, key, "episode-approved")}


# ─── the pages ───────────────────────────────────────────────────────────────
def host_line(c, gid):
    """The review's state on the hosts' page for the guest."""
    row = current(c, gid)
    if not row:
        return '<span class="muted">nothing for review yet</span>'
    ns = notes(c, gid, row["slug"], row["round"])
    a = approval(c, gid, row)
    line = f"{esc(row['slug'])} round {row['round']}, seen {esc(when(row['seen_at']))}; {len(ns)} note{'s' if len(ns) != 1 else ''}"
    if a:
        vetoed = json.loads(a["vetoed"])
        line += f"; approved on {esc(when(a['at']))}" + (
            f", vetoed {', '.join(esc(v) for v in vetoed)}" if vetoed else ""
        )
    else:
        line += "; not approved yet"
    done = [r["host"] for r in host_approvals(c, gid, row)]
    line += f"; hosts approved: {esc(', '.join(done))}" if done else "; no host has approved yet"
    return (
        line
        + f". {link(f'/guests/{gid}/review', 'Watch, note, approve')} "
        + f'<span class="mono">guests/{esc(gid)}/notes.json, approval.json; episodes/{esc(row["slug"])}/review/hosts.json</span>'
    )


def note_item(n, ask=True, action="/review/cut", csrf=None, label=None):
    """One note as the list shows it: who, its moment, its words, and the cut found for it with the answer; `ask`
    puts the question to the reader, with the forms at `action` (the engine's carry the form token)."""
    t = (
        f'<a class="t" href="#watch" data-t="{n["t"]:.1f}">{esc(mmss(n["t"]))}</a> '
        if n["t"] is not None
        else '<span class="t muted">general</span> '
    )
    who = f"<b>{esc(label)}</b>: " if label else ""
    token = f'<input type="hidden" name="_t" value="{esc(csrf)}">' if csrf else ""
    cut = ""
    if n["remove"]:
        phrase = esc(json.loads(n["remove"])["phrase"])
        if n["confirmed"] is None and ask:
            cut = (
                f'<div class="cut-ask"><p>Cut these words? <q>{phrase}</q></p>'
                f'<form method="post" action="{action}" class="inline" data-live>{token}'
                f'<input type="hidden" name="id" value="{n["id"]}"><input type="hidden" name="answer" value="yes">'
                '<button type="submit" class="signal">Yes, cut them</button></form> '
                f'<form method="post" action="{action}" class="inline" data-live>{token}'
                f'<input type="hidden" name="id" value="{n["id"]}"><input type="hidden" name="answer" value="no">'
                '<button type="submit">No, leave them</button></form></div>'
            )
        elif n["confirmed"]:
            cut = f'<p class="cut-state">Cut confirmed: <q>{phrase}</q></p>'
        elif n["confirmed"] is not None:
            cut = f'<p class="cut-state muted">Left in: <q>{phrase}</q></p>'
        else:
            cut = f'<p class="cut-state muted">Words found, not answered: <q>{phrase}</q></p>'
    return f"<li>{who}{t}{esc(n['text'])}{cut}</li>"


def notes_body(c, g, row, word="", host=None, csrf=None):
    """The notes section: this round's notes with any cut found for each, the
    last round's for the record, and the form for one more, which sends it.
    On the office (`host` None) the guest's notes are theirs to answer and
    the hosts' are shown by name; on the engine (`host` set) the other way,
    with the engine's routes and its form token."""
    ns = notes(c, g["id"], row["slug"], row["round"])
    first = g["name"].split()[0]
    base = f"/guests/{g['id']}/review" if host else "/review"

    def item(n, ask=True):
        mine = bool(n["author"]) == bool(host)
        label = n["author"] if n["author"] else (first if host else None)
        return note_item(n, ask=ask and mine, action=f"{base}/cut", csrf=csrf, label=label)

    items = [item(n) for n in ns]
    shown = (
        f'<p>{"On this round:" if host else "Sent to James and Abrar:"}</p><ul class="notes-list">{"".join(items)}</ul>'
        if items
        else "<p>No notes yet on this round.</p>"
    )
    if row["round"] > 1:
        before = notes(c, g["id"], row["slug"], row["round"] - 1)
        if before:
            shown += (
                f'<details class="before"><summary>What {"was" if host else "you"} asked on round {row["round"] - 1}</summary>'
                f'<ul class="notes-list">{"".join(item(n, ask=False) for n in before)}</ul></details>'
            )
    token = f'<input type="hidden" name="_t" value="{esc(csrf)}">' if csrf else ""
    hint = (
        f"Your notes go on {esc(first)}&#8217;s issue and {esc(first)} sees them here too. For a cut, say what should go: "
        "the words are found in the transcript and shown to you to confirm, and `episode.py notes` takes them into "
        "removals.json with the guest's."
        if host
        else 'Each note goes to James and Abrar as you send it. A moment makes it easy to act on; "at this moment" '
        "takes it from the player. For a cut, say what should go: the words are found in the transcript and shown to "
        "you to confirm, so what is cut is exactly what you meant. They re-cut from your notes, this page shows the "
        "new round, and you approve when it is right."
    )
    form = (
        f'<form method="post" action="{base}/note" class="block note-form" data-note data-live>{token}'
        '<label for="note-text">A note</label>'
        f'<textarea id="note-text" name="text" rows="3" maxlength="{NOTE_MAX}" required '
        'placeholder="What should change, or what you liked"></textarea>'
        '<div class="row"><label for="note-at">At</label>'
        '<input type="text" id="note-at" name="at" class="at" placeholder="m:ss, or blank" autocomplete="off">'
        '<button type="button" data-here hidden>At this moment</button>'
        '<button type="submit" class="signal">Send the note</button></div>'
        f'<p class="muted">{hint}</p>'
        f"{word}</form>"
    )
    return section("Notes", f"Round {row['round']}", shown + form, anchor="notes")


def hosts_body(c, g, row, host, csrf, word=""):
    """The approval section on the engine: the guest's state, each host's, and the button for this host."""
    a = approval(c, g["id"], row)
    if a:
        vetoed = json.loads(a["vetoed"])
        guest_line = f"<p><b>{esc(g['name'])}</b> approved round {row['round']} on {esc(when(a['at']))}" + (
            f", vetoing {esc(', '.join(vetoed))}." if vetoed else ", every clip kept."
        )
        guest_line += "</p>"
    else:
        guest_line = f"<p><b>{esc(g['name'])}</b> has not approved round {row['round']} yet.</p>"
    done = {r["host"]: r for r in host_approvals(c, g["id"], row)}
    lines = "".join(
        f"<li><b>{esc(h)}</b>: "
        + (f"approved on {esc(when(done[h]['at']))} from {esc(done[h]['ip'] or '')}" if h in done else "not yet")
        + "</li>"
        for h in hosts()
    )
    form = ""
    if host not in done:
        form = (
            f'<form method="post" action="/guests/{g["id"]}/review/approve" class="block" data-live>'
            f'<input type="hidden" name="_t" value="{esc(csrf)}">'
            f'<p><button type="submit" class="signal">Approve round {row["round"]} as {esc(host)}</button></p>'
            '<p class="muted">Recorded: you, the time, your address and device, and the hash of this round\'s record, '
            f"to episodes/{esc(row['slug'])}/review/hosts.json. `episode.py approve` refuses a guest's episode until the "
            f"guest and both hosts have approved the round.</p>{word}</form>"
        )
    return section(
        "Approval", "The guest, and the hosts", guest_line + f'<ul class="hosts">{lines}</ul>' + form, anchor="approve"
    )


def approve_body(c, g, row, word=""):
    """The approval section: the signed state, or the form for this round."""
    a = approval(c, g["id"], row)
    clips = clips_of(row)
    if a:
        vetoed = json.loads(a["vetoed"])
        kept = [cl["title"] for cl in clips if cl["file"] not in vetoed]
        out = [cl["title"] for cl in clips if cl["file"] in vetoed]
        text = (
            f"<p>You approved round {row['round']} on {esc(when(a['at']))}"
            + (f", with {len(kept)} clip{'s' if len(kept) != 1 else ''} to use" if clips else "")
            + (f" and {len(out)} not to ({esc(', '.join(out))})" if out else "")
            + f". A copy with your signature and the record went to {esc(a['address'])}. "
            f"{link('/review/approval.html', 'Your copy')}</p>"
            "<p>James and Abrar take it from here; the published episode carries the number they give it.</p>"
        )
        return section("Approval", "Done", text, anchor="approve")
    earlier = approval(c, g["id"])
    head = ""
    if earlier:
        head = (
            f"<p>You approved round {earlier['round']} on {esc(when(earlier['at']))}; this is round {row['round']}, "
            "so it asks for your approval again.</p>"
        )
    uses = "".join(
        f'<label><input type="checkbox" name="use-{cl["index"]}" value="yes" checked> Use clip {i}: {esc(cl["title"])}</label>'
        for i, cl in enumerate(clips, 1)
    )
    fields = (
        f'<input type="hidden" name="round" value="{row["round"]}">'
        + (
            f'<p><b>The clips.</b> Untick one and it is dropped, not re-cut.</p><div class="uses">{uses}</div>'
            if clips
            else ""
        )
        + '<label for="approve-name">Your full name</label>'
        f'<input type="text" id="approve-name" name="name" value="{esc(g["name"])}" required maxlength="80" autocomplete="name">'
    )
    form = release.pad(
        "/review/approve",
        '<label class="tick"><input type="checkbox" name="agree" value="yes" required> I have watched this round and I '
        "approve the episode for publication as it is.</label>"
        '<p><button type="submit" class="signal">Approve</button></p>'
        '<p class="muted">Recorded with your signature: your name as typed, this email address, the time, your internet '
        "address and your device, and the hash of this round's record, so the approval is of this cut and no other. "
        f"The copy comes to you by email.</p>{word}</form>",
        None,
        fields,
    )
    return section("Approval", "When it is right", head + form, anchor="approve")


def not_yet(c, g, note):
    if not g["signed_version"]:
        body = (
            '<article class="lead"><p class="kicker">The review</p><h1>The release first</h1>'
            '<p class="deck">The episode comes here to be watched and approved once it exists; the release is '
            f"signed before anything is recorded.</p></article>{note}<p>{go('/release', 'The release', 'signal')}</p>"
        )
    else:
        body = (
            '<article class="lead"><p class="kicker">The review</p><h1>Nothing to watch yet</h1>'
            '<p class="deck">When the edit of your episode exists it is here: the whole episode and each clip, to '
            "watch, note and approve before anything goes out. Nobody outside AgentSee sees it before you, and you get "
            f"an email when it is ready.</p></article>{note}<p>{go('/office', 'Back to the office', 'signal')}</p>"
        )
    return "The review", body


def review_page(c, g, msg=None, status=200, client=None):
    """The guest's page: the round to watch, the notes, the clips, the approval."""
    note = f'<p class="honesty"><b>{esc(msg)}</b></p>' if msg else ""
    row = fetch(c, g, client) if g["signed_version"] else None
    if not row:
        title, body = not_yet(c, g, note)
        return title, body, status
    return "The review", round_body(c, g, row, note, client) + approve_body(c, g, row), status


def host_review_page(c, g, host, csrf, msg=None, client=None):
    """A host's page for the guest's round, on the engine: the same round, their notes, the hosts' approvals."""
    note = f'<p class="honesty"><b>{esc(msg)}</b></p>' if msg else ""
    row = fetch(c, g, client)
    if not row:
        body = (
            f'<article class="lead"><p class="kicker">The review</p><h1>{esc(g["name"])}: nothing for review yet</h1>'
            '<p class="deck">`episode.py review &lt;slug&gt;` puts a round in the archive for this guest '
            f"(episode.json names them: {esc(g['id'])}); this page shows it then, to them and to you.</p></article>"
            f"{note}<p>{go(f'/guests/{g["id"]}', 'Back to the guest', 'signal')}</p>"
        )
        return f"{g['name']}: the review", body
    body = round_body(c, g, row, note, client, host=host, csrf=csrf) + hosts_body(c, g, row, host, csrf)
    return f"{g['name']}: the review", body


def round_body(c, g, row, note="", client=None, host=None, csrf=None):
    """The round as a page body, less the approval: the lead, the player, the notes, the clips."""
    doc = record(row)
    clips = clips_of(row)
    want = list(WANT) + [cl["file"] for cl in clips] + [cl["poster"] for cl in clips if cl["poster"]]
    urls = links(row, want, client)
    title = title_of(row)
    changed = doc.get("changed") or []
    if host:
        deck = (
            f"Round {row['round']}, from {esc(when(doc.get('at')))}, for {esc(g['name'])}. What they see; your notes "
            "go to their issue, and your approval to the archive."
        )
    else:
        deck = (
            f"Round {row['round']}, from {esc(when(doc.get('at')))}. Nobody outside AgentSee sees this before you. "
            "Watch it, note what should change, and approve it when it is right."
        )
    if row["round"] > 1 and changed:
        deck += f" Since the last round these changed: {esc(', '.join(changed))}."
    lead = f'<article class="lead"><p class="kicker">The review</p><h1>{esc(title)}</h1><p class="deck">{deck}</p></article>'
    poster = urls.get("thumbnail.png") or urls.get("episode-art.png")
    if urls.get("episode.mp4"):
        player = (
            '<video class="episode" data-episode controls playsinline preload="metadata"'
            + (f' poster="{esc(poster)}"' if poster else "")
            + f' src="{esc(urls["episode.mp4"])}"></video>'
        )
        downloads = " ".join(
            link(urls[n], t)
            for n, t in (("episode.mp4", "Download the video"), ("episode.mp3", "the audio"))
            if n in urls
        )
        watch = player + f"<p>{downloads}. The links work for an hour; reload the page for fresh ones.</p>"
    else:
        watch = '<p class="honesty"><b>The files could not be reached just now. Try again in a minute.</b></p>'
    # The episode, the notes under it, the clips, then the approval, which is the order a guest works in.
    body = lead + note + section("Watch", "The episode", watch, anchor="watch")
    body += notes_body(c, g, row, host=host, csrf=csrf)
    if clips:
        figs = "".join(
            '<figure class="clip"><video controls playsinline preload="none"'
            + (f' poster="{esc(urls[cl["poster"]])}"' if cl["poster"] and cl["poster"] in urls else "")
            + (f' src="{esc(urls[cl["file"]])}"' if cl["file"] in urls else "")
            + f"></video><figcaption>{i}. {esc(cl['title'])}</figcaption></figure>"
            for i, cl in enumerate(clips, 1)
        )
        body += section("Clips", f"{len(clips)} for the socials", f'<div class="clips">{figs}</div>', anchor="clips")
    return body
