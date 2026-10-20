#!/usr/bin/env python3
"""
Each episode's own links where it is: on YouTube, on Apple Podcasts and on
Spotify, into the episode's episode.json (`youtube`, `directories`), which
site.py puts on the episode page, where the short links land. Until a
directory's link is found, the page links the show there, one click away;
after, the episode itself.

YouTube is the channel's own uploads, under the channel's token
(tools/directories.env): the episode's video and each clip's Short, told
apart by the short link the engine put in every description (engine/plan.py
mints e<n>yte for the episode and e<n>ytc<ii> for clip ii), the episode by
its title as a fallback; public ones only, so a Short scheduled for a later
morning is picked up by a later run. Apple is a public lookup with no key:
the catalogue's episodes for the show's id, matched on the feed's guid.
Spotify is its Web API, which needs an app (the vault item `Spotify podcast
app`; since March 2026 the account that owns the app must hold Premium, or
the app stops answering): the show's episodes, matched on title and then
release date, because Spotify does not keep our guid. A source without its
credentials is skipped and the run says so.

    ./tools/directories.py                   # Apple only: no credentials
    op run --env-file=tools/directories.env -- ./tools/directories.py
    op run --env-file=tools/directories.env -- ./tools/directories.py ep1 --refresh

The show's own links come from tools/show.json (`directories`): the Apple
one carries the show's id and store, the Spotify one the show's id. Only
`youtube` and `directories` in episode.json are written, and only what was
found. A directory lists an episode some hours after its release and the
Shorts go public one a day, so a run on the morning finds some of it and a
run later finds more; nothing is lost by asking early or often.
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
YT_TOKEN = "https://oauth2.googleapis.com/token"
YT_API = "https://www.googleapis.com/youtube/v3"
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
    """(episode dir, release record) for every staged episode whose release
    day has come, in release order."""
    out = []
    for d in sorted(EPISODES.iterdir()) if EPISODES.exists() else []:
        rel = d / "out" / "release.json"
        if not rel.exists() or (only and d.name not in only):
            continue
        r = load(rel)
        if r["episode"]["release"] <= as_of:
            out.append((d, r))
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


# ─── YouTube ─────────────────────────────────────────────────────────────────

def youtube_token(env):
    data = urllib.parse.urlencode({
        "client_id": env["YOUTUBE_CLIENT_ID"], "client_secret": env["YOUTUBE_CLIENT_SECRET"],
        "refresh_token": env["YOUTUBE_REFRESH_TOKEN"], "grant_type": "refresh_token"}).encode()
    return get_json(YT_TOKEN, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})["access_token"]


def youtube_uploads(token):
    """Every video on the channel, as the owner sees them: id, title,
    description, and whether it is public yet."""
    h = {"Authorization": "Bearer " + token}
    ch = get_json(f"{YT_API}/channels?part=contentDetails&mine=true", headers=h)
    uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    out, page = [], None
    while True:
        q = {"part": "snippet,status", "playlistId": uploads, "maxResults": 50}
        if page:
            q["pageToken"] = page
        d = get_json(f"{YT_API}/playlistItems?" + urllib.parse.urlencode(q), headers=h)
        for it in d.get("items", []):
            sn = it.get("snippet") or {}
            if (sn.get("resourceId") or {}).get("kind") == "youtube#video":
                out.append({"id": sn["resourceId"]["videoId"], "title": sn.get("title") or "",
                            "description": sn.get("description") or "",
                            "public": (it.get("status") or {}).get("privacyStatus") == "public"})
        page = d.get("nextPageToken")
        if not page:
            return out


def youtube_links(videos, e, yt_title, clips):
    """The episode's watch URL and each clip's Short, from the public videos
    whose description carries the post's short link; the episode by its
    title when the link is not there. {"url": ..., "clips": {index: url}},
    each key only when found."""
    n = int(e["number"])

    def by_link(link_id):
        pat = re.compile(rf"/p/{re.escape(link_id)}(?!\w)")
        return next((v for v in videos if v["public"] and pat.search(v["description"])), None)

    out = {}
    ep = by_link(f"e{n}yte") or next((v for v in videos if v["public"] and yt_title and v["title"] == yt_title), None)
    if ep:
        out["url"] = f"https://www.youtube.com/watch?v={ep['id']}"
    found = {}
    for c in clips:
        v = by_link(f"e{n}ytc{int(c['index']):02d}")
        if v:
            found[str(int(c["index"]))] = f"https://www.youtube.com/shorts/{v['id']}"
    if found:
        out["clips"] = found
    return out


# ─── The run ─────────────────────────────────────────────────────────────────

def sources(show, env, log=print):
    """name → a function of (the release's episode record, the release) giving
    what was found: a link or None for a directory in show.json; for
    YouTube, {"url", "clips"} with only what is public."""
    out = {}
    if all(env.get(k) for k in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN")):
        try:
            videos = youtube_uploads(youtube_token(env))
            out["YouTube"] = lambda e, r: youtube_links(videos, e, (r.get("youtube") or {}).get("title"), r.get("clips") or [])
        except (urllib.error.URLError, ValueError, KeyError, IndexError) as err:
            log(f"  YouTube: not asked, {err}")
    else:
        log("  YouTube: skipped, YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN not set "
            "(op run --env-file=tools/directories.env)")
    dirs = show.get("directories", {})
    if dirs.get("Apple Podcasts"):
        store, show_id = apple_show(dirs["Apple Podcasts"])
        try:
            results = apple_episodes(show_id)
            out["Apple Podcasts"] = lambda e, r: apple_link(results, e["guid"], store)
        except (urllib.error.URLError, ValueError, KeyError) as err:
            log(f"  Apple Podcasts: not asked, {err}")
    if dirs.get("Spotify"):
        if env.get("SPOTIFY_CLIENT_ID") and env.get("SPOTIFY_CLIENT_SECRET"):
            market = (show.get("language", "en-GB").split("-") + ["GB"])[1].upper()
            try:
                items = spotify_episodes(spotify_show(dirs["Spotify"]), spotify_token(env), market)
                out["Spotify"] = lambda e, r: spotify_link(items, e["title"], e["release"])
            except (urllib.error.URLError, ValueError, KeyError) as err:
                log(f"  Spotify: not asked, {err}")
        else:
            log("  Spotify: skipped, SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET not set "
                "(op run --env-file=tools/directories.env, once the app exists)")
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
    for d, r in eps:
        e = r["episode"]
        path = d / "episode.json"
        ej = load(path)
        dirs = dict(ej.get("directories") or {})
        yt = dict(ej.get("youtube") or {})
        said, wrote = [], False
        for name, fn in look.items():
            if name == "YouTube":
                have_all = yt.get("url") and len(yt.get("clips") or {}) >= len(r.get("clips") or [])
                if have_all and not a.refresh:
                    said.append("YouTube: kept")
                    continue
                got = fn(e, r)
                if got.get("url") and (a.refresh or not yt.get("url")):
                    yt["url"], wrote = got["url"], True
                clips = {**(yt.get("clips") or {}), **got.get("clips", {})}
                if clips != (yt.get("clips") or {}):
                    yt["clips"], wrote = clips, True
                said.append(f"YouTube: episode {'found' if got.get('url') else 'kept' if yt.get('url') else 'not public yet'}, "
                            f"Shorts {len(yt.get('clips') or {})} of {len(r.get('clips') or [])} public")
                continue
            if name in dirs and not a.refresh:
                said.append(f"{name}: kept")
                continue
            url = fn(e, r)
            if url:
                dirs[name], wrote = url, True
            said.append(f"{name}: {'found' if url else 'not there yet'}")
        if wrote:
            if dirs:
                ej["directories"] = dirs
            if yt:
                ej["youtube"] = yt
            save(path, ej)
            changed += 1
        print(f"  {e['slug']}: {', '.join(said) or 'nothing to ask'}")
    print(f"{changed} episode{'s' if changed != 1 else ''} written" + ("; now run tools/site.py" if changed else ""))


if __name__ == "__main__":
    main()
