"""
The engine's pages, server-rendered in the site's typographic system: the
same stylesheet and fonts as agentsee.work, the masthead and the footer
lifted from the front page exactly as the podcast pages lift them, and one
small stylesheet of its own for what a private tool has and a broadsheet
does not: tables, forms, a status word. No script but the eye.
"""

import datetime as dt
import html as _html
import importlib.util
import pathlib
import urllib.parse

import ledger

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("agentsee_site", ROOT / "tools" / "site.py")
_site = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_site)

NAV = [
    ("/episodes", "Episodes"),
    ("/posts", "Posts"),
    ("/due", "By hand"),
    ("/reach", "Reach"),
    ("/audience", "Audience"),
    ("/health", "Health"),
]


def esc(s):
    return _html.escape("" if s is None else str(s), quote=True)


def when(iso, fmt="%a %d %b %Y, %H:%M"):
    t = ledger.local(iso)
    return t.strftime(fmt) if t else ""


def page(title, body, who="", path="/", lifted=None):
    parts = lifted or _site.lifted()
    today = dt.datetime.now(ledger.ZONE).strftime("%-d %B %Y")
    footer = _site.footer_for(parts, "AgentSee &middot; Engine", today)
    nav = ""
    for href, label in NAV:
        cur = ' aria-current="page"' if path == href or path.startswith(href + "/") else ""
        nav += f'<a href="{href}"{cur}>{label}</a>'

    return f"""<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{esc(title)} — AgentSee Engine</title>
<meta name="theme-color" content="#f7f3ea" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0a0a0b" media="(prefers-color-scheme: dark)">
<link rel="preload" href="/fonts/newsreader-latin.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="/assets/styles.css">
<link rel="stylesheet" href="/engine.css">
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
{parts["masthead"]}
<nav class="engine-nav" aria-label="Engine">{nav}<span class="who">{esc(who)}</span></nav>
<main id="main" class="engine">
{body}
</main>
{footer}

<script src="/assets/eye.js" defer></script>
</body>
</html>
"""


def table(headers, rows, cls=""):
    th = "".join(f"<th>{h}</th>" for h in headers)
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f'<table class="engine-table {cls}"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>'


def status(s):
    return f'<span class="status-word status-{esc(s)}" title="{esc(ledger.STATUSES.get(s, ""))}">{esc(s)}</span>'


def link(href, text=None, external=False):
    extra = ' rel="noopener" target="_blank"' if external else ""
    return f'<a href="{esc(href)}"{extra}>{esc(text or href)}</a>'


def button(action, label, csrf, cls="", confirm=False):
    return (
        f'<form method="post" action="{esc(action)}" class="inline{" confirm" if confirm else ""}">'
        f'<input type="hidden" name="_t" value="{esc(csrf)}">'
        f'<button type="submit" class="{cls}">{esc(label)}</button></form>'
    )


def composer(platform, text):
    """A link that opens the platform's composer with the text in it."""
    q = urllib.parse.quote(text, safe="")
    return {
        "x": f"https://x.com/intent/post?text={q}",
        "bluesky": f"https://bsky.app/intent/compose?text={q}",
        "mastodon": f"https://mastodon.social/share?text={q}",
        "threads": f"https://www.threads.net/intent/post?text={q}",
        "linkedin": f"https://www.linkedin.com/feed/?shareActive=true&text={q}",
        "youtube": "https://studio.youtube.com/",
    }.get(platform)


def section(kicker, heading, inner, anchor=None):
    aid = f' id="{esc(anchor)}"' if anchor else ""
    return (
        f'<section class="section engine-section"{aid}><p class="kicker">{kicker}</p>'
        f"<h2>{esc(heading)}</h2>{inner}</section>"
    )
