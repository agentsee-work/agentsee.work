"""
The guest's office: see.agentsee.work, the second hostname the engine serves
from the same process (AGE-104). The tunnel brings it in without Access, so
the door is the engine's own: an email address, a six-digit code by email,
a session cookie for this host only. The design is on AGE-92.

What is here:

  the guest, code and session tables in the ledger, and the archive copy
  of a guest's record (guests/<id>/guest.json) made on every change of
  state, because the ledger sits on the root disk and was lost once;
  the door on the guest host: /, /i/<invite>, /enter, /code, /office, /leave;
  the Guests screen on the engine host, where an invitation is made, sent,
  withdrawn and reissued.

The code is bound to the browser that asked for it: /enter sets a cookie
and /code accepts the code only together with it, so a code read over a
shoulder or talked out of someone is useless without the device. Six digits
with five tries and a cooldown is a million-to-one guess made five times.
A link in the email would be opened by mail scanners and bind a session to
whoever opens it, which is why the code won. Sessions and bindings are
signed with ENGINE_SESSION_KEY (the 1Password item "Engine session key"),
so a restart keeps every guest signed in; without it the key is made up for
the process and `serve` says so.

An invite link carries an id, not a secret. An invitation made without an
address is claimed by the first address that passes a code; one made with
an address refuses any other. Who signed in, when, from which address and
device is recorded on every sign-in, not enforced: the address the code
verified is the lock.
"""

import datetime as dt
import hashlib
import hmac
import http.cookies
import json
import os
import pathlib
import re
import secrets
import tempfile
import urllib.parse

import archive
import ledger
import mail
from pages import button, esc, link, section, see_page, table, when

ROOT = pathlib.Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
STATIC = ROOT / "engine" / "static"
SAFE = re.compile(r"^[A-Za-z0-9._-]+$")
HOST = os.environ.get("ENGINE_GUEST_HOST") or (ledger.CONFIG.get("guest") or {}).get("host", "see.agentsee.work")

CODE_TTL = 600  # a code lives ten minutes
CODE_TRIES = 5  # wrong guesses before it is void
COOLDOWN = 900  # seconds an address waits for a new code after a void one
PER_ADDRESS = (3, 600)  # codes an address may ask for in a window of seconds
PER_IP = (12, 600)  # codes one connection may ask for in a window
SESSION_DAYS = 30
SESSION_COOKIE, CODE_COOKIE = "see_session", "see_code"
ADDRESS = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
ISSUE = re.compile(r"^[A-Z]{2,6}-\d{1,6}$")
EPHEMERAL = secrets.token_hex(32)
STATES = {
    "invited": "a link exists; nobody has come through it yet",
    "claimed": "an address passed a code and holds the invitation",
    "revoked": "withdrawn; the link and every session are dead",
}

CODE_MAIL = """Hello,

Your code for the AgentSee office is

    {code}

Type it into the page that asked for it, on the same device, within ten
minutes. It works once.

Nobody at AgentSee will ever ask you for this code. If you did not ask for
one, ignore this message; nothing happens without it."""

INVITE_MAIL = """Hello {name},

{who} would like to record an episode of the AgentSee podcast with you.
Everything about it is arranged in one place, the office:

    {url}

Open it on any device. It asks for this email address and sends you a code;
then you can read and sign the release, flesh the idea out and pick a time.

If this reached your junk folder, mark it as not junk, so the code finds
your inbox. A reply, even one word, helps as well: it tells your mail
service that mail from AgentSee is wanted.

If this was not expected, ignore it; the link does nothing on its own."""


class Throttled(Exception):
    pass


# ─── keys and cookies ────────────────────────────────────────────────────────
def key():
    return (os.environ.get("ENGINE_SESSION_KEY") or EPHEMERAL).encode()


def persistent():
    return bool(os.environ.get("ENGINE_SESSION_KEY"))


def mac(*parts):
    return hmac.new(key(), "\x1f".join(parts).encode(), hashlib.sha256).hexdigest()


def cookies(h):
    jar = http.cookies.SimpleCookie()
    try:
        jar.load(h.headers.get("Cookie") or "")
    except http.cookies.CookieError:
        return {}
    return {k: v.value for k, v in jar.items()}


def set_cookie(name, value, max_age):
    return ("Set-Cookie", f"{name}={value}; Path=/; Max-Age={max_age}; Secure; HttpOnly; SameSite=Lax")


def session_cookie(sid):
    return f"{sid}.{mac('session', sid)}"


def _since(seconds):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


# ─── guests ──────────────────────────────────────────────────────────────────
def guest(c, gid):
    return c.execute("SELECT * FROM guest WHERE id=?", (gid,)).fetchone()


def by_invite(c, invite):
    return c.execute("SELECT * FROM guest WHERE invite=?", (invite,)).fetchone()


def by_address(c, address):
    return c.execute(
        "SELECT * FROM guest WHERE address=? AND state!='revoked' ORDER BY created_at DESC", (address,)
    ).fetchone()


def clean_address(s):
    s = " ".join((s or "").split()).lower()
    return s if ADDRESS.match(s) and len(s) <= 254 else None


def invite_url(g):
    return f"https://{HOST}/i/{g['invite']}"


def create(c, name, address, issue, who):
    name = " ".join((name or "").split())[:80]
    if not name:
        raise ValueError("A guest has a name")
    if address and address.strip():
        address = clean_address(address)
        if not address:
            raise ValueError("That is not an email address")
    else:
        address = None
    issue = " ".join((issue or "").split()).upper() or None
    if issue and not ISSUE.match(issue):
        raise ValueError("An issue is written AGE-73, or left empty until there is one")
    gid, now = secrets.token_hex(4), ledger.now()
    c.execute(
        "INSERT INTO guest (id, invite, name, address, issue, state, created_by, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (gid, secrets.token_urlsafe(12), name, address, issue, "invited", who, now, now),
    )
    ledger.event(c, "guest:invited", who, detail={"name": name, "address": address, "issue": issue}, guest=gid)
    mirror(c, gid)
    return guest(c, gid)


def change(c, gid, who, what, detail=None, **fields):
    fields["updated_at"] = ledger.now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    c.execute(f"UPDATE guest SET {sets} WHERE id = ?", (*fields.values(), gid))
    ledger.event(c, what, who, detail=detail, guest=gid)
    mirror(c, gid)


def revoke(c, gid, who):
    if guest(c, gid)["state"] == "revoked":
        raise ValueError("Already withdrawn")
    end_sessions(c, gid)
    change(c, gid, who, "guest:revoked", state="revoked", revoked_at=ledger.now())


def reissue(c, gid, who):
    """A new link for the same person. Every session ends; the address, if
    one is bound, stays bound, so only it can come through the new link."""
    end_sessions(c, gid)
    change(
        c,
        gid,
        who,
        "guest:reissued",
        invite=secrets.token_urlsafe(12),
        state="invited",
        claimed_at=None,
        revoked_at=None,
    )


def record(c, gid):
    """The guest's whole record, as the archive keeps it."""
    doc = dict(guest(c, gid))
    doc["sessions"] = [
        {
            "id": s["id"][:6],
            "address": s["address"],
            "ip": s["ip"],
            "ua": s["ua"],
            "from": s["created_at"],
            "last_seen": s["last_seen"],
            "ended": s["revoked_at"],
        }
        for s in c.execute("SELECT * FROM session WHERE guest=? ORDER BY created_at", (gid,))
    ]
    doc["events"] = [
        {"at": e["at"], "who": e["who"], "what": e["what"], "detail": e["detail"]}
        for e in c.execute("SELECT at, who, what, detail FROM event WHERE guest=? ORDER BY id", (gid,))
    ]
    return doc


def mirror(c, gid, client=None):
    """The guest's record to the archive, now. A failure is recorded against
    the guest and the ledger goes on; the nightly snapshot carries the table
    as well."""
    key_ = f"guests/{gid}/guest.json"
    doc = record(c, gid)
    try:
        client = client or archive.Client()
        fd, tmp = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            pathlib.Path(tmp).write_text(json.dumps(doc, indent=1), encoding="utf-8")
            client.put(key_, tmp, "application/json")
        finally:
            os.unlink(tmp)
    except archive.ArchiveError as e:
        ledger.event(c, "guest:mirror:failed", "engine", detail=str(e)[:300], guest=gid)
        return None
    return key_


# ─── codes ───────────────────────────────────────────────────────────────────
def request_code(c, address, invite, ip, ua):
    """A six-digit code for this address, bound to the browser asking.
    Returns (id, code, binding); raises Throttled with the sentence."""
    last = c.execute("SELECT * FROM code WHERE address=? ORDER BY created_at DESC LIMIT 1", (address,)).fetchone()
    if last and last["attempts"] >= CODE_TRIES and last["created_at"] > _since(COOLDOWN):
        raise Throttled("Five wrong tries; this address waits fifteen minutes before another code")
    n, window = PER_ADDRESS
    if (
        c.execute("SELECT COUNT(*) FROM code WHERE address=? AND created_at>?", (address, _since(window))).fetchone()[0]
        >= n
    ):
        raise Throttled(
            "That address has had its codes for now; the last one is good for ten minutes from when it was sent"
        )
    n, window = PER_IP
    if (
        ip
        and c.execute("SELECT COUNT(*) FROM code WHERE ip=? AND created_at>?", (ip, _since(window))).fetchone()[0] >= n
    ):
        raise Throttled("Too many codes from this connection; try again in ten minutes")
    cid, code, binding = secrets.token_urlsafe(9), f"{secrets.randbelow(10**6):06d}", secrets.token_urlsafe(24)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    c.execute(
        "INSERT INTO code (id, address, invite, code_hash, binding_hash, created_at, expires_at, ip, ua) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            cid,
            address,
            invite,
            mac("code", cid, code),
            mac("bind", cid, binding),
            now.isoformat(),
            (now + dt.timedelta(seconds=CODE_TTL)).isoformat(),
            ip,
            (ua or "")[:300],
        ),
    )
    return cid, code, binding


def verify_code(c, cid, typed, binding):
    """Returns (row, None, False) for the right code on the right device;
    otherwise (None, the sentence, whether the same code may be tried again)."""
    row = c.execute("SELECT * FROM code WHERE id=?", (cid,)).fetchone()
    if not row or row["used_at"] or row["expires_at"] < ledger.now():
        return None, "That code has expired or was used already; ask for another", False
    if row["attempts"] >= CODE_TRIES:
        return None, "That code is void after five wrong tries; ask for another in fifteen minutes", False
    if not binding or not hmac.compare_digest(row["binding_hash"], mac("bind", cid, binding)):
        return (
            None,
            "That code was asked for on another device, or this browser lost its cookie; ask for one here",
            False,
        )
    typed = re.sub(r"\D", "", typed or "")
    if not hmac.compare_digest(row["code_hash"], mac("code", cid, typed)):
        c.execute("UPDATE code SET attempts=attempts+1 WHERE id=?", (cid,))
        left = CODE_TRIES - row["attempts"] - 1
        if not left:
            return None, "Not that code, and that was the last try; ask for another in fifteen minutes", False
        return None, f"Not that code; {left} more {'try' if left == 1 else 'tries'}", True
    c.execute("UPDATE code SET used_at=? WHERE id=?", (ledger.now(), cid))
    return row, None, False


# ─── sessions ────────────────────────────────────────────────────────────────
def start_session(c, gid, address, ip, ua):
    sid, now = secrets.token_urlsafe(24), ledger.now()
    ua = (ua or "")[:300]
    c.execute(
        "INSERT INTO session (id, guest, address, ip, ua, created_at, last_seen) VALUES (?,?,?,?,?,?,?)",
        (sid, gid, address, ip, ua, now, now),
    )
    ledger.event(c, "guest:signin", address, detail={"ip": ip, "ua": ua, "session": sid[:6]}, guest=gid)
    return sid


def end_sessions(c, gid, sid=None):
    q, args = "UPDATE session SET revoked_at=? WHERE guest=? AND revoked_at IS NULL", [ledger.now(), gid]
    if sid:
        q += " AND id=?"
        args.append(sid)
    c.execute(q, args)


def current(c, h):
    """The signed-in guest and session on this request, or (None, None)."""
    sid, _, sig = cookies(h).get(SESSION_COOKIE, "").partition(".")
    if not sid or not re.fullmatch(r"[0-9a-f]{64}", sig) or not hmac.compare_digest(sig, mac("session", sid)):
        return None, None
    s = c.execute("SELECT * FROM session WHERE id=? AND revoked_at IS NULL", (sid,)).fetchone()
    if not s or s["created_at"] < _since(SESSION_DAYS * 86400):
        return None, None
    g = guest(c, s["guest"])
    if not g or g["state"] == "revoked":
        return None, None
    c.execute("UPDATE session SET last_seen=? WHERE id=?", (ledger.now(), sid))
    return g, s


def admit(c, row, ip, ua):
    """After a right code: the guest it admits and a session, or (None, None).
    The invite on the code, if any, is claimed by this address when it has
    none and refuses it when it has another; failing that, the address's own
    invitation counts."""
    address = row["address"]
    g = by_invite(c, row["invite"]) if row["invite"] else None
    if g and (g["state"] == "revoked" or (g["address"] and g["address"] != address)):
        g = None
    if g and not g["address"]:
        change(
            c,
            g["id"],
            address,
            "guest:claimed",
            detail={"ip": ip, "ua": (ua or "")[:300]},
            address=address,
            state="claimed",
            claimed_at=ledger.now(),
        )
        g = guest(c, g["id"])
    if not g:
        g = by_address(c, address)
    if not g:
        return None, None
    if g["state"] == "invited":
        change(
            c,
            g["id"],
            address,
            "guest:claimed",
            detail={"ip": ip, "ua": (ua or "")[:300]},
            state="claimed",
            claimed_at=ledger.now(),
        )
    sid = start_session(c, g["id"], address, ip, ua)
    mirror(c, g["id"])
    return guest(c, g["id"]), sid


# ─── the guest host ──────────────────────────────────────────────────────────
def is_guest_host(h):
    return (h.headers.get("Host") or "").split(":")[0].lower() == HOST


def client_ip(h):
    return h.headers.get("Cf-Connecting-Ip") or h.client_address[0]


def same_site(h):
    """A browser's POST from another site is refused: the origin check that
    stands in for a form token on a host whose pages anyone may read."""
    origin = h.headers.get("Origin")
    if origin and urllib.parse.urlparse(origin).netloc.split(":")[0].lower() != HOST:
        return False
    return h.headers.get("Sec-Fetch-Site", "same-origin") in ("same-origin", "none")


def page(h, title, body, status=200, headers=None):
    h.send(status, see_page(title, body), headers=headers)


def door(c, h, invite=None, msg=None, address="", status=200):
    g = by_invite(c, invite) if invite else None
    hello = ""
    if g and g["state"] != "revoked":
        hello = f"<p>An invitation for <b>{esc(g['name'])}</b>.</p>"
    elif invite:
        hello = '<p class="honesty">That invitation link is not one we know, or it has been withdrawn. The address you give is what counts.</p>'
    note = f'<p class="honesty"><b>{esc(msg)}</b></p>' if msg else ""
    hidden = f'<input type="hidden" name="invite" value="{esc(invite)}">' if invite else ""
    body = (
        '<article class="lead"><p class="kicker">see.agentsee.work</p><h1>The office</h1>'
        '<p class="deck">You have been asked onto the AgentSee podcast. This is where it is arranged: the release '
        "to read and sign, the idea to flesh out, a time to record, and later the episode to watch and approve.</p></article>"
        f"{hello}{note}"
        '<form method="post" action="/enter" class="block door">'
        '<label for="address">Your email address</label>'
        f'<input type="email" id="address" name="address" value="{esc(address)}" autocomplete="email" required>'
        f'{hidden}<p><button type="submit" class="signal">Send me a code</button></p></form>'
        '<p class="muted">We email a six-digit code. Type it here, on this device, within ten minutes. '
        "Nobody at AgentSee will ever ask you for it.</p>"
        '<p class="muted">Unlike the rest of agentsee.work, this page runs a little script of ours, for the door '
        "and the office. Nothing from anyone else, and nothing that follows you.</p>"
    )
    page(h, "The office", body, status)


def code_page(h, cid, address, msg=None, status=200, headers=None):
    note = f'<p class="honesty"><b>{esc(msg)}</b></p>' if msg else ""
    body = (
        '<article class="lead"><p class="kicker">Check your email</p><h1>A code is on its way</h1>'
        f'<p class="deck">Sent to <b>{esc(address)}</b>. It works once, for ten minutes, on this device.</p></article>'
        f"{note}"
        '<form method="post" action="/code" class="block door">'
        f'<input type="hidden" name="id" value="{esc(cid)}">'
        '<label for="code">The six digits</label>'
        '<input type="text" id="code" name="code" inputmode="numeric" autocomplete="one-time-code" '
        'pattern="[0-9 ]{6,7}" maxlength="7" required autofocus>'
        '<p><button type="submit" class="signal">Come in</button></p></form>'
        f'<p class="muted">Nothing arrived? Look in the junk folder, then {link("/", "ask for another")}.</p>'
    )
    page(h, "Your code", body, status, headers)


def office(c, h, g, s):
    first = g["name"].split()[0]
    body = (
        f'<article class="lead"><p class="kicker">The office</p><h1>You\'re in, {esc(first)}.</h1>'
        '<p class="deck">What happens from here, in order. Each step opens as it is ready; nothing is published '
        "until you have approved it.</p></article>"
        '<ol class="steps">'
        "<li><b>The release.</b> One page in plain words, already signed by James and Abrar; you read it and sign it. "
        '<span class="muted">Soon.</span></li>'
        "<li><b>The idea.</b> A conversation that fleshes it out to about an hour's listening and writes the run-sheet. "
        '<span class="muted">Soon.</span></li>'
        "<li><b>The time.</b> You pick a time and two backups; both hosts accept; you get a link that opens the studio "
        'when it is time. <span class="muted">Soon.</span></li>'
        "<li><b>The review.</b> The episode and the clips, to watch, note and approve before anything goes out. "
        '<span class="muted">After the recording.</span></li></ol>'
        f'<p class="muted">Signed in as {esc(s["address"])} on this device since {esc(when(s["created_at"]))}. '
        "A new device asks for a code of its own.</p>"
        '<form method="post" action="/leave" class="inline"><button type="submit">Leave</button></form>'
    )
    page(h, "The office", body)


def not_found(h, status=404, msg="Not that"):
    page(h, msg, f'<article class="lead"><h1>{esc(msg)}</h1></article><p>{link("/", "The door")}</p>', status)


def get(h, c, path, q):
    if path == "/see.css" or path == "/engine.css":
        return h.static(STATIC, path[1:])
    for prefix, base in (("/assets/", PUBLIC / "assets"), ("/fonts/", PUBLIC / "fonts")):
        if path.startswith(prefix):
            name = path[len(prefix) :]
            return h.static(base, name) if SAFE.match(name) and (base / name).is_file() else not_found(h)
    g, s = current(c, h)
    if path == "/":
        return office(c, h, g, s) if g else door(c, h)
    m = re.fullmatch(r"/i/([A-Za-z0-9_-]{8,40})", path)
    if m:
        return h.redirect("/office") if g else door(c, h, invite=m.group(1))
    if path == "/office":
        return office(c, h, g, s) if g else h.redirect("/")
    if path in ("/enter", "/code", "/leave"):
        return h.redirect("/")
    return not_found(h)


def post(h, c, path, form):
    if not same_site(h):
        return not_found(h, 403, "That form came from somewhere else")
    ip, ua = client_ip(h), h.headers.get("User-Agent", "")
    if path == "/enter":
        invite = " ".join((form.get("invite") or "").split()) or None
        if invite and not re.fullmatch(r"[A-Za-z0-9_-]{8,40}", invite):
            invite = None
        address = clean_address(form.get("address"))
        if not address:
            return door(c, h, invite, "That is not an email address", form.get("address", "")[:120], 400)
        try:
            cid, code, binding = request_code(c, address, invite, ip, ua)
        except Throttled as e:
            return door(c, h, invite, str(e), address, 429)
        try:
            msgid = mail.send(address, "Your code for the AgentSee office", CODE_MAIL.format(code=code))
        except mail.MailError as e:
            mail.record(c, "see", address, "code", error=e)
            why = f"We could not send the code: {e}. Try again in a minute; if it keeps happening, write to hello@agentsee.work."
            return door(c, h, invite, why, address, 502)
        mail.record(c, "see", address, "code", msgid)
        return code_page(h, cid, address, headers=[set_cookie(CODE_COOKIE, binding, CODE_TTL)])
    if path == "/code":
        cid = form.get("id", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", cid):
            return h.redirect("/")
        row, why, again = verify_code(c, cid, form.get("code", ""), cookies(h).get(CODE_COOKIE))
        if not row:
            if again:
                return code_page(
                    h, cid, c.execute("SELECT address FROM code WHERE id=?", (cid,)).fetchone()[0], why, 400
                )
            return door(c, h, msg=why, status=400)
        g, sid = admit(c, row, ip, ua)
        if not g:
            ledger.event(c, "guest:refused", row["address"], detail={"ip": ip, "invite": row["invite"]})
            why = "There is no invitation for this address. If you were expecting one, write to hello@agentsee.work."
            return door(c, h, msg=why, status=403)
        return h.redirect(
            "/office",
            headers=[
                set_cookie(SESSION_COOKIE, session_cookie(sid), SESSION_DAYS * 86400),
                set_cookie(CODE_COOKIE, "", 0),
            ],
        )
    if path == "/leave":
        g, s = current(c, h)
        if s:
            end_sessions(c, g["id"], s["id"])
            ledger.event(c, "guest:left", s["address"], detail={"session": s["id"][:6]}, guest=g["id"])
            mirror(c, g["id"])
        return h.redirect("/", headers=[set_cookie(SESSION_COOKIE, "", 0)])
    return not_found(h)


# ─── the engine's Guests screen ──────────────────────────────────────────────
def state_word(s):
    return f'<span class="status-word status-{esc(s)}" title="{esc(STATES.get(s, ""))}">{esc(s)}</span>'


def guests_page(c, csrf, msg=None):
    rows = []
    for g in c.execute("SELECT * FROM guest ORDER BY created_at DESC"):
        rows.append(
            [
                link(f"/guests/{g['id']}", g["name"]),
                esc(g["address"] or "—"),
                esc(g["issue"] or ""),
                state_word(g["state"]),
                f'<span class="mono">{esc(invite_url(g))}</span>'
                if g["state"] != "revoked"
                else '<span class="muted">withdrawn</span>',
                esc(when(g["claimed_at"])),
            ]
        )
    note = f'<p class="honesty"><b>{esc(msg)}</b></p>' if msg else ""
    form = (
        f'<form method="post" action="/guests/new" class="block"><input type="hidden" name="_t" value="{esc(csrf)}">'
        '<div class="row"><div><label for="name">Name</label><input type="text" id="name" name="name" required maxlength="80"></div>'
        '<div><label for="address">Email address, if known</label><input type="email" id="address" name="address"></div>'
        '<div><label for="issue">Their issue</label><input type="text" id="issue" name="issue" placeholder="AGE-73" '
        'pattern="[A-Za-z]{2,6}-[0-9]{1,6}"></div>'
        '<div><button type="submit" class="signal">Make the invitation</button></div></div></form>'
        '<p class="muted">A link carries an id, not a secret. Without an address, the first address that passes a code '
        "claims it; with one, no other address can. Send it from the guest's page, or paste the link yourself.</p>"
    )
    body = (
        '<article class="lead"><h1>Guests</h1><p class="deck">Who has been invited onto the podcast, and how far '
        f"each has come through {esc(HOST)}. The guest's own journey is theirs; this is the view from the office.</p></article>"
        + note
        + section("New", "An invitation", form)
        + section(
            "Guests",
            f"{len(rows)} invited",
            table(["Guest", "Address", "Issue", "State", "Invitation", "Claimed"], rows)
            if rows
            else "<p>No guests yet.</p>",
        )
    )
    return "Guests", body


def guest_page(c, gid, csrf, msg=None):
    g = guest(c, gid)
    if not g:
        return None
    facts = [
        ("State", state_word(g["state"]) + f' <span class="muted">{esc(STATES.get(g["state"], ""))}</span>'),
        (
            "Address",
            esc(g["address"]) if g["address"] else '<span class="muted">none yet; the first through claims it</span>',
        ),
        (
            "Issue",
            link(f"https://linear.app/agentsee/issue/{g['issue']}", g["issue"], True)
            if g["issue"]
            else '<span class="muted">none</span>',
        ),
        (
            "Invitation",
            f'<span class="mono">{esc(invite_url(g))}</span>'
            if g["state"] != "revoked"
            else '<span class="muted">withdrawn</span>',
        ),
        ("Made", f"{esc(when(g['created_at']))} by {esc(g['created_by'])}"),
        ("Claimed", esc(when(g["claimed_at"])) or '<span class="muted">not yet</span>'),
    ]
    if g["revoked_at"]:
        facts.append(("Withdrawn", esc(when(g["revoked_at"]))))
    facts.append(("Archive", f'<span class="mono">guests/{esc(gid)}/guest.json</span>'))
    dl = '<dl class="facts">' + "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in facts) + "</dl>"
    note = f'<p class="honesty"><b>{esc(msg)}</b></p>' if msg else ""
    acts = []
    if g["state"] == "revoked":
        acts.append(button(f"/guests/{gid}/reissue", "Reissue, with a new link", csrf, "signal"))
    else:
        if g["address"]:
            acts.append(button(f"/guests/{gid}/send", "Send the invitation by email", csrf, "signal"))
        acts.append(button(f"/guests/{gid}/reissue", "New link, every session ended", csrf))
        acts.append(button(f"/guests/{gid}/revoke", "Withdraw", csrf, confirm=True))
    sessions = c.execute("SELECT * FROM session WHERE guest=? ORDER BY created_at DESC", (gid,)).fetchall()
    srows = [
        [
            esc((s["ua"] or "")[:60]),
            f'<span class="mono">{esc(s["ip"] or "")}</span>',
            esc(when(s["created_at"])),
            esc(when(s["last_seen"])),
            '<span class="muted">ended</span>' if s["revoked_at"] else "open",
        ]
        for s in sessions
    ]
    events = c.execute("SELECT * FROM event WHERE guest=? ORDER BY at DESC LIMIT 50", (gid,)).fetchall()
    body = f'<article class="lead"><h1>{esc(g["name"])}</h1></article>{note}{dl}'
    body += section("Now", "Actions", '<div class="actions">' + "".join(acts) + "</div>")
    body += section(
        "Devices",
        f"{sum(1 for s in sessions if not s['revoked_at'])} signed in",
        table(["Device", "IP", "Signed in", "Last seen", "State"], srows) if srows else "<p>No sign-in yet.</p>",
    )
    body += section(
        "Record",
        "What happened, in the guest's words where they are the guest's",
        table(
            ["When", "Who", "What", "Detail"],
            [
                [
                    esc(when(ev["at"], "%d %b %H:%M")),
                    esc(ev["who"]),
                    esc(ev["what"]),
                    f'<span class="mono">{esc(ev["detail"] or "")}</span>',
                ]
                for ev in events
            ],
        ),
    )
    return g["name"], body


def act(c, g, action, who):
    """One action on one guest from the engine's screen. Returns the sentence the screen shows."""
    gid = g["id"]
    if action == "send":
        if g["state"] == "revoked":
            raise ValueError("Withdrawn; reissue first")
        if not g["address"]:
            raise ValueError("No address to send it to; paste the link instead")
        text = INVITE_MAIL.format(name=g["name"].split()[0], who=who.split("@")[0].capitalize(), url=invite_url(g))
        try:
            msgid = mail.send(g["address"], "An invitation to the AgentSee podcast", text)
        except mail.MailError as e:
            mail.record(c, who, g["address"], "invitation", error=e, guest=gid)
            raise ValueError(f"Not sent: {e}") from None
        mail.record(c, who, g["address"], "invitation", msgid, guest=gid)
        return f"Sent to {g['address']}"
    if action == "revoke":
        revoke(c, gid, who)
        return "Withdrawn; the link and every session are dead"
    if action == "reissue":
        reissue(c, gid, who)
        return "A new link; the old one and every session are dead"
    raise ValueError("No such action")
