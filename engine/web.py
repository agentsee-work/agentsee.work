"""
The screens, and the worker behind them. A plain threaded HTTP server on the
loopback address: on the VPS a Cloudflare Tunnel brings Access-authenticated
requests to it and nothing else can reach it; on a workstation it is a
browser tab. Who did what is the identity Access attaches to the request
(Cf-Access-Authenticated-User-Email), or ENGINE_USER, or "local".

Every mutation is a POST with the process's own token in the form, so a
page on another origin cannot submit one. No cookie, no session, no script
beyond the site's eye. Pages carry the same CSP as the site, plus the media
host for the posters.

The worker is a thread that runs the publisher every minute, and at once
when a screen asks for something now.
"""

import datetime as dt
import json
import mimetypes
import os
import pathlib
import re
import secrets
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import ledger
import publish
from pages import button, composer, esc, link, page, section, status, table, when
from plan import ACCOUNTS, short

ROOT = pathlib.Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
STATIC = ROOT / "engine" / "static"
SAFE = re.compile(r"^[A-Za-z0-9._-]+$")
CSP = ("default-src 'none'; style-src 'self'; script-src 'self'; font-src 'self'; "
       "img-src 'self' https://media.agentsee.work data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
CSRF = secrets.token_urlsafe(24)
STATE = {"ledger": None, "last_run": None, "poke": threading.Event(), "lifted": None}


# ─── what the screens show ───────────────────────────────────────────────────
def asset_label(p):
    if p["asset"] == "episode":
        return "Episode"
    stem = p["asset"].split(":", 1)[1]
    return f"Clip {stem[:2]}"


def handle(p):
    return ACCOUNTS.get(p["account"], {}).get("handle", p["account"])


def post_rows(posts, by_day=True):
    rows, day = [], None
    for p in posts:
        d = when(p["scheduled_at"], "%A %-d %B %Y")
        if by_day and d != day:
            day = d
            rows.append([f'<td colspan="6">{esc(day or "no date")}</td>'])
        rows.append([
            f'<span class="mono">{esc(when(p["scheduled_at"], "%H:%M"))}</span>',
            f'{esc(p["platform"])} <span class="muted">{esc(handle(p))}</span>',
            f'{link(f"/episodes/{p['episode']}", p["episode"])} · {esc(asset_label(p))}',
            status(p["status"]),
            link(f"/posts/{p['id']}", (p["title"] or "")[:80] or "(untitled)"),
            f'<span class="mono">{esc(p["link_id"] or "")}</span>',
        ])
    out = []
    for r in rows:
        if len(r) == 1:
            out.append(f'<tr class="day-head">{r[0]}</tr>')
        else:
            out.append("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
    head = "".join(f"<th>{h}</th>" for h in ("When", "Where", "What", "Status", "Post", "Link"))
    return f'<table class="engine-table"><thead><tr>{head}</tr></thead><tbody>{"".join(out)}</tbody></table>'


def episodes_page(c):
    rows = []
    for e in c.execute("SELECT * FROM episode ORDER BY release, number"):
        counts = c.execute("SELECT status, COUNT(*) n FROM post WHERE episode=? GROUP BY status", (e["slug"],)).fetchall()
        files = c.execute("SELECT COUNT(*) FROM deliverable WHERE episode=?", (e["slug"],)).fetchone()[0]
        summary = ", ".join(f"{r['n']} {r['status']}" for r in counts) or "no posts yet"
        rows.append([esc(e["number"]), link(f"/episodes/{e['slug']}", e["title"]), esc(when(e["release_at"])),
                     esc(e["approved_by"]), esc(when(e["staged_at"])), str(files), esc(summary)])
    body = ('<article class="lead"><h1>Episodes</h1><p class="deck">What has been approved and staged, '
            'and where each one has gone. An episode arrives here by <code>engine.py ingest</code>.</p></article>')
    body += table(["No.", "Title", "Release", "Approved by", "Staged", "Files", "Posts"], rows) if rows else \
        "<p>Nothing yet. Stage an episode, then <code>./engine/engine.py ingest &lt;slug&gt;</code>.</p>"
    return "Episodes", body


def episode_page(c, slug):
    e = ledger.episode(c, slug)
    if not e:
        return None
    r = ledger.release(e)
    facts = [("Number", e["number"]), ("Release", when(e["release_at"])), ("Approved", f"{e['approved_by']}, {e['approved_at']}"),
             ("Staged", e["staged_at"] or "no"), ("Page", link(e["url"], external=True)), ("Media", e["media_url"]),
             ("GUID", f'<span class="mono">{esc(e["guid"])}</span>'), ("Files from", f'<span class="mono">{esc(e["source_dir"])}</span>'),
             ("Ingested", when(e["ingested_at"]))]
    dl = '<dl class="facts">' + "".join(f"<dt>{esc(k)}</dt><dd>{v if '<' in str(v) else esc(v)}</dd>" for k, v in facts) + "</dl>"
    drows = []
    for d in c.execute("SELECT * FROM deliverable WHERE episode=? ORDER BY kind, name", (slug,)):
        size = f"{d['bytes'] / 1e6:,.1f} MB" if d["bytes"] else ""
        dims = f"{d['width']}×{d['height']}" if d["width"] else ""
        dur = f"{int(d['duration'] // 60)}:{int(d['duration'] % 60):02d}" if d["duration"] else ""
        drows.append([f'<span class="mono">{esc(d["name"])}</span>', esc(d["kind"]), f'<span class="num">{size}</span>', dur, dims,
                      link(d["url"], "media host", True) if d["url"] else '<span class="muted">archive only</span>',
                      "yes" if d["archived"] else '<span class="muted">no</span>'])
    posts = c.execute("SELECT * FROM post WHERE episode=? ORDER BY scheduled_at, id", (slug,)).fetchall()
    events = c.execute("SELECT * FROM event WHERE episode=? AND post IS NULL ORDER BY at DESC LIMIT 30", (slug,)).fetchall()
    body = f'<article class="lead"><h1>{esc(e["title"])}</h1><p class="deck">{esc(r["episode"].get("summary", ""))}</p></article>' + dl
    body += section("Deliverables", f"{len(drows)} files", table(["File", "Kind", "Size", "Length", "Frame", "Public", "Archived"], drows))
    body += section("Posts", f"{len(posts)} planned across {len({p['account'] for p in posts})} accounts",
                    post_rows(posts) if posts else "<p>No posts yet: <code>./engine/engine.py plan " + esc(slug) + "</code>.</p>")
    body += section("Record", "What happened to this episode here",
                    table(["When", "Who", "What", "Detail"], [[esc(when(ev["at"], "%d %b %H:%M")), esc(ev["who"]), esc(ev["what"]), f'<span class="mono">{esc(ev["detail"] or "")}</span>'] for ev in events]))
    return e["title"], body


def posts_page(c, q):
    where, args = [], []
    for k in ("status", "platform", "episode", "account"):
        if q.get(k):
            where.append(f"{k} = ?")
            args.append(q[k])
    sql = "SELECT * FROM post" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY scheduled_at, id"
    posts = c.execute(sql, args).fetchall()
    opts = lambda name, values: "".join(  # noqa: E731
        f'<option value="{esc(v)}"{" selected" if q.get(name) == v else ""}>{esc(v)}</option>' for v in values)
    statuses = [r[0] for r in c.execute("SELECT DISTINCT status FROM post ORDER BY status")]
    platforms = [r[0] for r in c.execute("SELECT DISTINCT platform FROM post ORDER BY platform")]
    episodes = [r[0] for r in c.execute("SELECT slug FROM episode ORDER BY release")]
    form = (f'<form method="get" action="/posts" class="row"><div><label for="f-status">Status</label>'
            f'<select id="f-status" name="status"><option value="">any</option>{opts("status", statuses)}</select></div>'
            f'<div><label for="f-platform">Platform</label><select id="f-platform" name="platform"><option value="">any</option>{opts("platform", platforms)}</select></div>'
            f'<div><label for="f-episode">Episode</label><select id="f-episode" name="episode"><option value="">any</option>{opts("episode", episodes)}</select></div>'
            f'<div><button type="submit">Show</button></div></form>')
    body = ('<article class="lead"><h1>Posts</h1><p class="deck">The plan as a calendar: every deliverable on every account, '
            'on its day. Open one to change its text or its time, publish it now, or paste back where it went.</p></article>')
    body += form + (post_rows(posts) if posts else "<p>No posts match.</p>")
    return "Posts", body


def card(c, p, e, csrf):
    """The hand-post card: everything a person needs, ready to copy."""
    media = c.execute("SELECT * FROM deliverable WHERE episode=? AND name=?", (p["episode"], p["media"])).fetchone() if p["media"] else None
    poster = None
    if media and media["kind"] == "clip":
        poster = c.execute("SELECT * FROM deliverable WHERE episode=? AND name=?", (p["episode"], p["media"].replace(".mp4", ".png"))).fetchone()
    links = []
    comp = composer(p["platform"], p["text"] or "")
    if comp:
        links.append(link(comp, "Open the composer with this text", True))
    if media and media["url"]:
        links.append(link(media["url"], f"The file ({media['name']}, {media['bytes'] / 1e6:,.1f} MB)", True))
    elif p["media"]:
        links.append(f'<span class="muted">The file: {esc(p["media"])} (not on the media host)</span>')
    if poster and poster["url"]:
        links.append(link(poster["url"], "The poster", True))
    if p["link_id"]:
        links.append(link(short(p["link_id"]), "The short link", True))
    img = f'<img class="poster" src="{esc(poster["url"])}" alt="">' if poster and poster["url"] else ""
    paste = (f'<form method="post" action="/posts/{p["id"]}/done" class="block"><input type="hidden" name="_t" value="{esc(csrf)}">'
             f'<label for="permalink">Posted? Paste the permalink and it counts as published</label>'
             f'<div class="row"><input type="url" id="permalink" name="permalink" placeholder="https://" required>'
             f'<button type="submit" class="signal">Done</button></div></form>')
    return (f'<pre class="card-text">{esc(p["text"])}</pre><p class="card-links">{" ".join(links)}</p>{img}{paste}')


def post_page(c, pid, csrf, msg=None):
    p = ledger.post(c, pid)
    if not p:
        return None
    e = ledger.episode(c, p["episode"])
    a = ACCOUNTS.get(p["account"], {})
    by_hand = a.get("adapter") == "hand"
    media = c.execute("SELECT * FROM deliverable WHERE episode=? AND name=?", (p["episode"], p["media"])).fetchone() if p["media"] else None
    facts = [("Episode", link(f"/episodes/{p['episode']}", e["title"])), ("Asset", asset_label(p)),
             ("Account", f"{esc(p['platform'])}, {esc(handle(p))}" + (" <span class=\"muted\">(by hand)</span>" if by_hand else "")),
             ("Due", when(p["scheduled_at"])), ("Status", status(p["status"]) + f' <span class="muted">{esc(ledger.STATUSES.get(p["status"], ""))}</span>'),
             ("Short link", (link(short(p["link_id"]), external=True) + f' <span class="muted">→ {esc(p["link_url"])}</span>') if p["link_id"] else "none"),
             ("Media", (link(media["url"], media["name"], True) if media and media["url"] else esc(p["media"] or "none")))]
    if p["remote_id"]:
        facts.append(("Platform id", f'<span class="mono">{esc(p["remote_id"])}</span>'))
    if p["permalink"]:
        facts.append(("Permalink", link(p["permalink"], external=True)))
    if p["published_at"]:
        facts.append(("Published" if p["status"] == "published" else "Goes public", when(p["published_at"])))
    if p["attempts"]:
        facts.append(("Attempts", str(p["attempts"])))
    if p["last_error"]:
        facts.append(("Last error", f'<span class="error">{esc(p["last_error"])}</span>'))
    dl = '<dl class="facts">' + "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in facts) + "</dl>"
    note = f'<p class="honesty"><b>{esc(msg)}</b></p>' if msg else ""
    editable = p["status"] in ("planned", "due", "hand", "failed", "skipped")
    text_form = (f'<form method="post" action="/posts/{pid}/text" class="block"><input type="hidden" name="_t" value="{esc(csrf)}">'
                 f'<label for="title">Title</label><input type="text" id="title" name="title" value="{esc(p["title"])}" maxlength="100">'
                 f'<label for="text">Text</label><textarea id="text" name="text">{esc(p["text"])}</textarea>'
                 f'<p><button type="submit">Save the text</button> <span class="muted mono">{len(p["text"] or "")} characters'
                 f'{f", the platform allows {a['limit']}" if a.get("limit") else ""}</span></p></form>') if editable else \
        f'<pre class="card-text">{esc(p["text"])}</pre><p class="muted">The text is fixed once the platform has it.</p>'
    local = ledger.local(p["scheduled_at"])
    sched_form = (f'<form method="post" action="/posts/{pid}/schedule" class="block"><input type="hidden" name="_t" value="{esc(csrf)}">'
                  f'<div class="row"><div><label for="date">Day</label><input type="date" id="date" name="date" value="{local.strftime("%Y-%m-%d") if local else ""}" required></div>'
                  f'<div><label for="time">Time ({esc(ledger.CONFIG["timezone"])})</label><input type="time" id="time" name="time" value="{local.strftime("%H:%M") if local else "09:00"}" required></div>'
                  f'<div><button type="submit">Move it</button></div></div></form>') if editable else \
        f'<p class="muted">Due {esc(when(p["scheduled_at"]))}; the platform holds it now.</p>'
    acts = []
    if p["status"] in ("planned", "hand", "failed", "skipped") and not by_hand:
        acts.append(button(f"/posts/{pid}/publish", "Publish now", csrf, "signal"))
    if p["status"] == "failed":
        acts.append(button(f"/posts/{pid}/retry", "Retry", csrf))
    if p["status"] in ("planned", "due", "hand", "failed"):
        acts.append(button(f"/posts/{pid}/skip", "Skip", csrf))
    if p["status"] == "skipped":
        acts.append(button(f"/posts/{pid}/unskip", "Back on the plan", csrf))
    events = c.execute("SELECT * FROM event WHERE post=? ORDER BY at DESC", (pid,)).fetchall()
    body = f'<article class="lead"><h1>{esc(p["title"] or asset_label(p))}</h1></article>{note}{dl}'
    body += section("Text", "What goes out", text_form)
    body += section("When", "Its day and hour", sched_form)
    if acts:
        body += section("Now", "Actions", '<div class="actions">' + "".join(acts) + "</div>")
    if by_hand or p["status"] in ("hand", "failed"):
        body += section("By hand", "The card", card(c, p, e, csrf), anchor="card")
    body += section("Record", "What happened to this post",
                    table(["When", "Who", "What", "Detail"],
                          [[esc(when(ev["at"], "%d %b %H:%M")), esc(ev["who"]), esc(ev["what"]), f'<span class="mono">{esc(ev["detail"] or "")}</span>'] for ev in events]))
    return p["title"] or asset_label(p), body


def due_page(c):
    end = dt.datetime.combine(dt.datetime.now(ledger.ZONE).date(), dt.time(23, 59), tzinfo=ledger.ZONE)
    rows = [p for p in c.execute("SELECT * FROM post WHERE status IN ('hand','planned','due') ORDER BY scheduled_at, id")
            if ACCOUNTS.get(p["account"], {}).get("adapter") == "hand"
            and (p["status"] == "hand" or (p["scheduled_at"] and dt.datetime.fromisoformat(p["scheduled_at"]) <= end))]
    body = ('<article class="lead"><h1>By hand</h1><p class="deck">What a person posts: the LinkedIn page, and every platform '
            'whose adapter is not written yet. Each one is a card with the text, the file, the short link and the composer. '
            'Paste the permalink back and it is published.</p></article>')
    body += post_rows(rows) if rows else "<p>Nothing due by hand today.</p>"
    return "By hand", body


def health_page(c):
    counts = c.execute("SELECT status, COUNT(*) n FROM post GROUP BY status ORDER BY status").fetchall()
    last = c.execute("SELECT at, detail FROM event WHERE what='publisher:run' ORDER BY at DESC LIMIT 1").fetchone()
    creds = []
    for name, a in ACCOUNTS.items():
        mod = publish.adapter(name)
        creds.append([esc(name), esc(a["adapter"]), "ready" if mod.ready(os.environ) else f'<span class="error">missing: {esc(mod.NEEDS)}</span>'])
    facts = [("Ledger", f'<span class="mono">{esc(STATE["ledger"] or ledger.data_dir() / "ledger.sqlite")}</span>'),
             ("Worker last ran", when(STATE["last_run"]) or "not in this process"),
             ("Last recorded run", f"{esc(when(last['at']))} <span class=\"mono\">{esc(last['detail'])}</span>" if last else "never"),
             ("Episodes", str(c.execute("SELECT COUNT(*) FROM episode").fetchone()[0]))]
    body = '<article class="lead"><h1>Health</h1></article><dl class="facts">' + "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in facts) + "</dl>"
    body += section("Posts", "By status", table(["Status", "Count"], [[status(r["status"]), str(r["n"])] for r in counts]))
    body += section("Credentials", "What each adapter can see in this process", table(["Account", "Adapter", "State"], creds))
    return "Health", body


# ─── the server ──────────────────────────────────────────────────────────────
class App(BaseHTTPRequestHandler):
    server_version = "AgentSeeEngine/0.1"

    def who(self):
        return self.headers.get("Cf-Access-Authenticated-User-Email") or os.environ.get("ENGINE_USER") or "local"

    def log_message(self, fmt, *args):
        if os.environ.get("ENGINE_LOG"):
            super().log_message(fmt, *args)

    def send(self, status_code, body, ctype="text/html; charset=utf-8", cache="no-store"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status_code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def html(self, title, body, status_code=200):
        self.send(status_code, page(title, body, self.who(), self.path.split("?")[0], STATE["lifted"]))

    def redirect(self, to):
        self.send_response(303)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def fail(self, status_code, msg):
        self.html("Not that", f'<article class="lead"><h1>{esc(msg)}</h1></article><p>{link("/posts", "Back to the posts")}</p>', status_code)

    def static(self, base, name):
        if not SAFE.match(name) or not (base / name).is_file():
            return self.fail(404, "No such file")
        data = (base / name).read_bytes()
        self.send(200, data, mimetypes.guess_type(name)[0] or "application/octet-stream", "public, max-age=600")

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        path = u.path
        if path == "/":
            return self.redirect("/episodes")
        if path == "/engine.css":
            return self.static(STATIC, "engine.css")
        if path.startswith("/assets/"):
            return self.static(PUBLIC / "assets", path[8:])
        if path.startswith("/fonts/"):
            return self.static(PUBLIC / "fonts", path[7:])
        c = ledger.connect(STATE["ledger"])
        try:
            if path == "/episodes":
                return self.html(*episodes_page(c))
            m = re.fullmatch(r"/episodes/([a-z0-9-]+)", path)
            if m:
                r = episode_page(c, m.group(1))
                return self.html(*r) if r else self.fail(404, "No such episode")
            if path == "/posts":
                return self.html(*posts_page(c, q))
            m = re.fullmatch(r"/posts/(\d+)", path)
            if m:
                r = post_page(c, int(m.group(1)), CSRF, q.get("msg"))
                return self.html(*r) if r else self.fail(404, "No such post")
            if path == "/due":
                return self.html(*due_page(c))
            if path == "/health":
                return self.html(*health_page(c))
            return self.fail(404, "No such page")
        finally:
            c.close()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode()).items()}
        if form.get("_t") != CSRF:
            return self.fail(403, "That form is stale; open the post again")
        m = re.fullmatch(r"/posts/(\d+)/(text|schedule|publish|retry|skip|unskip|done)", urllib.parse.urlparse(self.path).path)
        if not m:
            return self.fail(404, "No such action")
        pid, action = int(m.group(1)), m.group(2)
        c = ledger.connect(STATE["ledger"])
        try:
            p = ledger.post(c, pid)
            if not p:
                return self.fail(404, "No such post")
            who = self.who()
            msg = act(c, p, action, form, who)
            if action in ("publish", "retry"):
                STATE["poke"].set()
            return self.redirect(f"/posts/{pid}?" + urllib.parse.urlencode({"msg": msg}))
        except ValueError as e:
            return self.fail(400, str(e))
        finally:
            c.close()


def act(c, p, action, form, who):
    """One action on one post. Returns the sentence the screen shows."""
    pid = p["id"]
    editable = p["status"] in ("planned", "due", "hand", "failed", "skipped")
    if action == "text":
        if not editable:
            raise ValueError("The text is fixed once the platform has it")
        title, text = form.get("title", "").strip()[:100], form.get("text", "").replace("\r\n", "\n").strip()
        ledger.update_post(c, pid, title=title, text=text)
        ledger.event(c, "edit:text", who, post=pid, episode=p["episode"], detail={"chars": len(text)})
        return "Text saved"
    if action == "schedule":
        if not editable:
            raise ValueError("The platform holds the time now")
        try:
            at = ledger.at(form.get("date", ""), form.get("time", ""))
        except (ValueError, TypeError):
            raise ValueError("A day and a time, please") from None
        ledger.update_post(c, pid, scheduled_at=at)
        ledger.event(c, "reschedule", who, post=pid, episode=p["episode"], detail=at)
        return f"Moved to {when(at)}"
    if action == "publish":
        if p["status"] not in ("planned", "hand", "failed", "skipped"):
            raise ValueError("Not from this state")
        ledger.update_post(c, pid, status="due", scheduled_at=ledger.now())
        ledger.event(c, "publish:now", who, post=pid, episode=p["episode"])
        return "Due now; the worker takes it within the minute"
    if action == "retry":
        if p["status"] != "failed":
            raise ValueError("Only a failed post is retried")
        ledger.update_post(c, pid, status="due")
        ledger.event(c, "retry", who, post=pid, episode=p["episode"])
        return "Back in the queue"
    if action == "skip":
        if p["status"] not in ("planned", "due", "hand", "failed"):
            raise ValueError("Not from this state")
        ledger.update_post(c, pid, status="skipped")
        ledger.event(c, "skip", who, post=pid, episode=p["episode"])
        return "Skipped"
    if action == "unskip":
        if p["status"] != "skipped":
            raise ValueError("It is not skipped")
        ledger.update_post(c, pid, status="planned")
        ledger.event(c, "unskip", who, post=pid, episode=p["episode"])
        return "Back on the plan"
    if action == "done":
        url = form.get("permalink", "").strip()
        if not re.match(r"^https?://\S+$", url):
            raise ValueError("A permalink is a URL")
        ledger.update_post(c, pid, status="published", permalink=url, published_at=ledger.now(), last_error=None)
        ledger.event(c, "hand:done", who, post=pid, episode=p["episode"], detail=url)
        return "Published, by hand"
    raise ValueError("No such action")


# ─── the worker ──────────────────────────────────────────────────────────────
def worker(stop, interval=60):
    poke = STATE["poke"]
    while not stop.is_set():
        try:
            c = ledger.connect(STATE["ledger"])
            try:
                posts = publish.due(c)
                if posts:
                    res = publish.run(c, posts, who="worker", log=lambda s: print(s, flush=True))
                    ledger.event(c, "publisher:run", "worker", detail={"results": res})
                STATE["last_run"] = ledger.now()
            finally:
                c.close()
        except Exception as e:  # noqa: BLE001 — the worker must outlive a bad minute
            print(f"worker: {type(e).__name__}: {e}", flush=True)
        poke.wait(interval)
        poke.clear()


def serve(port=8877, with_worker=True, ledger_path=None, host="127.0.0.1"):
    STATE["ledger"] = str(ledger_path) if ledger_path else None
    STATE["lifted"] = None  # lifted fresh per page; the front page may change under us
    ledger.connect(STATE["ledger"]).close()
    stop = threading.Event()
    if with_worker:
        threading.Thread(target=worker, args=(stop,), daemon=True, name="publisher").start()
    srv = ThreadingHTTPServer((host, port), App)
    srv.daemon_threads = True
    print(f"engine at http://{host}:{srv.server_address[1]}/  (worker {'on' if with_worker else 'off'})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        srv.server_close()
    return srv
