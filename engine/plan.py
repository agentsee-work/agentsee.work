"""
The plan into posts. release.json carries a day plan (the episode on day 0,
a clip a day after); accounts.json says which accounts get a post for which
asset and at what hour. Each pair becomes a post row with its text drafted,
its media named, its due time set, and a short-link id minted: e1ytc02 is
episode 1, YouTube, clip 2, and nothing else will ever be. Posts that exist
are left alone, so a plan run after an edit changes nothing a person typed;
`prune` is the one exception, for planned posts the plan would no longer
make because an account was dropped or no longer takes that kind of asset.

The short link is what goes in every text, in place of the episode URL, so
the click is counted at the edge; the link itself points at the episode page
(at the clip's moment, for a clip). `links` pushes the id → URL entries to
the KV namespace the /p/ Worker reads; it needs the podcast's Cloudflare
token and is idempotent, so it can run after a plan or any time later.
"""

import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

import ledger

ROOT = pathlib.Path(__file__).resolve().parent.parent
ACCOUNTS = {k: v for k, v in json.loads((ROOT / "engine" / "accounts.json").read_text(encoding="utf-8")).items()
            if k != "note"}


def short(link_id):
    return ledger.CONFIG["short_link"].format(id=link_id)


def hashtags(tags):
    return " ".join("#" + re.sub(r"[^A-Za-z0-9_]", "", t) for t in tags if t)


def clip_for(r, asset):
    stem = asset.split(":", 1)[1]
    for clip in r["clips"]:
        if clip["file"] == f"clips/{stem}.mp4" or pathlib.Path(clip["file"]).stem == stem:
            return clip
    return None


def fit(head, body, tail, limit):
    """A post within a platform's budget: the head (a title, a hook) stays,
    the body gives way a sentence at a time, then the tail (the hashtags);
    the link is in the tail before the tags and is the last thing to go."""
    def build(b, t):
        return "\n\n".join(x for x in (head, b, *t) if x)
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", body.strip()) if x] if body else []
    for n in range(len(sentences), 0, -1):
        text = build(" ".join(sentences[:n]), tail)
        if len(text) <= limit:
            return text
    if sentences and len(build("", tail)) <= limit:
        room = limit - len(build("", tail)) - 2
        if room >= 40:  # a teaser of the first sentence, cut at a word
            teaser = sentences[0][:room - 1].rsplit(" ", 1)[0] + "…"
            return build(teaser, tail)
    for t in (tail, tail[:-1], tail[:1], []):
        text = build("", t)
        if len(text) <= limit:
            return text
    return head[:max(limit - 1, 0)] + "…"


def art_for(r):
    files = [n for n in r["files"] if n.startswith("episode-art.")]
    return next((n for n in files if n.endswith(".jpg")), files[0] if files else None)


def compose(r, asset, account, link_id):
    """Title, text and the media file for one post. The text is a draft: the
    Posts screen lets a person change it before it goes."""
    ep, show = r["episode"], r["show"]
    a = ACCOUNTS[account]
    url = short(link_id)
    limit = a.get("limit", 5000)
    if asset == "episode":
        if a["platform"] == "youtube":
            yt = r["youtube"]
            text = (r.get("_youtube_text") or "").strip() or yt["description"].strip()
            lines = text.split("\n")
            if lines and lines[0].strip() == yt["title"].strip():
                text = "\n".join(lines[1:]).strip()  # youtube.txt opens with the title; the description need not
            text = text.replace(ep["url"], url)
            if url not in text:
                text += f"\n\nEpisode page, with chapters and the transcript: {url}"
            return yt["title"], text[:limit], "episode.mp4"
        tags = hashtags(show.get("hashtags", []))
        return ep["title"], fit(ep["title"], ep["summary"], [url, tags], limit), art_for(r)
    clip = clip_for(r, asset)
    if not clip:
        return None, None, None
    tags = hashtags(clip.get("hashtags") or show.get("hashtags", []))
    post = clip["post"]
    body = post["text"].replace(post.get("link", ""), url)
    hook, _, rest = body.partition("\n\n")
    tail = [url] + ([tags] if tags and a["platform"] != "youtube" else [])
    return clip["title"], fit(hook, rest.replace(url, "").strip(), tail, limit), clip["file"]


def link_id(number, account, asset, r):
    code = ACCOUNTS[account]["code"]
    if asset == "episode":
        return f"e{number}{code}e"
    clip = clip_for(r, asset)
    return f"e{number}{code}c{int(clip['index']):02d}"


def link_url(r, asset):
    if asset == "episode":
        return r["episode"]["url"]
    clip = clip_for(r, asset)
    return clip["post"].get("link_at") or r["episode"]["url"]


def plan(c, slug, who="engine"):
    row = ledger.episode(c, slug)
    if not row:
        sys.exit(f"error: {slug} is not in the ledger: ingest it first")
    r = ledger.release(row)
    src = pathlib.Path(row["source_dir"] or "")
    yt_txt = src / r.get("notes", {}).get("youtube", "youtube.txt")
    if yt_txt.exists():
        r["_youtube_text"] = yt_txt.read_text(encoding="utf-8")
    made = []
    c.execute("BEGIN")
    for entry in r["plan"]:
        asset = entry["asset"]
        kind = "episode" if asset == "episode" else "clip"
        for account, a in ACCOUNTS.items():
            if kind not in a["assets"]:
                continue
            if c.execute("SELECT 1 FROM post WHERE episode=? AND asset=? AND platform=? AND account=?",
                         (slug, asset, a["platform"], account)).fetchone():
                continue
            title, text, media = compose(r, asset, account, link_id(row["number"], account, asset, r))
            if text is None:
                continue
            lid = link_id(row["number"], account, asset, r)
            t = ledger.now()
            cur = c.execute("""INSERT INTO post (episode, asset, platform, account, link_id, link_url, media, title, text,
                                 scheduled_at, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (slug, asset, a["platform"], account, lid, link_url(r, asset), media, title, text,
                             ledger.at(entry["date"], a.get("at", "09:00")), "planned", t, t))
            made.append(cur.lastrowid)
    if made:
        ledger.event(c, "plan", who, episode=slug, detail={"posts": len(made)})
    c.execute("COMMIT")
    return made


def prune(c, slug, who="engine"):
    """Planned posts the plan would no longer make, marked skipped: the account
    is gone from accounts.json, or no longer takes that kind of asset. Only
    `planned`: anything a person or a platform has touched is left alone."""
    gone = []
    for p in c.execute("SELECT id, asset, account FROM post WHERE episode=? AND status='planned' ORDER BY id",
                       (slug,)).fetchall():
        a = ACCOUNTS.get(p["account"])
        kind = "episode" if p["asset"] == "episode" else "clip"
        if a and kind in a["assets"]:
            continue
        why = "the account is gone from accounts.json" if not a else f"{p['account']} no longer takes a {kind}"
        ledger.update_post(c, p["id"], who, status="skipped")
        ledger.event(c, "prune", who, post=p["id"], episode=slug, detail=f"the plan no longer makes this post: {why}")
        gone.append(p["id"])
    return gone


# ─── the short links, into KV ────────────────────────────────────────────────
def kv_put(link_id, value, token=None):
    token = token or os.environ.get("CLOUDFLARE_API_TOKEN")
    if not token:
        raise RuntimeError("CLOUDFLARE_API_TOKEN is not set: run under `op run --env-file=engine/engine.env`")
    cfg = ledger.CONFIG
    url = (f"https://api.cloudflare.com/client/v4/accounts/{cfg['cloudflare_account']}/storage/kv/namespaces/"
           f"{cfg['links_namespace']}/values/{link_id}")
    req = urllib.request.Request(url, data=json.dumps(value).encode(), method="PUT",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"KV answered {e.code}: {e.read().decode(errors='replace')[:300]}")
    if not body.get("success"):
        raise RuntimeError(f"KV refused {link_id}: {body.get('errors')}")


def links(c, slug=None, put=kv_put, who="engine"):
    """Every post's short link into KV. Idempotent; a changed link_url is a changed entry."""
    q = "SELECT id, link_id, link_url, episode, asset, platform FROM post WHERE link_id IS NOT NULL"
    rows = c.execute(q + (" AND episode = ?" if slug else ""), (slug,) if slug else ()).fetchall()
    for p in rows:
        put(p["link_id"], {"url": p["link_url"], "episode": p["episode"], "post": p["asset"], "platform": p["platform"]})
    if rows:
        ledger.event(c, "links", who, episode=slug, detail={"links": len(rows)})
    return len(rows)
