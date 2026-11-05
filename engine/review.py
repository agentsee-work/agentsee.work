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
import ledger
import linear
import mail
import release
from pages import esc, go, link, section, when

LINK_TTL = 3600  # a signed link's life
REFRESH = 60  # seconds before the office looks in the archive again for a new round
NOTE_MAX = 2000
WANT = ("episode.mp4", "episode.mp3", "thumbnail.png", "episode-art.png")

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


def add_note(c, g, text, at_text, client=None):
    """A note, sent as it is made: kept, mirrored to the archive, mailed to the
    hosts; returns what the issue's job needs (guests.tell)."""
    row = current(c, g["id"])
    if not row:
        raise ReviewError("Nothing to note yet")
    text = "\n".join(" ".join(ln.split()) for ln in (text or "").strip().splitlines()).strip()[:NOTE_MAX]
    if len(text) < 2:
        raise ReviewError("Write the note first")
    t = parse_t(at_text)
    at = ledger.now()
    c.execute(
        "INSERT INTO review_note (guest, slug, round, t, text, at, sent_at) VALUES (?,?,?,?,?,?,?)",
        (g["id"], row["slug"], row["round"], t, text, at, at),
    )
    ledger.event(
        c,
        "guest:noted",
        g["address"],
        detail={"slug": row["slug"], "round": row["round"], "t": t, "text": text[:200]},
        guest=g["id"],
    )
    mirror_notes(c, g, client)
    title = title_of(row)
    line = f"- {'at ' + mmss(t) + ': ' if t is not None else ''}{text}"
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
        "mailed": mailed,
    }


def notes_record(c, gid):
    """Every round's notes, the way `guests/<id>/notes.json` holds them for the pipeline (tools/office.py)."""
    rounds = {}
    for n in notes(c, gid):
        r = rounds.setdefault(
            (n["slug"], n["round"]), {"n": n["round"], "slug": n["slug"], "at": n["at"], "notes": [], "sent": None}
        )
        r["notes"].append({"at": n["at"], "t": n["t"], "text": n["text"], "sent": n["sent_at"]})
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
    """The note on the guest's issue, quoted as the guest's words, with the moment it is about."""
    lines = "\n".join(
        f"{'At ' + mmss(n['t']) + ': ' if n.get('t') is not None else ''}{n['text']}" for n in a.get("notes") or []
    )
    head = (
        f'{a["name"]} left a note on review round {a["round"]} of "{a["title"]}" ({a["slug"]}) on {a["when"]}. '
        f"`episode.py notes {a['slug']}` brings the confirmed cuts in.\n\n"
    )
    cm = linear.comment(env, a["issue"], head + linear.quote(a["name"], lines))
    return {"issue": a["issue"], "comment": cm["url"]}


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
def step_line(c, g):
    """The review's line on the office page."""
    if not g["signed_version"]:
        return '<span class="muted">After the recording.</span>'
    row = current(c, g["id"])
    if not row:
        return '<span class="muted">After the recording.</span>'
    a = approval(c, g["id"], row)
    if a:
        return f"Round {row['round']} approved on {esc(when(a['at']))}. {link('/review', 'The episode, and your approval')}"
    return link("/review", f"Round {row['round']} is ready: watch, note, approve")


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
    return line + f' <span class="mono">guests/{esc(gid)}/notes.json, approval.json</span>'


def notes_body(c, g, row, word=""):
    """The notes section: this round's notes, and the form for one more, which sends it."""
    ns = notes(c, g["id"], row["slug"], row["round"])
    items = []
    for n in ns:
        t = (
            f'<a class="t" href="#watch" data-t="{n["t"]:.1f}">{esc(mmss(n["t"]))}</a> '
            if n["t"] is not None
            else '<span class="t muted">general</span> '
        )
        items.append(f"<li>{t}{esc(n['text'])}</li>")
    shown = (
        f'<p>Sent to James and Abrar:</p><ul class="notes-list">{"".join(items)}</ul>'
        if items
        else "<p>No notes yet on this round.</p>"
    )
    form = (
        '<form method="post" action="/review/note" class="block note-form" data-note data-live>'
        '<label for="note-text">A note</label>'
        f'<textarea id="note-text" name="text" rows="3" maxlength="{NOTE_MAX}" required '
        'placeholder="What should change, or what you liked"></textarea>'
        '<div class="row"><label for="note-at">At</label>'
        '<input type="text" id="note-at" name="at" class="at" placeholder="m:ss, or blank" autocomplete="off">'
        '<button type="button" data-here hidden>At this moment</button>'
        '<button type="submit" class="signal">Send the note</button></div>'
        '<p class="muted">Each note goes to James and Abrar as you send it. A moment makes it easy to act on; '
        '"at this moment" takes it from the player. They re-cut from your notes, this page shows the new round, '
        "and you approve when it is right.</p>"
        f"{word}</form>"
    )
    return section("Notes", f"Round {row['round']}", shown + form, anchor="notes")


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
    """The guest's page: the round to watch, the notes, the approval."""
    note = f'<p class="honesty"><b>{esc(msg)}</b></p>' if msg else ""
    row = fetch(c, g, client) if g["signed_version"] else None
    if not row:
        title, body = not_yet(c, g, note)
        return title, body, status
    doc = record(row)
    clips = clips_of(row)
    want = list(WANT) + [cl["file"] for cl in clips] + [cl["poster"] for cl in clips if cl["poster"]]
    urls = links(row, want, client)
    title = title_of(row)
    changed = doc.get("changed") or []
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
    body = lead + note + section("Watch", "The episode", watch, anchor="watch") + notes_body(c, g, row)
    if clips:
        figs = "".join(
            '<figure class="clip"><video controls playsinline preload="none"'
            + (f' poster="{esc(urls[cl["poster"]])}"' if cl["poster"] and cl["poster"] in urls else "")
            + (f' src="{esc(urls[cl["file"]])}"' if cl["file"] in urls else "")
            + f"></video><figcaption>{i}. {esc(cl['title'])}</figcaption></figure>"
            for i, cl in enumerate(clips, 1)
        )
        body += section("Clips", f"{len(clips)} for the socials", f'<div class="clips">{figs}</div>', anchor="clips")
    body += approve_body(c, g, row)
    return "The review", body, status
