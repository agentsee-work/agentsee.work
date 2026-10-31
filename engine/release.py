"""
The release (AGE-108): the one document with a guest's name on it, and the
first thing they meet inside the office.

The text is versioned in the ledger with its sha256 and mirrored to the
archive as release/v<n>.md and .json; it is written in Linear (a document on
AGE-55) and taken in by `engine.py release add`. Both hosts sign a version
from their profile on the engine host (/you), with a signature drawn once
and kept, before any guest sees it: a version is issued only when every
name in config.json's `hosts` has signed it. The guest reads it in the
office, types their name, draws a signature, ticks, and signs; the copy
they walk away with is a PDF (fpdf2, the image's one dependency) with the
three signatures and the record on its last page, the same as a
self-contained HTML page and a JSON record, all three in the archive as
guests/<id>/release-v<n>.*; the PDF goes to the guest by email with a copy
to hello@, and the guest's issue is told and labelled (linear.py).

A signature here is a simple electronic one: the typed name, the drawn
image, and the record (the address a code verified, the time, the IP, the
device, the version's hash). The record is what makes it worth something,
so nothing signs without all of it. A new version never voids an old
signature: the guest's copy is the version they signed, and the engine can
ask them to sign a newer one later (not yet).
"""

import base64
import datetime as dt
import hashlib
import io
import json
import os
import re
import struct
import tempfile

import archive
import ledger
import mail
from pages import button, esc, link, section, table, when

MAX_PNG = 400_000
PNG = b"\x89PNG\r\n\x1a\n"
FONTS = (  # a TrueType font with the characters a guest's name may have; the image installs the first
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
)
COPY_MAIL = """Hello {name},

Thank you for signing the release for the AgentSee podcast. Your copy is
attached: version {n}, signed by you on {when}, with James's and Abrar's
signatures on it and the record of yours on its last page.

Keep it; it is yours. The office at https://{host} shows it too, and the
next step, the idea."""


class ReleaseError(Exception):
    pass


def hosts():
    return [str(h) for h in ledger.CONFIG.get("hosts") or []]


def office():
    return (ledger.CONFIG.get("guest") or {}).get("host", "see.agentsee.work")


def stamp(iso):
    return f"{when(iso)} ({ledger.CONFIG.get('timezone', 'UTC')})"


# ─── versions ────────────────────────────────────────────────────────────────
def clean(text):
    lines = [ln.rstrip() for ln in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip("\n") + "\n"


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def title_of(text):
    for ln in text.splitlines():
        if ln.startswith("# "):
            return ln[2:].strip()
    return "The release"


def versions(c):
    return c.execute("SELECT * FROM release_version ORDER BY n").fetchall()


def version(c, n):
    return c.execute("SELECT * FROM release_version WHERE n=?", (n,)).fetchone()


def latest(c):
    return c.execute("SELECT * FROM release_version ORDER BY n DESC LIMIT 1").fetchone()


def add(c, text, who, source=None, client=None):
    """The next version, from its text; nothing a guest sees until both hosts sign it."""
    text = clean(text)
    if len(text) < 200:
        raise ReleaseError("that is not the release: fewer than 200 characters")
    last = latest(c)
    if last and last["text"] == text:
        raise ReleaseError(f"version {last['n']} already has exactly this text")
    n = (last["n"] + 1) if last else 1
    c.execute(
        "INSERT INTO release_version (n, sha256, title, text, created_by, created_at, source) VALUES (?,?,?,?,?,?,?)",
        (n, sha(text), title_of(text), text, who, ledger.now(), source),
    )
    ledger.event(c, "release:version", who, detail={"version": n, "sha256": sha(text), "source": source})
    mirror_version(c, n, client)
    return version(c, n)


def host_signatures(c, n):
    return c.execute("SELECT * FROM release_signature WHERE version=? AND role='host' ORDER BY id", (n,)).fetchall()


def signed_by(c, n):
    return {r["host"]: r for r in host_signatures(c, n)}


def waiting_for(c, n):
    return [h for h in hosts() if h not in signed_by(c, n)]


def issued(c):
    """The newest version every host has signed, or None: what a guest may be shown."""
    if not hosts():
        return None
    for v in c.execute("SELECT n FROM release_version ORDER BY n DESC"):
        if not waiting_for(c, v["n"]):
            return v["n"]
    return None


def mirror_version(c, n, client=None):
    v = version(c, n)
    rec = {
        "version": n,
        "sha256": v["sha256"],
        "title": v["title"],
        "created_by": v["created_by"],
        "created_at": v["created_at"],
        "source": v["source"],
        "signed": {r["host"]: {"at": r["at"], "address": r["address"], "ip": r["ip"]} for r in host_signatures(c, n)},
    }
    _put(c, client, f"release/v{n}.md", v["text"].encode("utf-8"), "text/markdown; charset=utf-8")
    _put(c, client, f"release/v{n}.json", json.dumps(rec, indent=1).encode("utf-8"), "application/json")


def _put(c, client, key, data, ctype, guest=None):
    """Bytes into the archive; a failure is an event, not a stop, since the ledger holds the same."""
    fd, tmp = tempfile.mkstemp()
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        (client or archive.Client()).put(key, tmp, ctype)
    except archive.ArchiveError as e:
        ledger.event(c, "release:mirror:failed", "engine", detail={"key": key, "error": str(e)[:300]}, guest=guest)
        return None
    finally:
        os.unlink(tmp)
    return key


# ─── signatures ──────────────────────────────────────────────────────────────
def check_png(image):
    """A PNG from a data URL or bytes, of a size a signature has; or a sentence."""
    if isinstance(image, str):
        m = re.fullmatch(r"data:image/png;base64,([A-Za-z0-9+/=\s]+)", image.strip())
        if not m:
            raise ReleaseError("Not a PNG image")
        try:
            data = base64.b64decode(m.group(1))
        except ValueError:
            raise ReleaseError("Not a PNG image") from None
    else:
        data = bytes(image or b"")
    if len(data) > MAX_PNG:
        raise ReleaseError("The image is too big: over 400 KB")
    if data[:8] != PNG or data[12:16] != b"IHDR":
        raise ReleaseError("Not a PNG image")
    w, h = struct.unpack(">II", data[16:24])
    if not (8 <= w <= 4000 and 8 <= h <= 4000):
        raise ReleaseError("Not an image of a size a signature has")
    return data


def host_image(c, host):
    return c.execute("SELECT * FROM host_signature WHERE host=?", (host,)).fetchone()


def save_host_image(c, host, who, image, client=None):
    """A host's drawn or uploaded signature, kept for every version they sign from now."""
    if host not in hosts():
        raise ReleaseError(f"This engine signs as {' and '.join(hosts())}; you are {who}")
    png = check_png(image)
    c.execute(
        "INSERT INTO host_signature (host, address, png, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(host) DO UPDATE SET address=excluded.address, png=excluded.png, updated_at=excluded.updated_at",
        (host, who, png, ledger.now()),
    )
    ledger.event(c, "release:host_image", who, detail={"host": host, "bytes": len(png)})
    _put(c, client, f"release/hosts/{host.lower()}.png", png, "image/png")


def sign_as_host(c, host, who, n, ip, ua, client=None):
    """One host's signature on one version, once; the image on file at the time is what goes on it."""
    if host not in hosts():
        raise ReleaseError(f"This engine signs as {' and '.join(hosts())}; you are {who}")
    v = version(c, n)
    if not v:
        raise ReleaseError(f"There is no version {n}")
    img = host_image(c, host)
    if not img:
        raise ReleaseError("Draw or upload your signature first")
    if host in signed_by(c, n):
        raise ReleaseError(f"You signed version {n} already")
    c.execute(
        "INSERT INTO release_signature (version, role, host, guest, name, address, png, ip, ua, at, sha256) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (n, "host", host, None, host, who, img["png"], ip, (ua or "")[:300], ledger.now(), v["sha256"]),
    )
    ledger.event(c, "release:signed", who, detail={"version": n, "host": host, "ip": ip})
    mirror_version(c, n, client)
    return not waiting_for(c, n)


def guest_signature(c, gid):
    return c.execute(
        "SELECT * FROM release_signature WHERE guest=? AND role='guest' ORDER BY id DESC LIMIT 1", (gid,)
    ).fetchone()


def signatures(c, g, n):
    """The hosts' signatures on the version, then the guest's, if any."""
    rows = host_signatures(c, n)
    mine = c.execute(
        "SELECT * FROM release_signature WHERE guest=? AND version=? AND role='guest' ORDER BY id DESC LIMIT 1",
        (g["id"], n),
    ).fetchone()
    return [*rows, mine] if mine else rows


def sign_as_guest(c, g, name, image, agreed, ip, ua, client=None):
    """The guest's signature on the issued version, with its record; then the
    copies and the mail. Returns the guest row as it is now, the version,
    the archive keys the copies got, and whether the mail went."""
    n = issued(c)
    if not n:
        raise ReleaseError("The release is not ready to sign yet")
    name = " ".join((name or "").split())[:80]
    if len(name) < 2:
        raise ReleaseError("Type your full name")
    if not agreed:
        raise ReleaseError("Tick the box to say you have read it and agree")
    png = check_png(image)
    v, at = version(c, n), ledger.now()
    c.execute(
        "INSERT INTO release_signature (version, role, host, guest, name, address, png, ip, ua, at, sha256) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (n, "guest", None, g["id"], name, g["address"], png, ip, (ua or "")[:300], at, v["sha256"]),
    )
    c.execute("UPDATE guest SET signed_at=?, signed_version=?, updated_at=? WHERE id=?", (at, n, at, g["id"]))
    ledger.event(
        c,
        "guest:signed",
        g["address"],
        detail={"version": n, "name": name, "ip": ip, "ua": (ua or "")[:300]},
        guest=g["id"],
    )
    g = c.execute("SELECT * FROM guest WHERE id=?", (g["id"],)).fetchone()
    keys, pdf = copies(c, g, n, client)
    sent = send_copy(c, g, n, pdf)
    return g, n, keys, sent


def signature_png(c, g, which):
    """A signature image for the guest's page: a host's on the version the guest sees, or "mine"."""
    n = g["signed_version"] or issued(c)
    if not n:
        return None
    if which == "mine":
        row = next((r for r in signatures(c, g, n) if r["role"] == "guest"), None)
    else:
        row = signed_by(c, n).get(which)
    return row["png"] if row else None


# ─── the copies ──────────────────────────────────────────────────────────────
def record(c, g, n):
    v = version(c, n)
    sigs = [
        {
            "role": r["role"],
            "name": r["name"],
            "host": r["host"],
            "address": r["address"] if r["role"] == "guest" else None,
            "at": r["at"],
            "ip": r["ip"],
            "ua": r["ua"],
        }
        for r in signatures(c, g, n)
    ]
    return {
        "guest": {"id": g["id"], "name": g["name"], "address": g["address"], "issue": g["issue"]},
        "version": {"n": n, "sha256": v["sha256"], "title": v["title"], "created_at": v["created_at"]},
        "text": v["text"],
        "signatures": sigs,
        "office": office(),
        "copies": {k: f"guests/{g['id']}/release-v{n}.{k}" for k in ("pdf", "html", "json")},
    }


def record_rows(c, g, n):
    """The record as people read it, on the copy's last page."""
    v = version(c, n)
    mine = next((r for r in signatures(c, g, n) if r["role"] == "guest"), None)
    rows = [
        ("Document", f"{v['title']}, version {n}"),
        ("Text's sha256", v["sha256"]),
        ("Guest", mine["name"] if mine else g["name"]),
        ("Email address", f"{g['address']} (verified by a code emailed to it)"),
    ]
    if mine:
        rows += [("Signed at", stamp(mine["at"])), ("From", mine["ip"] or ""), ("Device", (mine["ua"] or "")[:200])]
    for r in host_signatures(c, n):
        rows.append((f"{r['name']}, for AgentSee", f"signed this version on {stamp(r['at'])}"))
    rows.append(("Copies", f"guests/{g['id']}/release-v{n}.pdf, .html and .json in AgentSee's archive"))
    return rows


def blocks(text):
    """The text's shape: a title, headings, paragraphs, list items."""
    out, para = [], []

    def flush():
        if para:
            out.append(("p", " ".join(para)))
            para.clear()

    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            flush()
        elif s.startswith("# "):
            flush()
            out.append(("h1", s[2:].strip()))
        elif s.startswith("## "):
            flush()
            out.append(("h2", s[3:].strip()))
        elif s.startswith(("- ", "* ")):
            flush()
            out.append(("li", s[2:].strip()))
        else:
            para.append(s)
    flush()
    return out


def render_html(text):
    out, in_list = [], False
    for kind, t in blocks(text):
        if kind == "li" and not in_list:
            out.append("<ul>")
            in_list = True
        elif kind != "li" and in_list:
            out.append("</ul>")
            in_list = False
        out.append(f"<{kind}>{esc(t)}</{kind}>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def signature_block(rows, src):
    parts = []
    for r in rows:
        who = r["name"] if r["role"] == "guest" else f"{r['name']}, for AgentSee"
        parts.append(
            f'<figure class="signature"><img src="{src(r)}" alt="{esc(r["name"])}&#8217;s signature" width="320" height="120">'
            f"<figcaption><b>{esc(who)}</b><br>signed on {esc(when(r['at']))}</figcaption></figure>"
        )
    return '<div class="signatures">' + "".join(parts) + "</div>"


def html_copy(c, g, n):
    """The signed copy as one HTML file: the text, the signatures as images inside it, the record."""
    v = version(c, n)
    rows = signatures(c, g, n)
    sigs = signature_block(rows, lambda r: "data:image/png;base64," + base64.b64encode(r["png"]).decode())
    rec = "".join(f"<tr><td>{esc(k)}</td><td>{esc(val)}</td></tr>" for k, val in record_rows(c, g, n))
    style = (
        "body{font:16px/1.55 Georgia,'Times New Roman',serif;color:#1a1a1a;background:#fff;margin:0;padding:2rem 1.2rem}"
        "main{max-width:40rem;margin:0 auto}h1{font-size:1.7rem}h2{font-size:1.05rem;margin:1.3rem 0 .3rem}"
        ".signatures{display:flex;flex-wrap:wrap;gap:1.4rem;margin:1rem 0}.signatures figure{margin:0;max-width:15rem}"
        ".signatures img{display:block;width:100%;height:auto;border-bottom:1px solid #333}figcaption{font-size:.9rem;margin-top:.3rem}"
        "table{border-collapse:collapse;font-size:.9rem}td{padding:.2rem .8rem .2rem 0;vertical-align:top;overflow-wrap:anywhere}"
        "td:first-child{color:#555;white-space:nowrap}"
    )
    return (
        '<!doctype html>\n<html lang="en-GB"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        f"<title>{esc(v['title'])}, version {n}, signed by {esc(g['name'])}</title><style>{style}</style></head>"
        f'<body><main><article class="release">{render_html(v["text"])}</article>'
        f"<h2>Signed</h2>{sigs}<h2>The record</h2><table>{rec}</table></main></body></html>\n"
    )


def pdf_copy(c, g, n):
    """The signed copy as a PDF: the text, a page of signatures, the record. fpdf2, with a
    TrueType font when the machine has one (the engine's image does), so a name in any
    alphabet is set as written; without one, Helvetica and the Latin alphabet."""
    try:
        from fpdf import FPDF
        from fpdf.enums import XPos, YPos
    except ImportError:
        raise ReleaseError("fpdf2 is not installed in this interpreter (engine/home/Dockerfile pins it)") from None
    v = version(c, n)
    rows = signatures(c, g, n)
    mine = next((r for r in rows if r["role"] == "guest"), None)
    font = next((f for f in FONTS if os.path.exists(f)), None)
    try:
        pdf = FPDF(format="A4")
        pdf.set_margins(22, 20, 22)
        pdf.set_auto_page_break(True, margin=20)
        if font:
            pdf.add_font("Body", fname=font)
            bold = font.replace(".ttf", "-Bold.ttf")
            pdf.add_font("Body", style="B", fname=bold if os.path.exists(bold) else font)
            fam, fix = "Body", str
        else:
            fam, fix = "Helvetica", (lambda s: s.encode("latin-1", "replace").decode("latin-1"))

        def put(text, height, style="", size=10):  # a paragraph, the cursor back at the left margin after it
            pdf.set_font(fam, style, size)
            pdf.multi_cell(0, height, fix(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        pdf.set_title(fix(f"{v['title']}, version {n}"))
        pdf.set_author("AgentSee")
        pdf.set_subject(fix(f"Signed by {mine['name'] if mine else g['name']}"))
        pdf.set_creation_date(dt.datetime.fromisoformat(mine["at"] if mine else v["created_at"]))
        pdf.add_page()
        for kind, t in blocks(v["text"]):
            if kind == "h1":
                put(t, 8, "B", 17)
                pdf.ln(3)
            elif kind == "h2":
                pdf.ln(2)
                put(t, 6.5, "B", 11.5)
                pdf.ln(1)
            else:
                put(("-  " + t) if kind == "li" else t, 5.2)
                pdf.ln(1 if kind == "li" else 2)
        pdf.add_page()
        put("Signed", 7, "B", 13)
        pdf.ln(2)
        for r in rows:
            pdf.image(io.BytesIO(r["png"]), w=55)
            who = r["name"] if r["role"] == "guest" else f"{r['name']}, for AgentSee"
            put(f"{who}, on {when(r['at'])}", 5.6)
            pdf.ln(5)
        pdf.ln(2)
        put("The record", 6.5, "B", 11.5)
        pdf.ln(1)
        for k, val in record_rows(c, g, n):
            put(f"{k}: {val}", 5, "", 9)
        return bytes(pdf.output())
    except Exception as e:  # noqa: BLE001 — the signature stands whatever the renderer says; the copy is said to be missing
        raise ReleaseError(f"the PDF could not be made: {type(e).__name__}: {str(e)[:200]}") from e


def copies(c, g, n, client=None):
    """The three copies into the archive. The PDF's absence is an event, not a stop:
    the signature is in the ledger either way, and the HTML copy goes in its place."""
    keys = {}
    rec = json.dumps(record(c, g, n), indent=1).encode("utf-8")
    keys["json"] = _put(c, client, f"guests/{g['id']}/release-v{n}.json", rec, "application/json", g["id"])
    page = html_copy(c, g, n).encode("utf-8")
    keys["html"] = _put(c, client, f"guests/{g['id']}/release-v{n}.html", page, "text/html; charset=utf-8", g["id"])
    try:
        pdf = pdf_copy(c, g, n)
    except ReleaseError as e:
        ledger.event(c, "release:pdf:failed", "engine", detail=str(e)[:300], guest=g["id"])
        pdf = None
    else:
        keys["pdf"] = _put(c, client, f"guests/{g['id']}/release-v{n}.pdf", pdf, "application/pdf", g["id"])
    return keys, pdf


def send_copy(c, g, n, pdf):
    text = COPY_MAIL.format(name=g["name"].split()[0], n=n, when=when(g["signed_at"]), host=office())
    if pdf:
        att = [(f"agentsee-release-v{n}.pdf", pdf, "application/pdf")]
    else:
        att = [(f"agentsee-release-v{n}.html", html_copy(c, g, n).encode("utf-8"), "text/html")]
    try:
        msgid = mail.send(
            g["address"], "Your signed release for the AgentSee podcast", text, attachments=att, copy=True
        )
    except mail.MailError as e:
        mail.record(c, "see", g["address"], "release copy", error=e, guest=g["id"])
        return False
    mail.record(c, "see", g["address"], "release copy", msgid, guest=g["id"])
    return True


# ─── the pages ───────────────────────────────────────────────────────────────
def pad(action, button_text, csrf=None, name_field=""):
    """The signature pad: a canvas drawn with a finger or a mouse, or a photo of a
    signature placed on it (static/sign.js); the PNG travels in a hidden field."""
    token = f'<input type="hidden" name="_t" value="{esc(csrf)}">' if csrf else ""
    return (
        f'<form method="post" action="{action}" class="block sign" data-sign>{token}{name_field}'
        '<label for="pad">Your signature</label>'
        '<canvas id="pad" class="sig" width="640" height="240" aria-label="Draw your signature here"></canvas>'
        '<div class="row pad-row"><button type="button" data-clear>Clear</button>'
        '<label class="file">Or a photo of it <input type="file" accept="image/*"></label></div>'
        '<p class="honesty" data-empty hidden><b>Draw your signature in the box, or choose a photo of it.</b></p>'
        '<input type="hidden" name="signature" value=""><input type="hidden" name="strokes" value="0">'
    ) + button_text


def you_page(c, host, who, csrf, msg=None):
    """A host's profile on the engine host: their signature on file, and the release's versions to sign."""
    note = f'<p class="honesty"><b>{esc(msg)}</b></p>' if msg else ""
    if host not in hosts():
        body = (
            f'<article class="lead"><h1>You</h1><p class="deck">This engine signs as {esc(" and ".join(hosts()))}; '
            f"you are {esc(who)}, so there is nothing to sign here.</p></article>{note}"
        )
        return "You", body
    img = host_image(c, host)
    on_file = (
        f'<figure class="signature"><img src="/you/signature.png?v={esc(img["updated_at"])}" alt="Your signature" '
        f'width="320" height="120"><figcaption>On file since {esc(when(img["updated_at"]))}</figcaption></figure>'
        if img
        else '<p class="muted">No signature on file yet.</p>'
    )
    form = pad(
        "/you/signature",
        '<p><button type="submit" class="signal">Save this signature</button></p>'
        '<p class="muted">Drawn with a finger or a mouse, or a photo of the one you use on paper. It is kept in the '
        "archive and placed on each version you sign from now; a version already signed keeps the image it was "
        "signed with.</p></form>",
        csrf,
    )
    cur = issued(c)
    rows = []
    for v in reversed(versions(c)):
        signed = signed_by(c, v["n"])
        waiting = waiting_for(c, v["n"])
        if v["n"] == cur:
            state = '<span class="status-word status-published">issued</span>'
        elif waiting:
            state = f'<span class="muted">waiting for {esc(", ".join(waiting))}</span>'
        else:
            state = '<span class="muted">superseded</span>'
        act = ""
        if host not in signed:
            act = button(f"/you/sign/{v['n']}", f"Sign version {v['n']}", csrf, "signal") if img else "draw yours first"
        rows.append(
            [
                f"v{v['n']}",
                esc(v["title"]),
                f'<span class="mono">{esc(v["sha256"][:12])}</span>',
                f"{esc(when(v['created_at']))} by {esc(v['created_by'])}",
                ", ".join(f"{esc(h)} on {esc(when(r['at'], '%d %b'))}" for h, r in signed.items()) or "nobody yet",
                state,
                act,
            ]
        )
    last = latest(c)
    text = (
        section(
            "Text",
            f"Version {last['n']}, as a guest will read it",
            f'<article class="release">{render_html(last["text"])}</article>',
        )
        if last
        else ""
    )
    body = (
        f'<article class="lead"><h1>{esc(host)}</h1><p class="deck">Signed in as {esc(who)}. Your signature, '
        "and the release's versions: a guest is shown a version only once both of you have signed it, and "
        "the copy they walk away with carries your signatures beside theirs.</p></article>"
        + note
        + section("Signature", "Yours, on file", on_file + form)
        + section(
            "Release",
            "Versions",
            table(["Version", "Title", "Hash", "Made", "Signed by", "State", ""], rows)
            if rows
            else '<p>No versions yet: <span class="mono">engine.py release add --issue AGE-55</span> takes the text in.</p>',
        )
        + text
    )
    return "You", body


def release_page(c, g, msg=None, status=200):
    """The guest's page: the issued version to read and sign, or the one they signed, with its copy."""
    note = f'<p class="honesty"><b>{esc(msg)}</b></p>' if msg else ""
    n = g["signed_version"] or issued(c)
    if not n:
        body = (
            '<article class="lead"><p class="kicker">The release</p><h1>Not quite ready</h1>'
            '<p class="deck">James and Abrar are signing this version. Come back in a day or two and it is here, '
            f"with their signatures on it.</p></article>{note}<p>{link('/office', 'Back to the office')}</p>"
        )
        return "The release", body, status
    v = version(c, n)
    rows = signatures(c, g, n)
    mine = next((r for r in rows if r["role"] == "guest"), None)
    sigs = signature_block(
        rows, lambda r: "/release/signature/" + ("mine" if r["role"] == "guest" else r["host"]) + ".png"
    )
    text = f'<article class="release">{render_html(v["text"])}</article>'
    if mine:
        after = (
            f"<p>Signed on {esc(when(mine['at']))} from {esc(mine['address'])}. A copy with all three signatures and "
            f"the record went to that address. {link('/release.pdf', 'Your copy, as a PDF')}</p>"
            f"<p>{link('/office', 'Back to the office')}</p>"
        )
        body = (
            f'<article class="lead"><p class="kicker">The release</p><h1>Signed</h1><p class="deck">Version {n}, '
            f"by James, Abrar and you.</p></article>{note}{text}" + section("Signed", "All three", sigs + after)
        )
        return "The release, signed", body, status
    name_field = (
        '<label for="name">Your full name</label>'
        f'<input type="text" id="name" name="name" value="{esc(g["name"])}" required maxlength="80" autocomplete="name">'
    )
    form = pad(
        "/release/sign",
        '<label class="tick"><input type="checkbox" name="agree" value="yes" required> I have read this and I agree to it.</label>'
        '<p><button type="submit" class="signal">Sign the release</button></p>'
        '<p class="muted">Recorded with your signature: your name as typed, this email address, the time, your '
        "internet address and your device, and the text's version and its hash. That record is what makes a "
        "signature made this way worth something. The copy comes to you by email.</p></form>",
        None,
        name_field,
    )
    body = (
        f'<article class="lead"><p class="kicker">The release</p><h1>{esc(v["title"])}</h1><p class="deck">One page, '
        "in plain words. James and Abrar have signed it; read it, then sign it yourself, and a copy with all three "
        f"signatures is yours.</p></article>{note}{text}"
        + section("Signed by", "AgentSee", sigs)
        + section("Sign", "You", form)
    )
    return "The release", body, status
