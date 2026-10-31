"""
The idea conversation (AGE-110): a guest who has signed the release sits
down with the office and fleshes the episode out, one question at a time,
into a run-sheet draft the hosts can record from. The skill
.claude/skills/guest-episode/SKILL.md is the one source: the system prompt
the engine runs, and the file a guest takes to their own assistant instead.

The Claude API over urllib, like every other platform here: the key from
the vault (ANTHROPIC_API_KEY, the item "Anthropic podcast key"), the model
and the caps from config.json ("idea"), the reply streamed to the page as
server-sent events (static/chat.js) and shown whole to a browser without
script. The controls are what make hosting this safe: only a signed-in
guest who has signed the release; forty turns and a token budget per
guest; no tools; the model sees this guest's conversation and nothing
else; the whole conversation is kept on the guest's issue as one comment
in the guest's words, and the run-sheet draft is its own comment the guest
can revise. The worst case is one named guest spending a few pounds.
"""

import datetime as dt
import html
import json
import os
import pathlib
import re
import threading
import urllib.error
import urllib.request
import zoneinfo

import archive
import ledger
import linear
from pages import esc, link, section, when

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude" / "skills" / "guest-episode" / "SKILL.md"
SITE_INDEX = ROOT / "public" / "index.html"
API = "https://api.anthropic.com/v1/messages"
VERSION = "2023-06-01"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
NEEDS = "ANTHROPIC_API_KEY"
TIMEOUT = 180
MAX_SAY = 4000  # characters in one message from the guest
MAX_BRIEF = 20000
MAX_HEADSHOT = 6_000_000
START = "(The guest has sat down in the office. Begin.)"
IMAGE = {b"\xff\xd8\xff": ("image/jpeg", "jpg"), b"\x89PNG\r\n\x1a\n": ("image/png", "png")}
_locks, _guard = {}, threading.Lock()  # one reply at a time per guest


class IdeaError(Exception):
    pass


def ready(env):
    return bool(env.get("ANTHROPIC_API_KEY"))


def settings():
    s = {
        "model": "claude-opus-5-5",
        "max_turns": 40,
        "max_tokens": 1500,
        "token_budget": 400000,
        "effort": "medium",
        "fallbacks": True,
    }
    s.update(ledger.CONFIG.get("idea") or {})
    return s


def office():
    return (ledger.CONFIG.get("guest") or {}).get("host", "see.agentsee.work")


# ─── the skill, and what the model is told ───────────────────────────────────
def questions():
    """The site's ten questions, from its front page, as tools/release.py reads them."""
    try:
        src = SITE_INDEX.read_text(encoding="utf-8")
    except OSError:
        return []
    m = re.search(r'<ol class="questions">(.*?)</ol>', src, re.S)
    if not m:
        return []
    items = re.findall(r"<li[^>]*>(.*?)</li>", m.group(1), re.S)
    return [" ".join(html.unescape(re.sub(r"<[^>]+>", "", it)).split()) for it in items]


def skill_text():
    """The skill without its frontmatter, with the ten questions in it."""
    text = SKILL.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        text = text[end + 5 :] if end >= 0 else text
    qs = "\n".join(f"{n}. {q}" for n, q in enumerate(questions(), 1)) or "(see https://agentsee.work)"
    return text.replace("<!-- questions -->", qs).strip() + "\n"


def system_prompt(g):
    today = dt.datetime.now(ledger.ZONE).strftime("%-d %B %Y")
    return (
        skill_text()
        + "\n## This guest\n\n"
        + f"You are inside the office at {office()} with {g['name']}, who has signed the release. Today is {today}. "
        "The office has its own form for the name as shown, the line, the link, the headshot and the time zone, so "
        "skip questions 21 and 22 unless they ask. When you write the run-sheet draft, put it in one fenced block "
        'beginning "# Run-sheet draft:", exactly as above; the office saves it and sends it to James and Abrar, and '
        "says so to the guest. You have no tools and no memory beyond this conversation.\n"
    )


# ─── the wire ────────────────────────────────────────────────────────────────
def open_stream(url, data, headers, timeout=TIMEOUT):
    """The streaming response, or the error response, to read line by line; the tests put their own here."""
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        return e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise IdeaError(f"could not reach {url}: {e}") from e


def said(status, body):
    try:
        j = json.loads(body)
        err = j.get("error") or {}
        return f"{status}: {err.get('type', 'error')}: {err.get('message', '')}".strip(": ")
    except ValueError, TypeError, AttributeError:
        return f"{status}: {body[:300]!r}"


def request(env, system, messages, on_delta=lambda s: None):
    """One reply, streamed: the text, the usage, the stop reason. No tools, ever."""
    cfg = settings()
    payload = {
        "model": cfg["model"],
        "max_tokens": int(cfg["max_tokens"]),
        "stream": True,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": messages,
        "output_config": {"effort": cfg["effort"]},
    }
    headers = {
        "Content-Type": "application/json",
        "x-api-key": env["ANTHROPIC_API_KEY"],
        "anthropic-version": VERSION,
        "Accept": "text/event-stream",
    }
    if cfg.get("fallbacks"):
        payload["fallbacks"] = "default"
        headers["anthropic-beta"] = FALLBACK_BETA
    resp = open_stream(API, json.dumps(payload).encode("utf-8"), headers)
    status = getattr(resp, "status", None) or getattr(resp, "code", 0)
    if status != 200:
        with resp:
            raise IdeaError("the assistant would not answer: " + said(status, resp.read()))
    text, usage, stop, model = [], {"input": 0, "output": 0}, None, cfg["model"]
    with resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line.startswith("data:"):
                continue
            try:
                d = json.loads(line[5:].strip())
            except ValueError:
                continue
            kind = d.get("type")
            if kind == "message_start":
                u = d.get("message", {}).get("usage", {}) or {}
                model = d.get("message", {}).get("model", model)
                usage["input"] = sum(
                    int(u.get(k) or 0)
                    for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
                )
            elif kind == "content_block_delta" and (d.get("delta") or {}).get("type") == "text_delta":
                s = d["delta"].get("text", "")
                text.append(s)
                on_delta(s)
            elif kind == "message_delta":
                usage["output"] = int((d.get("usage") or {}).get("output_tokens") or usage["output"])
                stop = (d.get("delta") or {}).get("stop_reason", stop)
            elif kind == "error":
                raise IdeaError("the assistant stopped: " + str((d.get("error") or {}).get("message", d)))
            elif kind == "message_stop":
                break
    if stop == "refusal" and not text:
        raise IdeaError("the assistant declined to answer that; say it another way")
    return "".join(text), usage, stop, model


# ─── the conversation ────────────────────────────────────────────────────────
def turns(c, gid):
    return c.execute("SELECT * FROM idea_turn WHERE guest=? ORDER BY id", (gid,)).fetchall()


def exchanges(c, gid):
    return c.execute("SELECT COUNT(*) FROM idea_turn WHERE guest=? AND role='guest'", (gid,)).fetchone()[0]


def add_turn(c, gid, role, text, usage=None, stop=None, model=None):
    u = usage or {}
    c.execute(
        "INSERT INTO idea_turn (guest, role, text, at, input_tokens, output_tokens, model, stop) VALUES (?,?,?,?,?,?,?,?)",
        (gid, role, text, ledger.now(), u.get("input"), u.get("output"), model, stop),
    )


def conversation(c, gid):
    """The turns as the API takes them: the guest's and the start as the user, the office's as the assistant,
    neighbours of one role joined, so a reply that never came does not leave two in a row."""
    out = []
    for t in turns(c, gid):
        role = "assistant" if t["role"] == "agent" else "user"
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + t["text"]
        else:
            out.append({"role": role, "content": t["text"]})
    return out


def allowed(c, g, env):
    """Why this guest may not talk now, as a sentence, or None."""
    cfg = settings()
    if not g["signed_version"]:
        return "The release comes first: read and sign it, then the idea."
    if not ready(env):
        return f"The office's assistant is not set up on this engine (missing {NEEDS}); take the questions to your own instead."
    if exchanges(c, g["id"]) >= int(cfg["max_turns"]):
        return f"That is the office's limit of {cfg['max_turns']} turns. The draft so far is saved; change it below, or finish with your own assistant."
    if (g["idea_tokens"] or 0) >= int(cfg["token_budget"]):
        return "The conversation has used all the office set aside for it. The draft so far is saved; change it below, or finish with your own assistant."
    return None


def reply(c, g, env, text=None, on_delta=lambda s: None, start=False):
    """The guest's message in, the office's reply out, streamed through on_delta and kept whole.
    Then the caps, the issue and, when the reply holds the run-sheet, the draft."""
    why = allowed(c, g, env)
    if why:
        raise IdeaError(why)
    gid = g["id"]
    with _guard:
        lock = _locks.setdefault(gid, threading.Lock())
    if not lock.acquire(blocking=False):
        raise IdeaError("One thing at a time: the office is still writing its last reply.")
    try:
        if start:
            if turns(c, gid):
                raise IdeaError("The conversation has begun already.")
            add_turn(c, gid, "start", START)
        else:
            text = (text or "").strip()
            if not text:
                raise IdeaError("Say something first.")
            if len(text) > MAX_SAY:
                raise IdeaError(f"That is over {MAX_SAY} characters; a shorter message, please.")
            add_turn(c, gid, "guest", text)
        out, usage, stop, model = request(env, system_prompt(g), conversation(c, gid), on_delta)
        add_turn(c, gid, "agent", out, usage, stop, model)
        c.execute(
            "UPDATE guest SET idea_tokens = COALESCE(idea_tokens, 0) + ?, updated_at=? WHERE id=?",
            (usage["input"] + usage["output"], ledger.now(), gid),
        )
        ledger.event(
            c,
            "idea:turn",
            g["address"],
            detail={
                "exchange": exchanges(c, gid),
                "in": usage["input"],
                "out": usage["output"],
                "stop": stop,
                "model": model,
            },
            guest=gid,
        )
        brief = extract_brief(out)
        if brief:
            save_brief(c, g, brief, "office")
        if g["issue"]:
            linear.enqueue(c, "idea", {"guest": gid, "issue": g["issue"], "name": g["name"]}, guest=gid)
        return out, stop, bool(brief)
    finally:
        lock.release()


# ─── the draft ───────────────────────────────────────────────────────────────
def extract_brief(text):
    m = re.search(r"```[a-z]*\n(# Run-sheet draft:.*?)```", text or "", re.S)
    return m.group(1).strip() + "\n" if m else None


def save_brief(c, g, text, source):
    """The run-sheet draft on the guest, from the office or revised by the guest; to the issue either way."""
    text = (text or "").strip()
    if len(text) < 20:
        raise IdeaError("That is not a run-sheet.")
    if len(text) > MAX_BRIEF:
        raise IdeaError(f"That is over {MAX_BRIEF} characters.")
    if not text.startswith("# Run-sheet draft"):
        text = "# Run-sheet draft\n\n" + text
    at = ledger.now()
    c.execute("UPDATE guest SET brief=?, brief_at=?, updated_at=? WHERE id=?", (text, at, at, g["id"]))
    ledger.event(
        c,
        "idea:brief",
        g["address"] if source != "office" else "office",
        detail={"source": source, "chars": len(text)},
        guest=g["id"],
    )
    if g["issue"]:
        linear.enqueue(
            c,
            "brief",
            {"issue": g["issue"], "name": g["name"], "source": source, "text": text, "when": when(at)},
            guest=g["id"],
        )


# ─── about the guest ─────────────────────────────────────────────────────────
def zones():
    return sorted(z for z in zoneinfo.available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/")))


def save_about(c, g, form, files, client=None):
    """What the pipeline wants of the guest: the name as shown, a line, a link, a time zone, a headshot."""
    shown = " ".join((form.get("shown_name") or "").split())[:80] or g["name"]
    bio = " ".join((form.get("bio") or "").split())[:200]
    url = (form.get("link") or "").strip()[:200]
    if url and not re.match(r"^https?://[^\s]+$", url):
        raise IdeaError("The link should start with https://")
    tz = (form.get("tz") or "").strip()
    if tz and tz not in zoneinfo.available_timezones():
        raise IdeaError("That is not a time zone the office knows")
    at = ledger.now()
    c.execute(
        "UPDATE guest SET shown_name=?, bio=?, link=?, tz=?, updated_at=? WHERE id=?",
        (shown, bio, url, tz, at, g["id"]),
    )
    key = None
    shot = files.get("headshot") if files else None
    if shot and shot[1]:
        name, data, _ = shot
        kind = next((v for magic, v in IMAGE.items() if data.startswith(magic)), None)
        if not kind:
            raise IdeaError("The headshot should be a JPEG or a PNG")
        if len(data) > MAX_HEADSHOT:
            raise IdeaError("The headshot is over 6 MB; a smaller one, please")
        c.execute(
            "INSERT INTO guest_file (guest, kind, name, type, data, at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(guest, kind) DO UPDATE SET name=excluded.name, type=excluded.type, data=excluded.data, at=excluded.at",
            (g["id"], "headshot", name[:120], kind[0], data, at),
        )
        key = _put(c, client, f"guests/{g['id']}/headshot.{kind[1]}", data, kind[0], g["id"])
    ledger.event(c, "idea:about", g["address"], detail={"shown": shown, "tz": tz, "headshot": key}, guest=g["id"])
    if g["issue"]:
        linear.enqueue(
            c,
            "about",
            {
                "issue": g["issue"],
                "name": g["name"],
                "shown": shown,
                "bio": bio,
                "link": url,
                "tz": tz,
                "headshot": key,
                "when": when(at),
            },
            guest=g["id"],
        )


def headshot(c, gid):
    return c.execute("SELECT * FROM guest_file WHERE guest=? AND kind='headshot'", (gid,)).fetchone()


def _put(c, client, key, data, ctype, guest=None):
    import tempfile

    fd, tmp = tempfile.mkstemp()
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        (client or archive.Client()).put(key, tmp, ctype)
    except archive.ArchiveError as e:
        ledger.event(c, "idea:mirror:failed", "engine", detail={"key": key, "error": str(e)[:300]}, guest=guest)
        return None
    finally:
        os.unlink(tmp)
    return key


# ─── the issue ───────────────────────────────────────────────────────────────
def quoted(text):
    return "\n".join("> " + ln for ln in (text or "").strip().splitlines() or [""])


def transcript(c, g):
    rows = [t for t in turns(c, g["id"]) if t["role"] != "start"]
    first = g["name"].split()[0]
    head = (
        f"**{linear.plain(g['name'])}'s conversation with the office, through [{office()}](https://{office()}); "
        f"{exchanges(c, g['id'])} exchanges, the last at {when(rows[-1]['at']) if rows else ''}.** "
        "Their words are theirs; the office's are the engine's, from the guest-episode skill.\n\n"
    )
    parts = []
    for t in rows:
        who = "The office" if t["role"] == "agent" else linear.plain(first)
        parts.append(f"> **{who}:**\n" + quoted(t["text"]))
    return head + "\n>\n".join(parts)


def job_transcript(env, a, c):
    """The whole conversation so far, as the one comment the office keeps up to date."""
    g = c.execute("SELECT * FROM guest WHERE id=?", (a["guest"],)).fetchone()
    if not g:
        raise linear.LinearError(f"no guest {a['guest']}")
    body = transcript(c, g)
    if g["idea_comment"]:
        cm = linear.update_comment(env, g["idea_comment"], body)
    else:
        cm = linear.comment(env, g["issue"], body)
        c.execute("UPDATE guest SET idea_comment=? WHERE id=?", (cm["id"], g["id"]))
    return {"issue": g["issue"], "comment": cm["url"], "exchanges": exchanges(c, g["id"])}


def job_brief(env, a, c=None):
    """The run-sheet draft as its own comment, quoted, and the label."""
    by = "written by the office from what they said" if a["source"] == "office" else "as they revised it"
    head = f"**The run-sheet draft for {linear.plain(a['name'])}, {by}, {a['when']}:**\n\n"
    cm = linear.comment(env, a["issue"], head + quoted(a["text"]))
    linear.label(env, a["issue"], "idea-drafted")
    return {"issue": a["issue"], "comment": cm["url"], "label": "idea-drafted"}


def job_about(env, a, c=None):
    lines = [
        f"Name, as shown: {a['shown']}",
        f"Line: {a.get('bio') or ''}",
        f"Link: {a.get('link') or ''}",
        f"Time zone: {a.get('tz') or ''}",
    ]
    if a.get("headshot"):
        lines.append(f"Headshot: {a['headshot']} in the archive")
    body = (
        f"**About {linear.plain(a['name'])}, as they gave it through [{office()}](https://{office()}), {a['when']}:**\n\n"
        + quoted("\n".join(lines))
    )
    cm = linear.comment(env, a["issue"], body)
    return {"issue": a["issue"], "comment": cm["url"]}


# ─── the page ────────────────────────────────────────────────────────────────
def inline(s):
    """Escaped text with **bold** kept."""
    s = esc(s)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)


def turn_html(role, text):
    """One turn as HTML: the guest's as paragraphs; the office's with headings, lists, bold and fenced blocks."""
    if role != "agent":
        paras = [p for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]
        return '<div class="turn guest">' + "".join(f"<p>{esc(p)}</p>" for p in paras) + "</div>"
    out, para, in_list = [], [], False

    def flush():
        nonlocal in_list
        if para:
            if in_list:
                out.append("</ul>")
                in_list = False
            s = " ".join(para)
            cls = ' class="progress"' if re.match(r"^Question \d+ of", s) else ""
            out.append(f"<p{cls}>{inline(s)}</p>")
            para.clear()

    lines = (text or "").strip().splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if s.startswith("```"):
            flush()
            j = i + 1
            block = []
            while j < len(lines) and not lines[j].strip().startswith("```"):
                block.append(lines[j])
                j += 1
            out.append(f'<pre class="brief">{esc(chr(10).join(block))}</pre>')
            i = j + 1
            continue
        if not s:
            flush()
            if in_list:
                out.append("</ul>")
                in_list = False
        elif s.startswith("# "):
            flush()
            out.append(f"<h2>{inline(s[2:])}</h2>")
        elif s.startswith("## "):
            flush()
            out.append(f"<h3>{inline(s[3:])}</h3>")
        elif re.match(r"^[-*] ", s) or re.match(r"^\(?[a-f]\) ", s):
            flush()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(re.sub(r'^[-*] ', '', s))}</li>")
        else:
            para.append(s)
        i += 1
    flush()
    if in_list:
        out.append("</ul>")
    return '<div class="turn agent">' + "".join(out) + "</div>"


def idea_page(c, g, env, msg=None, status=200):
    note = f'<p class="honesty"><b>{esc(msg)}</b></p>' if msg else ""
    if not g["signed_version"]:
        body = (
            '<article class="lead"><p class="kicker">The idea</p><h1>The release comes first</h1>'
            f'<p class="deck">Read and sign it, then come back here. {link("/release", "The release")}</p></article>{note}'
        )
        return "The idea", body, status
    rows = [t for t in turns(c, g["id"]) if t["role"] != "start"]
    why = allowed(c, g, env)
    log = "".join(turn_html(t["role"], t["text"]) for t in rows)
    if why and not rows:
        talk = f'<p class="honesty">{esc(why)}</p>'
    elif why:
        talk = f'<p class="honesty">{esc(why)}</p>'
    elif not rows:
        talk = (
            '<form method="post" action="/idea/say" class="block say" data-say><input type="hidden" name="start" value="1">'
            '<p><button type="submit" class="signal">Begin</button></p></form>'
        )
    else:
        talk = (
            '<form method="post" action="/idea/say" class="block say" data-say>'
            '<label for="text">Your answer</label>'
            f'<textarea id="text" name="text" rows="3" maxlength="{MAX_SAY}" required></textarea>'
            '<p><button type="submit" class="signal">Send</button></p></form>'
        )
    cfg = settings()
    about = about_form(c, g)
    brief = g["brief"] or ""
    draft = (
        f"<p>Saved {esc(when(g['brief_at']))} and sent to James and Abrar. Change anything; a change goes to them too.</p>"
        if brief
        else '<p class="muted">The office writes it at the end of the conversation. Or paste one here, from your own assistant.</p>'
    )
    draft += (
        '<form method="post" action="/idea/brief" class="block"><label for="brief">The run-sheet draft</label>'
        f'<textarea id="brief" name="brief" rows="14" maxlength="{MAX_BRIEF}">{esc(brief)}</textarea>'
        '<p><button type="submit" class="signal">Save the draft</button></p></form>'
    )
    elsewhere = (
        f"<p>{link('/idea/skill.md', 'The questions, as a file')} for Claude, ChatGPT or whichever assistant you keep: open "
        "a chat, attach or paste the file, and it asks you the same questions. Paste the run-sheet it writes into the box "
        "above.</p>"
    )
    body = (
        '<article class="lead"><p class="kicker">The idea</p><h1>Flesh it out</h1>'
        '<p class="deck">A conversation with the office, one question at a time, that ends in a run-sheet draft for '
        f"James and Abrar. About twenty questions; {cfg['max_turns']} turns at most. Everything you say here is kept on "
        "your episode's page for the hosts, in your words.</p></article>"
        + note
        + section("Conversation", "With the office", f'<div class="chat" data-log>{log}</div>{talk}')
        + section("Run-sheet", "The draft", draft)
        + section("About you", "For the page and the schedule", about)
        + section("Elsewhere", "Your own assistant instead", elsewhere)
        + f"<p>{link('/office', 'Back to the office')}</p>"
    )
    return "The idea", body, status


def about_form(c, g):
    shot = headshot(c, g["id"])
    img = (
        f'<img class="headshot" src="/idea/headshot?v={esc(shot["at"])}" alt="Your headshot" width="120" height="120">'
        if shot
        else ""
    )
    options = "".join(
        f'<option value="{esc(z)}"{" selected" if z == (g["tz"] or "Europe/London") else ""}>{esc(z)}</option>'
        for z in zones()
    )
    return (
        '<form method="post" action="/idea/about" enctype="multipart/form-data" class="block about">'
        '<label for="shown_name">Your name, as it should be shown</label>'
        f'<input type="text" id="shown_name" name="shown_name" maxlength="80" value="{esc(g["shown_name"] or g["name"])}">'
        '<label for="bio">One line about you</label>'
        f'<input type="text" id="bio" name="bio" maxlength="200" value="{esc(g["bio"] or "")}">'
        '<label for="link">A link</label>'
        f'<input type="url" id="link" name="link" maxlength="200" placeholder="https://" value="{esc(g["link"] or "")}">'
        '<label for="tz">Your time zone</label>'
        f'<select id="tz" name="tz">{options}</select>'
        f'<label for="headshot">A headshot, JPEG or PNG</label>{img}'
        '<input type="file" id="headshot" name="headshot" accept="image/jpeg,image/png">'
        '<p><button type="submit" class="signal">Save</button></p></form>'
    )
