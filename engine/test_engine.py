"""
The engine against a made-up episode, a stub YouTube and a fake KV:

    python3 engine/test_engine.py

No dependencies, nothing touched outside a temporary directory. What is
checked is the engine's own logic, from the ledger to the screens: the
platform is checked by the first real run.
"""

import contextlib
import datetime as dt
import http.server
import io
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ingest  # noqa: E402
import ledger  # noqa: E402
import plan  # noqa: E402
import publish  # noqa: E402
import web  # noqa: E402
from publish import youtube  # noqa: E402

DAY0 = (dt.date.today() + dt.timedelta(days=10)).isoformat()
DAY1 = (dt.date.today() + dt.timedelta(days=11)).isoformat()
EP_URL = "https://agentsee.work/podcast/ep9/"
MEDIA = "https://media.agentsee.work/podcast/ep9/"


def fixture(root):
    out = root / "ep9" / "out"
    (out / "clips").mkdir(parents=True)
    files = {
        "episode.mp4": os.urandom(3 * 256 * 1024 + 1000),  # three chunks and a bit, at the test chunk size
        "episode.mp3": b"ID3mp3" * 100, "captions.srt": b"1\n00:00:00,000 --> 00:00:01,000\nHello\n",
        "thumbnail.png": b"\x89PNG" + b"\0" * 100, "episode-art.jpg": b"\xff\xd8" + b"\0" * 50,
        "clips/01-the-point.mp4": os.urandom(256 * 1024 + 7), "clips/01-the-point.png": b"\x89PNG" + b"\0" * 20,
        "clips/01-the-point.vtt": b"WEBVTT\n\n00:00.000 --> 00:01.000\nHello\n",
    }
    for name, data in files.items():
        (out / name).write_bytes(data)
    (out / "youtube.txt").write_text(f"Nine | AgentSee Podcast, episode 9\n\nA summary.\n\nEpisode page: {EP_URL}\n\nChapters\n00:00 Start\n")
    release = {
        "slug": "ep9",
        "show": {"name": "AgentSee Podcast", "hashtags": ["AgentSee", "AIagents"]},
        "episode": {"number": "9", "title": "Nine", "slug": "ep9", "guid": "g9", "date": DAY0, "release": DAY0,
                    "url": EP_URL, "media_url": MEDIA, "summary": "What happened in nine.",
                    "approved": {"by": "Tester", "at": "2026-10-15T10:00:00", "release": DAY0}},
        "youtube": {"title": "Nine | AgentSee Podcast, episode 9", "description": "desc", "tags": ["AgentSee", "x" * 490, "the podcast"],
                    "category": 28, "playlist": "AgentSee Podcast"},
        "notes": {"youtube": "youtube.txt"},
        "files": {n: {"bytes": len(d), "type": "video/mp4" if n.endswith(".mp4") else "application/octet-stream",
                      "sha256": "0" * 64} for n, d in files.items()},
        "clips": [{"index": 1, "slug": "the-point", "title": "The point", "file": "clips/01-the-point.mp4",
                   "poster": "clips/01-the-point.png", "captions": "clips/01-the-point.vtt",
                   "post": {"text": f"The point\n\nFrom episode 9.", "link": EP_URL, "link_at": EP_URL + "#t=42", "at_hms": "00:42"},
                   "hashtags": ["AgentSee"]}],
        "plan": [{"asset": "episode", "day": 0, "date": DAY0}, {"asset": "clip:01-the-point", "day": 1, "date": DAY1}],
    }
    (out / "release.json").write_text(json.dumps(release))
    public = [n for n in files if n != "episode.mp4"]
    staged = {"slug": "ep9", "at": "2026-10-15T11:00:00", "approved": {},
              "media": {n: {"url": MEDIA + n, "sha256": "0" * 64, "state": "uploaded"} for n in public},
              "archive": {f"episodes/ep9/out/{n}": {"sha256": "0" * 64, "state": "uploaded"} for n in files}}
    (out / "staged.json").write_text(json.dumps(staged))
    return out


class StubYouTube(http.server.BaseHTTPRequestHandler):
    """Google's token endpoint, the resumable upload, captions, thumbnails,
    playlists. One 503 on the second chunk of every upload, to see the resume."""
    state = {"uploads": {}, "playlists": [], "items": [], "captions": [], "thumbs": [], "fail_session": False, "metas": []}

    def out(self, code, obj=None, headers=None):
        b = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        s = StubYouTube.state
        if u.path == "/token":
            assert "grant_type=refresh_token" in self.body().decode()
            return self.out(200, {"access_token": "AT", "expires_in": 3600})
        assert self.headers.get("Authorization") == "Bearer AT", self.headers.get("Authorization")
        if u.path == "/upload/videos":
            if s["fail_session"]:
                return self.out(403, {"error": {"message": "The user has exceeded the number of videos they may upload.",
                                                "errors": [{"reason": "uploadLimitExceeded"}]}})
            meta = json.loads(self.body())
            size = int(self.headers["X-Upload-Content-Length"])
            uid = f"u{len(s['uploads']) + 1}"
            s["uploads"][uid] = {"size": size, "got": 0, "meta": meta, "failed_once": False, "type": self.headers["X-Upload-Content-Type"]}
            s["metas"].append(meta)
            return self.out(200, None, {"Location": f"http://{self.headers['Host']}/session/{uid}"})
        if u.path == "/upload/captions":
            assert "multipart/related" in self.headers["Content-Type"]
            b = self.body()
            assert b'"videoId"' in b and b"WEBVTT" in b or b"-->" in b
            s["captions"].append(b)
            return self.out(200, {"id": f"cap{len(s['captions'])}"})
        if u.path == "/upload/thumbnails/set":
            vid = urllib.parse.parse_qs(u.query)["videoId"][0]
            self.body()
            s["thumbs"].append(vid)
            if vid.startswith("VIDclip"):
                return self.out(403, {"error": {"message": "forbidden", "errors": [{"reason": "forbidden"}]}})
            return self.out(200, {"items": []})
        if u.path == "/playlists":
            meta = json.loads(self.body())
            s["playlists"].append({"id": "PL1", "snippet": {"title": meta["snippet"]["title"]}})
            return self.out(200, {"id": "PL1"})
        if u.path == "/playlistItems":
            s["items"].append(json.loads(self.body()))
            return self.out(200, {"id": "PI1"})
        self.out(404, {"error": {"message": "no such path " + u.path}})

    def do_PUT(self):
        s = StubYouTube.state
        uid = self.path.rsplit("/", 1)[1]
        up = s["uploads"][uid]
        rng = self.headers["Content-Range"]
        data = self.body()
        if rng.startswith("bytes */"):
            return self.out(308, None, {"Range": f"bytes=0-{up['got'] - 1}"} if up["got"] else {})
        start = int(rng.split(" ")[1].split("-")[0])
        assert start == up["got"], (start, up["got"])
        if start > 0 and not up["failed_once"]:
            up["failed_once"] = True
            return self.out(503, {"error": {"message": "backend"}})
        up["got"] += len(data)
        if up["got"] >= up["size"]:
            kind = "clip" if up["type"] == "video/mp4" and up["size"] < 300 * 1024 else "ep"
            vid = f"VID{kind}{uid}"
            return self.out(200, {"id": vid, "snippet": up["meta"]["snippet"], "status": up["meta"]["status"]})
        return self.out(308, None, {"Range": f"bytes=0-{up['got'] - 1}"})

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/playlists":
            return self.out(200, {"items": StubYouTube.state["playlists"]})
        self.out(404, {"error": {"message": "no"}})

    def log_message(self, *_):
        pass


class EngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(cls.tmp.name)
        cls.src = fixture(root)
        cls.db = root / "ledger.sqlite"
        cls.stub = http.server.HTTPServer(("127.0.0.1", 0), StubYouTube)
        threading.Thread(target=cls.stub.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.stub.server_address[1]}"
        youtube.TOKEN_URL, youtube.API, youtube.UPLOAD = base + "/token", base, base + "/upload"
        youtube.CHUNK = 256 * 1024
        cls.env = {"YOUTUBE_CLIENT_ID": "id", "YOUTUBE_CLIENT_SECRET": "s", "YOUTUBE_REFRESH_TOKEN": "r"}

    @classmethod
    def tearDownClass(cls):
        cls.stub.shutdown()
        cls.tmp.cleanup()

    def c(self):
        return ledger.connect(self.db)

    def test_01_ingest(self):
        c = self.c()
        n = ingest.ingest(c, "ep9", self.src, "tester")
        self.assertEqual(n, 8)
        e = ledger.episode(c, "ep9")
        self.assertEqual(e["title"], "Nine")
        self.assertTrue(e["release_at"].startswith(DAY0 + "T06:00"))
        kinds = {r["name"]: (r["kind"], r["url"], r["archived"]) for r in c.execute("SELECT * FROM deliverable WHERE episode='ep9'")}
        self.assertEqual(kinds["episode.mp4"], ("video", None, 1))
        self.assertEqual(kinds["clips/01-the-point.mp4"], ("clip", MEDIA + "clips/01-the-point.mp4", 1))
        self.assertEqual(kinds["clips/01-the-point.vtt"][0], "clip-captions")
        self.assertEqual(kinds["episode-art.jpg"][0], "art")
        self.assertEqual(ingest.ingest(c, "ep9", self.src), 8, "re-ingest is idempotent")

    def test_02_plan(self):
        c = self.c()
        made = plan.plan(c, "ep9", "tester")
        episode_accounts = [k for k, a in plan.ACCOUNTS.items() if "episode" in a["assets"]]
        clip_accounts = [k for k, a in plan.ACCOUNTS.items() if "clip" in a["assets"]]
        self.assertEqual(len(made), len(episode_accounts) + len(clip_accounts))
        posts = c.execute("SELECT * FROM post WHERE episode='ep9'").fetchall()
        ids = [p["link_id"] for p in posts]
        self.assertEqual(len(set(ids)), len(ids))
        for i in ids:
            self.assertRegex(i, r"^[a-z0-9]{2,16}$")
        yt = next(p for p in posts if p["account"] == "youtube" and p["asset"] == "episode")
        self.assertEqual(yt["link_id"], "e9yte")
        self.assertEqual(yt["title"], "Nine | AgentSee Podcast, episode 9")
        self.assertIn("A summary.", yt["text"])
        self.assertIn(plan.short("e9yte"), yt["text"])
        self.assertNotIn(EP_URL, yt["text"], "the raw episode URL is replaced by the short link")
        self.assertEqual(yt["media"], "episode.mp4")
        self.assertTrue(yt["scheduled_at"].startswith(DAY0 + "T06:00"))
        x = next(p for p in posts if p["account"] == "x" and p["asset"].startswith("clip:"))
        self.assertEqual(x["link_id"], "e9xc01")
        self.assertEqual(x["link_url"], EP_URL + "#t=42")
        self.assertIn("#AgentSee", x["text"])
        self.assertIn(plan.short("e9xc01"), x["text"])
        self.assertTrue(x["scheduled_at"].startswith(DAY1 + "T09:00"))
        lp = next(p for p in posts if p["account"] == "linkedin-page" and p["asset"] == "episode")
        self.assertEqual(lp["media"], "episode-art.jpg")
        self.assertFalse(yt["text"].startswith("Nine |"), "the title opens youtube.txt and is not the description")
        xe = next(p for p in posts if p["account"] == "x" and p["asset"] == "episode")
        self.assertLessEqual(len(xe["text"]), 280)
        self.assertTrue(xe["text"].startswith("Nine\n\nWhat happened in nine.\n\nhttps://agentsee.work/p/e9xe"), xe["text"])
        long = "A sentence that is long enough to matter. " * 12
        fitted = plan.fit("Head", long, ["https://agentsee.work/p/e9xe", "#AgentSee"], 280)
        self.assertLessEqual(len(fitted), 280)
        self.assertTrue(fitted.startswith("Head\n\nA sentence") and fitted.endswith("#AgentSee"), fitted)
        self.assertEqual(plan.fit("Head", "", ["https://agentsee.work/p/e9xe", "#AgentSee"], 40), "Head\n\nhttps://agentsee.work/p/e9xe")
        self.assertEqual(plan.fit("Head", "", ["https://agentsee.work/p/e9xe"], 10), "Head")
        teaser = plan.fit("Head", "One very long opening sentence that will not fit in the budget as it stands. Second.", ["https://agentsee.work/p/e9xe"], 90)
        self.assertLessEqual(len(teaser), 90)
        self.assertTrue(teaser.startswith("Head\n\nOne very long") and "…\n\nhttps://" in teaser, teaser)
        self.assertEqual(plan.plan(c, "ep9"), [], "a second plan adds nothing")
        ledger.update_post(c, x["id"], text="edited")
        plan.plan(c, "ep9")
        self.assertEqual(ledger.post(c, x["id"])["text"], "edited")
        ledger.update_post(c, x["id"], text=x["text"])

    def test_03_links(self):
        c = self.c()
        got = {}
        n = plan.links(c, "ep9", put=lambda k, v: got.__setitem__(k, v))
        self.assertEqual(n, len(got))
        self.assertEqual(got["e9xc01"], {"url": EP_URL + "#t=42", "episode": "ep9", "post": "clip:01-the-point", "platform": "x"})
        self.assertEqual(got["e9lpe"]["url"], EP_URL)
        with self.assertRaises(RuntimeError):
            plan.kv_put("e9xc01", {}, token=None) if not os.environ.get("CLOUDFLARE_API_TOKEN") else (_ for _ in ()).throw(RuntimeError())

    def test_04_due(self):
        c = self.c()
        before = publish.due(c, "2026-01-01T00:00:00+00:00")
        self.assertEqual({p["account"] for p in before}, {"youtube"}, "only the platform that schedules is due early")
        later = publish.due(c, DAY1 + "T09:30:00+01:00")
        accounts = {p["account"] for p in later}
        self.assertIn("x", accounts)
        self.assertIn("linkedin-page", accounts)
        self.assertNotIn("instagram", {p["account"] for p in later if p["asset"] == "episode"})

    def test_05_youtube(self):
        c = self.c()
        posts = [p for p in c.execute("SELECT * FROM post WHERE episode='ep9' AND account='youtube' ORDER BY asset")]
        self.assertEqual(len(posts), 2)
        quiet = lambda s: None  # noqa: E731
        res = publish.run(c, posts, env=self.env, dry_run=True, log=quiet)
        self.assertEqual([r[1] for r in res], ["dry-run", "dry-run"])
        res = publish.run(c, posts, env={}, log=quiet)
        self.assertEqual([r[1] for r in res], ["skipped-no-credentials"] * 2)
        res = publish.run(c, posts, env=self.env, who="tester", log=quiet)
        self.assertEqual([r[1] for r in res], ["scheduled", "scheduled"], res)
        clip = ledger.post(c, [p for p in posts if p["asset"] != "episode"][0]["id"])
        ep = ledger.post(c, [p for p in posts if p["asset"] == "episode"][0]["id"])
        self.assertTrue(ep["remote_id"].startswith("VIDep"))
        self.assertEqual(ep["permalink"], f"https://www.youtube.com/watch?v={ep['remote_id']}")
        self.assertTrue(clip["permalink"].startswith("https://www.youtube.com/shorts/VIDclip"))
        self.assertEqual(ep["attempts"], 1)
        self.assertIsNone(ep["last_error"])
        s = StubYouTube.state
        ep_meta = next(m for m in s["metas"] if m["snippet"]["title"].startswith("Nine |"))
        self.assertEqual(ep_meta["status"]["privacyStatus"], "private")
        want = dt.datetime.fromisoformat(ep["scheduled_at"]).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(ep_meta["status"]["publishAt"], want)
        self.assertFalse(ep_meta["status"]["selfDeclaredMadeForKids"])
        self.assertEqual(ep_meta["snippet"]["categoryId"], "28")
        self.assertEqual(ep_meta["snippet"]["tags"], ["AgentSee", "x" * 490], "tags stop before the 500th character, quotes and commas counted")
        for up in s["uploads"].values():
            self.assertEqual(up["got"], up["size"], "every byte arrived despite the 503")
            self.assertTrue(up["failed_once"])
        self.assertEqual(len(s["captions"]), 2)
        self.assertEqual(s["items"][0]["snippet"]["resourceId"]["videoId"], ep["remote_id"])
        self.assertEqual(len(s["playlists"]), 1, "the playlist was created once")
        notes = {ev["post"]: json.loads(ev["detail"])["note"] for ev in c.execute("SELECT * FROM event WHERE what='publish:scheduled'")}
        self.assertIn("playlist (created)", notes[ep["id"]])
        self.assertIn("thumbnail set", notes[ep["id"]])
        self.assertIn("not verified", notes[clip["id"]])
        self.assertIn("captions", notes[clip["id"]])

    def test_06_youtube_now_and_failure(self):
        c = self.c()
        p = c.execute("SELECT * FROM post WHERE episode='ep9' AND account='youtube' AND asset='episode'").fetchone()
        ledger.update_post(c, p["id"], status="planned", scheduled_at=ledger.now(), remote_id=None, permalink=None)
        res = publish.run(c, [ledger.post(c, p["id"])], env=self.env, log=lambda s: None)
        self.assertEqual(res[0][1], "published", "a post whose time has come goes public at once")
        meta = StubYouTube.state["metas"][-1]
        self.assertEqual(meta["status"]["privacyStatus"], "public")
        self.assertNotIn("publishAt", meta["status"])
        StubYouTube.state["fail_session"] = True
        ledger.update_post(c, p["id"], status="planned")
        res = publish.run(c, [ledger.post(c, p["id"])], env=self.env, log=lambda s: None)
        self.assertEqual(res[0][1], "failed")
        p2 = ledger.post(c, p["id"])
        self.assertEqual(p2["status"], "failed")
        self.assertIn("uploadLimitExceeded", p2["last_error"])
        StubYouTube.state["fail_session"] = False
        self.assertEqual(web.act(c, p2, "retry", {}, "tester"), "Back in the queue")
        self.assertEqual(ledger.post(c, p["id"])["status"], "due")
        self.assertIn(p["id"], [q["id"] for q in publish.due(c, "2026-01-01T00:00:00+00:00")])

    def test_07_hand(self):
        c = self.c()
        p = c.execute("SELECT * FROM post WHERE episode='ep9' AND account='linkedin-page' AND asset='episode'").fetchone()
        res = publish.run(c, [p], log=lambda s: None)
        self.assertEqual(res[0][1], "hand")
        self.assertEqual(web.act(c, ledger.post(c, p["id"]), "done", {"permalink": "https://www.linkedin.com/posts/x"}, "james"), "Published, by hand")
        p2 = ledger.post(c, p["id"])
        self.assertEqual((p2["status"], p2["permalink"]), ("published", "https://www.linkedin.com/posts/x"))
        with self.assertRaises(ValueError):
            web.act(c, p2, "done", {"permalink": "not a url"}, "james")
        with self.assertRaises(ValueError):
            web.act(c, p2, "text", {"text": "x"}, "james")

    def test_08_web(self):
        web.STATE["ledger"] = str(self.db)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), web.App)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def get(path):
            try:
                with urllib.request.urlopen(base + path, timeout=10) as r:
                    return r.status, dict(r.headers), r.read().decode(errors="replace")
            except urllib.error.HTTPError as e:
                return e.code, dict(e.headers), e.read().decode(errors="replace")

        def post(path, data):
            req = urllib.request.Request(base + path, data=urllib.parse.urlencode(data).encode(), method="POST")
            opener = urllib.request.build_opener(NoRedirect)
            try:
                with opener.open(req, timeout=10) as r:
                    return r.status, dict(r.headers), r.read().decode()
            except urllib.error.HTTPError as e:
                return e.code, dict(e.headers), e.read().decode()

        st, h, body = get("/episodes")
        self.assertEqual(st, 200)
        self.assertIn("Nine", body)
        self.assertIn("default-src 'none'", h["Content-Security-Policy"])
        self.assertIn('class="masthead"', body)
        st, h, body = get("/episodes/ep9")
        self.assertEqual(st, 200)
        self.assertIn("clips/01-the-point.mp4", body)
        self.assertEqual(get("/episodes/nope")[0], 404)
        st, h, body = get("/posts?platform=x")
        self.assertEqual(st, 200)
        self.assertIn("e9xc01", body)
        c = self.c()
        x = c.execute("SELECT * FROM post WHERE link_id='e9xc01'").fetchone()
        st, h, body = get(f"/posts/{x['id']}")
        self.assertEqual(st, 200)
        self.assertIn("<textarea", body)
        self.assertIn("x.com/intent/post", body)
        self.assertIn(MEDIA + "clips/01-the-point.mp4", body)
        self.assertIn("Paste the permalink", body)
        st, h, body = post(f"/posts/{x['id']}/text", {"_t": "wrong", "text": "nope"})
        self.assertEqual(st, 403)
        st, h, body = post(f"/posts/{x['id']}/text", {"_t": web.CSRF, "title": "New title", "text": "New text\r\nwith lines"})
        self.assertEqual(st, 303)
        self.assertTrue(h["Location"].startswith(f"/posts/{x['id']}?msg="))
        x2 = ledger.post(c, x["id"])
        self.assertEqual((x2["title"], x2["text"]), ("New title", "New text\nwith lines"))
        st, h, body = post(f"/posts/{x['id']}/schedule", {"_t": web.CSRF, "date": DAY1, "time": "17:30"})
        self.assertEqual(st, 303)
        self.assertTrue(ledger.post(c, x["id"])["scheduled_at"].startswith(DAY1 + "T17:30"))
        st, h, body = post(f"/posts/{x['id']}/schedule", {"_t": web.CSRF, "date": "soon", "time": "17:30"})
        self.assertEqual(st, 400)
        self.assertEqual(post(f"/posts/{x['id']}/done", {"_t": web.CSRF, "permalink": "https://x.com/agentseework/status/1"})[0], 303)
        self.assertEqual(ledger.post(c, x["id"])["status"], "published")
        self.assertEqual(c.execute("SELECT who FROM event WHERE what='hand:done' AND post=?", (x["id"],)).fetchone()[0], "local")
        st, h, body = get("/due")
        self.assertEqual(st, 200)
        st, h, body = get("/health")
        self.assertEqual(st, 200)
        self.assertIn("missing", body)
        st, h, body = get("/engine.css")
        self.assertEqual((st, h["Content-Type"]), (200, "text/css"))
        self.assertEqual(get("/assets/styles.css")[0], 200)
        self.assertEqual(get("/assets/..%2Fstyles.css")[0], 404)
        self.assertEqual(get("/assets/nope.css")[0], 404)
        self.assertEqual(get("/")[0], 200, "the root lands on the episodes")
        srv.shutdown()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


if __name__ == "__main__":
    unittest.main(verbosity=1)
