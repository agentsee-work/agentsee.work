#!/usr/bin/env python3
"""
Each episode's own link in the directories that list the show: on Apple
Podcasts and on Spotify, into the episode's episode.json (`directories`),
which site.py puts on the episode page, where the short links land. Until
it is found, the page links the show there, one click away; after, the
episode itself.

Apple is a public lookup with no key: the catalogue's episodes for the
show's id, matched on the feed's guid. Spotify is its Web API, which needs
an app (tools/spotify.env, the vault item `Spotify podcast app`; since
March 2026 the account that owns the app must hold Premium, or the app
stops answering): the show's episodes, matched on title and then release
date, because Spotify does not keep our guid. Without the app, Spotify is
skipped and the run says so.

    ./tools/directories.py                   # every released episode missing a link
    ./tools/directories.py ep1 --refresh     # one, asking again for links it has
    op run --env-file=tools/spotify.env -- ./tools/directories.py

The show's own links come from tools/show.json (`directories`): the Apple
one carries the show's id and store, the Spotify one the show's id. Only
`directories` in episode.json is written, and only what was found. A
directory lists an episode some hours after its release, so the run on the
morning finds nothing and a run later does; nothing is lost by asking early.
"""

import argparse
import base64
import datetime as dt
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
EPISODES = ROOT / "build" / "episodes"
SHOW = ROOT / "tools" / "show.json"
LONDON = ZoneInfo("Europe/London")
APPLE = "https://itunes.apple.com/lookup"
SPOTIFY_TOKEN = "https://accounts.spotify.com/api/token"
SPOTIFY_API = "https://api.spotify.com/v1"
UA = "agentsee-directories/1 (+https://agentsee.work/podcast/)"


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save(p, data):
    pathlib.Path(p).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def get_json(url, headers=None, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def released(as_of, only=()):
    """(episode dir, release's episode record) for every staged episode whose
    release day has come, in release order."""
    out = []
    for d in sorted(EPISODES.iterdir()) if EPISODES.exists() else []:
        rel = d / "out" / "release.json"
        if not rel.exists() or (only and d.name not in only):
            continue
        e = load(rel)["episode"]
        if e["release"] <= as_of:
            out.append((d, e))
    return out


# ─── Apple Podcasts ──────────────────────────────────────────────────────────

def apple_show(url):
    """(store, show id) from the show's Apple link."""
    m = re.search(r"podcasts\.apple\.com/(?:([a-z]{2})/)?.*?id(\d+)", url)
    return (m.group(1) or "us", m.group(2)) if m else (None, None)


def apple_episodes(show_id):
    q = urllib.parse.urlencode({"id": show_id, "media": "podcast", "entity": "podcastEpisode", "limit": 200})
    return [r for r in get_json(f"{APPLE}?{q}").get("results", []) if r.get("wrapperType") == "podcastEpisode"]


def apple_link(results, guid, store):
    """The episode's link in the given store: Apple's own URL for it, with
    the lookup's store and tracking query replaced by ours and none."""
    for r in results:
        if r.get("episodeGuid") == guid and r.get("trackViewUrl"):
            u = urllib.parse.urlsplit(r["trackViewUrl"])
            q = [(k, v) for k, v in urllib.parse.parse_qsl(u.query) if k == "i"]
            path = re.sub(r"^/[a-z]{2}/", f"/{store}/", u.path)
            return urllib.parse.urlunsplit((u.scheme, u.netloc, path, urllib.parse.urlencode(q), ""))
    return None


# ─── Spotify ─────────────────────────────────────────────────────────────────

def spotify_show(url):
    m = re.search(r"open\.spotify\.com/show/([A-Za-z0-9]+)", url)
    return m.group(1) if m else None


def spotify_token(env):
    """Client credentials: the public catalogue and nothing of any user."""
    basic = base64.b64encode(f"{env['SPOTIFY_CLIENT_ID']}:{env['SPOTIFY_CLIENT_SECRET']}".encode()).decode()
    d = get_json(SPOTIFY_TOKEN, data=b"grant_type=client_credentials",
                 headers={"Authorization": "Basic " + basic, "Content-Type": "application/x-www-form-urlencoded"})
    return d["access_token"]


def spotify_episodes(show_id, token, market):
    """Every episode of the show, as Spotify lists them for the market (without
    a market Spotify calls everything unavailable)."""
    items = []
    url = f"{SPOTIFY_API}/shows/{show_id}/episodes?" + urllib.parse.urlencode({"market": market, "limit": 50})
    while url:
        d = get_json(url, headers={"Authorization": "Bearer " + token})
        items += [i for i in d.get("items", []) if i]
        url = d.get("next")
    return items


def spotify_link(items, title, release):
    """By title, then by release day when a title repeats."""
    same = [i for i in items if (i.get("name") or "").strip() == title.strip()]
    if len(same) > 1:
        same = [i for i in same if i.get("release_date") == release]
    if len(same) == 1:
        return (same[0].get("external_urls") or {}).get("spotify") or f"https://open.spotify.com/episode/{same[0]['id']}"
    return None


# ─── The run ─────────────────────────────────────────────────────────────────

def sources(show, env, log=print):
    """name → a function of the release's episode record giving its link or
    None, for each directory in show.json this tool can ask."""
    out = {}
    dirs = show.get("directories", {})
    if dirs.get("Apple Podcasts"):
        store, show_id = apple_show(dirs["Apple Podcasts"])
        try:
            results = apple_episodes(show_id)
            out["Apple Podcasts"] = lambda e: apple_link(results, e["guid"], store)
        except (urllib.error.URLError, ValueError, KeyError) as err:
            log(f"  Apple Podcasts: not asked, {err}")
    if dirs.get("Spotify"):
        if env.get("SPOTIFY_CLIENT_ID") and env.get("SPOTIFY_CLIENT_SECRET"):
            market = (show.get("language", "en-GB").split("-") + ["GB"])[1].upper()
            try:
                items = spotify_episodes(spotify_show(dirs["Spotify"]), spotify_token(env), market)
                out["Spotify"] = lambda e: spotify_link(items, e["title"], e["release"])
            except (urllib.error.URLError, ValueError, KeyError) as err:
                log(f"  Spotify: not asked, {err}")
        else:
            log("  Spotify: skipped, SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET not set "
                "(op run --env-file=tools/spotify.env)")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slugs", nargs="*", help="episodes to ask for (default: every released one)")
    ap.add_argument("--refresh", action="store_true", help="ask again for links an episode already has")
    ap.add_argument("--as-of", default=dt.datetime.now(LONDON).date().isoformat(),
                    help="the day (default today, London); an episode is asked for once released")
    a = ap.parse_args()
    eps = released(a.as_of, set(a.slugs))
    if not eps:
        sys.exit("nothing released to look for" + (f" among {', '.join(a.slugs)}" if a.slugs else ""))
    look = sources(load(SHOW), os.environ)
    changed = 0
    for d, e in eps:
        path = d / "episode.json"
        ej = load(path)
        have = dict(ej.get("directories") or {})
        found = {}
        for name, fn in look.items():
            if name in have and not a.refresh:
                continue
            url = fn(e)
            if url:
                found[name] = url
        if found:
            have.update(found)
            ej["directories"] = have
            save(path, ej)
            changed += 1
        said = ", ".join(f"{n}: " + ("found" if n in found else "kept" if n in have else "not there yet") for n in look)
        print(f"  {e['slug']}: {said or 'nothing to ask'}")
    print(f"{changed} episode{'s' if changed != 1 else ''} written" + ("; now run tools/site.py" if changed else ""))


if __name__ == "__main__":
    main()
