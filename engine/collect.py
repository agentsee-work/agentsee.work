#!/usr/bin/env python3
"""
The collector: what each post and each file did, read back into the ledger
as dated snapshots, so there is a time series even where a platform only
ever says a running total.

    ./engine/engine.py collect                 # every source, now
    ./engine/engine.py collect --only edge     # one of them

The worker does it once a day at `collect_at` (config.json). Each source is
a function below that takes the ledger, the environment, the instant of the
run and a log, and returns one line for the record:

    edge      the two Analytics Engine datasets the Workers write
              (infra/podcast/workers/README.md): one row per request to the
              media host into `download`, one per short-link click into
              `click`, the user agent classified with the OPAWG lists in
              useragents.json. The last day is read again every run, so a
              row that arrived late is not missed and nothing counts twice.
    youtube   videos.list for views, likes, comments and the video's state
              (a rejected upload fails the post with the reason; a scheduled
              one that went public is marked published), then the Analytics
              API for watch time, average view length and share, shares and
              subscribers gained, which run two or three days behind.
    bluesky   the public API, no token: likes, reposts, replies, quotes and
              bookmarks, from the permalink a person pasted back.
    mastodon  the instance's public status endpoint, no token: favourites,
              boosts, replies.
    x         the owned-post lookup with the account's token, once the X
              adapter is on: impressions, likes, reposts, replies, quotes,
              bookmarks, link clicks, profile clicks. A cent per thousand
              reads; X forgets after 30 days, so the snapshots are the record.

A listen, for the Reach screen, is the IAB's idea of one: one address hash
per file per day, from a 200 or 206 that carried at least a minute's worth
of the file, by something that is not a bot or an HTTP library. Bytes
offered, not heard; nothing here knows more than the Worker logged.
"""

import datetime as dt
import json
import math
import os
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request

import ledger
from plan import ACCOUNTS

ROOT = pathlib.Path(__file__).resolve().parent
UA = json.loads((ROOT / "useragents.json").read_text(encoding="utf-8"))
KIND = {"bots": "bot", "apps": "app", "libraries": "library", "browsers": "browser"}
SQL_URL = "https://api.cloudflare.com/client/v4/accounts/{account}/analytics_engine/sql"
ANALYTICS = "https://youtubeanalytics.googleapis.com/v2"
BSKY = "https://public.api.bsky.app/xrpc"
OVERLAP = dt.timedelta(days=1)  # the edge is read again this far back every run
REACH = dt.timedelta(days=90)  # and this far back the first time; Analytics Engine keeps three months
YT_METRICS = (
    "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,comments,shares,subscribersGained"
)
YT_NAMES = {
    "estimatedMinutesWatched": "watch_minutes",
    "averageViewDuration": "avg_view_seconds",
    "averageViewPercentage": "avg_view_pct",
    "subscribersGained": "subscribers_gained",
}


class CollectError(Exception):
    pass


# ─── user agents ─────────────────────────────────────────────────────────────
_patterns = None


def classify(ua):
    """(kind, name): bot, app, library, browser or unknown, and the name the list gives it."""
    global _patterns
    if _patterns is None:
        _patterns = [
            (KIND[k], r["name"], re.compile(r["pattern"]))
            for k in ("bots", "apps", "libraries", "browsers")
            for r in UA[k]
        ]
    s = (ua or "").replace("\n", " ")
    for kind, name, rx in _patterns:
        if rx.search(s):
            return kind, name
    return "unknown", ""


def ua_class(ua):
    kind, name = classify(ua)
    return f"{kind}:{name}" if name else kind


# ─── http ────────────────────────────────────────────────────────────────────
def http(method, url, body=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.read()


def get_json(url, headers=None):
    st, body = http("GET", url, headers=headers)
    if st != 200:
        raise CollectError(f"{url.split('?')[0]} said {st}: {body[:200].decode(errors='replace')}")
    return json.loads(body)


def snap(c, post, metrics, raw, at):
    """One row per metric; the platform's answer as it came, once."""
    first = True
    for m, v in metrics.items():
        if v is None:
            continue
        c.execute(
            "INSERT INTO snapshot (post, account, platform, metric, observed_at, value, raw) VALUES (?,?,?,?,?,?,?)",
            (
                post["id"],
                post["account"],
                post["platform"],
                m,
                at,
                float(v),
                json.dumps(raw) if first and raw is not None else None,
            ),
        )
        first = False


# ─── the edge ────────────────────────────────────────────────────────────────
def ae_time(s):
    """Analytics Engine's 'YYYY-MM-DD HH:MM:SS', UTC, as an ISO instant."""
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).isoformat()


def sql(query, env):
    url = SQL_URL.format(account=ledger.CONFIG["cloudflare_account"])
    st, body = http(
        "POST",
        url,
        query.encode(),
        {"Authorization": f"Bearer {env['CLOUDFLARE_API_TOKEN']}", "Content-Type": "text/plain"},
    )
    if st != 200:
        raise CollectError(f"Analytics Engine said {st}: {body[:300].decode(errors='replace')}")
    return json.loads(body).get("data", [])


def window(c, table, at):
    """(start, hours): from the newest row held, less the overlap, or the full reach."""
    last = c.execute(f"SELECT MAX(at) FROM {table}").fetchone()[0]
    now = dt.datetime.fromisoformat(at)
    start = max(dt.datetime.fromisoformat(last) - OVERLAP if last else now - REACH, now - REACH)
    return start.isoformat(), max(1, math.ceil((now - start).total_seconds() / 3600))


def edge(c, env, at, log):
    if not env.get("CLOUDFLARE_API_TOKEN"):
        return "skipped: CLOUDFLARE_API_TOKEN not set"
    ds = ledger.CONFIG["datasets"]
    start, hours = window(c, "download", at)
    rows = sql(
        f"SELECT timestamp, index1, blob3, blob4, blob5, blob6, double1, double2, double3, double4, double5 "
        f"FROM {ds['media']} WHERE timestamp > NOW() - INTERVAL '{hours}' HOUR ORDER BY timestamp "
        f"LIMIT 100000 FORMAT JSON",
        env,
    )
    c.execute("BEGIN")
    c.execute("DELETE FROM download WHERE at >= ?", (start,))
    n = 0
    for r in rows:
        t = ae_time(r["timestamp"])
        if t < start:
            continue
        m = re.fullmatch(r"podcast/([a-z0-9-]+)/(.+)", r.get("index1") or "")
        episode, name = (m.group(1), m.group(2)) if m else (None, r.get("index1") or "")
        c.execute(
            "INSERT INTO download (episode, name, at, ua_class, hash, bytes, first, last, status, country) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                episode,
                name,
                t,
                ua_class(r.get("blob3")),
                r.get("blob5"),
                int(r.get("double2") or 0),
                int(r["double3"]) if r.get("double3") is not None else -1,
                int(r["double4"]) if r.get("double4") is not None else -1,
                int(r.get("double1") or 0),
                r.get("blob4"),
            ),
        )
        n += 1
    c.execute("COMMIT")
    start2, hours2 = window(c, "click", at)
    rows = sql(
        f"SELECT timestamp, index1, blob2, blob3, blob4, blob5, double1 FROM {ds['links']} "
        f"WHERE timestamp > NOW() - INTERVAL '{hours2}' HOUR ORDER BY timestamp LIMIT 100000 FORMAT JSON",
        env,
    )
    c.execute("BEGIN")
    c.execute("DELETE FROM click WHERE at >= ?", (start2,))
    k = 0
    for r in rows:
        t = ae_time(r["timestamp"])
        if t < start2 or int(r.get("double1") or 0) != 302:
            continue
        c.execute(
            "INSERT INTO click (link_id, at, country, ua_class, hash, referer) VALUES (?,?,?,?,?,?)",
            (r.get("index1"), t, r.get("blob3"), ua_class(r.get("blob2")), r.get("blob4"), r.get("blob5")),
        )
        k += 1
    c.execute("COMMIT")
    return f"{n} requests and {k} clicks since {start[:16]}"


# ─── YouTube ─────────────────────────────────────────────────────────────────
def youtube(c, env, at, log):
    from publish import youtube as yt

    posts = c.execute(
        "SELECT * FROM post WHERE account='youtube' AND remote_id IS NOT NULL "
        "AND status IN ('scheduled', 'published') ORDER BY id"
    ).fetchall()
    if not posts:
        return "nothing to read"
    if not yt.ready(env):
        return f"skipped: {yt.NEEDS} not set"
    tok = yt.token(env)
    by_id = {p["remote_id"]: p for p in posts}
    ids = list(by_id)
    read = 0
    for i in range(0, len(ids), 50):
        q = urllib.parse.urlencode({"part": "statistics,status", "id": ",".join(ids[i : i + 50])})
        for item in get_json(f"{yt.API}/videos?{q}", {"Authorization": f"Bearer {tok}"}).get("items", []):
            p = by_id.get(item["id"])
            if not p:
                continue
            s, st = item.get("statistics", {}), item.get("status", {})
            snap(
                c,
                p,
                {"views": s.get("viewCount"), "likes": s.get("likeCount"), "comments": s.get("commentCount")},
                item,
                at,
            )
            read += 1
            if st.get("uploadStatus") == "rejected" and p["status"] != "failed":
                why = (
                    f"YouTube rejected the video ({st.get('rejectionReason') or 'no reason given'}); "
                    f"delete it there, then publish --post {p['id']}"
                )
                ledger.update_post(c, p["id"], status="failed", last_error=why)
                ledger.event(c, "collect:rejected", "collector", post=p["id"], episode=p["episode"], detail=why)
                log(f"  post {p['id']}: {why}")
            elif st.get("privacyStatus") == "public" and p["status"] == "scheduled":
                ledger.update_post(c, p["id"], status="published", published_at=p["published_at"] or at)
                ledger.event(c, "collect:public", "collector", post=p["id"], episode=p["episode"], detail=item["id"])
    # Lifetime so far, per video. Two or three days behind the counts above.
    start = min((p["published_at"] or at)[:10] for p in posts)
    q = urllib.parse.urlencode(
        {
            "ids": "channel==MINE",
            "startDate": start,
            "endDate": at[:10],
            "dimensions": "video",
            "filters": "video==" + ",".join(ids[:500]),
            "metrics": YT_METRICS,
            "sort": "-views",
            "maxResults": 200,
        }
    )
    d = get_json(f"{ANALYTICS}/reports?{q}", {"Authorization": f"Bearer {tok}"})
    cols = [h["name"] for h in d.get("columnHeaders", [])]
    n = 0
    for row in d.get("rows", []):
        r = dict(zip(cols, row))
        p = by_id.get(r.get("video"))
        if not p:
            continue
        snap(
            c,
            p,
            {YT_NAMES.get(k, k): v for k, v in r.items() if k not in ("video", "views", "likes", "comments")},
            None,
            at,
        )
        n += 1
    return f"{read} videos, analytics for {n}"


# ─── Bluesky, Mastodon ───────────────────────────────────────────────────────
_did = {}


def bluesky(c, env, at, log):
    posts = c.execute(
        "SELECT * FROM post WHERE platform='bluesky' AND status='published' AND permalink IS NOT NULL"
    ).fetchall()
    if not posts:
        return "nothing to read"
    uris = {}
    for p in posts:
        if (p["remote_id"] or "").startswith("at://"):  # the adapter's own record: no handle to resolve
            uris[p["remote_id"]] = p
            continue
        m = re.search(r"bsky\.app/profile/([^/]+)/post/([\w.:-]+)", p["permalink"])
        if not m:
            continue
        actor, rkey = m.group(1), m.group(2)
        if not actor.startswith("did:"):
            if actor not in _did:
                _did[actor] = get_json(f"{BSKY}/com.atproto.identity.resolveHandle?handle={urllib.parse.quote(actor)}")[
                    "did"
                ]
            actor = _did[actor]
        uris[f"at://{actor}/app.bsky.feed.post/{rkey}"] = p
    keys, n = list(uris), 0
    for i in range(0, len(keys), 25):
        q = "&".join("uris=" + urllib.parse.quote(u, safe="") for u in keys[i : i + 25])
        for item in get_json(f"{BSKY}/app.bsky.feed.getPosts?{q}").get("posts", []):
            p = uris.get(item.get("uri"))
            if not p:
                continue
            snap(
                c,
                p,
                {
                    "likes": item.get("likeCount"),
                    "reposts": item.get("repostCount"),
                    "replies": item.get("replyCount"),
                    "quotes": item.get("quoteCount"),
                    "bookmarks": item.get("bookmarkCount"),
                },
                {k: v for k, v in item.items() if k.endswith("Count") or k in ("uri", "cid", "indexedAt")},
                at,
            )
            n += 1
    return f"{n} posts"


def mastodon(c, env, at, log):
    posts = c.execute(
        "SELECT * FROM post WHERE platform='mastodon' AND status='published' AND permalink IS NOT NULL"
    ).fetchall()
    if not posts:
        return "nothing to read"
    n = 0
    for p in posts:
        m = re.match(r"(https?://[^/]+)/@[^/]+/(\d+)", p["permalink"])
        if not m:
            continue
        d = get_json(f"{m.group(1)}/api/v1/statuses/{m.group(2)}")
        snap(
            c,
            p,
            {"likes": d.get("favourites_count"), "reposts": d.get("reblogs_count"), "replies": d.get("replies_count")},
            {k: d.get(k) for k in ("id", "url", "created_at", "favourites_count", "reblogs_count", "replies_count")},
            at,
        )
        n += 1
    return f"{n} posts"


# ─── X ───────────────────────────────────────────────────────────────────────
def x(c, env, at, log):
    if ACCOUNTS.get("x", {}).get("adapter") != "x":
        return "skipped: the X adapter is off"
    from publish import x as xa

    posts = c.execute(
        "SELECT * FROM post WHERE platform='x' AND status='published' "
        "AND (remote_id IS NOT NULL OR permalink IS NOT NULL)"
    ).fetchall()
    if not posts:
        return "nothing to read"
    if not xa.ready(env):
        return f"skipped: {xa.NEEDS} not set"
    by_id = {}
    for p in posts:
        m = re.search(r"/status/(\d+)", p["permalink"] or "")
        pid = p["remote_id"] or (m.group(1) if m else None)
        if pid:
            by_id[pid] = p
    tok = xa.token(env, log)
    ids, n = list(by_id), 0
    for i in range(0, len(ids), 100):
        q = urllib.parse.urlencode(
            {"ids": ",".join(ids[i : i + 100]), "tweet.fields": "public_metrics,non_public_metrics"}
        )
        for item in get_json(f"{xa.API}/tweets?{q}", {"Authorization": f"Bearer {tok}"}).get("data", []):
            p = by_id.get(item.get("id"))
            if not p:
                continue
            pm, npm = item.get("public_metrics", {}), item.get("non_public_metrics", {})
            snap(
                c,
                p,
                {
                    "impressions": pm.get("impression_count"),
                    "likes": pm.get("like_count"),
                    "reposts": pm.get("retweet_count"),
                    "replies": pm.get("reply_count"),
                    "quotes": pm.get("quote_count"),
                    "bookmarks": pm.get("bookmark_count"),
                    "link_clicks": npm.get("url_link_clicks"),
                    "profile_clicks": npm.get("user_profile_clicks"),
                },
                item,
                at,
            )
            n += 1
    return f"{n} posts"


# ─── the run ─────────────────────────────────────────────────────────────────
SOURCES = {"edge": edge, "youtube": youtube, "bluesky": bluesky, "mastodon": mastodon, "x": x}


def run(c, only=None, env=None, who="engine", log=print):
    env = os.environ if env is None else env
    at = ledger.now()
    results = {}
    for name, fn in SOURCES.items():
        if only and name not in only:
            continue
        try:
            results[name] = fn(c, env, at, log)
        except Exception as e:  # noqa: BLE001 — one source failing must not stop the others
            if c.in_transaction:
                c.execute("ROLLBACK")
            results[name] = f"failed: {type(e).__name__}: {e}"[:500]
            ledger.event(c, "collect:failed", who, detail={"source": name, "error": results[name]})
        log(f"  {name}: {results[name]}")
    ledger.event(c, "collect:run", who, detail=results)
    return results


# ─── what the screens ask ────────────────────────────────────────────────────
LISTEN = (
    "FROM download d JOIN deliverable v ON v.episode = d.episode AND v.name = d.name "
    "WHERE d.status IN (200, 206) AND d.name LIKE 'episode.%' "
    "AND d.ua_class NOT LIKE 'bot:%' AND d.ua_class NOT LIKE 'library:%' "
    "AND d.bytes >= CASE WHEN v.duration > 0 THEN v.bytes * 60.0 / v.duration ELSE 1 END"
)
ONE = "d.hash || '/' || d.name || '/' || substr(d.at, 1, 10)"  # a listener, a file, a day


def since(days):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()


def listens(c, days=None):
    """{episode: {day: listens}}, every day or the last `days`."""
    cond, args = (" AND d.at >= ?", (since(days),)) if days else ("", ())
    out = {}
    for r in c.execute(
        f"SELECT d.episode, substr(d.at, 1, 10) AS day, COUNT(DISTINCT {ONE}) AS n {LISTEN}{cond} "
        "GROUP BY 1, 2 ORDER BY 1, 2",
        args,
    ):
        out.setdefault(r["episode"], {})[r["day"]] = r["n"]
    return out


def by_app(c, days=30):
    return [
        (r["ua_class"], r["n"])
        for r in c.execute(
            f"SELECT d.ua_class, COUNT(DISTINCT {ONE}) AS n {LISTEN} AND d.at >= ? GROUP BY 1 ORDER BY n DESC, 1",
            (since(days),),
        )
    ]


def by_country(c, days=30):
    return [
        (r["country"], r["n"])
        for r in c.execute(
            f"SELECT d.country, COUNT(DISTINCT {ONE}) AS n {LISTEN} AND d.at >= ? GROUP BY 1 ORDER BY n DESC, 1",
            (since(days),),
        )
    ]


def clicks(c, days=None):
    """{link_id: clicks}."""
    cond, args = (" WHERE at >= ?", (since(days),)) if days else ("", ())
    return {r["link_id"]: r["n"] for r in c.execute(f"SELECT link_id, COUNT(*) AS n FROM click{cond} GROUP BY 1", args)}


def clicks_from(c, days=30):
    """[(referer origin, clicks)], where the clicks came from."""
    return [
        (r["referer"] or "no referer", r["n"])
        for r in c.execute(
            "SELECT referer, COUNT(*) AS n FROM click WHERE at >= ? GROUP BY 1 ORDER BY n DESC, 1", (since(days),)
        )
    ]


def latest(c):
    """{post: {metric: {"now": newest value, "week": the value a week or more ago, or None}}}."""
    week, out = since(7), {}
    for r in c.execute("SELECT post, metric, observed_at, value FROM snapshot ORDER BY post, metric, observed_at"):
        m = out.setdefault(r["post"], {}).setdefault(r["metric"], {"now": None, "week": None})
        m["now"] = r["value"]
        if r["observed_at"] <= week:
            m["week"] = r["value"]
    return out
