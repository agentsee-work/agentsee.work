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
import mimetypes
import os
import pathlib
import re
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import archive
import collect
import guests
import ledger
import mail
import publish
from pages import button, composer, esc, link, page, section, status, table, when
from plan import ACCOUNTS, short

ROOT = pathlib.Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
STATIC = ROOT / "engine" / "static"
SAFE = re.compile(r"^[A-Za-z0-9._-]+$")
CSP = (
    "default-src 'none'; style-src 'self'; script-src 'self'; font-src 'self'; "
    "img-src 'self' https://media.agentsee.work data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
CSRF = secrets.token_urlsafe(24)
STATE = {"ledger": None, "last_run": None, "poke": threading.Event(), "lifted": None, "snapshot_day": None}


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
        rows.append(
            [
                f'<span class="mono">{esc(when(p["scheduled_at"], "%H:%M"))}</span>',
                f'{esc(p["platform"])} <span class="muted">{esc(handle(p))}</span>',
                f"{link(f'/episodes/{p["episode"]}', p['episode'])} · {esc(asset_label(p))}",
                status(p["status"]),
                link(f"/posts/{p['id']}", (p["title"] or "")[:80] or "(untitled)"),
                f'<span class="mono">{esc(p["link_id"] or "")}</span>',
            ]
        )
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
        counts = c.execute(
            "SELECT status, COUNT(*) n FROM post WHERE episode=? GROUP BY status", (e["slug"],)
        ).fetchall()
        files = c.execute("SELECT COUNT(*) FROM deliverable WHERE episode=?", (e["slug"],)).fetchone()[0]
        summary = ", ".join(f"{r['n']} {r['status']}" for r in counts) or "no posts yet"
        rows.append(
            [
                esc(e["number"]),
                link(f"/episodes/{e['slug']}", e["title"]),
                esc(when(e["release_at"])),
                esc(e["approved_by"]),
                esc(when(e["staged_at"])),
                str(files),
                esc(summary),
            ]
        )
    body = (
        '<article class="lead"><h1>Episodes</h1><p class="deck">What has been approved and staged, '
        "and where each one has gone. An episode arrives here by <code>engine.py ingest</code>.</p></article>"
    )
    body += (
        table(["No.", "Title", "Release", "Approved by", "Staged", "Files", "Posts"], rows)
        if rows
        else "<p>Nothing yet. Stage an episode, then <code>./engine/engine.py ingest &lt;slug&gt;</code>.</p>"
    )
    return "Episodes", body


def episode_page(c, slug):
    e = ledger.episode(c, slug)
    if not e:
        return None
    r = ledger.release(e)
    facts = [
        ("Number", e["number"]),
        ("Release", when(e["release_at"])),
        ("Approved", f"{e['approved_by']}, {e['approved_at']}"),
        ("Staged", e["staged_at"] or "no"),
        ("Page", link(e["url"], external=True)),
        ("Media", e["media_url"]),
        ("GUID", f'<span class="mono">{esc(e["guid"])}</span>'),
        ("Files from", f'<span class="mono">{esc(e["source_dir"])}</span>'),
        ("Ingested", when(e["ingested_at"])),
    ]
    dl = (
        '<dl class="facts">'
        + "".join(f"<dt>{esc(k)}</dt><dd>{v if '<' in str(v) else esc(v)}</dd>" for k, v in facts)
        + "</dl>"
    )
    drows = []
    for d in c.execute("SELECT * FROM deliverable WHERE episode=? ORDER BY kind, name", (slug,)):
        size = f"{d['bytes'] / 1e6:,.1f} MB" if d["bytes"] else ""
        dims = f"{d['width']}×{d['height']}" if d["width"] else ""
        dur = f"{int(d['duration'] // 60)}:{int(d['duration'] % 60):02d}" if d["duration"] else ""
        drows.append(
            [
                f'<span class="mono">{esc(d["name"])}</span>',
                esc(d["kind"]),
                f'<span class="num">{size}</span>',
                dur,
                dims,
                link(d["url"], "media host", True) if d["url"] else '<span class="muted">archive only</span>',
                "yes" if d["archived"] else '<span class="muted">no</span>',
            ]
        )
    posts = c.execute("SELECT * FROM post WHERE episode=? ORDER BY scheduled_at, id", (slug,)).fetchall()
    events = c.execute(
        "SELECT * FROM event WHERE episode=? AND post IS NULL ORDER BY at DESC LIMIT 30", (slug,)
    ).fetchall()
    body = (
        f'<article class="lead"><h1>{esc(e["title"])}</h1><p class="deck">{esc(r["episode"].get("summary", ""))}</p></article>'
        + dl
    )
    body += section(
        "Deliverables",
        f"{len(drows)} files",
        table(["File", "Kind", "Size", "Length", "Frame", "Public", "Archived"], drows),
    )
    body += section(
        "Posts",
        f"{len(posts)} planned across {len({p['account'] for p in posts})} accounts",
        post_rows(posts) if posts else "<p>No posts yet: <code>./engine/engine.py plan " + esc(slug) + "</code>.</p>",
    )
    body += section(
        "Record",
        "What happened to this episode here",
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
        f'<option value="{esc(v)}"{" selected" if q.get(name) == v else ""}>{esc(v)}</option>' for v in values
    )
    statuses = [r[0] for r in c.execute("SELECT DISTINCT status FROM post ORDER BY status")]
    platforms = [r[0] for r in c.execute("SELECT DISTINCT platform FROM post ORDER BY platform")]
    episodes = [r[0] for r in c.execute("SELECT slug FROM episode ORDER BY release")]
    form = (
        f'<form method="get" action="/posts" class="row"><div><label for="f-status">Status</label>'
        f'<select id="f-status" name="status"><option value="">any</option>{opts("status", statuses)}</select></div>'
        f'<div><label for="f-platform">Platform</label><select id="f-platform" name="platform"><option value="">any</option>{opts("platform", platforms)}</select></div>'
        f'<div><label for="f-episode">Episode</label><select id="f-episode" name="episode"><option value="">any</option>{opts("episode", episodes)}</select></div>'
        f'<div><button type="submit">Show</button></div></form>'
    )
    body = (
        '<article class="lead"><h1>Posts</h1><p class="deck">The plan as a calendar: every deliverable on every account, '
        "on its day. Open one to change its text or its time, publish it now, or paste back where it went.</p></article>"
    )
    body += form + (post_rows(posts) if posts else "<p>No posts match.</p>")
    return "Posts", body


def card(c, p, e, csrf):
    """The hand-post card: everything a person needs, ready to copy."""
    media = (
        c.execute("SELECT * FROM deliverable WHERE episode=? AND name=?", (p["episode"], p["media"])).fetchone()
        if p["media"]
        else None
    )
    poster = None
    if media and media["kind"] == "clip":
        poster = c.execute(
            "SELECT * FROM deliverable WHERE episode=? AND name=?", (p["episode"], p["media"].replace(".mp4", ".png"))
        ).fetchone()
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
    paste = (
        f'<form method="post" action="/posts/{p["id"]}/done" class="block"><input type="hidden" name="_t" value="{esc(csrf)}">'
        f'<label for="permalink">Posted? Paste the permalink and it counts as published</label>'
        f'<div class="row"><input type="url" id="permalink" name="permalink" placeholder="https://" required>'
        f'<button type="submit" class="signal">Done</button></div></form>'
    )
    return f'<pre class="card-text">{esc(p["text"])}</pre><p class="card-links">{" ".join(links)}</p>{img}{paste}'


def post_page(c, pid, csrf, msg=None):
    p = ledger.post(c, pid)
    if not p:
        return None
    e = ledger.episode(c, p["episode"])
    a = ACCOUNTS.get(p["account"], {})
    by_hand = a.get("adapter") == "hand"
    media = (
        c.execute("SELECT * FROM deliverable WHERE episode=? AND name=?", (p["episode"], p["media"])).fetchone()
        if p["media"]
        else None
    )
    facts = [
        ("Episode", link(f"/episodes/{p['episode']}", e["title"])),
        ("Asset", asset_label(p)),
        (
            "Account",
            f"{esc(p['platform'])}, {esc(handle(p))}" + (' <span class="muted">(by hand)</span>' if by_hand else ""),
        ),
        ("Due", when(p["scheduled_at"])),
        ("Status", status(p["status"]) + f' <span class="muted">{esc(ledger.STATUSES.get(p["status"], ""))}</span>'),
        (
            "Short link",
            (link(short(p["link_id"]), external=True) + f' <span class="muted">→ {esc(p["link_url"])}</span>')
            if p["link_id"]
            else "none",
        ),
        ("Media", (link(media["url"], media["name"], True) if media and media["url"] else esc(p["media"] or "none"))),
    ]
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
    text_form = (
        (
            f'<form method="post" action="/posts/{pid}/text" class="block"><input type="hidden" name="_t" value="{esc(csrf)}">'
            f'<label for="title">Title</label><input type="text" id="title" name="title" value="{esc(p["title"])}" maxlength="100">'
            f'<label for="text">Text</label><textarea id="text" name="text">{esc(p["text"])}</textarea>'
            f'<p><button type="submit">Save the text</button> <span class="muted mono">{len(p["text"] or "")} characters'
            f"{f', the platform allows {a["limit"]}' if a.get('limit') else ''}</span></p></form>"
        )
        if editable
        else f'<pre class="card-text">{esc(p["text"])}</pre><p class="muted">The text is fixed once the platform has it.</p>'
    )
    local = ledger.local(p["scheduled_at"])
    sched_form = (
        (
            f'<form method="post" action="/posts/{pid}/schedule" class="block"><input type="hidden" name="_t" value="{esc(csrf)}">'
            f'<div class="row"><div><label for="date">Day</label><input type="date" id="date" name="date" value="{local.strftime("%Y-%m-%d") if local else ""}" required></div>'
            f'<div><label for="time">Time ({esc(ledger.CONFIG["timezone"])})</label><input type="time" id="time" name="time" value="{local.strftime("%H:%M") if local else "09:00"}" required></div>'
            f'<div><button type="submit">Move it</button></div></div></form>'
        )
        if editable
        else f'<p class="muted">Due {esc(when(p["scheduled_at"]))}; the platform holds it now.</p>'
    )
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
    body += section(
        "Record",
        "What happened to this post",
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
    return p["title"] or asset_label(p), body


def due_page(c):
    end = dt.datetime.combine(dt.datetime.now(ledger.ZONE).date(), dt.time(23, 59), tzinfo=ledger.ZONE)
    rows = [
        p
        for p in c.execute("SELECT * FROM post WHERE status IN ('hand','planned','due') ORDER BY scheduled_at, id")
        if ACCOUNTS.get(p["account"], {}).get("adapter") == "hand"
        and (p["status"] == "hand" or (p["scheduled_at"] and dt.datetime.fromisoformat(p["scheduled_at"]) <= end))
    ]
    body = (
        '<article class="lead"><h1>By hand</h1><p class="deck">What a person posts: LinkedIn, by decision, and every platform '
        "whose adapter is not written yet. Each one is a card with the text, the file, the short link and the composer. "
        "Paste the permalink back and it is published.</p></article>"
    )
    body += post_rows(rows) if rows else "<p>Nothing due by hand today.</p>"
    held = publish.late(c)
    if held:
        body += section(
            "Late",
            "Past their time, not sent",
            f"<p>These were due more than {ledger.CONFIG.get('late_hours', 6):g} hours ago while nothing was "
            "running to send them. Open one to publish it now, move it or skip it.</p>" + post_rows(held),
            "late",
        )
    return "By hand", body


def num(v, pct=False):
    if v is None:
        return '<span class="muted">—</span>'
    if pct:
        return f"{v:.1f}%"
    return f"{int(round(v)):,}" if abs(v - round(v)) < 1e-9 else f"{v:,.1f}"


def delta(m):
    """A metric's newest value, and beside it how much of it arrived in the last week."""
    if not m or m.get("now") is None:
        return num(None)
    s = num(m["now"])
    if m.get("week") is not None and m["now"] - m["week"] > 0:
        s += f' <span class="muted">+{num(m["now"] - m["week"])}</span>'
    return s


def app_name(ua_class):
    kind, _, name = (ua_class or "unknown").partition(":")
    return name or {
        "unknown": "Not named",
        "bot": "Bots",
        "library": "Libraries",
        "browser": "Browsers",
        "app": "Apps",
    }.get(kind, kind)


def reach_page(c):
    eps = c.execute("SELECT * FROM episode ORDER BY release_at, slug").fetchall()
    all_l, week_l = collect.listens(c), collect.listens(c, 7)
    newest, clicks = collect.latest(c), collect.clicks(c)
    posts = c.execute(
        "SELECT * FROM post WHERE status IN ('scheduled', 'published') ORDER BY episode, scheduled_at, id"
    ).fetchall()
    rows = []
    for e in eps:
        mine = [p for p in posts if p["episode"] == e["slug"]]
        views = sum(
            (newest.get(p["id"], {}).get("views", {}).get("now") or 0) for p in mine if p["account"] == "youtube"
        )
        links = [r["link_id"] for r in c.execute("SELECT link_id FROM post WHERE episode=?", (e["slug"],))]
        rows.append(
            [
                link(f"/episodes/{e['slug']}", e["title"]),
                num(sum(all_l.get(e["slug"], {}).values())),
                num(sum(week_l.get(e["slug"], {}).values())),
                num(views),
                num(sum(clicks.get(k, 0) for k in links)),
            ]
        )
    body = (
        '<article class="lead"><h1>Reach</h1><p class="deck">What each episode and each post did, as last read. '
        "Listens are the media host's own count: one listener, one file, one day, at least a minute of it, no bots. "
        "The rest is what each platform says, read once a day; YouTube's watch figures run two or three days behind.</p></article>"
    )
    body += section(
        "Episodes",
        "Listens, views, clicks",
        table(["Episode", "Listens", "Last 7 days", "YouTube views", "Link clicks"], rows)
        if rows
        else "<p>No episodes yet.</p>",
    )
    prow = []
    for p in posts:
        m = newest.get(p["id"], {})
        prow.append(
            [
                link(f"/posts/{p['id']}", p["title"] or asset_label(p)),
                esc(p["platform"]),
                asset_label(p),
                delta(m.get("views") or m.get("impressions")),
                delta(m.get("likes")),
                delta(m.get("reposts") or m.get("shares")),
                delta(m.get("replies") or m.get("comments")),
                num(clicks.get(p["link_id"])) if p["link_id"] else num(None),
            ]
        )
    body += section(
        "Posts",
        "Each one, as last read; the small figure is the last week's share",
        table(["Post", "Platform", "Asset", "Views", "Likes", "Reposts", "Replies", "Clicks"], prow)
        if prow
        else "<p>Nothing published yet.</p>",
    )
    yt = [p for p in posts if p["account"] == "youtube" and newest.get(p["id"], {}).get("watch_minutes")]
    if yt:

        def g(p, k):
            return newest[p["id"]].get(k, {}).get("now")

        body += section(
            "YouTube",
            "Watch time, from the Analytics API",
            table(
                ["Video", "Watch minutes", "Average view", "Average %", "Shares", "Subscribers gained"],
                [
                    [
                        link(f"/posts/{p['id']}", p["title"] or asset_label(p)),
                        num(g(p, "watch_minutes")),
                        (num(g(p, "avg_view_seconds")) + " s") if g(p, "avg_view_seconds") is not None else num(None),
                        num(g(p, "avg_view_pct"), pct=True),
                        num(g(p, "shares")),
                        num(g(p, "subscribers_gained")),
                    ]
                    for p in yt
                ],
            ),
        )
    return "Reach", body


def audience_page(c):
    days = 30
    apps, countries, origins = collect.by_app(c, days), collect.by_country(c, days), collect.clicks_from(c, days)
    daily = collect.listens(c, days)
    body = (
        '<article class="lead"><h1>Audience</h1><p class="deck">Who listens and from where, over the last thirty days, '
        "from the media host's log alone: the app named in the user agent, the country Cloudflare saw, and where the "
        "short-link clicks came from. No one is identified; the address is hashed under a key that changes daily.</p></article>"
    )
    body += section(
        "Apps",
        "Listens by app",
        table(["App", "Listens"], [[esc(app_name(a)), num(n)] for a, n in apps]) if apps else "<p>No listens yet.</p>",
    )
    body += section(
        "Countries",
        "Listens by country",
        table(["Country", "Listens"], [[esc(k or "unknown"), num(n)] for k, n in countries])
        if countries
        else "<p>No listens yet.</p>",
    )
    body += section(
        "Clicks",
        "Short-link clicks, by where they came from",
        table(["From", "Clicks"], [[esc(o), num(n)] for o, n in origins]) if origins else "<p>No clicks yet.</p>",
    )
    seen = sorted({d for e in daily.values() for d in e})
    if seen:
        eps = sorted(daily)
        body += section(
            "Days",
            "Listens by day and episode",
            table(["Day"] + [esc(e) for e in eps], [[esc(d)] + [num(daily[e].get(d, 0)) for e in eps] for d in seen]),
        )
    return "Audience", body


def health_page(c):
    counts = c.execute("SELECT status, COUNT(*) n FROM post GROUP BY status ORDER BY status").fetchall()
    last = c.execute("SELECT at, detail FROM event WHERE what='publisher:run' ORDER BY at DESC LIMIT 1").fetchone()
    snap = c.execute(
        "SELECT at, what, detail FROM event WHERE what IN ('snapshot', 'snapshot:failed') ORDER BY at DESC LIMIT 1"
    ).fetchone()
    coll = c.execute("SELECT at, detail FROM event WHERE what='collect:run' ORDER BY at DESC LIMIT 1").fetchone()
    creds = []
    for name, a in ACCOUNTS.items():
        mod = publish.adapter(name)
        creds.append(
            [
                esc(name),
                esc(a["adapter"]),
                "ready" if mod.ready(os.environ) else f'<span class="error">missing: {esc(mod.NEEDS)}</span>',
            ]
        )
    creds.append(
        [
            esc(mail.settings()["from"]),
            "mail",
            "ready" if mail.ready(os.environ) else f'<span class="error">missing: {esc(mail.NEEDS)}</span>',
        ]
    )
    bounced = mail.last_failure(c)
    facts = [
        ("Ledger", f'<span class="mono">{esc(STATE["ledger"] or ledger.data_dir() / "ledger.sqlite")}</span>'),
        ("Worker last ran", when(STATE["last_run"]) or "not in this process"),
        (
            "Resting",
            "; ".join(f"{esc(a)} until {esc(when(u.isoformat()))}: {esc(why)}" for a, (u, why) in publish.HELD.items())
            or "no account",
        ),
        (
            "Last recorded run",
            f'{esc(when(last["at"]))} <span class="mono">{esc(last["detail"])}</span>' if last else "never",
        ),
        (
            "Last snapshot",
            (
                f"{esc(when(snap['at']))} "
                + (
                    f'<span class="error">failed: {esc(snap["detail"])}</span>'
                    if snap["what"].endswith("failed")
                    else f'<span class="mono">{esc(snap["detail"])}</span>'
                )
            )
            if snap
            else f"never (nightly at {esc(ledger.CONFIG.get('snapshot_at', 'no hour set'))} London, by the worker)",
        ),
        (
            "Last collection",
            f'{esc(when(coll["at"]))} <span class="mono">{esc(coll["detail"])}</span>'
            if coll
            else f"never (daily at {esc(ledger.CONFIG.get('collect_at', 'no hour set'))} London, by the worker)",
        ),
        ("Episodes", str(c.execute("SELECT COUNT(*) FROM episode").fetchone()[0])),
        (
            "Last mail failure",
            f'{esc(when(bounced["at"]))} <span class="error">{esc(bounced["detail"])}</span>' if bounced else "none",
        ),
        (
            "Guest host",
            f"{esc(guests.HOST)}; sessions "
            + (
                "survive a restart (ENGINE_SESSION_KEY)"
                if guests.persistent()
                else '<span class="error">end at a restart: ENGINE_SESSION_KEY is not set</span>'
            ),
        ),
    ]
    body = (
        '<article class="lead"><h1>Health</h1></article><dl class="facts">'
        + "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in facts)
        + "</dl>"
    )
    body += section(
        "Posts", "By status", table(["Status", "Count"], [[status(r["status"]), str(r["n"])] for r in counts])
    )
    body += section(
        "Credentials", "What each adapter can see in this process", table(["Account", "Adapter", "State"], creds)
    )
    return "Health", body


# ─── the server ──────────────────────────────────────────────────────────────
class App(BaseHTTPRequestHandler):
    server_version = "AgentSeeEngine/0.1"

    def who(self):
        return self.headers.get("Cf-Access-Authenticated-User-Email") or os.environ.get("ENGINE_USER") or "local"

    def log_message(self, fmt, *args):
        if os.environ.get("ENGINE_LOG"):
            super().log_message(fmt, *args)

    def send(self, status_code, body, ctype="text/html; charset=utf-8", cache="no-store", headers=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status_code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in headers or ():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def html(self, title, body, status_code=200):
        self.send(status_code, page(title, body, self.who(), self.path.split("?")[0], STATE["lifted"]))

    def redirect(self, to, headers=None):
        self.send_response(303)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        for k, v in headers or ():
            self.send_header(k, v)
        self.end_headers()

    def fail(self, status_code, msg):
        self.html(
            "Not that",
            f'<article class="lead"><h1>{esc(msg)}</h1></article><p>{link("/posts", "Back to the posts")}</p>',
            status_code,
        )

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
        if guests.is_guest_host(self):  # the guest host: its door and its office, nothing of the engine's
            c = ledger.connect(STATE["ledger"])
            try:
                return guests.get(self, c, path, q)
            finally:
                c.close()
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
            if path == "/reach":
                return self.html(*reach_page(c))
            if path == "/audience":
                return self.html(*audience_page(c))
            if path == "/health":
                return self.html(*health_page(c))
            if path == "/guests":
                return self.html(*guests.guests_page(c, CSRF, q.get("msg")))
            m = re.fullmatch(r"/guests/([0-9a-f]{8})", path)
            if m:
                r = guests.guest_page(c, m.group(1), CSRF, q.get("msg"))
                return self.html(*r) if r else self.fail(404, "No such guest")
            return self.fail(404, "No such page")
        finally:
            c.close()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode()).items()}
        path = urllib.parse.urlparse(self.path).path
        if guests.is_guest_host(self):  # no form token on the guest host: its pages are public; the origin is checked
            c = ledger.connect(STATE["ledger"])
            try:
                return guests.post(self, c, path, form)
            finally:
                c.close()
        if form.get("_t") != CSRF:
            return self.fail(403, "That form is stale; open the post again")
        c = ledger.connect(STATE["ledger"])
        try:
            who = self.who()
            if path == "/guests/new":
                try:
                    g = guests.create(c, form.get("name"), form.get("address"), form.get("issue"), who)
                except ValueError as e:
                    return self.redirect("/guests?" + urllib.parse.urlencode({"msg": str(e)}))
                return self.redirect(f"/guests/{g['id']}?" + urllib.parse.urlencode({"msg": "Invitation made"}))
            m = re.fullmatch(r"/guests/([0-9a-f]{8})/(send|revoke|reissue)", path)
            if m:
                g = guests.guest(c, m.group(1))
                if not g:
                    return self.fail(404, "No such guest")
                try:
                    msg = guests.act(c, g, m.group(2), who)
                except ValueError as e:
                    msg = str(e)
                return self.redirect(f"/guests/{g['id']}?" + urllib.parse.urlencode({"msg": msg}))
            m = re.fullmatch(r"/posts/(\d+)/(text|schedule|publish|retry|skip|unskip|done)", path)
            if not m:
                return self.fail(404, "No such action")
            pid, action = int(m.group(1)), m.group(2)
            p = ledger.post(c, pid)
            if not p:
                return self.fail(404, "No such post")
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
        except ValueError, TypeError:
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
        fields = {"status": "published", "published_at": ledger.now()}
        m = re.search(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/)|youtu\.be/)([\w-]{11})", url)
        if m:
            fields["remote_id"] = m.group(1)  # a video recorded by hand keeps its id, for the collector
            its_time = dt.datetime.fromisoformat(p["scheduled_at"]) if p["scheduled_at"] else None
            if its_time and its_time > dt.datetime.now(dt.timezone.utc):
                fields.update(status="scheduled", published_at=p["scheduled_at"])  # YouTube holds it until then
        ledger.update_post(c, pid, permalink=url, last_error=None, **fields)
        ledger.event(c, "hand:done", who, post=pid, episode=p["episode"], detail=url)
        return (
            "Published, by hand"
            if fields["status"] == "published"
            else "Recorded; the platform holds it until its time"
        )
    raise ValueError("No such action")


# ─── the worker ──────────────────────────────────────────────────────────────
def recover(c, who="engine"):
    """A post left in 'publishing' by a process that stopped mid-attempt:
    marked failed so a person sees it, with the warning that the platform may
    hold the result (an upload that finished a moment before the stop)."""
    n = 0
    for p in c.execute("SELECT * FROM post WHERE status='publishing'").fetchall():
        err = "the engine stopped during the attempt; look for the result on the platform before a retry"
        ledger.update_post(c, p["id"], status="failed", last_error=err)
        ledger.event(c, "publish:interrupted", who, post=p["id"], episode=p["episode"], detail=err)
        n += 1
    return n


def daily_due(what):
    """Once a day, after the configured hour (`<what>_at`, London), and not
    before the process has seen that hour pass: a restart at 04:00 does not
    run the 03:50 job again."""
    at = ledger.CONFIG.get(f"{what}_at")
    if not at:
        return False
    now = dt.datetime.now(ledger.ZONE)
    hh, mm = (int(x) for x in at.split(":"))
    due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    key = f"{what}_day"
    if STATE.get(key) is None:
        STATE[key] = now.date() if now >= due else now.date() - dt.timedelta(days=1)
    return now >= due and STATE[key] < now.date()


def snapshot_due():
    return daily_due("snapshot")


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
                if snapshot_due():
                    try:
                        archive.snapshot(c, who="worker", log=lambda s: print(s, flush=True))
                    except archive.ArchiveError as e:
                        print(f"worker: snapshot failed: {e}", flush=True)
                        ledger.event(c, "snapshot:failed", "worker", detail=str(e)[:500])
                    STATE["snapshot_day"] = dt.datetime.now(ledger.ZONE).date()
                if daily_due("collect"):
                    collect.run(c, who="worker", log=lambda s: print(s, flush=True))
                    STATE["collect_day"] = dt.datetime.now(ledger.ZONE).date()
            finally:
                c.close()
        except Exception as e:  # noqa: BLE001 — the worker must outlive a bad minute
            print(f"worker: {type(e).__name__}: {e}", flush=True)
        poke.wait(interval)
        poke.clear()


def serve(port=8877, with_worker=True, ledger_path=None, host="127.0.0.1"):
    STATE["ledger"] = str(ledger_path) if ledger_path else None
    STATE["lifted"] = None  # lifted fresh per page; the front page may change under us
    c = ledger.connect(STATE["ledger"])
    try:
        n = recover(c)
        if n:
            print(f"{n} post(s) were mid-attempt when the last process stopped; marked failed, for a look", flush=True)
    finally:
        c.close()
    stop = threading.Event()
    if with_worker:
        threading.Thread(target=worker, args=(stop,), daemon=True, name="publisher").start()
    srv = ThreadingHTTPServer((host, port), App)
    srv.daemon_threads = True
    print(f"engine at http://{host}:{srv.server_address[1]}/  (worker {'on' if with_worker else 'off'})", flush=True)
    print(
        f"guest host {guests.HOST} on the same port, by Host; sessions "
        + ("survive a restart" if guests.persistent() else "END AT A RESTART: ENGINE_SESSION_KEY is not set"),
        flush=True,
    )
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        srv.server_close()
    return srv
