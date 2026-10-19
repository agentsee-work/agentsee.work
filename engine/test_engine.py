"""
The engine against a made-up episode, a stub YouTube and a fake KV:

    python3 engine/test_engine.py

No dependencies, nothing touched outside a temporary directory. What is
checked is the engine's own logic, from the ledger to the screens: the
platform is checked by the first real run.
"""

import contextlib
import datetime as dt
import hashlib
import http.server
import io
import json
import os
import pathlib
import re
import sys
import tempfile
import threading
import unittest
import unittest.mock
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
from publish import x  # noqa: E402
import vault  # noqa: E402
import archive  # noqa: E402
import collect  # noqa: E402

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
                      "sha256": hashlib.sha256(d).hexdigest()} for n, d in files.items()},
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


class Stub(http.server.BaseHTTPRequestHandler):
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

    def log_message(self, *_):
        pass


class StubYouTube(Stub):
    """Google's token endpoint, the resumable upload, captions, thumbnails,
    playlists. One 503 on the second chunk of every upload, to see the resume."""
    state = {"uploads": {}, "playlists": [], "items": [], "captions": [], "thumbs": [], "fail_session": False, "metas": [],
             "aborted": set(), "playlist_down": False}

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        s = StubYouTube.state
        if u.path == "/token":
            assert "grant_type=refresh_token" in self.body().decode()
            return self.out(200, {"access_token": "AT", "expires_in": 3600})
        assert self.headers.get("Authorization") == "Bearer AT", self.headers.get("Authorization")
        if u.path == "/upload/videos":
            if s["fail_session"]:
                s["refused"] = s.get("refused", 0) + 1
                if s["fail_session"] == "broken":
                    return self.out(403, {"error": {"message": "forbidden", "errors": [{"reason": "forbidden"}]}})
                return self.out(400, {"error": {"message": "The user has exceeded the number of videos they may upload.",
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
            item = json.loads(self.body())
            vid = item["snippet"]["resourceId"]["videoId"]
            if s["playlist_down"] or vid not in s["aborted"]:  # the 409 YouTube throws at the odd call, once per video
                s["aborted"].add(vid)
                return self.out(409, {"error": {"message": "The operation was aborted.", "errors": [{"reason": "SERVICE_UNAVAILABLE"}]}})
            s["items"].append(item)
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
        if u.path == "/videos":
            q = urllib.parse.parse_qs(u.query)
            assert q["part"] == ["statistics,status"]
            items = []
            for vid in q["id"][0].split(","):
                st = ({"uploadStatus": "rejected", "rejectionReason": "length", "privacyStatus": "private"} if vid == "VIDbad"
                      else {"uploadStatus": "processed", "privacyStatus": "public" if vid == "VIDpublic" else "private"})
                items.append({"id": vid, "statistics": {"viewCount": "12", "likeCount": "3", "commentCount": "1"}, "status": st})
            return self.out(200, {"items": items})
        self.out(404, {"error": {"message": "no"}})


class StubX(Stub):
    """X's token endpoint with the rotating refresh token, the chunked media
    upload with its processing poll, and the post. The vault is FakeVault."""
    state = {"refresh": "r1", "n": 1, "refreshes": 0, "media": {}, "tweets": [], "polls": 0, "refuse": False}

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        s = StubX.state
        if u.path == "/2/oauth2/token":
            assert self.headers.get("Authorization", "").startswith("Basic "), "a confidential client sends Basic"
            q = urllib.parse.parse_qs(self.body().decode())
            assert q["grant_type"] == ["refresh_token"]
            if q["refresh_token"][0] != s["refresh"]:
                return self.out(400, {"error": "invalid_request", "error_description": "Value passed for the token was invalid."})
            s["n"] += 1
            s["refresh"] = f"r{s['n']}"
            s["refreshes"] += 1
            return self.out(200, {"token_type": "bearer", "expires_in": 7200, "access_token": f"XAT{s['n']}",
                                  "scope": "tweet.read tweet.write users.read media.write offline.access",
                                  "refresh_token": s["refresh"]})
        assert self.headers.get("Authorization") == f"Bearer XAT{s['n']}", self.headers.get("Authorization")
        if u.path == "/2/media/upload/initialize":
            j = json.loads(self.body())
            mid = f"m{len(s['media']) + 1}"
            s["media"][mid] = {**j, "segments": [], "got": 0, "finalized": False}
            return self.out(200, {"data": {"id": mid, "media_key": "k" + mid, "expires_after_secs": 86400}})
        m = re.match(r"^/2/media/upload/(m\d+)/(append|finalize)$", u.path)
        if m:
            mid, op = m.groups()
            up = s["media"][mid]
            if op == "append":
                ctype = self.headers["Content-Type"]
                assert ctype.startswith("multipart/form-data; boundary="), ctype
                boundary = ctype.split("boundary=", 1)[1].encode()
                idx = data = None
                for part in self.body().split(b"--" + boundary):
                    if b'name="segment_index"' in part:
                        idx = int(part.split(b"\r\n\r\n", 1)[1].strip())
                    elif b'name="media"' in part:
                        data = part.split(b"\r\n\r\n", 1)[1]
                        data = data[:-2] if data.endswith(b"\r\n") else data
                assert idx == len(up["segments"]), (idx, len(up["segments"]))
                assert data is not None and len(data) <= 5 * 1024 * 1024
                up["segments"].append(len(data))
                up["got"] += len(data)
                return self.out(204)
            assert up["got"] == up["total_bytes"], (up["got"], up["total_bytes"])
            up["finalized"] = True
            if up["media_category"] == "tweet_video":
                return self.out(200, {"data": {"id": mid, "processing_info": {"state": "pending", "check_after_secs": 0}}})
            return self.out(200, {"data": {"id": mid}})
        if u.path == "/2/tweets":
            j = json.loads(self.body())
            assert len(j["text"]) <= 280
            for mid in j.get("media", {}).get("media_ids", []):
                assert s["media"][mid]["finalized"]
            if s["refuse"]:
                return self.out(403, {"title": "Forbidden", "detail": "You are not permitted to perform this action.", "status": 403})
            s["tweets"].append(j)
            return self.out(201, {"data": {"id": f"19{len(s['tweets']):04d}", "text": j["text"]}})
        self.out(404, {"title": "Not Found", "detail": u.path, "status": 404})

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        s = StubX.state
        if u.path == "/2/tweets":
            q = urllib.parse.parse_qs(u.query)
            assert "public_metrics" in q["tweet.fields"][0]
            return self.out(200, {"data": [{"id": i, "public_metrics": {"impression_count": 900, "like_count": 7, "retweet_count": 2,
                                                                         "reply_count": 1, "quote_count": 0, "bookmark_count": 4},
                                            "non_public_metrics": {"url_link_clicks": 30, "user_profile_clicks": 5}}
                                           for i in q["ids"][0].split(",")]})
        if u.path == "/2/media/upload":
            q = urllib.parse.parse_qs(u.query)
            assert q["command"] == ["STATUS"]
            s["polls"] += 1
            state = "in_progress" if s["polls"] % 2 else "succeeded"
            return self.out(200, {"data": {"id": q["media_id"][0], "processing_info": {"state": state, "check_after_secs": 0}}})
        self.out(404, {"title": "Not Found", "detail": u.path, "status": 404})


class FakeR2(Stub):
    """R2's S3 face, as far as the engine uses it: PUT, GET and HEAD of one
    object at a time under /bucket/key, with the SigV4 shape of every
    request checked and the payload hash verified against the body."""
    state = {"objects": {}, "requests": []}

    def check(self, body):
        auth = self.headers.get("Authorization", "")
        assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKID/"), auth
        assert "/auto/s3/aws4_request, SignedHeaders=" in auth and "Signature=" in auth, auth
        assert self.headers.get("x-amz-content-sha256") == hashlib.sha256(body).hexdigest(), "payload hash"
        assert re.fullmatch(r"\d{8}T\d{6}Z", self.headers.get("x-amz-date", "")), "x-amz-date"
        bucket, _, key = self.path.lstrip("/").partition("/")
        assert bucket == "archive-test", bucket
        FakeR2.state["requests"].append((self.command, key))
        return key

    def do_PUT(self):
        body = self.body()
        key = self.check(body)
        meta = {k.lower()[11:]: v for k, v in self.headers.items() if k.lower().startswith("x-amz-meta-")}
        FakeR2.state["objects"][key] = {"body": body, "type": self.headers.get("Content-Type"), "meta": meta}
        self.out(200)

    def do_HEAD(self):
        key = self.check(b"")
        o = FakeR2.state["objects"].get(key)
        if not o:
            return self.out(404)
        self.send_response(200)
        self.send_header("Content-Length", str(len(o["body"])))
        for k, v in o["meta"].items():
            self.send_header("x-amz-meta-" + k, v)
        self.end_headers()

    def do_GET(self):
        key = self.check(b"")
        o = FakeR2.state["objects"].get(key)
        if not o:
            return self.out(404, {"error": "NoSuchKey"})
        self.send_response(200)
        self.send_header("Content-Length", str(len(o["body"])))
        self.send_header("Content-Type", o["type"] or "application/octet-stream")
        for k, v in o["meta"].items():
            self.send_header("x-amz-meta-" + k, v)
        self.end_headers()
        self.wfile.write(o["body"])


class StubReach(Stub):
    """Analytics Engine's SQL endpoint with a day of the two datasets, Bluesky's
    public API, a Mastodon instance's status endpoint, the YouTube Analytics API."""
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path.endswith("/analytics_engine/sql"):
            assert self.headers.get("Authorization") == "Bearer cf"
            q = self.body().decode()
            assert "FORMAT JSON" in q and "INTERVAL" in q, q
            t = lambda m: (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731
            if "podcast_media" in q:
                row = lambda m, ua, h, st, b: {"timestamp": t(m), "index1": "podcast/ep9/episode.mp3", "blob3": ua, "blob4": "GB",  # noqa: E731
                                               "blob5": h, "blob6": "", "double1": st, "double2": b, "double3": 0, "double4": b - 1, "double5": 12000000}
                rows = [row(50, "Overcast/2024 (+http://overcast.fm/; iOS podcast app)", "h1", 206, 2000000),
                        row(40, "Overcast/2024 (+http://overcast.fm/; iOS podcast app)", "h1", 206, 3000000),
                        row(30, "Googlebot/2.1 (+http://www.google.com/bot.html)", "h2", 200, 12000000),
                        row(20, "Mozilla/5.0 (Macintosh) AppleWebKit/605 Version/17 Safari/605", "h3", 206, 100000),
                        row(10, "python-requests/2.31", "h4", 200, 12000000),
                        row(5, "Overcast/2024 (+http://overcast.fm/; iOS podcast app)", "h5", 304, 0)]
                return self.out(200, {"meta": [], "data": rows, "rows": len(rows)})
            if "podcast_links" in q:
                rows = [{"timestamp": t(45), "index1": "e9yte", "blob2": "Mozilla/5.0 (iPhone) Safari", "blob3": "GB", "blob4": "h1",
                         "blob5": "https://www.youtube.com", "double1": 302},
                        {"timestamp": t(44), "index1": "nope", "blob2": "curl/8", "blob3": "US", "blob4": "h9", "blob5": "", "double1": 404}]
                return self.out(200, {"meta": [], "data": rows, "rows": len(rows)})
        self.out(404, {"error": "no such path " + u.path})

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/xrpc/com.atproto.identity.resolveHandle":
            assert q["handle"] == ["agentsee.work"]
            return self.out(200, {"did": "did:plc:test"})
        if u.path == "/xrpc/app.bsky.feed.getPosts":
            return self.out(200, {"posts": [{"uri": uri, "cid": "c", "likeCount": 5, "repostCount": 2, "replyCount": 1, "quoteCount": 0, "bookmarkCount": 3}
                                            for uri in q["uris"]]})
        if u.path == "/api/v1/statuses/123":
            return self.out(200, {"id": "123", "url": "x", "created_at": "2026-10-20T09:00:00Z", "favourites_count": 4, "reblogs_count": 1, "replies_count": 0})
        if u.path == "/analytics/reports":
            assert q["ids"] == ["channel==MINE"] and q["dimensions"] == ["video"] and q["sort"] == ["-views"]
            ids = q["filters"][0].removeprefix("video==").split(",")
            names = ["video"] + collect.YT_METRICS.split(",")
            return self.out(200, {"columnHeaders": [{"name": n} for n in names],
                                  "rows": [[v, 12, 50.5, 31.2, 61.0, 3, 1, 2, 1] for v in ids]})
        self.out(404, {"error": "no such path " + u.path})


class FakeVault:
    """The one item the engine writes, in memory. Can refuse one write."""
    def __init__(self, value):
        self.value, self.fail_write, self.writes = value, False, 0

    def read(self, vault_, item, field):
        assert (vault_, item, field) == ("Engineering", "X podcast token", "credential")
        return self.value

    def write(self, vault_, item, field, value):
        self.writes += 1
        if self.fail_write:
            self.fail_write = False
            raise vault.VaultError("no session")
        self.value = value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


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
        youtube.PLAYLIST_WAIT = (0, 0)
        cls.env = {"YOUTUBE_CLIENT_ID": "id", "YOUTUBE_CLIENT_SECRET": "s", "YOUTUBE_REFRESH_TOKEN": "r"}
        cls.xstub = http.server.HTTPServer(("127.0.0.1", 0), StubX)
        threading.Thread(target=cls.xstub.serve_forever, daemon=True).start()
        xbase = f"http://127.0.0.1:{cls.xstub.server_address[1]}"
        x.TOKEN_URL, x.API, x.CHUNK = xbase + "/2/oauth2/token", xbase + "/2", 64 * 1024
        cls.vault = FakeVault("r1")
        x.vault = cls.vault
        ledger.CONFIG["x"] = {"vault": "Engineering", "item": "X podcast token", "field": "credential"}
        plan.ACCOUNTS["x"]["adapter"] = "x"  # the switch-on, here regardless of accounts.json
        cls.xenv = {"X_APP_ID": "cid", "X_APP_SECRET": "cs", "X_TOKEN": "123", "X_TOKEN_SECRET": "r1"}
        cls.r2 = http.server.HTTPServer(("127.0.0.1", 0), FakeR2)
        threading.Thread(target=cls.r2.serve_forever, daemon=True).start()
        cls.r2env = {"R2_ACCOUNT_ID": "acct", "R2_ARCHIVE_BUCKET": "archive-test", "AWS_ACCESS_KEY_ID": "AKID",
                     "AWS_SECRET_ACCESS_KEY": "secret", "R2_ENDPOINT": f"http://127.0.0.1:{cls.r2.server_address[1]}"}
        cls.reach = http.server.HTTPServer(("127.0.0.1", 0), StubReach)
        threading.Thread(target=cls.reach.serve_forever, daemon=True).start()
        rbase = f"http://127.0.0.1:{cls.reach.server_address[1]}"
        collect.SQL_URL = rbase + "/client/v4/accounts/{account}/analytics_engine/sql"
        collect.BSKY, collect.ANALYTICS = rbase + "/xrpc", rbase + "/analytics"
        cls.rbase = rbase
        cls.data = root / "data"
        os.environ["ENGINE_DATA"] = str(cls.data)

    @classmethod
    def tearDownClass(cls):
        for srv in (cls.stub, cls.xstub, cls.r2, cls.reach):
            srv.shutdown()
            srv.server_close()
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
        xe = c.execute("SELECT scheduled_at FROM post WHERE episode='ep9' AND account='x' AND asset='episode'").fetchone()[0]
        plus = lambda iso, h: (dt.datetime.fromisoformat(iso) + dt.timedelta(hours=h)).isoformat()  # noqa: E731
        later = publish.due(c, plus(xe, 7))
        accounts = {p["account"] for p in later}
        self.assertIn("linkedin-page", accounts, "a card is due whenever its day has come")
        self.assertNotIn("x", accounts, "seven hours past on a platform the engine sends to: held for a person")
        self.assertIn("x", {p["account"] for p in publish.late(c, plus(xe, 7))})
        self.assertIn("x", {p["account"] for p in publish.due(c, plus(xe, 1))}, "an hour past: sent")
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
        self.assertIn(ep["remote_id"], s["aborted"], "the first playlist call was aborted with a 409; the retry went through")
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
        StubYouTube.state["fail_session"] = "broken"
        ledger.update_post(c, p["id"], status="planned")
        res = publish.run(c, [ledger.post(c, p["id"])], env=self.env, log=lambda s: None)
        self.assertEqual(res[0][1], "failed")
        p2 = ledger.post(c, p["id"])
        self.assertEqual(p2["status"], "failed")
        self.assertIn("forbidden", p2["last_error"])
        StubYouTube.state["fail_session"] = False
        self.assertEqual(web.act(c, p2, "retry", {}, "tester"), "Back in the queue")
        self.assertEqual(ledger.post(c, p["id"])["status"], "due")
        self.assertIn(p["id"], [q["id"] for q in publish.due(c, "2026-01-01T00:00:00+00:00")])
        # The daily upload cap is a hold, not a failure: the post keeps its state, the account rests, the worker comes back
        s = StubYouTube.state
        s["fail_session"], s["refused"] = True, 0
        clip = c.execute("SELECT * FROM post WHERE episode='ep9' AND account='youtube' AND asset!='episode'").fetchone()
        ledger.update_post(c, clip["id"], status="planned")
        res = publish.run(c, [ledger.post(c, p["id"]), ledger.post(c, clip["id"])], env=self.env, log=lambda s: None)
        self.assertEqual([r[1] for r in res], ["held", "held"], "the first refusal rests the rest of the account")
        self.assertEqual(s["refused"], 1, "the second post was not even tried")
        self.assertEqual(ledger.post(c, p["id"])["status"], "due", "a held post keeps its state")
        self.assertEqual(ledger.post(c, clip["id"])["status"], "planned")
        self.assertIn("uploadLimitExceeded", ledger.post(c, p["id"])["last_error"])
        self.assertIn("youtube", publish.HELD)
        ids = [q["id"] for q in publish.due(c)]
        self.assertNotIn(p["id"], ids)
        self.assertNotIn(clip["id"], ids)
        later = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=3)).isoformat()
        self.assertIn(p["id"], [q["id"] for q in publish.due(c, later)], "after the hold the account is due again")
        self.assertEqual(publish.HELD, {}, "an expired hold is forgotten")
        self.assertIn("publish:held", [ev["what"] for ev in c.execute("SELECT what FROM event WHERE post=?", (p["id"],))])
        s["fail_session"] = False
        ledger.update_post(c, clip["id"], status=clip["status"])
        # The playlist refusing for good: the video is up, so the post is published with a note, never failed
        StubYouTube.state["playlist_down"] = True
        ledger.update_post(c, p["id"], status="planned", remote_id=None, permalink=None)
        res = publish.run(c, [ledger.post(c, p["id"])], env=self.env, log=lambda s: None)
        self.assertEqual(res[0][1], "published", "a step after the upload is a note, never the loss of the video")
        p3 = ledger.post(c, p["id"])
        self.assertTrue(p3["remote_id"].startswith("VIDep"))
        note = json.loads(c.execute("SELECT detail FROM event WHERE post=? AND what='publish:published' ORDER BY id DESC",
                                    (p["id"],)).fetchone()[0])["note"]
        self.assertIn("could not add to the playlist: 409", note)
        self.assertIn("by hand", note)
        StubYouTube.state["playlist_down"] = False
        ledger.update_post(c, p["id"], status="due")

    def test_06b_interrupted_and_recorded_by_hand(self):
        c = self.c()
        p = c.execute("SELECT * FROM post WHERE episode='ep9' AND account='youtube' AND asset!='episode'").fetchone()
        ledger.update_post(c, p["id"], status="publishing")
        self.assertEqual(web.recover(c, "tester"), 1)
        p2 = ledger.post(c, p["id"])
        self.assertEqual(p2["status"], "failed")
        self.assertIn("look for the result on the platform", p2["last_error"])
        self.assertEqual(web.recover(c, "tester"), 0)
        soon = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat()
        ledger.update_post(c, p["id"], scheduled_at=soon)
        self.assertEqual(web.act(c, ledger.post(c, p["id"]), "done", {"permalink": "https://www.youtube.com/watch?v=abcDEF12-_5&t=9"}, "james"),
                         "Recorded; the platform holds it until its time")
        p3 = ledger.post(c, p["id"])
        self.assertEqual((p3["status"], p3["remote_id"], p3["published_at"]), ("scheduled", "abcDEF12-_5", soon),
                         "a YouTube link pasted before its time: the id out of the link, scheduled, public at its hour")
        ledger.update_post(c, p["id"], status="failed", scheduled_at="2026-01-01T09:00:00+00:00")
        self.assertEqual(web.act(c, ledger.post(c, p["id"]), "done", {"permalink": "https://youtu.be/abcDEF12-_5"}, "james"), "Published, by hand")
        self.assertEqual(ledger.post(c, p["id"])["status"], "published")
        ledger.update_post(c, p["id"], status=p["status"], remote_id=p["remote_id"], permalink=p["permalink"],
                           scheduled_at=p["scheduled_at"], published_at=p["published_at"])

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
        self.assertIn("Publish now", body)
        self.assertNotIn("x.com/intent/post", body, "X goes by API: no composer card")
        bs = c.execute("SELECT * FROM post WHERE link_id='e9bsc01'").fetchone()
        st, h, body = get(f"/posts/{bs['id']}")
        self.assertEqual(st, 200)
        self.assertIn("bsky.app/intent/compose", body)
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
        self.assertEqual(post(f"/posts/{bs['id']}/done", {"_t": web.CSRF, "permalink": "https://bsky.app/profile/agentsee.work/post/1"})[0], 303)
        self.assertEqual(ledger.post(c, bs["id"])["status"], "published")
        self.assertEqual(c.execute("SELECT who FROM event WHERE what='hand:done' AND post=?", (bs["id"],)).fetchone()[0], "local")
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


    def test_09_x(self):
        c = self.c()
        posts = [p for p in c.execute("SELECT * FROM post WHERE episode='ep9' AND account='x' ORDER BY asset")]
        self.assertEqual(len(posts), 2)
        quiet = lambda s: None  # noqa: E731
        res = publish.run(c, posts, env=self.xenv, dry_run=True, log=quiet)
        self.assertEqual([r[1] for r in res], ["dry-run"] * 2)
        res = publish.run(c, posts, env={}, log=quiet)
        self.assertEqual([r[1] for r in res], ["skipped-no-credentials"] * 2)
        res = publish.run(c, posts, env=self.xenv, who="tester", log=quiet)
        self.assertEqual([r[1] for r in res], ["published", "published"], res)
        s = StubX.state
        self.assertEqual(s["refreshes"], 1, "one refresh served both posts")
        self.assertEqual(self.vault.value, "r2", "the replacement refresh token is in the vault")
        self.assertEqual(self.vault.writes, 1)
        ep = ledger.post(c, [p for p in posts if p["asset"] == "episode"][0]["id"])
        clip = ledger.post(c, [p for p in posts if p["asset"] != "episode"][0]["id"])
        self.assertEqual(ep["permalink"], f"https://x.com/agentseework/status/{ep['remote_id']}")
        self.assertTrue(ep["published_at"])
        video = next(m for m in s["media"].values() if m["media_category"] == "tweet_video")
        image = next(m for m in s["media"].values() if m["media_category"] == "tweet_image")
        self.assertEqual((video["media_type"], image["media_type"]), ("video/mp4", "image/jpeg"))
        self.assertEqual(len(video["segments"]), 5, "256 KiB and 7 bytes in 64 KiB segments")
        self.assertEqual(sum(video["segments"]), video["total_bytes"])
        self.assertEqual(s["polls"], 2, "polled until X had processed the video; the image needed no poll")
        texts = {t["text"]: t for t in s["tweets"]}
        self.assertIn(ep["text"], texts)
        self.assertIn(clip["text"], texts)
        self.assertIn("https://agentsee.work/p/e9xe", ep["text"])
        self.assertEqual(len(texts[clip["text"]]["media"]["media_ids"]), 1)
        notes = {ev["post"]: json.loads(ev["detail"])["note"] for ev in c.execute("SELECT * FROM event WHERE what='publish:published'")}
        self.assertIn("of 280 characters", notes[clip["id"]])
        self.assertIn("with a link", notes[ep["id"]])

    def test_10_x_vault(self):
        c = self.c()
        p = c.execute("SELECT * FROM post WHERE episode='ep9' AND account='x' AND asset='episode'").fetchone()
        ledger.update_post(c, p["id"], status="planned", remote_id=None, permalink=None)
        quiet = lambda s: None  # noqa: E731
        x._token.update(access=None, until=0.0)  # two hours have passed
        self.vault.fail_write = True
        res = publish.run(c, [ledger.post(c, p["id"])], env=self.xenv, log=quiet)
        self.assertEqual(res[0][1], "failed")
        p2 = ledger.post(c, p["id"])
        self.assertIn("vault write failed", p2["last_error"])
        self.assertIn("Do not restart", p2["last_error"])
        self.assertEqual(self.vault.value, "r2", "the vault still holds the retired token")
        self.assertEqual(StubX.state["refresh"], "r3")
        self.assertEqual(x._token["unsaved"], "r3", "the new token is held in the process")
        self.assertEqual(len(StubX.state["tweets"]), 2, "nothing was posted")
        self.assertEqual(web.act(c, p2, "retry", {}, "tester"), "Back in the queue")
        res = publish.run(c, [ledger.post(c, p["id"])], env=self.xenv, log=quiet)
        self.assertEqual(res[0][1], "published", res)
        self.assertEqual(self.vault.value, "r3", "the held token went to the vault first")
        self.assertIsNone(x._token["unsaved"])
        self.assertEqual(StubX.state["refreshes"], 2, "the access token from the held refresh was used, not another refresh")
        # the vault holding a token something else already used
        x._token.update(access=None, until=0.0)
        self.vault.value = "r1"
        ledger.update_post(c, p["id"], status="planned")
        res = publish.run(c, [ledger.post(c, p["id"])], env=self.xenv, log=quiet)
        self.assertEqual(res[0][1], "failed")
        self.assertIn("retired token", ledger.post(c, p["id"])["last_error"])
        self.assertEqual(StubX.state["refreshes"], 2)
        self.vault.value = StubX.state["refresh"]
        ledger.update_post(c, p["id"], status="published")

    def test_11_late(self):
        c = self.c()
        p = c.execute("SELECT * FROM post WHERE episode='ep9' AND account='x' AND asset!='episode'").fetchone()
        card = c.execute("SELECT * FROM post WHERE episode='ep9' AND account='linkedin-page' AND asset='episode'").fetchone()
        now = dt.datetime.now(dt.timezone.utc)
        ago = lambda h: (now - dt.timedelta(hours=h)).isoformat(timespec="seconds")  # noqa: E731
        ids = lambda rows: [r["id"] for r in rows]  # noqa: E731
        ledger.update_post(c, p["id"], status="planned", scheduled_at=ago(7), remote_id=None, permalink=None)
        ledger.update_post(c, card["id"], status="planned", scheduled_at=ago(7))
        self.assertNotIn(p["id"], ids(publish.due(c)), "seven hours late: left for a person")
        self.assertIn(p["id"], ids(publish.late(c)))
        self.assertIn(p["id"], ids(publish.due(c, include_late=True)))
        self.assertIn(card["id"], ids(publish.due(c)), "a card is never late")
        self.assertNotIn(card["id"], ids(publish.late(c)))
        self.assertIn("Past their time, not sent", web.due_page(c)[1])
        ledger.update_post(c, p["id"], scheduled_at=ago(1))
        self.assertIn(p["id"], ids(publish.due(c)), "an hour late goes")
        ledger.update_post(c, p["id"], status="due", scheduled_at=ago(7))
        self.assertIn(p["id"], ids(publish.due(c)), "marked due by a person, it goes regardless")
        ledger.update_post(c, p["id"], status="skipped")
        ledger.update_post(c, card["id"], status="published")


    def test_12_signer(self):
        # Fixed on 15 October 2026 against botocore 1.43.112's SigV4Auth for the same inputs.
        when = dt.datetime(2026, 10, 15, 18, 0, 0, tzinfo=dt.timezone.utc)
        h = archive.sign("PUT", "9468eccb7caed9f96283a9139c37a4df.r2.cloudflarestorage.com",
                         "/agentsee-podcast-archive/engine/ledger-latest.sqlite", {},
                         {"content-type": "application/vnd.sqlite3", "x-amz-meta-sha256": "ab" * 32},
                         hashlib.sha256(b"hello ledger").hexdigest(),
                         "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "auto", when)
        self.assertEqual(h["authorization"], "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20261015/auto/s3/aws4_request, "
                         "SignedHeaders=content-type;host;x-amz-content-sha256;x-amz-date;x-amz-meta-sha256, "
                         "Signature=cde30d8e9da6181aee80e9254e409b09b6d566487066c1d7079f31c0265a6260")
        self.assertEqual(h["x-amz-date"], "20261015T180000Z")

    def test_13_archive_files(self):
        c = self.c()
        client = archive.Client(self.r2env)
        with self.assertRaises(archive.ArchiveError):
            archive.Client({})
        # the archive holds the clip and the captions, as stage would have put them
        for name in ("clips/01-the-point.mp4", "clips/01-the-point.vtt"):
            client.put(f"episodes/ep9/out/{name}", self.src / name, "video/mp4", {"sha256": "x"})
        self.assertIsNone(client.head("episodes/ep9/out/nope"))
        self.assertEqual(client.head("episodes/ep9/out/clips/01-the-point.mp4")["x-amz-meta-sha256"], "x")
        ep = ledger.episode(c, "ep9")
        files = archive.Files(c, ep, client)
        self.assertEqual(files / "episode.mp4", self.src / "episode.mp4", "on this machine: the local path")
        # an episode whose files are not here
        away = dict(ep)
        away["source_dir"] = str(self.src.parent / "gone")
        files = archive.Files(c, away, client)
        p = files / "clips/01-the-point.mp4"
        self.assertEqual(p, self.data / "episodes" / "ep9" / "out" / "clips" / "01-the-point.mp4")
        self.assertTrue(p.exists(), "fetched from the archive")
        self.assertEqual(p.read_bytes(), (self.src / "clips/01-the-point.mp4").read_bytes())
        self.assertFalse(list(p.parent.glob("*.part")))
        n = len(FakeR2.state["requests"])
        self.assertEqual(files / "clips/01-the-point.mp4", p)
        self.assertEqual(len(FakeR2.state["requests"]), n, "a second look does not fetch again")
        self.assertFalse((files / "episode.mp4").exists(), "not in the archive: the path is absent, not an error")
        # a file that does not match what the ledger says
        client.put("episodes/ep9/out/thumbnail.png", self.src / "episode-art.jpg", "image/png")
        with self.assertRaises(archive.ArchiveError):
            files / "thumbnail.png"
        self.assertFalse((self.data / "episodes" / "ep9" / "out" / "thumbnail.png").exists())
        self.assertTrue((files / "clips/01-the-point.vtt").exists())
        # without credentials the path is simply absent
        bare = archive.Files(c, away, None)
        self.assertFalse((bare / "captions.srt").exists())
        # and the publisher reaches the archive through the same door
        self.assertIsInstance(publish.archive.Files(c, ep), archive.Files)

    def test_14_archive_ingest_snapshot_restore(self):
        c = self.c()
        client = archive.Client(self.r2env)
        with self.assertRaises(archive.ArchiveError) as cm:
            archive.fetch_release("ep9", client)
        self.assertIn("release.json is not in the archive", str(cm.exception))
        client.put("episodes/ep9/out/release.json", self.src / "release.json", "application/json")
        with self.assertRaises(archive.ArchiveError) as cm:
            archive.fetch_release("ep9", client)
        self.assertIn("re-run `episode.py stage`", str(cm.exception))
        client.put("episodes/ep9/out/staged.json", self.src / "staged.json", "application/json")
        d = archive.fetch_release("ep9", client)
        self.assertEqual(d, self.data / "episodes" / "ep9" / "out")
        self.assertTrue((d / "release.json").exists() and (d / "staged.json").exists())
        # an ingest on a machine without build/: the data dir becomes the source
        with unittest.mock.patch.object(archive, "Client", lambda env=None: client):
            n = ingest.ingest(c, "ep9", None, "tester", from_archive=True)
        self.assertGreater(n, 5)
        self.assertEqual(ledger.episode(c, "ep9")["source_dir"], str(d))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM post WHERE episode='ep9'").fetchone()[0],
                         c.execute("SELECT COUNT(*) FROM post").fetchone()[0], "posts untouched")
        # the snapshot: two keys, the day's and latest, with the hash in the metadata
        key = archive.snapshot(c, client, who="tester", log=lambda s: None)
        day = dt.datetime.now(ledger.ZONE).strftime("%Y-%m-%d")
        self.assertEqual(key, f"engine/ledger-{day}.sqlite")
        objs = FakeR2.state["objects"]
        self.assertIn(archive.LATEST, objs)
        self.assertEqual(objs[key]["body"], objs[archive.LATEST]["body"])
        self.assertEqual(objs[key]["meta"]["sha256"], hashlib.sha256(objs[key]["body"]).hexdigest())
        self.assertEqual(objs[key]["type"], "application/vnd.sqlite3")
        ev = c.execute("SELECT detail FROM event WHERE what='snapshot' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(json.loads(ev["detail"])["bytes"], len(objs[key]["body"]))
        # the copy is a working ledger with everything in it
        back = self.data / "restored.sqlite"
        archive.restore(back, client)
        r = ledger.connect(back)
        self.assertEqual(r.execute("SELECT COUNT(*) FROM post").fetchone()[0], c.execute("SELECT COUNT(*) FROM post").fetchone()[0])
        r.close()
        with self.assertRaises(archive.ArchiveError):
            archive.restore(back, client)
        archive.restore(back, client, force=True)
        with self.assertRaises(archive.ArchiveError):
            archive.restore(self.data / "x.sqlite", client, key="engine/ledger-1999-01-01.sqlite")
        # the worker's nightly: not on the first look, once after the hour, not again that day
        web.STATE["snapshot_day"] = None
        ledger.CONFIG["snapshot_at"] = "00:00"
        self.assertFalse(web.snapshot_due(), "the first look only sets the day")
        web.STATE["snapshot_day"] = dt.date(2000, 1, 1)
        self.assertTrue(web.snapshot_due())
        web.STATE["snapshot_day"] = dt.datetime.now(ledger.ZONE).date()
        self.assertFalse(web.snapshot_due())
        ledger.CONFIG["snapshot_at"] = "23:59"
        web.STATE["snapshot_day"] = None
        web.snapshot_due()
        self.assertEqual(web.STATE["snapshot_day"], dt.datetime.now(ledger.ZONE).date() - dt.timedelta(days=1), "before the hour: yesterday counts as done")

    def test_15_collect(self):
        c = self.c()
        quiet = lambda s: None  # noqa: E731
        ep = c.execute("SELECT * FROM post WHERE episode='ep9' AND account='youtube' AND asset='episode'").fetchone()
        clip = c.execute("SELECT * FROM post WHERE episode='ep9' AND account='youtube' AND asset!='episode'").fetchone()
        ledger.update_post(c, ep["id"], status="scheduled", remote_id="VIDpublic", published_at="2026-10-20T05:00:00+00:00")
        ledger.update_post(c, clip["id"], status="scheduled", remote_id="VIDbad")
        bs = c.execute("SELECT * FROM post WHERE episode='ep9' AND platform='bluesky' AND asset='episode'").fetchone()
        web.act(c, bs, "done", {"permalink": "https://bsky.app/profile/agentsee.work/post/3abc"}, "james")
        ma = c.execute("SELECT * FROM post WHERE episode='ep9' AND platform='mastodon' AND asset='episode'").fetchone()
        web.act(c, ma, "done", {"permalink": f"{self.rbase}/@agentseework/123"}, "james")
        xp = c.execute("SELECT * FROM post WHERE episode='ep9' AND platform='x' AND asset='episode'").fetchone()
        ledger.update_post(c, xp["id"], status="published", remote_id="777")
        c.execute("UPDATE deliverable SET duration=600, bytes=12000000 WHERE episode='ep9' AND name='episode.mp3'")  # a minute is 1.2 MB
        env = {**self.env, **self.xenv, "CLOUDFLARE_API_TOKEN": "cf"}
        res = collect.run(c, env=env, who="tester", log=quiet)
        self.assertFalse([v for v in res.values() if v.startswith("failed")], res)
        # The edge: six requests, one listen (a listener twice, a bot, a library, under a minute, a 304)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM download").fetchone()[0], 6)
        self.assertEqual(sum(collect.listens(c)["ep9"].values()), 1)
        self.assertEqual(collect.by_app(c), [("app:Overcast", 1)])
        self.assertEqual(collect.by_country(c), [("GB", 1)])
        self.assertEqual(collect.clicks(c), {"e9yte": 1}, "the 404 is not a click")
        self.assertEqual(collect.clicks_from(c), [("https://www.youtube.com", 1)])
        collect.run(c, only=["edge"], env=env, who="tester", log=quiet)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM download").fetchone()[0], 6, "the overlap is re-read, not counted twice")
        self.assertEqual(c.execute("SELECT COUNT(*) FROM click").fetchone()[0], 1)
        # YouTube: counts and state from videos.list, watch time from Analytics
        m = collect.latest(c)
        self.assertEqual((m[ep["id"]]["views"]["now"], m[ep["id"]]["likes"]["now"]), (12, 3))
        self.assertEqual((m[ep["id"]]["watch_minutes"]["now"], m[ep["id"]]["avg_view_pct"]["now"]), (50.5, 61.0))
        self.assertEqual(ledger.post(c, ep["id"])["status"], "published", "public on YouTube: the post is published")
        bad = ledger.post(c, clip["id"])
        self.assertEqual(bad["status"], "failed")
        self.assertIn("rejected the video (length)", bad["last_error"])
        # The free socials, from the pasted permalinks; X from the id
        self.assertEqual((m[bs["id"]]["likes"]["now"], m[bs["id"]]["bookmarks"]["now"]), (5, 3))
        self.assertEqual((m[ma["id"]]["likes"]["now"], m[ma["id"]]["reposts"]["now"]), (4, 1))
        self.assertEqual((m[xp["id"]]["impressions"]["now"], m[xp["id"]]["link_clicks"]["now"]), (900, 30))
        raw = c.execute("SELECT raw FROM snapshot WHERE post=? AND raw IS NOT NULL", (xp["id"],)).fetchall()
        self.assertEqual(len(raw), 1, "the platform's answer is kept once per read, not per metric")
        # The user agents
        self.assertEqual(collect.classify("Overcast/2024 (+http://overcast.fm/; iOS podcast app)"), ("app", "Overcast"))
        self.assertIn(collect.classify("python-requests/2.31")[0], ("bot", "library"), "the bots list is asked first and claims it; neither listens")
        self.assertEqual(collect.classify("Googlebot/2.1 (+http://www.google.com/bot.html)")[0], "bot")
        self.assertEqual(collect.classify(""), ("unknown", ""))
        # The screens and Health
        title, body = web.reach_page(c)
        self.assertIn("Nine", body)
        self.assertIn("50.5", body)
        title, body = web.audience_page(c)
        self.assertIn("Overcast", body)
        self.assertIn("https://www.youtube.com", body)
        self.assertIn("Last collection", web.health_page(c)[1])
        self.assertIn("collect:run", [r["what"] for r in c.execute("SELECT what FROM event ORDER BY id DESC LIMIT 3")])
        # The daily hour, like the snapshot's
        web.STATE["collect_day"] = dt.date(2000, 1, 1)
        ledger.CONFIG["collect_at"] = "00:00"
        self.assertTrue(web.daily_due("collect"))
        # Nothing to read says so, and a source that is off says so
        plan.ACCOUNTS["x"]["adapter"] = "hand"
        self.assertEqual(collect.x(c, env, ledger.now(), quiet), "skipped: the X adapter is off")
        plan.ACCOUNTS["x"]["adapter"] = "x"
        self.assertEqual(collect.edge(c, {}, ledger.now(), quiet), "skipped: CLOUDFLARE_API_TOKEN not set")


if __name__ == "__main__":
    unittest.main(verbosity=1)
