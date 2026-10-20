#!/usr/bin/env python3
"""
The podcast on the site: the feed, the show page, and a page per episode.

    ./tools/site.py                       # every staged episode whose day has come
    ./tools/site.py --as-of 2026-10-27    # what the site will say on that day

Reads build/episodes/*/out/release.json and takes an episode only once
out/staged.json says its files are on the media host AND its release date
(from `episode.py approve --release`) has arrived, London time. A staged
episode whose day is still to come is named on the show page with its date
and is otherwise absent: no page, no feed item, until a run on or after the
day puts it there. Going live is therefore: run this, commit, push. Writes:

  public/podcast/feed.xml           RSS 2.0 with the itunes and podcast namespaces
  public/podcast/index.html         the show: what it is, how to listen, the episodes
  public/podcast/<slug>/index.html  the episode: player, chapters, notes, people, transcript
  public/podcast/<slug>/art.jpg     the 1400 px episode art, from here so img-src stays 'self'
  public/sitemap.xml                the block between its podcast markers

The masthead, the classifieds and the footer are lifted from public/index.html
when this runs, so the mark and the ads have one copy. The pages keep the
site's rules: first-party only, no cookies, usable without JavaScript. The one
script, assets/player.js, makes a chapter or transcript link seek the player;
without it the link is a time.

Every date is the episode's release date (`episode.py approve --release`),
at 06:00 London time in the feed. Output is deterministic: a re-run with
nothing changed writes nothing new.

Commit what it writes. The site deploys from the repository on push to main.
"""

import argparse
import datetime as dt
import email.utils
import html
import json
import pathlib
import re
import shutil
import sys
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
EPISODES = ROOT / "build" / "episodes"
SHOW = ROOT / "tools" / "show.json"
LONDON = ZoneInfo("Europe/London")
COVER = "/assets/brand/show-cover.png"  # 3000 px square; Apple wants 1400 to 3000


def esc(s):
    return html.escape(str(s), quote=True)


def clock(t):
    t = int(round(t))
    h, m, s = t // 3600, t % 3600 // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def spoken(seconds):
    m, s = int(seconds) // 60, int(seconds) % 60
    return f"{m} min {s} s" if m else f"{s} s"


def long_date(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%B %Y')}"


def day_date(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B %Y')}"


def rfc2822(iso):
    d = dt.datetime.combine(dt.date.fromisoformat(iso), dt.time(6, 0), tzinfo=LONDON)
    return email.utils.format_datetime(d)


def iso_duration(seconds):
    t = int(round(seconds))
    h, m, s = t // 3600, t % 3600 // 60, t % 60
    return "PT" + (f"{h}H" if h else "") + (f"{m}M" if m else "") + f"{s}S"


def cdata(s):
    return "<![CDATA[" + s.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write(p, text):
    """Write only on change, so a no-op run leaves mtimes and git alone."""
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and p.read_text(encoding="utf-8") == text:
        return False
    p.write_text(text, encoding="utf-8")
    return True


# ─── what is on the site already ─────────────────────────────────────────────
OPEN_MASTHEAD = '<header class="masthead">\n'
HOME_LINK = '  <a class="home" href="/" aria-label="AgentSee, the front page">\n'


def lifted():
    """The masthead, the classifieds and the footer, from the front page; the
    masthead made a link home, since no page written here is the front."""
    s = (PUBLIC / "index.html").read_text(encoding="utf-8")
    parts = {}
    for key, pat in (("masthead", r'<header class="masthead">.*?</header>'),
                     ("ads", r'<div class="classifieds">.*?</div>'),
                     ("footer", r"<footer>.*?</footer>")):
        m = re.search(pat, s, re.S)
        if not m:
            sys.exit(f"error: public/index.html has no {key} to lift")
        parts[key] = m.group(0)
    # Every page this script writes is a page other than the front, where the
    # masthead is a link home.
    parts["masthead"] = (parts["masthead"]
                         .replace(OPEN_MASTHEAD, OPEN_MASTHEAD + HOME_LINK, 1)
                         .replace("</header>", "  </a>\n</header>", 1))
    return parts


def footer_for(parts, label, dated):
    f = parts["footer"]
    f = re.sub(r"<span>AgentSee &middot; Issue No\.&nbsp;\d+</span>", f"<span>{label}</span>", f, count=1)
    f = re.sub(r'<span id="colophon-date">.*?</span>', f"<span>{dated}</span>", f, count=1)
    return f


def portrait(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"/assets/{slug}.jpg" if (PUBLIC / "assets" / f"{slug}.jpg").exists() else None


# ─── the episodes ────────────────────────────────────────────────────────────
def episodes(as_of):
    out = []
    for d in sorted(EPISODES.iterdir()) if EPISODES.exists() else []:
        rel, st = d / "out" / "release.json", d / "out" / "staged.json"
        if not rel.exists():
            continue
        if not st.exists():
            print(f"  {d.name}: not staged, left out")
            continue
        r, s = load(rel), load(st)
        stale = [k for k, v in s["media"].items()
                 if k in r["files"] and r["files"][k]["sha256"] != v["sha256"]]
        if stale:
            print(f"  {d.name}: the media host has older files than the release "
                  f"({', '.join(stale)}): stage again. Left out")
            continue
        missing = [k for k in ("episode.mp3", "episode-art.jpg", "transcript.vtt",
                               "transcript.json", "chapters.json") if k not in s["media"]]
        if missing:
            print(f"  {d.name}: not on the media host: {', '.join(missing)}. Left out")
            continue
        out.append((r, s, d))
    out.sort(key=lambda x: (x[0]["episode"]["release"], int(x[0]["episode"]["number"])))
    due = [e for e in out if e[0]["episode"]["release"] <= as_of]
    later = [e for e in out if e[0]["episode"]["release"] > as_of]
    return due, later


def media(s, relp):
    return s["media"][relp]["url"]


def notes_html(r, d):
    """release.json's notes block names the files; this is the HTML one."""
    return (d / "out" / r["notes"]["html"]).read_text(encoding="utf-8").strip()


def page_notes(r, d):
    """What the directories' description carries that the page does not already
    show: the links, and which of the site's questions the episode touched.
    The deck is the summary and the chapters have their own section."""
    cfg = load(d / "episode.json").get("notes") or {}
    out = []
    links = r["notes"].get("links") or []
    if links:
        out.append("      <h3>Mentioned</h3>\n      <ul>")
        out += [f'        <li><a href="{esc(l["url"])}">{esc(l["title"])}</a></li>' for l in links]
        out.append("      </ul>")
    quoted = cfg.get("questions_quoted") or {}
    nums = [str(n) for n in (r["episode"].get("questions") or []) if str(n) in quoted]
    if nums:
        out.append("      <h3>Of the questions on the site, this one touches</h3>\n      <ul>")
        out += [f'        <li><a href="/#premise">{esc(n)}.</a> {esc(quoted[n])}</li>' for n in nums]
        out.append("      </ul>")
    return "\n".join(out)


# ─── the feed ────────────────────────────────────────────────────────────────
def person_tags(people, indent):
    out = []
    for p in people:
        attrs = f' role="{esc(p.get("role", "host"))}"'
        if p.get("href"):
            attrs += f' href="{esc(p["href"])}"'
        img = portrait(p["name"])
        if img:
            attrs += f' img="https://agentsee.work{img}"'
        out.append(f"{indent}<podcast:person{attrs}>{esc(p['name'])}</podcast:person>")
    return "\n".join(out)


def feed(eps, show, hosts):
    items = []
    for r, s, d in eps:
        ep = r["episode"]
        mp3 = r["files"]["episode.mp3"]
        number = int(ep["number"])
        lines = [
            "    <item>",
            f"      <title>{esc(ep['title'])}</title>",
            f"      <itunes:title>{esc(ep['title'])}</itunes:title>",
            f"      <link>{esc(ep['url'])}</link>",
            f'      <guid isPermaLink="false">{esc(ep["guid"])}</guid>',
            f"      <pubDate>{rfc2822(ep['release'])}</pubDate>",
            f"      <description>{cdata(notes_html(r, d))}</description>",
            f"      <content:encoded>{cdata(notes_html(r, d))}</content:encoded>",
            f'      <enclosure url="{esc(media(s, "episode.mp3"))}" length="{mp3["bytes"]}" type="audio/mpeg"/>',
            f"      <itunes:duration>{int(round(ep['duration']))}</itunes:duration>",
        ]
        # Apple wants a non-zero integer here, so episode 0 carries none: its
        # place in the run comes from the date, and its name says 0 on the site.
        if number > 0:
            lines.append(f"      <itunes:episode>{number}</itunes:episode>")
        if ep.get("season"):
            lines.append(f"      <itunes:season>{int(ep['season'])}</itunes:season>")
        lines += [
            f"      <itunes:episodeType>{esc(ep.get('type') or 'full')}</itunes:episodeType>",
            f"      <itunes:explicit>{'true' if ep.get('explicit') else 'false'}</itunes:explicit>",
            f'      <itunes:image href="{esc(media(s, "episode-art.jpg"))}"/>',
            f'      <podcast:transcript url="{esc(media(s, "transcript.vtt"))}" type="text/vtt" language="en"/>',
            f'      <podcast:transcript url="{esc(media(s, "transcript.json"))}" type="application/json" language="en"/>',
            f'      <podcast:chapters url="{esc(media(s, "chapters.json"))}" type="application/json+chapters"/>',
            person_tags(r["people"], "      "),
        ]
        for sb in r.get("soundbites", []):
            lines.append(f'      <podcast:soundbite startTime="{sb["startTime"]:.3f}" '
                         f'duration="{sb["duration"]:.1f}">{esc(sb["title"])}</podcast:soundbite>')
        lines.append("    </item>")
        items.append("\n".join(lines))

    desc = show["description"]
    cats = "\n".join(f'    <itunes:category text="{esc(c)}"/>' for c in show["categories"])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:podcast="https://podcastindex.org/namespace/1.0"
     xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{esc(show['name'])}</title>
    <link>{esc(show['site'])}/podcast/</link>
    <atom:link href="{esc(show['feed_url'])}" rel="self" type="application/rss+xml"/>
    <description>{esc(desc['paragraph'])}</description>
    <language>{esc(show['language'].lower())}</language>
    <image>
      <url>{esc(show['site'])}{COVER}</url>
      <title>{esc(show['name'])}</title>
      <link>{esc(show['site'])}/podcast/</link>
    </image>
    <itunes:image href="{esc(show['site'])}{COVER}"/>
    <itunes:author>{esc(show['owner']['name'])}</itunes:author>
    <itunes:owner>
      <itunes:name>{esc(show['owner']['name'])}</itunes:name>
      <itunes:email>{esc(show['owner']['email'])}</itunes:email>
    </itunes:owner>
{cats}
    <itunes:explicit>{'true' if show.get('explicit') else 'false'}</itunes:explicit>
    <itunes:type>episodic</itunes:type>
    <podcast:guid>{esc(show['guid'])}</podcast:guid>
    <podcast:locked owner="{esc(show['owner']['email'])}">yes</podcast:locked>
{person_tags(hosts, "    ")}
{chr(10).join(reversed(items))}
  </channel>
</rss>
"""


# ─── the pages ───────────────────────────────────────────────────────────────
def head(title, description, url, image, image_size, extra=""):
    return f"""<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(url)}">
<link rel="alternate" type="application/rss+xml" title="AgentSee Podcast" href="/podcast/feed.xml">

<meta property="og:type" content="website">
<meta property="og:url" content="{esc(url)}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:image" content="{esc(image)}">
<meta property="og:image:width" content="{image_size[0]}">
<meta property="og:image:height" content="{image_size[1]}">
<meta property="og:site_name" content="AgentSee">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{esc(image)}">

<meta name="theme-color" content="#f7f3ea" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0a0a0b" media="(prefers-color-scheme: dark)">

<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 240 210'%3E%3Cpath d='M70 96C68 60 74 40 92 38c8 10 48 10 56 0 18 2 24 22 22 58z' fill='%231a1712'/%3E%3Cellipse cx='120' cy='96' rx='97' ry='14' fill='%231a1712'/%3E%3Cpath d='M44 142c28-38 124-38 152 0-28 36-124 36-152 0z' fill='%23f7f3ea' stroke='%231a1712' stroke-width='9' stroke-linejoin='round'/%3E%3Ccircle cx='120' cy='142' r='30' fill='%23a8431d'/%3E%3Ccircle cx='120' cy='142' r='13' fill='%231a1712'/%3E%3C/svg%3E">

<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">

<link rel="preload" href="/fonts/newsreader-latin.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="/assets/styles.css">
{extra}</head>
<body>

<a class="skip" href="#opening">Skip to content</a>
"""


def dateline(left, mid, right):
    return f"""
<div class="dateline">
  <span class="edge">{left}</span>
  <span class="mid">{mid}</span>
  <span class="edge">{right}</span>
</div>
"""


def people_section(kicker, people, lines):
    cards = []
    for p in people:
        img = portrait(p["name"])
        pic = (f'<img class="portrait" src="{img}" alt="" width="256" height="256" '
               f'loading="lazy" decoding="async">\n          ' if img else "")
        role = (p.get("role") or "host").capitalize()
        body = lines.get(p["name"]) or p.get("line") or ""
        link = f'\n          <p class="links"><a href="{esc(p["href"])}">LinkedIn</a></p>' if p.get("href") else ""
        cards.append(f"""      <article class="person">
        <div class="byline">
          {pic}<div>
            <h3>{esc(p['name'])}</h3>
            <p class="role">{esc(role)}</p>
          </div>
        </div>
        <div class="body">{('<p>' + esc(body) + '</p>') if body else ''}{link}
        </div>
      </article>""")
    return f"""
  <section class="section" id="people">
    <p class="kicker">{kicker}</p>
    <div class="people">
{chr(10).join(cards)}
    </div>
  </section>
"""


def classifieds(parts, kicker):
    return f"""
  <section class="section" id="contact">
    <p class="kicker">{kicker}</p>
    <h2>Everything below reaches both of us directly. No sequence, no drip, no CRM.</h2>

    {parts['ads']}
  </section>
"""


def transcript_turns(segments):
    turns = []
    for seg in segments:
        if turns and turns[-1]["who"] == seg["speaker"]:
            turns[-1]["text"] += " " + seg["body"].strip()
        else:
            turns.append({"who": seg["speaker"], "at": seg["startTime"], "text": seg["body"].strip()})
    out = []
    for t in turns:
        at = int(t["at"])
        out.append(f'      <p class="turn"><a class="at" href="#t={at}" data-t="{at}">{clock(at)}</a>'
                   f'<b class="who">{esc(t["who"])}</b> {esc(t["text"])}</p>')
    return "\n".join(out), len(turns)


def episode_page(r, s, d, parts, show):
    ep = r["episode"]
    n = int(ep["number"])
    title = ep["title"]
    url = ep["url"]
    mp3 = media(s, "episode.mp3")
    art_rel = f"/podcast/{ep['slug']}/art.jpg"
    words = r["transcript"].get("words")
    segments = load(d / "out" / "transcript.json")["segments"]
    own = load(d / "episode.json")  # the edit's record: the YouTube and directory links live here
    turns_html, n_turns = transcript_turns(segments)
    youtube = (own.get("youtube") or {}).get("url")
    # The episode in each directory that lists the show (tools/directories.py
    # finds its own link there); the show's link until then, one click away.
    listed = [(name, (own.get("directories") or {}).get(name), show_url)
              for name, show_url in load(SHOW).get("directories", {}).items()]

    chapters = "\n".join(
        f'      <li><a href="#t={int(c["start"])}" data-t="{int(c["start"])}">{clock(c["start"])}</a>'
        f"<span>{esc(c['title'])}</span></li>" for c in r["chapters"])
    shorts = (own.get("youtube") or {}).get("clips") or {}  # clip index → its Short, once public
    moments = "\n".join(
        f'      <li><a href="#t={int(c["at"])}" data-t="{int(c["at"])}">{clock(c["at"])}</a>'
        f"<span>{esc(c['title'])}"
        + (f' &middot; <a href="{esc(shorts[str(int(c["index"]))])}">on YouTube</a>' if str(int(c["index"])) in shorts else "")
        + "</span></li>" for c in r["clips"])

    ld = {
        "@context": "https://schema.org", "@type": "PodcastEpisode",
        "url": url, "name": title, "datePublished": ep["release"],
        "description": ep["summary"], "duration": iso_duration(ep["duration"]),
        "image": f"https://agentsee.work{art_rel}",
        "associatedMedia": {"@type": "MediaObject", "contentUrl": mp3, "encodingFormat": "audio/mpeg"},
        "partOfSeries": {"@type": "PodcastSeries", "name": show["name"],
                         "url": f"{show['site']}/podcast/", "webFeed": show["feed_url"]},
        "author": [{"@type": "Person", "name": p["name"], **({"url": p["href"]} if p.get("href") else {})}
                   for p in r["people"]],
    }
    if n > 0:
        ld["episodeNumber"] = n
    if any(u for _, u, _ in listed):
        ld["sameAs"] = [u for _, u, _ in listed if u]
    extra = ('<script type="application/ld+json">'
             + json.dumps(ld, ensure_ascii=False).replace("</", "<\\/") + "</script>\n")

    listen = [f'<a href="{esc(mp3)}" download>Download the mp3</a>',
              '<a href="/podcast/feed.xml">RSS feed</a>']
    for name, u, show_url in reversed(listed):
        listen.insert(0, f'<a href="{esc(u or show_url)}">Listen on {esc(name)}</a>')
    if youtube:
        listen.insert(0, f'<a href="{esc(youtube)}">Watch on YouTube</a>')
    lines = {p["name"]: p.get("line") for p in load(SHOW)["people"].values() if p.get("line")}
    lines.update({g.get("name"): g.get("line") for g in (r["notes"].get("guests") or []) if g.get("name")})

    page = head(f"{title} — {show['name']}, episode {n}", ep["summary"], url,
                f"https://agentsee.work{art_rel}", (1400, 1400), extra)
    counter = iter(range(1, 20))

    def kicker(label):
        return f"{next(counter):02d} — {label}"

    page += parts["masthead"]
    page += dateline(esc(show["name"]), f"Episode {n} &middot; {long_date(ep['release'])}",
                     f"{spoken(ep['duration'])} &middot; Free, obviously")
    page += f"""
<main>

  <article class="lead" id="opening">
    <h1>{esc(title)}</h1>
    <p class="deck">{esc(ep['summary'])}</p>
  </article>

  <figure class="plate">
    <audio controls preload="none" data-player src="{esc(mp3)}"></audio>
    <figcaption>Episode {n}, {clock(ep['duration'])}. Chapters and the transcript below seek the player.</figcaption>
    <p class="links listen">
      {chr(10).join('      ' + a for a in listen).strip()}
    </p>
  </figure>

  <section class="section" id="chapters">
    <p class="kicker">{kicker('Chapters')}</p>
    <ol class="chapters">
{chapters}
    </ol>
  </section>
"""
    notes = page_notes(r, d)
    if notes:
        page += f"""
  <section class="section" id="notes">
    <p class="kicker">{kicker('Show notes')}</p>
    <div class="notes">
{notes}
    </div>
  </section>
"""
    if r["clips"]:
        page += f"""
  <section class="section" id="moments">
    <p class="kicker">{kicker('Moments')}</p>
    <p class="aside">The stretches that went out as clips. Each one seeks the player.</p>
    <ol class="chapters">
{moments}
    </ol>
  </section>
"""
    page += people_section(kicker("On this episode"), r["people"], lines)
    page += f"""
  <section class="section" id="transcript">
    <p class="kicker">{kicker('Transcript')}</p>
    <details class="transcript">
      <summary>Read the transcript: {n_turns} turns{f', {words:,} words' if words else ''}</summary>
{turns_html}
    </details>
  </section>
"""
    page += classifieds(parts, kicker("Classifieds"))
    page += "\n</main>\n\n"
    page += footer_for(parts, f"{esc(show['name'])} &middot; Episode {n}", f"Released {long_date(ep['release'])}")
    page += '\n\n<script src="/assets/eye.js" defer></script>\n<script src="/assets/player.js" defer></script>\n</body>\n</html>\n'
    return page


def show_page(due, later, parts, show, hosts):
    desc = show["description"]
    rows = []
    for r, s, d in reversed(later):
        ep = r["episode"]
        rows.append(f"""      <div class="issue pending">
        <span class="issue-no">Episode {int(ep['number'])}</span>
        <span class="issue-title">{esc(ep['title'])}</span>
        <span class="issue-meta">{day_date(ep['release'])}</span>
      </div>""")
    for r, s, d in reversed(due):
        ep = r["episode"]
        rows.append(f"""      <a class="issue" href="/podcast/{esc(ep['slug'])}/">
        <span class="issue-no">Episode {int(ep['number'])}</span>
        <span class="issue-title">{esc(ep['title'])}</span>
        <span class="issue-meta">{long_date(ep['release'])} &middot; {clock(ep['duration'])}</span>
      </a>""")
    eps = due
    count = f"{len(eps)} episode{'s' if len(eps) != 1 else ''}"
    so_far = f"{count} so far" if eps else f"First episode {day_date(later[0][0]['episode']['release'])}"
    paras = "\n".join(f"      <p{' class=\"drop\"' if i == 0 else ''}>{esc(p)}</p>"
                      for i, p in enumerate(desc["long"].split("\n\n")))
    # The directories that have accepted the show, as links, from show.json
    # itself (not the release's copy): a directory is added when it answers,
    # with no re-release.
    listed = "".join(f'\n      <a href="{esc(u)}">{esc(n)}</a>'
                     for n, u in load(SHOW).get("directories", {}).items())
    page = head(f"{show['name']} — {desc['line']}", desc["paragraph"], f"{show['site']}/podcast/",
                f"{show['site']}{COVER}", (3000, 3000))
    page += parts["masthead"]
    page += dateline("The podcast", so_far, "Audio &middot; Free, obviously")
    page += f"""
<main>

  <article class="lead" id="opening">
    <h1>{esc(show['name'])}</h1>
    <p class="deck">{esc(desc['line'])}</p>
  </article>

  <section class="section" id="about">
    <p class="kicker">01 — What it is</p>
    <div class="cols two">
{paras}
    </div>
  </section>

  <section class="section" id="listen">
    <p class="kicker">02 — How to listen</p>
    <h2>In any podcast app, by its feed. The directories follow.</h2>
    <p class="aside">Paste <code>{esc(show['feed_url'])}</code> into the app's "add by URL". Every episode page
      has a player, the chapters, the notes and the transcript. The video of each episode is on YouTube.</p>
    <p class="links listen">
      <a href="/podcast/feed.xml">RSS feed</a>{listed}
      <a href="https://www.youtube.com/@agentseework">YouTube</a>
    </p>
  </section>

  <section class="section" id="episodes">
    <p class="kicker">03 — Episodes</p>
    <div class="register">
{chr(10).join(rows)}
    </div>
  </section>
"""
    page += people_section("04 — Hosts", hosts, {})
    page += classifieds(parts, "05 — Classifieds")
    page += "\n</main>\n\n"
    page += footer_for(parts, f"{esc(show['name'])}", so_far)
    page += '\n\n<script src="/assets/eye.js" defer></script>\n</body>\n</html>\n'
    return page


# ─── the sitemap ─────────────────────────────────────────────────────────────
OPEN, CLOSE = "  <!-- podcast: written by tools/site.py -->", "  <!-- /podcast -->"


def sitemap(eps, show):
    p = PUBLIC / "sitemap.xml"
    s = p.read_text(encoding="utf-8")
    urls = [f"{show['site']}/podcast/"] + [r["episode"]["url"] for r, _, _ in eps]
    block = "\n".join([OPEN] + [f"  <url>\n    <loc>{esc(u)}</loc>\n  </url>" for u in urls] + [CLOSE])
    if OPEN in s:
        s = re.sub(re.escape(OPEN) + r".*?" + re.escape(CLOSE), block, s, count=1, flags=re.S)
    else:
        s = s.replace("</urlset>", block + "\n</urlset>")
    return write(p, s)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", default=dt.datetime.now(LONDON).date().isoformat(),
                    help="the day to write the site for (default today, London)")
    a = ap.parse_args()
    due, later = episodes(a.as_of)
    if not due and not later:
        sys.exit("error: no staged episode to put on the site")
    eps = due
    # The show as release.py wrote it (name, guid, feed URL, description), which
    # is the shape the publisher reads too; the people from show.json itself.
    show = (due or later)[-1][0]["show"]
    hosts = [{"name": p["name"], "role": p.get("role", "host"), "href": p.get("href"), "line": p.get("line")}
             for p in load(SHOW)["people"].values()]
    parts = lifted()
    changed = []
    for r, s, d in eps:
        slug = r["episode"]["slug"]
        if write(PUBLIC / "podcast" / slug / "index.html", episode_page(r, s, d, parts, show)):
            changed.append(f"podcast/{slug}/index.html")
        art = PUBLIC / "podcast" / slug / "art.jpg"
        src = d / "out" / "episode-art.jpg"
        if not art.exists() or art.read_bytes() != src.read_bytes():
            shutil.copyfile(src, art)
            changed.append(f"podcast/{slug}/art.jpg")
    # A page that went up for an earlier date, or a preview, comes down again.
    for r, s, d in later:
        slug = r["episode"]["slug"]
        for name in ("index.html", "art.jpg"):
            f = PUBLIC / "podcast" / slug / name
            if f.exists():
                f.unlink()
                changed.append(f"podcast/{slug}/{name} removed")
        if (PUBLIC / "podcast" / slug).is_dir() and not any((PUBLIC / "podcast" / slug).iterdir()):
            (PUBLIC / "podcast" / slug).rmdir()
    if write(PUBLIC / "podcast" / "index.html", show_page(due, later, parts, show, hosts)):
        changed.append("podcast/index.html")
    if write(PUBLIC / "podcast" / "feed.xml", feed(eps, show, hosts)):
        changed.append("podcast/feed.xml")
    if sitemap(eps, show):
        changed.append("sitemap.xml")
    print(f"as of {a.as_of}: {len(eps)} episode{'s' if len(eps) != 1 else ''} on the site"
          + (": " + ", ".join(r["episode"]["slug"] for r, _, _ in eps) if eps else "")
          + (("; still to come: " + ", ".join(f"{r['episode']['slug']} on {r['episode']['release']}"
                                              for r, _, _ in later)) if later else ""))
    print("  written: " + (", ".join(changed) if changed else "nothing, already current"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
