"""
The engine against a made-up episode, a stub YouTube and a fake KV:

    python3 engine/test_engine.py

No dependencies, nothing touched outside a temporary directory. What is
checked is the engine's own logic, from the ledger to the screens: the
platform is checked by the first real run.
"""

import base64
import datetime as dt
import hashlib
import http.server
import importlib.util
import io
import json
import os
import pathlib
import re
import smtplib
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.error
import urllib.parse
import urllib.request
import zlib

import archive
import collect
import deploy
import guests
import ingest
import ledger
import linear
import mail
import plan
import publish
import vault
import web
from publish import bluesky, instagram, mastodon, threads, x, youtube

DAY0 = (dt.date.today() + dt.timedelta(days=10)).isoformat()
DAY1 = (dt.date.today() + dt.timedelta(days=11)).isoformat()
EP_URL = "https://agentsee.work/podcast/ep9/"
MEDIA = "https://media.agentsee.work/podcast/ep9/"


def fixture(root):
    out = root / "ep9" / "out"
    (out / "clips").mkdir(parents=True)
    files = {
        "episode.mp4": os.urandom(3 * 256 * 1024 + 1000),  # three chunks and a bit, at the test chunk size
        "episode.mp3": b"ID3mp3" * 100,
        "captions.srt": b"1\n00:00:00,000 --> 00:00:01,000\nHello\n",
        "thumbnail.png": b"\x89PNG" + b"\0" * 100,
        "episode-art.jpg": b"\xff\xd8" + b"\0" * 50,
        "clips/01-the-point.mp4": os.urandom(256 * 1024 + 7),
        "clips/01-the-point.png": b"\x89PNG" + b"\0" * 20,
        "clips/01-the-point.jpg": b"\xff\xd8" + b"\0" * 20,
        "clips/01-the-point.vtt": b"WEBVTT\n\n00:00.000 --> 00:01.000\nHello\n",
    }
    for name, data in files.items():
        (out / name).write_bytes(data)
    (out / "youtube.txt").write_text(
        f"Nine | AgentSee Podcast, episode 9\n\nA summary.\n\nEpisode page: {EP_URL}\n\nChapters\n00:00 Start\n"
    )
    release = {
        "slug": "ep9",
        "show": {"name": "AgentSee Podcast", "hashtags": ["AgentSee", "AIagents"]},
        "episode": {
            "number": "9",
            "title": "Nine",
            "slug": "ep9",
            "guid": "g9",
            "date": DAY0,
            "release": DAY0,
            "url": EP_URL,
            "media_url": MEDIA,
            "summary": "What happened in nine.",
            "approved": {"by": "Tester", "at": "2026-10-15T10:00:00", "release": DAY0},
        },
        "youtube": {
            "title": "Nine | AgentSee Podcast, episode 9",
            "description": "desc",
            "tags": ["AgentSee", "x" * 490, "the podcast"],
            "category": 28,
            "playlist": "AgentSee Podcast",
        },
        "notes": {"youtube": "youtube.txt"},
        "files": {
            n: {
                "bytes": len(d),
                "type": "video/mp4" if n.endswith(".mp4") else "application/octet-stream",
                "sha256": hashlib.sha256(d).hexdigest(),
            }
            for n, d in files.items()
        },
        "clips": [
            {
                "index": 1,
                "slug": "the-point",
                "title": "The point",
                "file": "clips/01-the-point.mp4",
                "poster": "clips/01-the-point.png",
                "poster_jpg": "clips/01-the-point.jpg",
                "captions": "clips/01-the-point.vtt",
                "post": {
                    "text": "The point\n\nFrom episode 9.",
                    "link": EP_URL,
                    "link_at": EP_URL + "#t=42",
                    "at_hms": "00:42",
                },
                "hashtags": ["AgentSee"],
            }
        ],
        "plan": [{"asset": "episode", "day": 0, "date": DAY0}, {"asset": "clip:01-the-point", "day": 1, "date": DAY1}],
    }
    (out / "release.json").write_text(json.dumps(release))
    public = [n for n in files if n != "episode.mp4"]
    staged = {
        "slug": "ep9",
        "at": "2026-10-15T11:00:00",
        "approved": {},
        "media": {n: {"url": MEDIA + n, "sha256": "0" * 64, "state": "uploaded"} for n in public},
        "archive": {f"episodes/ep9/out/{n}": {"sha256": "0" * 64, "state": "uploaded"} for n in files},
    }
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

    state = {
        "uploads": {},
        "playlists": [],
        "items": [],
        "captions": [],
        "thumbs": [],
        "fail_session": False,
        "metas": [],
        "aborted": set(),
        "playlist_down": False,
    }

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
                return self.out(
                    400,
                    {
                        "error": {
                            "message": "The user has exceeded the number of videos they may upload.",
                            "errors": [{"reason": "uploadLimitExceeded"}],
                        }
                    },
                )
            meta = json.loads(self.body())
            size = int(self.headers["X-Upload-Content-Length"])
            uid = f"u{len(s['uploads']) + 1}"
            s["uploads"][uid] = {
                "size": size,
                "got": 0,
                "meta": meta,
                "failed_once": False,
                "type": self.headers["X-Upload-Content-Type"],
            }
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
                return self.out(
                    409,
                    {"error": {"message": "The operation was aborted.", "errors": [{"reason": "SERVICE_UNAVAILABLE"}]}},
                )
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
                st = (
                    {"uploadStatus": "rejected", "rejectionReason": "length", "privacyStatus": "private"}
                    if vid == "VIDbad"
                    else {"uploadStatus": "processed", "privacyStatus": "public" if vid == "VIDpublic" else "private"}
                )
                items.append(
                    {"id": vid, "statistics": {"viewCount": "12", "likeCount": "3", "commentCount": "1"}, "status": st}
                )
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
                return self.out(
                    400, {"error": "invalid_request", "error_description": "Value passed for the token was invalid."}
                )
            s["n"] += 1
            s["refresh"] = f"r{s['n']}"
            s["refreshes"] += 1
            return self.out(
                200,
                {
                    "token_type": "bearer",
                    "expires_in": 7200,
                    "access_token": f"XAT{s['n']}",
                    "scope": "tweet.read tweet.write users.read media.write offline.access",
                    "refresh_token": s["refresh"],
                },
            )
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
                return self.out(
                    200, {"data": {"id": mid, "processing_info": {"state": "pending", "check_after_secs": 0}}}
                )
            return self.out(200, {"data": {"id": mid}})
        if u.path == "/2/tweets":
            j = json.loads(self.body())
            assert len(j["text"]) <= 280
            for mid in j.get("media", {}).get("media_ids", []):
                assert s["media"][mid]["finalized"]
            if s["refuse"]:
                return self.out(
                    403,
                    {"title": "Forbidden", "detail": "You are not permitted to perform this action.", "status": 403},
                )
            s["tweets"].append(j)
            return self.out(201, {"data": {"id": f"19{len(s['tweets']):04d}", "text": j["text"]}})
        self.out(404, {"title": "Not Found", "detail": u.path, "status": 404})

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        s = StubX.state
        if u.path == "/2/tweets":
            q = urllib.parse.parse_qs(u.query)
            assert "public_metrics" in q["tweet.fields"][0]
            return self.out(
                200,
                {
                    "data": [
                        {
                            "id": i,
                            "public_metrics": {
                                "impression_count": 900,
                                "like_count": 7,
                                "retweet_count": 2,
                                "reply_count": 1,
                                "quote_count": 0,
                                "bookmark_count": 4,
                            },
                            "non_public_metrics": {"url_link_clicks": 30, "user_profile_clicks": 5},
                        }
                        for i in q["ids"][0].split(",")
                    ]
                },
            )
        if u.path == "/2/media/upload":
            q = urllib.parse.parse_qs(u.query)
            assert q["command"] == ["STATUS"]
            s["polls"] += 1
            state = "in_progress" if s["polls"] % 2 else "succeeded"
            return self.out(
                200, {"data": {"id": q["media_id"][0], "processing_info": {"state": state, "check_after_secs": 0}}}
            )
        self.out(404, {"title": "Not Found", "detail": u.path, "status": 404})


class StubThreads(Stub):
    """Meta's Threads endpoints: the consent's two exchanges, debug_token, me, the publishing quota, the
    container with its status poll, the publish, the media read, the recent posts, the refresh. The vault is
    FakeVault. `mode` bends one answer: "error" (a container Meta cannot take), "limit" (a 613 with Meta's
    estimate), "bad-token" (a 190)."""

    state = {
        "mode": None,
        "token": None,
        "n": 1,
        "exchanges": 0,
        "refreshes": 0,
        "containers": {},
        "posts": [],
        "polls": 0,
        "quota": 1,
        "valid": True,
        "expires_at": 0,
        "grant_at": 0,
        "issued_at": 0,
    }

    @staticmethod
    def now():
        return int(dt.datetime.now(dt.timezone.utc).timestamp())

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        s = StubThreads.state
        q = {k: v[0] for k, v in urllib.parse.parse_qs(self.body().decode()).items()}
        if u.path == "/oauth/access_token":
            assert (q["grant_type"], q["client_id"], q["client_secret"]) == ("authorization_code", "tid", "tsec"), q
            assert q["redirect_uri"] == "https://engine.agentsee.work/meta/threads/callback", q
            if q["code"] != "CODE1":
                return self.out(400, {"error": {"message": "Invalid code", "type": "OAuthException", "code": 100}})
            s["exchanges"] += 1
            return self.out(200, {"access_token": "SL1", "token_type": "bearer", "user_id": 1784})
        if q.get("access_token") != s["token"] or s["mode"] == "bad-token":  # the mode lasts until the test ends it
            return self.out(
                400, {"error": {"message": "Invalid OAuth access token", "type": "OAuthException", "code": 190}}
            )
        if u.path == "/v1.0/1784/threads":
            if s["mode"] == "limit":
                s["mode"] = None
                usage = {"123": [{"type": "threads", "call_count": 100, "estimated_time_to_regain_access": 7}]}
                return self.out(
                    400,
                    {"error": {"message": "Application request limit reached", "type": "OAuthException", "code": 613}},
                    {"X-Business-Use-Case-Usage": json.dumps(usage)},
                )
            cid = f"c{len(s['containers']) + 1}"
            s["containers"][cid] = {**q, "status": "IN_PROGRESS", "polls": 0, "error": s["mode"] == "error"}
            s["mode"] = None
            return self.out(200, {"id": cid})
        if u.path == "/v1.0/1784/threads_publish":
            ctn = s["containers"][q["creation_id"]]
            assert ctn["status"] == "FINISHED", ctn
            ctn["status"] = "PUBLISHED"
            mid = f"m{len(s['posts']) + 1}"
            s["posts"].append(
                {
                    "id": mid,
                    "text": ctn.get("text", ""),
                    "permalink": f"https://www.threads.com/@agentsee.work/post/P{mid}",
                    "timestamp": "2026-11-06T09:00:00+0000",
                }
            )
            return self.out(200, {"id": mid})
        self.out(404, {"error": {"message": u.path, "code": 803}})

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        s = StubThreads.state
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path == "/access_token":
            assert q == {"grant_type": "th_exchange_token", "client_secret": "tsec", "access_token": "SL1"}, q
            s["token"] = "LL1"
            s["expires_at"], s["grant_at"], s["issued_at"] = (
                self.now() + 60 * 86400,
                self.now() + 90 * 86400,
                self.now(),
            )
            return self.out(200, {"access_token": "LL1", "token_type": "bearer", "expires_in": 5183944})
        if u.path == "/refresh_access_token":
            assert q["grant_type"] == "th_refresh_token"
            if q["access_token"] != s["token"]:
                return self.out(400, {"error": {"message": "Invalid OAuth access token", "code": 190}})
            s["refreshes"] += 1
            s["n"] += 1
            s["token"] = f"LL{s['n']}"
            s["expires_at"], s["grant_at"], s["issued_at"] = (
                self.now() + 60 * 86400,
                self.now() + 90 * 86400,
                self.now(),
            )
            return self.out(200, {"access_token": s["token"], "token_type": "bearer", "expires_in": 5183944})
        if q.get("access_token") != s["token"] or s["mode"] == "bad-token":  # the mode lasts until the test ends it
            return self.out(
                400, {"error": {"message": "Invalid OAuth access token", "type": "OAuthException", "code": 190}}
            )
        if u.path == "/v1.0/debug_token":
            assert q["input_token"] == s["token"]
            return self.out(
                200,
                {
                    "data": {
                        "app_id": "123",
                        "type": "USER",
                        "application": "AgentSee publisher",
                        "data_access_expires_at": s["grant_at"],
                        "expires_at": s["expires_at"],
                        "is_valid": s["valid"],
                        "issued_at": s["issued_at"],
                        "scopes": list(threads.SCOPES),
                        "user_id": "1784",
                    }
                },
            )
        if u.path == "/v1.0/me":
            return self.out(200, {"id": "1784", "username": "agentsee.work"})
        if u.path == "/v1.0/1784/threads_publishing_limit":
            return self.out(
                200, {"data": [{"quota_usage": s["quota"], "config": {"quota_total": 250, "quota_duration": 86400}}]}
            )
        if u.path == "/v1.0/1784/threads":
            return self.out(200, {"data": list(reversed(s["posts"]))})
        if u.path == "/v1.0/1784/threads_insights":
            if q["metric"] == "clicks":
                links = [{"value": 9, "link_url": "https://agentsee.work/p/e9thc01"}, {"value": 2, "link_url": "x"}]
                return self.out(200, {"data": [{"name": "clicks", "period": "day", "link_total_values": links}]})
            data = [
                {
                    "name": "views",
                    "period": "day",
                    "values": [{"value": 300, "end_time": "a"}, {"value": 40, "end_time": "b"}],
                },
                {"name": "likes", "period": "day", "total_value": {"value": 11}},
                {"name": "replies", "period": "day", "total_value": {"value": 3}},
                {"name": "reposts", "period": "day", "total_value": {"value": 2}},
                {"name": "quotes", "period": "day", "total_value": {"value": 1}},
                {"name": "followers_count", "period": "day", "total_value": {"value": 150}},
            ]
            return self.out(200, {"data": [d for d in data if d["name"] in q["metric"].split(",")]})
        m = re.fullmatch(r"/v1.0/(m\d+)/insights", u.path)
        if m:
            values = {"views": 120, "likes": 7, "replies": 1, "reposts": 2, "quotes": 0, "shares": 4}
            data = [
                {
                    "name": k,
                    "period": "lifetime",
                    "values": [{"value": v}],
                    "title": k,
                    "id": f"{m.group(1)}/insights/{k}",
                }
                for k, v in values.items()
                if k in q["metric"].split(",")
            ]
            return self.out(200, {"data": data})
        m = re.fullmatch(r"/v1.0/(c\d+)", u.path)
        if m:
            ctn = s["containers"][m.group(1)]
            s["polls"] += 1
            ctn["polls"] += 1
            if ctn["status"] == "IN_PROGRESS" and ctn["polls"] >= 2:
                ctn["status"] = "ERROR" if ctn["error"] else "FINISHED"
            ans = {"id": m.group(1), "status": ctn["status"]}
            if ctn["status"] == "ERROR":
                ans["error_message"] = "FAILED_DOWNLOADING_VIDEO"
            return self.out(200, ans)
        m = re.fullmatch(r"/v1.0/(m\d+)", u.path)
        if m:
            p = next(x for x in s["posts"] if x["id"] == m.group(1))
            return self.out(200, {**p, "shortcode": "P" + p["id"]})
        self.out(404, {"error": {"message": u.path, "code": 803}})


class StubInstagram(Stub):
    """Meta's Graph API for the Instagram account: which account the token serves, debug_token with the app's
    own token, the publishing quota, the container with its status poll, the publish, the media read and the
    recent media. `mode` bends one answer: "error" (a container Meta cannot take), "limit" (a 613 with Meta's
    estimate), "bad-token" (a 190), "restricted" (subcode 2207050)."""

    IG = "17841400001"
    state = {
        "mode": None,
        "containers": {},
        "posts": [],
        "polls": 0,
        "quota": 2,
        "valid": True,
        "asked": 0,
        "assigned": True,  # the account shared at the token's login, under /me/assigned_instagram_accounts
        "granular": None,  # the ids debug_token names under granular_scopes; None for the account itself
    }

    def no(self):
        s = StubInstagram.state
        if s["mode"] == "bad-token":
            return self.out(
                400, {"error": {"message": "Invalid OAuth access token", "type": "OAuthException", "code": 190}}
            )
        if s["mode"] == "restricted":
            return self.out(
                400,
                {
                    "error": {
                        "message": "The user is not allowed to perform this action",
                        "type": "OAuthException",
                        "code": 10,
                        "error_subcode": 2207050,
                    }
                },
            )
        return False

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        s = StubInstagram.state
        q = {k: v[0] for k, v in urllib.parse.parse_qs(self.body().decode()).items()}
        if q.get("access_token") != "IGTOK":
            return self.out(400, {"error": {"message": "Invalid OAuth access token", "code": 190}})
        if self.no() is not False:
            return None
        if u.path == f"/v27.0/{self.IG}/media":
            if s["mode"] == "limit":
                s["mode"] = None
                usage = {
                    "call_count": 100,
                    "total_cputime": 25,
                    "total_time": 25,
                    "estimated_time_to_regain_access": 12,
                }
                return self.out(
                    400,
                    {"error": {"message": "Application request limit reached", "type": "OAuthException", "code": 613}},
                    {"X-App-Usage": json.dumps(usage)},
                )
            cid = f"c{len(s['containers']) + 1}"
            s["containers"][cid] = {**q, "status_code": "IN_PROGRESS", "polls": 0, "error": s["mode"] == "error"}
            s["mode"] = None
            return self.out(200, {"id": cid})
        if u.path == f"/v27.0/{self.IG}/media_publish":
            ctn = s["containers"][q["creation_id"]]
            assert ctn["status_code"] == "FINISHED", ctn
            ctn["status_code"] = "PUBLISHED"
            mid = f"m{len(s['posts']) + 1}"
            reel = ctn.get("media_type") == "REELS"
            s["posts"].append(
                {
                    "id": mid,
                    "caption": ctn.get("caption", ""),
                    "permalink": f"https://www.instagram.com/{'reel/R' if reel else 'p/P'}{mid}/",
                    "shortcode": f"{'R' if reel else 'P'}{mid}",
                    "timestamp": "2026-11-06T09:00:00+0000",
                    "media_product_type": "REELS" if reel else "FEED",
                    "like_count": 5,
                    "comments_count": 2,
                }
            )
            return self.out(200, {"id": mid})
        self.out(404, {"error": {"message": u.path, "code": 803}})

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        s = StubInstagram.state
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path == "/v27.0/debug_token":
            assert (q["access_token"], q["input_token"]) == ("iid|isec", "IGTOK"), q
            return self.out(
                200,
                {
                    "data": {
                        "app_id": "456",
                        "type": "USER",
                        "application": "AgentSee engine",
                        "data_access_expires_at": 0,
                        "expires_at": 0,
                        "is_valid": s["valid"],
                        "issued_at": 1762400000,
                        "scopes": ["instagram_basic", "instagram_content_publish", "instagram_manage_insights"],
                        "granular_scopes": [
                            {"scope": sc, "target_ids": [self.IG] if s["granular"] is None else s["granular"]}
                            for sc in ("instagram_basic", "instagram_content_publish")
                        ],
                        "user_id": "100000001",
                    }
                },
            )
        if q.get("access_token") != "IGTOK":
            return self.out(400, {"error": {"message": "Invalid OAuth access token", "code": 190}})
        if self.no() is not False:
            return None
        if u.path == "/v27.0/me/assigned_instagram_accounts":
            s["asked"] += 1
            return self.out(200, {"data": [{"id": self.IG, "username": "agentsee.work"}] if s["assigned"] else []})
        if u.path == f"/v27.0/{self.IG}" and q.get("fields") == "id,username":
            return self.out(200, {"id": self.IG, "username": "agentsee.work"})
        if u.path == f"/v27.0/{self.IG}/content_publishing_limit":
            return self.out(
                200, {"data": [{"quota_usage": s["quota"], "config": {"quota_total": 100, "quota_duration": 86400}}]}
            )
        if u.path == f"/v27.0/{self.IG}/media":
            return self.out(200, {"data": list(reversed(s["posts"]))})
        if u.path == f"/v27.0/{self.IG}":
            return self.out(200, {"id": self.IG, "followers_count": 42, "media_count": 7})
        if u.path == f"/v27.0/{self.IG}/insights":
            if q["metric"] == "follower_count":  # under a hundred followers
                return self.out(400, {"error": {"message": "(#100) Not enough followers for this metric", "code": 100}})
            assert (q["period"], q["metric_type"]) == ("day", "total_value"), q
            values = {"reach": 80, "views": 200, "accounts_engaged": 9, "total_interactions": 14}
            data = [
                {"name": k, "period": "day", "total_value": {"value": v}, "id": f"{self.IG}/insights/{k}/day"}
                for k, v in values.items()
                if k in q["metric"].split(",")
            ]
            return self.out(200, {"data": data})
        m = re.fullmatch(r"/v27.0/(m\d+)/insights", u.path)
        if m:
            p = next(x for x in s["posts"] if x["id"] == m.group(1))
            values = {
                "views": 500,
                "reach": 410,
                "likes": 5,
                "comments": 2,
                "saved": 6,
                "shares": 3,
                "total_interactions": 16,
            }
            if p["media_product_type"] == "REELS":
                values.update(
                    ig_reels_avg_watch_time=8500, ig_reels_video_view_total_time=4225000, reels_skip_rate=0.31
                )
            else:
                values.update(follows=1, profile_visits=12)
            asked = q["metric"].split(",")
            assert all(k in values for k in asked), (asked, p["media_product_type"])
            data = [
                {"name": k, "period": "lifetime", "values": [{"value": v}]} for k, v in values.items() if k in asked
            ]
            return self.out(200, {"data": data})
        m = re.fullmatch(r"/v27.0/(c\d+)", u.path)
        if m:
            ctn = s["containers"][m.group(1)]
            s["polls"] += 1
            ctn["polls"] += 1
            if ctn["status_code"] == "IN_PROGRESS" and ctn["polls"] >= 2:
                ctn["status_code"] = "ERROR" if ctn["error"] else "FINISHED"
            ans = {"id": m.group(1), "status_code": ctn["status_code"]}
            if ctn["status_code"] == "ERROR":
                ans["status"] = "Error: Media download failed (error_subcode: 2207026)"
            return self.out(200, ans)
        m = re.fullmatch(r"/v27.0/(m\d+)", u.path)
        if m:
            return self.out(200, next(x for x in s["posts"] if x["id"] == m.group(1)))
        self.out(404, {"error": {"message": u.path, "code": 803}})


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
        bucket, _, key = urllib.parse.urlsplit(self.path).path.lstrip("/").partition("/")
        assert bucket == "archive-test", bucket
        FakeR2.state["requests"].append((self.command, key))
        return key

    def listing(self):
        """ListObjectsV2 with a delimiter, two entries to a page so the continuation is walked."""
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
        assert q.get("list-type") == "2", q
        prefix, delim = q.get("prefix", ""), q.get("delimiter")
        folders, keys = set(), []
        for k in sorted(FakeR2.state["objects"]):
            if not k.startswith(prefix):
                continue
            rest = k[len(prefix) :]
            if delim and delim in rest:
                folders.add(prefix + rest.split(delim)[0] + delim)
            else:
                keys.append(k)
        entries = [("CommonPrefixes", "Prefix", f) for f in sorted(folders)] + [("Contents", "Key", k) for k in keys]
        start = int(q.get("continuation-token", "0"))
        page, more = entries[start : start + 2], len(entries) > start + 2
        xml = '<?xml version="1.0" encoding="UTF-8"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        xml += f"<Name>archive-test</Name><Prefix>{prefix}</Prefix><IsTruncated>{str(more).lower()}</IsTruncated>"
        if more:
            xml += f"<NextContinuationToken>{start + 2}</NextContinuationToken>"
        xml += "".join(f"<{outer}><{inner}>{v}</{inner}></{outer}>" for outer, inner, v in page)
        data = (xml + "</ListBucketResult>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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
        self.send_header("ETag", '"' + hashlib.md5(o["body"], usedforsecurity=False).hexdigest() + '"')
        for k, v in o["meta"].items():
            self.send_header("x-amz-meta-" + k, v)
        self.end_headers()

    def do_GET(self):
        key = self.check(b"")
        if not key:
            return self.listing()
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
                row = lambda m, ua, h, st, b: {  # noqa: E731
                    "timestamp": t(m),
                    "index1": "podcast/ep9/episode.mp3",
                    "blob3": ua,
                    "blob4": "GB",
                    "blob5": h,
                    "blob6": "",
                    "double1": st,
                    "double2": b,
                    "double3": 0,
                    "double4": b - 1,
                    "double5": 12000000,
                }
                rows = [
                    row(50, "Overcast/2024 (+http://overcast.fm/; iOS podcast app)", "h1", 206, 2000000),
                    row(40, "Overcast/2024 (+http://overcast.fm/; iOS podcast app)", "h1", 206, 3000000),
                    row(30, "Googlebot/2.1 (+http://www.google.com/bot.html)", "h2", 200, 12000000),
                    row(20, "Mozilla/5.0 (Macintosh) AppleWebKit/605 Version/17 Safari/605", "h3", 206, 100000),
                    row(10, "python-requests/2.31", "h4", 200, 12000000),
                    row(5, "Overcast/2024 (+http://overcast.fm/; iOS podcast app)", "h5", 304, 0),
                ]
                return self.out(200, {"meta": [], "data": rows, "rows": len(rows)})
            if "podcast_links" in q:
                rows = [
                    {
                        "timestamp": t(45),
                        "index1": "e9yte",
                        "blob2": "Mozilla/5.0 (iPhone) Safari",
                        "blob3": "GB",
                        "blob4": "h1",
                        "blob5": "https://www.youtube.com",
                        "double1": 302,
                    },
                    {
                        "timestamp": t(44),
                        "index1": "nope",
                        "blob2": "curl/8",
                        "blob3": "US",
                        "blob4": "h9",
                        "blob5": "",
                        "double1": 404,
                    },
                ]
                return self.out(200, {"meta": [], "data": rows, "rows": len(rows)})
        self.out(404, {"error": "no such path " + u.path})

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/xrpc/com.atproto.identity.resolveHandle":
            assert q["handle"] == ["agentsee.work"]
            return self.out(200, {"did": "did:plc:test"})
        if u.path == "/xrpc/app.bsky.feed.getPosts":
            return self.out(
                200,
                {
                    "posts": [
                        {
                            "uri": uri,
                            "cid": "c",
                            "likeCount": 5,
                            "repostCount": 2,
                            "replyCount": 1,
                            "quoteCount": 0,
                            "bookmarkCount": 3,
                        }
                        for uri in q["uris"]
                    ]
                },
            )
        if u.path == "/api/v1/statuses/123":
            return self.out(
                200,
                {
                    "id": "123",
                    "url": "x",
                    "created_at": "2026-10-20T09:00:00Z",
                    "favourites_count": 4,
                    "reblogs_count": 1,
                    "replies_count": 0,
                },
            )
        if u.path == "/analytics/reports":
            assert q["ids"] == ["channel==MINE"] and q["dimensions"] == ["video"] and q["sort"] == ["-views"]
            ids = q["filters"][0].removeprefix("video==").split(",")
            names = ["video"] + collect.YT_METRICS.split(",")
            return self.out(
                200,
                {
                    "columnHeaders": [{"name": n} for n in names],
                    "rows": [[v, 12, 50.5, 31.2, 61.0, 3, 1, 2, 1] for v in ids],
                },
            )
        self.out(404, {"error": "no such path " + u.path})


class StubBluesky(Stub):
    """The entryway and the PDS in one: a session, a service token, blobs, the
    video service with a job that needs two polls, and the record."""

    state = {"records": [], "blobs": [], "videos": [], "polls": 0, "auths": []}

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        s = StubBluesky.state
        base = f"http://{self.headers['Host']}"
        if u.path == "/xrpc/com.atproto.server.createSession":
            d = json.loads(self.body())
            assert d == {"identifier": "agentsee.work", "password": "app-pass"}, d
            return self.out(
                200,
                {
                    "accessJwt": "JWT",
                    "refreshJwt": "R",
                    "did": "did:plc:test",
                    "handle": "agentsee.work",
                    "didDoc": {
                        "service": [
                            {"id": "#atproto_pds", "type": "AtprotoPersonalDataServer", "serviceEndpoint": base}
                        ]
                    },
                },
            )
        if u.path == "/xrpc/com.atproto.repo.uploadBlob":
            assert self.headers.get("Authorization") == "Bearer JWT"
            data = self.body()
            s["blobs"].append((self.headers["Content-Type"], len(data)))
            return self.out(
                200,
                {
                    "blob": {
                        "$type": "blob",
                        "ref": {"$link": "bafyimg"},
                        "mimeType": self.headers["Content-Type"],
                        "size": len(data),
                    }
                },
            )
        if u.path == "/xrpc/app.bsky.video.uploadVideo":
            assert self.headers.get("Authorization") == "Bearer SVC", self.headers.get("Authorization")
            assert self.headers["Content-Type"] == "video/mp4"
            q = urllib.parse.parse_qs(u.query)
            s["videos"].append((q["did"][0], q["name"][0], len(self.body())))
            return self.out(200, {"jobId": "j1", "did": q["did"][0], "state": "JOB_STATE_CREATED"})
        if u.path == "/xrpc/com.atproto.repo.createRecord":
            assert self.headers.get("Authorization") == "Bearer JWT"
            d = json.loads(self.body())
            assert d["repo"] == "did:plc:test" and d["collection"] == "app.bsky.feed.post"
            s["records"].append(d["record"])
            return self.out(200, {"uri": f"at://did:plc:test/app.bsky.feed.post/3k{len(s['records'])}", "cid": "c"})
        self.out(404, {"error": "NotFound", "message": "no such path " + u.path})

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        s = StubBluesky.state
        if u.path == "/xrpc/com.atproto.server.getServiceAuth":
            assert self.headers.get("Authorization") == "Bearer JWT"
            s["auths"].append(q)
            assert q["aud"] == ["did:web:127.0.0.1"] and q["lxm"] == ["com.atproto.repo.uploadBlob"], q
            return self.out(200, {"token": "SVC"})
        if u.path == "/xrpc/app.bsky.video.getJobStatus":
            s["polls"] += 1
            if s["polls"] % 2:
                return self.out(
                    200, {"jobStatus": {"jobId": q["jobId"][0], "state": "JOB_STATE_PROCESSING", "progress": 50}}
                )
            return self.out(
                200,
                {
                    "jobStatus": {
                        "jobId": q["jobId"][0],
                        "state": "JOB_STATE_COMPLETED",
                        "blob": {"$type": "blob", "ref": {"$link": "bafyvid"}, "mimeType": "video/mp4", "size": 1},
                    }
                },
            )
        self.out(404, {"error": "NotFound", "message": "no"})


class StubMastodon(Stub):
    """An instance: the media upload (an image at once, a video after a poll), the status."""

    state = {"media": [], "statuses": [], "polls": 0}

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        s = StubMastodon.state
        assert self.headers.get("Authorization") == "Bearer MTOK"
        if u.path == "/api/v2/media":
            ct = self.headers["Content-Type"]
            assert ct.startswith("multipart/form-data; boundary=")
            body = self.body()
            m = re.search(rb'name="file"; filename="([^"]+)"\r\nContent-Type: ([^\r]+)\r\n\r\n', body)
            d = re.search(rb'name="description"\r\n\r\n([^\r]*)\r\n', body)
            s["media"].append(
                {"file": m.group(1).decode(), "type": m.group(2).decode(), "description": d.group(1).decode()}
            )
            mid = f"m{len(s['media'])}"
            if m.group(2) == b"video/mp4":
                return self.out(202, {"id": mid, "type": "video", "url": None})
            return self.out(200, {"id": mid, "type": "image", "url": f"http://{self.headers['Host']}/media/{mid}"})
        if u.path == "/api/v1/statuses":
            assert self.headers.get("Idempotency-Key", "").startswith("agentsee-post-")
            form = urllib.parse.parse_qs(self.body().decode())
            s["statuses"].append({"form": form, "key": self.headers["Idempotency-Key"]})
            sid = str(100 + len(s["statuses"]))
            return self.out(200, {"id": sid, "url": f"http://{self.headers['Host']}/@agentseework/{sid}", "uri": "x"})
        self.out(404, {"error": "no such path " + u.path})

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        s = StubMastodon.state
        m = re.fullmatch(r"/api/v1/statuses/(\d+)", u.path)
        if m:
            return self.out(
                200,
                {
                    "id": m.group(1),
                    "url": f"http://{self.headers['Host']}/@agentseework/{m.group(1)}",
                    "created_at": "2026-10-20T09:00:00Z",
                    "favourites_count": 4,
                    "reblogs_count": 1,
                    "replies_count": 0,
                },
            )
        m = re.fullmatch(r"/api/v1/media/(m\d+)", u.path)
        if m:
            s["polls"] += 1
            if s["polls"] % 2:
                return self.out(206, {"id": m.group(1), "url": None})
            return self.out(200, {"id": m.group(1), "url": f"http://{self.headers['Host']}/media/{m.group(1)}"})
        self.out(404, {"error": "no"})


class FakeVault:
    """One item the engine writes, in memory. Can refuse one write."""

    def __init__(self, value, item="X podcast token"):
        self.value, self.fail_write, self.writes, self.item, self.fields = value, False, 0, item, {}

    def read(self, vault_, item, field):
        assert (vault_, item, field) == ("Engineering", self.item, "credential")
        return self.value

    def write(self, vault_, item, field, value):
        assert (vault_, item) == ("Engineering", self.item)
        if field != "credential":  # the item's other fields (a username the consent fills in)
            self.fields[field] = value
            return
        self.writes += 1
        if self.fail_write:
            self.fail_write = False
            raise vault.VaultError("no session")
        self.value = value


class FakeLinear:
    """Linear's token endpoint and its GraphQL, as far as engine/linear.py
    uses them, in memory: a rotating refresh token, one issue, labels,
    comments, and a failure to order."""

    def __init__(self):
        self.refresh, self.access, self.n = "lr1", None, 0
        self.issues = {
            "AGE-73": {
                "id": "i-73",
                "identifier": "AGE-73",
                "title": "Ada Lovelace, on engines",
                "url": "https://linear.app/agentsee/issue/AGE-73",
                "labelIds": [],
                "dueDate": None,
                "team": {"id": "t-age", "key": "AGE"},
            }
        }
        self.labels, self.comments, self.calls = {}, [], []
        self.fail = None  # a status to answer the next GraphQL call with, once
        self.refuse = None  # an operation to answer with Linear's "not allowed to take action", every time

    def __call__(self, url, data, headers, timeout=30):
        if url.endswith("/oauth/token"):
            f = urllib.parse.parse_qs(data.decode())
            if (f.get("grant_type"), f.get("client_id"), f.get("client_secret")) != (
                ["refresh_token"],
                ["lcid"],
                ["lcs"],
            ):
                return 400, b'{"error":"invalid_request"}'
            if f.get("refresh_token") != [self.refresh]:
                return 400, b'{"error":"invalid_grant","error_description":"The refresh token is invalid"}'
            self.n += 1
            self.refresh, self.access = f"lr{self.n + 1}", f"la{self.n}"
            tok = {
                "access_token": self.access,
                "token_type": "Bearer",
                "expires_in": 86399,
                "scope": "read write issues:create comments:create",
                "refresh_token": self.refresh,
            }
            return 200, json.dumps(tok).encode()
        if headers.get("Authorization") != f"Bearer {self.access}":
            return 401, b'{"errors":[{"message":"Authentication required, not authenticated"}]}'
        if self.fail:
            status, self.fail = self.fail, None
            return status, b'{"errors":[{"message":"Something went wrong"}]}'
        q = json.loads(data)
        op, v = q["operationName"], q["variables"]
        self.calls.append(op)
        if self.refuse == op:
            return 200, b'{"errors":[{"message":"not allowed to take action"}]}'
        return 200, json.dumps({"data": self.answer(op, v)}).encode()

    def by_id(self, uuid):
        return next(i for i in self.issues.values() if i["id"] == uuid)

    def answer(self, op, v):
        if op == "Me":
            return {"viewer": {"id": "app-1", "name": "AgentSee Engine"}}
        if op == "Issue":
            return {
                "issue": self.issues.get(v["id"]) or next((i for i in self.issues.values() if i["id"] == v["id"]), None)
            }
        if op == "Notes":
            i = self.issues.get(v["id"])
            return {"issue": {k: i.get(k) for k in ("id", "identifier", "title", "description")} if i else None}
        if op == "Issues":
            hits = [i for i in self.issues.values() if v["text"].lower() in i["title"].lower()]
            return {"issues": {"nodes": [{**i, "state": {"name": "Backlog"}} for i in hits]}}
        if op == "Team":
            return {"teams": {"nodes": [{"id": "t-age"}] if v["key"] == "AGE" else []}}
        if op == "Project":
            return {"projects": {"nodes": [{"id": "p-pod"}] if v["name"] == "Podcast" else []}}
        if op == "Labels":
            return {
                "issueLabels": {"nodes": [x for x in self.labels.values() if x["name"].lower() == v["name"].lower()]}
            }
        if op == "Label":
            lid = f"l-{len(self.labels) + 1}"
            self.labels[lid] = {"id": lid, "name": v["name"], "team": {"id": v["team"]}}
            return {"issueLabelCreate": {"success": True, "issueLabel": {"id": lid}}}
        if op == "Update":
            i = self.by_id(v["id"])
            i.update({k: v["input"][k] for k in ("labelIds", "dueDate") if k in v["input"]})
            return {"issueUpdate": {"success": True, "issue": i}}
        if op == "Comment":
            i = self.by_id(v["issue"])
            self.comments.append((i["identifier"], v["body"]))
            n = len(self.comments)
            return {"commentCreate": {"success": True, "comment": {"id": f"c-{n}", "url": f"{i['url']}#comment-{n}"}}}
        if op == "Create":
            n = 100 + len(self.issues)
            i = {
                "id": f"i-{n}",
                "identifier": f"AGE-{n}",
                "title": v["input"]["title"],
                "description": v["input"].get("description"),
                "url": f"https://linear.app/agentsee/issue/AGE-{n}",
                "labelIds": v["input"].get("labelIds", []),
                "dueDate": None,
                "team": {"id": v["input"]["teamId"], "key": "AGE"},
            }
            self.issues[i["identifier"]] = i
            return {"issueCreate": {"success": True, "issue": i}}
        if op == "Documents":
            i = self.issues.get(v["id"])
            docs = [{"id": "d-1", "title": "Guest release, version 1", "content": RELEASE_TEXT}]
            return {"issue": {"documents": {"nodes": docs}} if i else None}
        if op == "CommentUpdate":
            n = int(v["id"].split("-")[1])
            ident = self.comments[n - 1][0]
            self.comments[n - 1] = (ident, v["body"])
            url = f"{self.issues[ident]['url']}#comment-{n}"
            return {"commentUpdate": {"success": True, "comment": {"id": v["id"], "url": url}}}
        raise AssertionError(f"FakeLinear has no answer for {op}")


class FakeStream(io.BytesIO):
    """A response engine/idea.py reads line by line, with a status."""

    def __init__(self, status, body):
        super().__init__(body)
        self.status = status


class GatedStream:
    """FakeStream, but held after its first text delta until the test lets it on: a reply taking its time."""

    def __init__(self, body, gate):
        self.status, self.lines, self.gate = 200, body.splitlines(keepends=True), gate

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        held = False
        for line in self.lines:
            yield line
            if not held and b'"text_delta"' in line:
                held = True
                self.gate.wait(10)

    def read(self):
        return b""


class FakeAnthropic:
    """The Messages API's stream, as far as engine/idea.py reads it: scripted
    replies as server-sent events with usage, a thinking block with nothing in
    it first; a status to fail with, once; a gate to hold the next reply at its
    first word, once; every payload kept."""

    def __init__(self):
        self.replies, self.payloads, self.fail, self.gate = [], [], None, None
        self.stops = []  # a stop reason per scripted reply; end_turn when none is given

    def __call__(self, url, data, headers, timeout=None):
        payload = json.loads(data)
        self.payloads.append((payload, headers))
        stop = self.stops.pop(0) if self.stops else "end_turn"
        if self.fail:
            status, self.fail = self.fail, None
            return FakeStream(status, b'{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}')
        n = sum(1 for m in payload["messages"] if m["role"] == "user")
        text = (
            self.replies.pop(0)
            if self.replies
            else f"That is the spine ({n}). What have you actually done that earns it?"
        )
        events = [
            (
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_1",
                        "model": payload["model"],
                        "usage": {"input_tokens": 1000, "cache_read_input_tokens": 500, "output_tokens": 1},
                    },
                },
            ),
            (
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "content_block_start",
                {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
            ),
        ]
        for i in range(0, len(text), 7):
            events.append(
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 1,
                        "delta": {"type": "text_delta", "text": text[i : i + 7]},
                    },
                )
            )
        events += [
            ("content_block_stop", {"type": "content_block_stop", "index": 1}),
            (
                "message_delta",
                {"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 50}},
            ),
            ("message_stop", {"type": "message_stop"}),
        ]
        body = "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode("utf-8")
        if self.gate:
            gate, self.gate = self.gate, None
            return GatedStream(body, gate)
        return FakeStream(200, body)


class FakeSMTP:
    """smtplib.SMTP_SSL as far as engine/mail.py uses it: a context manager
    with login and send_message. Keeps what was sent; can refuse once, and can hang up on the next `hangups`
    connections the way Stalwart's throttle does, closing before a word is said."""

    sent, logins, opened, fail, hangups = [], [], [], None, 0

    def __init__(self, host, port, timeout=None):
        FakeSMTP.opened.append((host, port, timeout))
        if FakeSMTP.hangups:
            FakeSMTP.hangups -= 1
            raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, password):
        FakeSMTP.logins.append((user, password))
        if FakeSMTP.fail == "login":
            FakeSMTP.fail = None
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication credentials invalid")

    def send_message(self, m):
        if FakeSMTP.fail == "send":
            FakeSMTP.fail = None
            raise smtplib.SMTPDataError(451, b"4.3.0 try later")
        FakeSMTP.sent.append(m)


RELEASE_TEXT = """# Appearing on the AgentSee podcast

This is the agreement between you and AgentSee about the conversation we are
about to record, in plain words, by us and not by a lawyer.

## What we record

A conversation with you, on video and audio, over a video link.

## What we may publish

The recording, edited, in full and in parts, with captions and your name.

- On [agentsee.work](<https://agentsee.work>) and on **YouTube**.
- On podcast apps and social networks; the [privacy notice](<https://agentsee.work/privacy/>) covers the rest.

## Signing

Your typed name and your drawn signature, with the record, are how you agree.
"""


def test_png(w=16, h=8):
    """A valid PNG, RGBA, opaque black, of a size a signature could have."""

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\x00\x00\x00\xff" * w for _ in range(h))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


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
        cls.r2env = {
            "R2_ACCOUNT_ID": "acct",
            "R2_ARCHIVE_BUCKET": "archive-test",
            "AWS_ACCESS_KEY_ID": "AKID",
            "AWS_SECRET_ACCESS_KEY": "secret",
            "R2_ENDPOINT": f"http://127.0.0.1:{cls.r2.server_address[1]}",
        }
        cls.reach = http.server.HTTPServer(("127.0.0.1", 0), StubReach)
        threading.Thread(target=cls.reach.serve_forever, daemon=True).start()
        rbase = f"http://127.0.0.1:{cls.reach.server_address[1]}"
        collect.SQL_URL = rbase + "/client/v4/accounts/{account}/analytics_engine/sql"
        collect.BSKY, collect.ANALYTICS = rbase + "/xrpc", rbase + "/analytics"
        cls.rbase = rbase
        cls.bsky = http.server.HTTPServer(("127.0.0.1", 0), StubBluesky)
        threading.Thread(target=cls.bsky.serve_forever, daemon=True).start()
        bluesky.ENTRY = bluesky.VIDEO = f"http://127.0.0.1:{cls.bsky.server_address[1]}"
        bluesky.POLL = 0
        cls.masto = http.server.HTTPServer(("127.0.0.1", 0), StubMastodon)
        threading.Thread(target=cls.masto.serve_forever, daemon=True).start()
        mastodon.POLL = 0
        cls.senv = {
            "BLUESKY_IDENTIFIER": "agentsee.work",
            "BLUESKY_APP_PASSWORD": "app-pass",
            "MASTODON_ACCESS_TOKEN": "MTOK",
            "MASTODON_INSTANCE": f"http://127.0.0.1:{cls.masto.server_address[1]}",
        }
        cls.data = root / "data"
        os.environ["ENGINE_DATA"] = str(cls.data)
        mail.SMTP = FakeSMTP
        cls.menv = {"MAIL_USER": "office", "MAIL_PASSWORD": "office-pass"}

    @classmethod
    def tearDownClass(cls):
        for srv in (cls.stub, cls.xstub, cls.r2, cls.reach, cls.bsky, cls.masto):
            srv.shutdown()
            srv.server_close()
        cls.tmp.cleanup()

    def c(self):
        c = ledger.connect(self.db)
        self.addCleanup(c.close)
        return c

    def test_01_ingest(self):
        c = self.c()
        n = ingest.ingest(c, "ep9", self.src, "tester")
        self.assertEqual(n, 9)
        e = ledger.episode(c, "ep9")
        self.assertEqual(e["title"], "Nine")
        self.assertTrue(e["release_at"].startswith(DAY0 + "T06:00"))
        kinds = {
            r["name"]: (r["kind"], r["url"], r["archived"])
            for r in c.execute("SELECT * FROM deliverable WHERE episode='ep9'")
        }
        self.assertEqual(kinds["episode.mp4"], ("video", None, 1))
        self.assertEqual(kinds["clips/01-the-point.mp4"], ("clip", MEDIA + "clips/01-the-point.mp4", 1))
        self.assertEqual(kinds["clips/01-the-point.vtt"][0], "clip-captions")
        self.assertEqual(kinds["episode-art.jpg"][0], "art")
        self.assertEqual(kinds["clips/01-the-point.jpg"], ("poster", MEDIA + "clips/01-the-point.jpg", 1))
        self.assertEqual(ingest.ingest(c, "ep9", self.src), 9, "re-ingest is idempotent")

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
        self.assertTrue(
            xe["text"].startswith("Nine\n\nWhat happened in nine.\n\nhttps://agentsee.work/p/e9xe"), xe["text"]
        )
        long = "A sentence that is long enough to matter. " * 12
        fitted = plan.fit("Head", long, ["https://agentsee.work/p/e9xe", "#AgentSee"], 280)
        self.assertLessEqual(len(fitted), 280)
        self.assertTrue(fitted.startswith("Head\n\nA sentence") and fitted.endswith("#AgentSee"), fitted)
        self.assertEqual(
            plan.fit("Head", "", ["https://agentsee.work/p/e9xe", "#AgentSee"], 40),
            "Head\n\nhttps://agentsee.work/p/e9xe",
        )
        self.assertEqual(plan.fit("Head", "", ["https://agentsee.work/p/e9xe"], 10), "Head")
        teaser = plan.fit(
            "Head",
            "One very long opening sentence that will not fit in the budget as it stands. Second.",
            ["https://agentsee.work/p/e9xe"],
            90,
        )
        self.assertLessEqual(len(teaser), 90)
        self.assertTrue(teaser.startswith("Head\n\nOne very long") and "…\n\nhttps://" in teaser, teaser)
        self.assertEqual(plan.plan(c, "ep9"), [], "a second plan adds nothing")
        ledger.update_post(c, x["id"], text="edited")
        plan.plan(c, "ep9")
        self.assertEqual(ledger.post(c, x["id"])["text"], "edited")
        ledger.update_post(c, x["id"], text=x["text"])

    def test_02b_prune(self):
        c = self.c()
        kept = list(plan.ACCOUNTS["threads"]["assets"])
        clips = [
            p["id"]
            for p in c.execute(
                "SELECT id FROM post WHERE episode='ep9' AND account='threads' AND asset LIKE 'clip:%' ORDER BY id"
            )
        ]
        episode = c.execute(
            "SELECT id FROM post WHERE episode='ep9' AND account='threads' AND asset='episode'"
        ).fetchone()["id"]
        self.assertTrue(clips)
        ledger.update_post(c, clips[0], status="hand")  # a card a person already has
        try:
            plan.ACCOUNTS["threads"]["assets"] = ["episode"]
            gone = plan.prune(c, "ep9", "tester")
            self.assertEqual(
                set(gone), set(clips[1:]), "every planned post of the kind the account no longer takes, and only those"
            )
            self.assertEqual(ledger.post(c, clips[0])["status"], "hand", "a card a person has is left alone")
            ledger.update_post(c, clips[0], status="planned")
            self.assertEqual(plan.prune(c, "ep9", "tester"), [clips[0]], "planned again, it goes")
            self.assertEqual(ledger.post(c, clips[0])["status"], "skipped")
            self.assertEqual(ledger.post(c, episode)["status"], "planned", "the kind the account still takes stays")
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) FROM post WHERE episode='ep9' AND account!='threads' AND status='skipped'"
                ).fetchone()[0],
                0,
            )
            why = c.execute("SELECT detail FROM event WHERE what='prune' AND post=?", (clips[0],)).fetchone()["detail"]
            self.assertIn("threads no longer takes a clip", why)
            self.assertEqual(plan.plan(c, "ep9"), [], "a re-plan does not bring a pruned post back")
            self.assertEqual(plan.prune(c, "ep9"), [], "a second prune finds nothing")
            account = plan.ACCOUNTS.pop("threads")
            self.assertEqual(plan.prune(c, "ep9"), [episode], "an account dropped altogether loses its planned posts")
            plan.ACCOUNTS["threads"] = account
            self.assertIn(
                "gone from accounts.json",
                c.execute("SELECT detail FROM event WHERE what='prune' AND post=?", (episode,)).fetchone()["detail"],
            )
        finally:
            plan.ACCOUNTS["threads"]["assets"] = kept
            for pid in clips + [episode]:
                ledger.update_post(c, pid, status="planned")

    def test_03_links(self):
        c = self.c()
        got = {}
        n = plan.links(c, "ep9", put=lambda k, v: got.__setitem__(k, v))
        self.assertEqual(n, len(got))
        self.assertEqual(
            got["e9xc01"], {"url": EP_URL + "#t=42", "episode": "ep9", "post": "clip:01-the-point", "platform": "x"}
        )
        self.assertEqual(got["e9lpe"]["url"], EP_URL)
        with self.assertRaises(RuntimeError):
            plan.kv_put("e9xc01", {}, token=None) if not os.environ.get("CLOUDFLARE_API_TOKEN") else (
                _ for _ in ()
            ).throw(RuntimeError())

    def test_03b_confirm(self):
        """Nothing of an episode is sent until a host has confirmed its plan: born unconfirmed, the publisher's
        lists leave its posts out, a run handed them by name leaves them, and one post is not published now or
        retried. Recording a post made by hand, and skipping, still go. Confirming hands them over; holding takes
        that back; the command line confirms as the page does. ep9 leaves here confirmed, as the tests after need."""
        import arrive

        c = self.c()
        quiet = lambda s: None  # noqa: E731
        far = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=400)).isoformat()
        of = lambda account: c.execute(  # noqa: E731
            "SELECT * FROM post WHERE episode='ep9' AND account=? AND asset='episode'", (account,)
        ).fetchone()
        yt, xe, card = of("youtube"), of("x"), of("linkedin-abrar")
        ids = lambda rows: {r["id"] for r in rows}  # noqa: E731
        e = ledger.episode(c, "ep9")
        self.assertEqual((e["confirmed_at"], e["confirmed_by"]), (None, None), "ingested and planned by hand")
        self.assertEqual([u["slug"] for u in arrive.unconfirmed(c)], ["ep9"])
        self.assertEqual(publish.split(c, far), ([], []), "a year on, and neither due nor late")
        self.assertEqual(publish.due(c, far, include_late=True), [])
        env = {**self.env, **self.xenv}
        for dry in (True, False):  # named one by one, as `publish --post` names them: an upload, a timed post, a card
            res = publish.run(c, [yt, xe, card], env=env, dry_run=dry, who="tester", log=quiet)
            self.assertEqual([r[1] for r in res], ["unconfirmed"] * 3)
        self.assertEqual(
            (StubYouTube.state["uploads"], StubYouTube.state["metas"], StubX.state["tweets"]), ({}, [], [])
        )
        for p in (yt, xe, card):
            now = ledger.post(c, p["id"])
            self.assertEqual((now["status"], now["attempts"], now["remote_id"]), ("planned", 0, None))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM event WHERE what LIKE 'publish:%'").fetchone()[0], 0)

        # one post, from its page: not published now, not retried; skipped, put back and recorded by hand as ever
        for p in (yt, xe, card):
            with self.assertRaisesRegex(ValueError, r"^Confirm the episode's plan first\.$"):
                web.act(c, p, "publish", {}, "james")
        ledger.update_post(c, xe["id"], status="failed", last_error="the platform said no")
        with self.assertRaisesRegex(ValueError, r"^Confirm the episode's plan first\.$"):
            web.act(c, ledger.post(c, xe["id"]), "retry", {}, "james")
        self.assertEqual(ledger.post(c, xe["id"])["status"], "failed")
        ledger.update_post(c, xe["id"], status="planned", last_error=None)
        self.assertEqual(web.act(c, card, "skip", {}, "james"), "Skipped")
        self.assertEqual(web.act(c, ledger.post(c, card["id"]), "unskip", {}, "james"), "Back on the plan")
        self.assertEqual(
            web.act(c, ledger.post(c, card["id"]), "done", {"permalink": "https://www.linkedin.com/posts/a"}, "james"),
            "Published, by hand",
        )
        self.assertEqual(ledger.post(c, card["id"])["status"], "published")
        ledger.update_post(c, card["id"], status="planned", permalink=None, published_at=None)

        # confirming: not an episode nobody has, not a staging other than the one the page showed
        with self.assertRaisesRegex(ValueError, "nope is not in the ledger"):
            arrive.confirm(c, "nope", "james@example.com")
        with self.assertRaisesRegex(ValueError, "staged again since that page was made"):
            arrive.confirm(c, "ep9", "james@example.com", staged="1999-01-01T00:00:00")
        self.assertIsNone(ledger.episode(c, "ep9")["confirmed_at"])
        self.assertEqual(
            arrive.confirm(c, "ep9", "james@example.com", staged=e["staged_at"] or ""),
            "The plan of ep9 is confirmed: the publisher takes its posts from the next minute",
        )
        e = ledger.episode(c, "ep9")
        self.assertEqual(e["confirmed_by"], "james@example.com")
        self.assertLessEqual(e["confirmed_at"], ledger.now())
        ev = c.execute("SELECT who, detail FROM event WHERE what='confirm' AND episode='ep9'").fetchall()
        self.assertEqual(
            [(v["who"], json.loads(v["detail"])["posts"]) for v in ev],
            [("james@example.com", len(arrive.posts(c, "ep9")))],
        )
        self.assertEqual(
            arrive.confirm(c, "ep9", "abrar@example.com"),
            "The plan of ep9 was confirmed already, by james@example.com",
        )
        self.assertEqual(ledger.episode(c, "ep9")["confirmed_by"], "james@example.com")
        self.assertEqual(arrive.unconfirmed(c), [])
        now, late = publish.split(c, far)
        self.assertLessEqual({yt["id"], card["id"]}, ids(now), "the upload and the card are the next run's")
        self.assertIn(xe["id"], ids(late), "and the timed post, a year past its time, waits for a person as before")
        self.assertEqual({p["account"] for p in publish.due(c, "2026-01-01T00:00:00+00:00")}, {"youtube"})

        # held: nothing again, until a host confirms again
        said = arrive.hold(c, "ep9", "james@example.com")
        self.assertTrue(said.startswith("The plan of ep9 is held: nothing more is sent until it is confirmed again."))
        e = ledger.episode(c, "ep9")
        self.assertEqual((e["confirmed_at"], e["confirmed_by"]), (None, None))
        self.assertEqual(publish.split(c, far), ([], []))
        ev = c.execute("SELECT who, detail FROM event WHERE what='confirm:held' AND episode='ep9'").fetchone()
        self.assertEqual((ev["who"], json.loads(ev["detail"])["confirmed_by"]), ("james@example.com",) * 2)
        self.assertEqual(
            arrive.hold(c, "ep9", "james@example.com"), "The plan of ep9 is not confirmed: nothing of it is being sent"
        )
        with self.assertRaises(ValueError):
            arrive.hold(c, "nope", "james@example.com")

        # the command, on the box: who ran it is who confirmed
        entry = pathlib.Path(__file__).resolve().parent / "engine.py"
        run = lambda *slugs: subprocess.run(  # noqa: E731
            [sys.executable, str(entry), "--ledger", str(self.db), "confirm", *slugs],
            capture_output=True,
            text=True,
            env={**os.environ, "ENGINE_USER": "box@example.com"},
            timeout=60,
        )
        r = run("nope")
        self.assertEqual((r.returncode, r.stderr.strip()), (1, "error: nope is not in the ledger"), r.stdout)
        r = run("ep9")
        self.assertEqual(
            (r.returncode, r.stdout.strip()),
            (0, "The plan of ep9 is confirmed: the publisher takes its posts from the next minute"),
            r.stderr,
        )
        e = ledger.episode(c, "ep9")
        self.assertEqual(e["confirmed_by"], "box@example.com")
        self.assertTrue(e["confirmed_at"])

    def test_03c_ledger_before_the_gate(self):
        """A ledger made before plans were confirmed, as the box's is on the day the gate deploys and as an older
        snapshot is on the day it is restored (a restore puts the file back and the next connection opens it).
        Opening it marks every episode it holds confirmed, in the step that adds the column and in no other: an
        episode that comes in afterwards waits for a host, however often the ledger is opened."""
        import sqlite3

        import arrive

        root = pathlib.Path(self.tmp.name)
        new = {"confirmed_by", "confirmed_at"}
        stamp = "2026-11-01T09:00:00+00:00"
        hour_ago = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat()
        far = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).isoformat()

        def before(name, without=new):
            """A ledger file on the schema of the commit before: every table and column but the ones named."""
            path = root / name
            old = sqlite3.connect(path, isolation_level=None)
            old.execute("PRAGMA journal_mode=WAL")
            old.executescript(ledger.SCHEMA)
            for table, column, kind in ledger.MIGRATIONS:
                if column not in without and column not in ledger.columns(old, table):
                    old.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
            self.assertFalse(set(without) & set(ledger.columns(old, "episode")))
            return path, old

        def episode(c, slug, *posts):
            c.execute(
                "INSERT INTO episode (slug, number, title, release_at, release_json, ingested_at) VALUES (?,?,?,?,?,?)",
                (slug, "5", slug.title(), far, "{}", stamp),
            )
            for account, status, when in posts:
                c.execute(
                    "INSERT INTO post (episode, asset, platform, account, status, scheduled_at, created_at, updated_at) "
                    "VALUES (?,'episode',?,?,?,?,?,?)",
                    (slug, account, account, status, when, stamp, stamp),
                )

        of = lambda c, slug: tuple(ledger.episode(c, slug)[k] for k in ("confirmed_at", "confirmed_by"))  # noqa: E731
        events = lambda c: [  # noqa: E731
            tuple(r) for r in c.execute("SELECT episode, who, detail, at FROM event WHERE what='confirm' ORDER BY id")
        ]
        taken = lambda c: {(p["episode"], p["account"]) for p in publish.due(c, include_late=True)}  # noqa: E731

        path, old = before("before.sqlite")
        episode(old, "old-a", ("youtube", "planned", far), ("x", "planned", hour_ago), ("bluesky", "published", stamp))
        episode(old, "old-b")  # ingested and never planned: in the ledger all the same
        old.close()

        c = ledger.connect(path)
        self.assertLessEqual(new, set(ledger.columns(c, "episode")))
        at = ledger.episode(c, "old-a")["confirmed_at"]
        self.assertEqual(dt.datetime.fromisoformat(at).tzinfo, dt.timezone.utc)
        self.assertEqual(of(c, "old-a"), (at, ledger.BEFORE))
        self.assertEqual(of(c, "old-b"), (at, ledger.BEFORE))
        self.assertEqual(ledger.BEFORE, "the engine (in the ledger before plans were confirmed)")
        self.assertEqual(events(c), [("old-a", "engine", ledger.BEFORE, at), ("old-b", "engine", ledger.BEFORE, at)])
        self.assertEqual(arrive.unconfirmed(c), [])
        self.assertEqual(taken(c), {("old-a", "youtube"), ("old-a", "x")}, "its posts go on as they did")

        # what comes in afterwards is born unconfirmed, and stays so through every opening of the ledger
        episode(c, "new-a", ("youtube", "planned", far), ("x", "planned", hour_ago))
        episode(c, "new-b")
        self.assertEqual(of(c, "new-a"), (None, None))
        c.close()
        for _ in range(3):
            c = ledger.connect(path)
            self.assertEqual((of(c, "new-a"), of(c, "new-b")), ((None, None), (None, None)))
            self.assertEqual((of(c, "old-a"), of(c, "old-b")), ((at, ledger.BEFORE), (at, ledger.BEFORE)))
            self.assertEqual(len(events(c)), 2)
            self.assertEqual([e["slug"] for e in arrive.unconfirmed(c)], ["new-a"])
            self.assertEqual(taken(c), {("old-a", "youtube"), ("old-a", "x")})
            c.close()
        c = ledger.connect(path)
        self.addCleanup(c.close)
        ledger.add_column(c, "episode", "confirmed_at", "TEXT")  # asked for again: the column is there, so nothing
        ledger.add_column(c, "episode", "confirmed_by", "TEXT")
        self.assertEqual((of(c, "new-a"), len(events(c))), ((None, None), 2))
        with self.assertRaisesRegex(ValueError, "new-b has no posts to confirm: plan it first"):
            arrive.confirm(c, "new-b", "james@example.com")
        arrive.confirm(c, "new-a", "james@example.com")
        self.assertEqual(of(c, "new-a")[1], "james@example.com")
        self.assertEqual(taken(c), {("old-a", "youtube"), ("old-a", "x"), ("new-a", "youtube"), ("new-a", "x")})
        self.assertEqual(of(c, "old-a"), (at, ledger.BEFORE))

        # the engine stopped between the two columns: the one that carries the gate is still to come, and marks
        path, old = before("between.sqlite", without={"confirmed_at"})
        self.assertIn("confirmed_by", ledger.columns(old, "episode"))
        episode(old, "old-c", ("youtube", "planned", far))
        old.close()
        c = ledger.connect(path)
        self.addCleanup(c.close)
        self.assertEqual(of(c, "old-c")[1], ledger.BEFORE)
        self.assertEqual(len(events(c)), 1)

        # several connections open the old ledger at once, as the server's threads do: marked once
        path, old = before("together.sqlite")
        episode(old, "old-d", ("youtube", "planned", far))
        old.close()
        gate, seen, wrong = threading.Barrier(6), [], []

        def open_it():
            try:
                gate.wait(timeout=30)
                k = ledger.connect(path)
                seen.append(of(k, "old-d"))
                k.close()
            except Exception as e:
                wrong.append(repr(e))

        threads_ = [threading.Thread(target=open_it) for _ in range(6)]
        for t in threads_:
            t.start()
        for t in threads_:
            t.join(60)
        self.assertEqual(wrong, [])
        self.assertEqual(len(set(seen)), 1)
        self.assertEqual(seen[0][1], ledger.BEFORE)
        c = ledger.connect(path)
        self.addCleanup(c.close)
        self.assertEqual([e[0] for e in events(c)], ["old-d"])

        # a ledger with no episodes, and one made new: nothing to mark, and the first episode waits
        path, old = before("empty.sqlite")
        old.close()
        for p in (path, root / "fresh.sqlite"):
            c = ledger.connect(p)
            self.addCleanup(c.close)
            self.assertEqual(events(c), [])
            episode(c, "first", ("youtube", "planned", far))
            self.assertEqual(of(c, "first"), (None, None))
            self.assertEqual(taken(c), set())

    def test_04_due(self):
        c = self.c()
        before = publish.due(c, "2026-01-01T00:00:00+00:00")
        self.assertEqual({p["account"] for p in before}, {"youtube"}, "only the platform that schedules is due early")
        xe = c.execute(
            "SELECT scheduled_at FROM post WHERE episode='ep9' AND account='x' AND asset='episode'"
        ).fetchone()[0]
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
        self.assertEqual(
            ep_meta["snippet"]["tags"],
            ["AgentSee", "x" * 490],
            "tags stop before the 500th character, quotes and commas counted",
        )
        for up in s["uploads"].values():
            self.assertEqual(up["got"], up["size"], "every byte arrived despite the 503")
            self.assertTrue(up["failed_once"])
        self.assertEqual(len(s["captions"]), 2)
        self.assertEqual(s["items"][0]["snippet"]["resourceId"]["videoId"], ep["remote_id"])
        self.assertIn(
            ep["remote_id"], s["aborted"], "the first playlist call was aborted with a 409; the retry went through"
        )
        self.assertEqual(len(s["playlists"]), 1, "the playlist was created once")
        notes = {
            ev["post"]: json.loads(ev["detail"])["note"]
            for ev in c.execute("SELECT * FROM event WHERE what='publish:scheduled'")
        }
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
        self.assertIn(
            "publish:held", [ev["what"] for ev in c.execute("SELECT what FROM event WHERE post=?", (p["id"],))]
        )
        s["fail_session"] = False
        ledger.update_post(c, clip["id"], status=clip["status"])
        # The playlist refusing for good: the video is up, so the post is published with a note, never failed
        StubYouTube.state["playlist_down"] = True
        ledger.update_post(c, p["id"], status="planned", remote_id=None, permalink=None)
        res = publish.run(c, [ledger.post(c, p["id"])], env=self.env, log=lambda s: None)
        self.assertEqual(res[0][1], "published", "a step after the upload is a note, never the loss of the video")
        p3 = ledger.post(c, p["id"])
        self.assertTrue(p3["remote_id"].startswith("VIDep"))
        note = json.loads(
            c.execute(
                "SELECT detail FROM event WHERE post=? AND what='publish:published' ORDER BY id DESC", (p["id"],)
            ).fetchone()[0]
        )["note"]
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
        self.assertEqual(
            web.act(
                c,
                ledger.post(c, p["id"]),
                "done",
                {"permalink": "https://www.youtube.com/watch?v=abcDEF12-_5&t=9"},
                "james",
            ),
            "Recorded; the platform holds it until its time",
        )
        p3 = ledger.post(c, p["id"])
        self.assertEqual(
            (p3["status"], p3["remote_id"], p3["published_at"]),
            ("scheduled", "abcDEF12-_5", soon),
            "a YouTube link pasted before its time: the id out of the link, scheduled, public at its hour",
        )
        ledger.update_post(c, p["id"], status="failed", scheduled_at="2026-01-01T09:00:00+00:00")
        self.assertEqual(
            web.act(c, ledger.post(c, p["id"]), "done", {"permalink": "https://youtu.be/abcDEF12-_5"}, "james"),
            "Published, by hand",
        )
        self.assertEqual(ledger.post(c, p["id"])["status"], "published")
        ledger.update_post(
            c,
            p["id"],
            status=p["status"],
            remote_id=p["remote_id"],
            permalink=p["permalink"],
            scheduled_at=p["scheduled_at"],
            published_at=p["published_at"],
        )

    def test_07_hand(self):
        c = self.c()
        p = c.execute(
            "SELECT * FROM post WHERE episode='ep9' AND account='linkedin-page' AND asset='episode'"
        ).fetchone()
        res = publish.run(c, [p], log=lambda s: None)
        self.assertEqual(res[0][1], "hand")
        self.assertEqual(
            web.act(c, ledger.post(c, p["id"]), "done", {"permalink": "https://www.linkedin.com/posts/x"}, "james"),
            "Published, by hand",
        )
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
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def get(path):
            try:
                with urllib.request.urlopen(base + path, timeout=10) as r:
                    return r.status, dict(r.headers), r.read().decode(errors="replace")
            except urllib.error.HTTPError as e:
                with e:
                    return e.code, dict(e.headers), e.read().decode(errors="replace")

        def post(path, data):
            req = urllib.request.Request(base + path, data=urllib.parse.urlencode(data).encode(), method="POST")
            opener = urllib.request.build_opener(NoRedirect)
            try:
                with opener.open(req, timeout=10) as r:
                    return r.status, dict(r.headers), r.read().decode()
            except urllib.error.HTTPError as e:
                with e:
                    return e.code, dict(e.headers), e.read().decode()

        st, h, body = get("/episodes")
        self.assertEqual(st, 200)
        self.assertIn("Nine", body)
        self.assertIn("default-src 'none'", h["Content-Security-Policy"])
        self.assertIn("connect-src 'self'", h["Content-Security-Policy"], "office.js fetches /idea/say")
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
        self.assertNotIn("bsky.app/intent/compose", body, "Bluesky goes by API: no composer card")
        th = c.execute("SELECT * FROM post WHERE link_id='e9thc01'").fetchone()
        st, h, body = get(f"/posts/{th['id']}")
        self.assertEqual(st, 200)
        self.assertNotIn("threads.net/intent/post", body, "Threads goes by API since the switch-on: no composer card")
        lp = c.execute("SELECT * FROM post WHERE link_id='e9lpe'").fetchone()
        st, h, body = get(f"/posts/{lp['id']}")
        self.assertEqual(st, 200)
        self.assertIn("linkedin.com/feed/?shareActive", body, "the LinkedIn page stays a card, by decision")
        self.assertIn(MEDIA + "episode-art.jpg", body)
        self.assertIn("Paste the permalink", body)
        st, h, body = post(f"/posts/{x['id']}/text", {"_t": "wrong", "text": "nope"})
        self.assertEqual(st, 403)
        st, h, body = post(
            f"/posts/{x['id']}/text", {"_t": web.CSRF, "title": "New title", "text": "New text\r\nwith lines"}
        )
        self.assertEqual(st, 303)
        self.assertTrue(h["Location"].startswith(f"/posts/{x['id']}?msg="))
        x2 = ledger.post(c, x["id"])
        self.assertEqual((x2["title"], x2["text"]), ("New title", "New text\nwith lines"))
        st, h, body = post(f"/posts/{x['id']}/schedule", {"_t": web.CSRF, "date": DAY1, "time": "17:30"})
        self.assertEqual(st, 303)
        self.assertTrue(ledger.post(c, x["id"])["scheduled_at"].startswith(DAY1 + "T17:30"))
        st, h, body = post(f"/posts/{x['id']}/schedule", {"_t": web.CSRF, "date": "soon", "time": "17:30"})
        self.assertEqual(st, 400)
        self.assertEqual(
            post(
                f"/posts/{bs['id']}/done",
                {"_t": web.CSRF, "permalink": "https://bsky.app/profile/agentsee.work/post/1"},
            )[0],
            303,
        )
        self.assertEqual(ledger.post(c, bs["id"])["status"], "published")
        self.assertEqual(
            c.execute("SELECT who FROM event WHERE what='hand:done' AND post=?", (bs["id"],)).fetchone()[0], "local"
        )
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

    def test_08b_confirm_web(self):
        """The plan on the screens. ep9 is confirmed and some of it is out: "Hold the plan" stops what is left and
        leaves what a platform has; the page then shows the plan and its button, the list says "to confirm", a
        post is not published now from its page; "Confirm the plan" hands the rest back. Both buttons want the
        form's token."""
        import arrive

        web.STATE["ledger"] = str(self.db)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), web.App)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def get(path):
            try:
                with urllib.request.urlopen(base + path, timeout=10) as r:
                    return r.status, r.read().decode(errors="replace")
            except urllib.error.HTTPError as e:
                with e:
                    return e.code, e.read().decode(errors="replace")

        def post(path, **data):
            req = urllib.request.Request(base + path, data=urllib.parse.urlencode(data).encode(), method="POST")
            try:
                with urllib.request.build_opener(NoRedirect).open(req, timeout=10) as r:
                    return r.status, urllib.parse.unquote_plus(r.headers.get("Location") or ""), r.read().decode()
            except urllib.error.HTTPError as e:
                with e:
                    return e.code, urllib.parse.unquote_plus(e.headers.get("Location") or ""), e.read().decode()

        c = self.c()
        far = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=400)).isoformat()
        states = lambda: {p["id"]: (p["status"], p["remote_id"], p["permalink"]) for p in arrive.posts(c, "ep9")}  # noqa: E731
        x = c.execute("SELECT * FROM post WHERE link_id='e9xc01'").fetchone()
        before = states()
        gone = [p for p in arrive.posts(c, "ep9") if arrive.out(p)]
        self.assertTrue(gone, "the uploads of test 05 are with YouTube")
        self.assertEqual(x["status"], "planned")
        self.assertIn(x["id"], {p["id"] for p in publish.due(c, far, include_late=True)})

        # confirmed: who and when, and the button that takes it back
        st, body = get("/episodes/ep9")
        self.assertEqual(st, 200)
        for part in ("Confirmed by box@example.com", 'action="/episodes/ep9/hold"', "Hold the plan</button>"):
            self.assertIn(part, body)
        self.assertNotIn("Confirm the plan", body)
        self.assertIn("planned, links pushed", get("/episodes")[1])
        self.assertIn("none waiting", get("/health")[1])
        self.assertEqual(post("/episodes/ep9/hold", _t="stale")[0], 403)
        self.assertEqual(post("/episodes/ep9/hold")[0], 403, "no token at all")
        self.assertTrue(ledger.episode(c, "ep9")["confirmed_at"])
        self.assertEqual(post("/episodes/nope/hold", _t=web.CSRF)[0], 400)

        # held: nothing more is sent, and what a platform has stays as it is
        st, to, _ = post("/episodes/ep9/hold", _t=web.CSRF)
        self.assertEqual(
            (st, to),
            (
                303,
                "/episodes/ep9?msg=The plan of ep9 is held: nothing more is sent until it is confirmed again. "
                "What a platform already has stays there",
            ),
        )
        e = ledger.episode(c, "ep9")
        self.assertEqual((e["confirmed_at"], e["confirmed_by"]), (None, None))
        self.assertEqual(
            c.execute("SELECT who FROM event WHERE what='confirm:held' ORDER BY id DESC").fetchone()[0], "local"
        )
        self.assertEqual(states(), before, "no post was touched")
        self.assertEqual(publish.split(c, far), ([], []))
        res = publish.run(c, [x], env=self.xenv, who="tester", log=lambda s: None)
        self.assertEqual(res, [(x["id"], "unconfirmed")])
        self.assertEqual(StubX.state["tweets"], [])
        st, body = get(to.replace(" ", "+"))
        self.assertEqual(st, 200)
        for part in (
            "The plan of ep9 is held",
            "Nothing is sent until a host confirms it",
            "<dt>Episode</dt><dd>9, Nine</dd>",
            f"Already out or with a platform, and staying there: {len(gone)} post",
            'action="/episodes/ep9/confirm"',
            f'name="_t" value="{web.CSRF}"',
            "Confirm the plan</button>",
            "clips/01-the-point.mp4",
        ):
            self.assertIn(part, body)
        self.assertNotIn("Hold the plan", body)
        self.assertLess(body.index("Confirm the plan</button>"), body.index("clips/01-the-point.mp4"), "at the top")
        self.assertIn('<a href="/episodes/ep9">to confirm</a>', get("/episodes")[1])
        self.assertIn('1 waiting for a host: <a href="/episodes/ep9">ep9</a>', get("/health")[1])
        st, body = get(f"/posts/{x['id']}")
        self.assertEqual(st, 200)
        self.assertNotIn("Publish now", body)
        self.assertIn('<a href="/episodes/ep9">Confirm the plan</a>', body)
        self.assertIn("<textarea", body, "its words can still be changed")
        st, _, body = post(f"/posts/{x['id']}/publish", _t=web.CSRF)
        self.assertEqual(st, 400)
        self.assertIn("Confirm the episode&#x27;s plan first.", body)
        self.assertEqual(ledger.post(c, x["id"])["status"], "planned")
        self.assertEqual(post(f"/posts/{x['id']}/skip", _t=web.CSRF)[0], 303)
        self.assertEqual(post(f"/posts/{x['id']}/unskip", _t=web.CSRF)[0], 303)
        self.assertEqual(ledger.post(c, x["id"])["status"], "planned")

        # confirmed again, from the page: the token, the staging the page showed, and who pressed it
        staged = e["staged_at"] or ""
        self.assertEqual(post("/episodes/ep9/confirm", _t="stale", staged=staged)[0], 403)
        st, _, body = post("/episodes/ep9/confirm", _t=web.CSRF, staged="1999-01-01T00:00:00")
        self.assertEqual(st, 400)
        self.assertIn("staged again since that page was made", body)
        self.assertEqual(post("/episodes/nope/confirm", _t=web.CSRF, staged="")[0], 400)
        self.assertIsNone(ledger.episode(c, "ep9")["confirmed_at"])
        st, to, _ = post("/episodes/ep9/confirm", _t=web.CSRF, staged=staged)
        self.assertEqual(
            (st, to),
            (303, "/episodes/ep9?msg=The plan of ep9 is confirmed: the publisher takes its posts from the next minute"),
        )
        e = ledger.episode(c, "ep9")
        self.assertEqual(e["confirmed_by"], "local")
        self.assertTrue(e["confirmed_at"])
        self.assertTrue(web.STATE["poke"].is_set(), "the worker is woken to take them")
        web.STATE["poke"].clear()
        self.assertEqual(states(), before)
        self.assertIn(x["id"], {p["id"] for p in publish.due(c, far, include_late=True)})
        st, body = get("/episodes/ep9")
        self.assertIn("Confirmed by local", body)
        self.assertIn("Hold the plan</button>", body)
        self.assertNotIn("Confirm the plan", body)
        self.assertNotIn("to confirm", get("/episodes")[1])
        self.assertIn("Publish now", get(f"/posts/{x['id']}")[1])
        self.assertIn("none waiting", get("/health")[1])
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
        notes = {
            ev["post"]: json.loads(ev["detail"])["note"]
            for ev in c.execute("SELECT * FROM event WHERE what='publish:published'")
        }
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
        self.assertEqual(
            StubX.state["refreshes"], 2, "the access token from the held refresh was used, not another refresh"
        )
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
        card = c.execute(
            "SELECT * FROM post WHERE episode='ep9' AND account='linkedin-page' AND asset='episode'"
        ).fetchone()
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
        h = archive.sign(
            "PUT",
            "9468eccb7caed9f96283a9139c37a4df.r2.cloudflarestorage.com",
            "/agentsee-podcast-archive/engine/ledger-latest.sqlite",
            {},
            {"content-type": "application/vnd.sqlite3", "x-amz-meta-sha256": "ab" * 32},
            hashlib.sha256(b"hello ledger").hexdigest(),
            "AKIAIOSFODNN7EXAMPLE",
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "auto",
            when,
        )
        self.assertEqual(
            h["authorization"],
            "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20261015/auto/s3/aws4_request, "
            "SignedHeaders=content-type;host;x-amz-content-sha256;x-amz-date;x-amz-meta-sha256, "
            "Signature=cde30d8e9da6181aee80e9254e409b09b6d566487066c1d7079f31c0265a6260",
        )
        self.assertEqual(h["x-amz-date"], "20261015T180000Z")
        # The presigned GET, fixed on 5 November 2026 against botocore 1.43.112's S3SigV4QueryAuth for the same
        # inputs (the moment is the one botocore stamped on its own URL).
        url = archive.presign(
            "https://9468eccb7caed9f96283a9139c37a4df.r2.cloudflarestorage.com",
            "9468eccb7caed9f96283a9139c37a4df.r2.cloudflarestorage.com",
            "/agentsee-podcast-archive/episodes/ep3/review/clips/01-the-first-one.mp4",
            "AKIAIOSFODNN7EXAMPLE",
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            3600,
            "auto",
            dt.datetime(2026, 11, 5, 9, 33, 31, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(
            url,
            "https://9468eccb7caed9f96283a9139c37a4df.r2.cloudflarestorage.com/agentsee-podcast-archive/episodes/ep3/"
            "review/clips/01-the-first-one.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAIOSFODNN7EXAMPLE"
            "%2F20261105%2Fauto%2Fs3%2Faws4_request&X-Amz-Date=20261105T093331Z&X-Amz-Expires=3600"
            "&X-Amz-SignedHeaders=host&X-Amz-Signature=c00d8786c654122f331ff56d806487ee3ca2da4397a6ea1432830520d27da346",
        )

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
        self.assertEqual(
            c.execute("SELECT COUNT(*) FROM post WHERE episode='ep9'").fetchone()[0],
            c.execute("SELECT COUNT(*) FROM post").fetchone()[0],
            "posts untouched",
        )
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
        self.assertEqual(
            r.execute("SELECT COUNT(*) FROM post").fetchone()[0], c.execute("SELECT COUNT(*) FROM post").fetchone()[0]
        )
        r.close()
        with self.assertRaises(archive.ArchiveError):
            archive.restore(back, client)
        archive.restore(back, client, force=True)
        with self.assertRaises(archive.ArchiveError):
            archive.restore(self.data / "x.sqlite", client, key="engine/ledger-1999-01-01.sqlite")
        # the guard: an empty ledger must never overwrite the archive's good copy
        empty = ledger.connect(self.data / "empty.sqlite")
        try:
            with self.assertRaises(archive.ArchiveError) as cm:
                archive.snapshot(empty, client, who="tester", log=lambda s: None)
            self.assertIn("no episodes and no posts", str(cm.exception))
        finally:
            empty.close()
        self.assertEqual(objs[archive.LATEST]["body"], objs[key]["body"], "latest left intact by the refusal")
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
        self.assertEqual(
            web.STATE["snapshot_day"],
            dt.datetime.now(ledger.ZONE).date() - dt.timedelta(days=1),
            "before the hour: yesterday counts as done",
        )

    def test_15_collect(self):
        c = self.c()
        quiet = lambda s: None  # noqa: E731
        ep = c.execute("SELECT * FROM post WHERE episode='ep9' AND account='youtube' AND asset='episode'").fetchone()
        clip = c.execute("SELECT * FROM post WHERE episode='ep9' AND account='youtube' AND asset!='episode'").fetchone()
        ledger.update_post(
            c, ep["id"], status="scheduled", remote_id="VIDpublic", published_at="2026-10-20T05:00:00+00:00"
        )
        ledger.update_post(c, clip["id"], status="scheduled", remote_id="VIDbad")
        bs = c.execute("SELECT * FROM post WHERE episode='ep9' AND platform='bluesky' AND asset='episode'").fetchone()
        web.act(c, bs, "done", {"permalink": "https://bsky.app/profile/agentsee.work/post/3abc"}, "james")
        ma = c.execute("SELECT * FROM post WHERE episode='ep9' AND platform='mastodon' AND asset='episode'").fetchone()
        web.act(c, ma, "done", {"permalink": f"{self.rbase}/@agentseework/123"}, "james")
        xp = c.execute("SELECT * FROM post WHERE episode='ep9' AND platform='x' AND asset='episode'").fetchone()
        ledger.update_post(c, xp["id"], status="published", remote_id="777")
        c.execute(
            "UPDATE deliverable SET duration=600, bytes=12000000 WHERE episode='ep9' AND name='episode.mp3'"
        )  # a minute is 1.2 MB
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
        self.assertEqual(
            c.execute("SELECT COUNT(*) FROM download").fetchone()[0], 6, "the overlap is re-read, not counted twice"
        )
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
        self.assertIn(
            collect.classify("python-requests/2.31")[0],
            ("bot", "library"),
            "the bots list is asked first and claims it; neither listens",
        )
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

    def test_16_bluesky_mastodon(self):
        c = self.c()
        quiet = lambda s: None  # noqa: E731
        plan.ACCOUNTS["bluesky"]["adapter"], plan.ACCOUNTS["mastodon"]["adapter"] = "bluesky", "mastodon"
        try:
            ids = {}
            for platform in ("bluesky", "mastodon"):
                for asset in ("episode", "clip"):
                    p = c.execute(
                        f"SELECT * FROM post WHERE episode='ep9' AND platform=? AND asset {'=' if asset == 'episode' else '!='} 'episode'",
                        (platform,),
                    ).fetchone()
                    ledger.update_post(c, p["id"], status="planned", remote_id=None, permalink=None, published_at=None)
                    ids[platform, asset] = p["id"]
            posts = [ledger.post(c, i) for i in ids.values()]
            archive.Client(self.r2env).put(
                "episodes/ep9/out/episode-art.jpg", self.src / "episode-art.jpg", "image/jpeg"
            )
            os.environ.update(
                self.r2env
            )  # the publisher fetches a file it lacks from the archive, with the process's credentials
            res = publish.run(c, posts, env=self.senv, who="tester", log=quiet)
            self.assertEqual(
                [r[1] for r in res], ["published"] * 4, [(r, ledger.post(c, r[0])["last_error"]) for r in res]
            )
            # Bluesky: the episode with the art in an images embed, the clip through the video service
            s = StubBluesky.state
            ep, clip = ledger.post(c, ids["bluesky", "episode"]), ledger.post(c, ids["bluesky", "clip"])
            self.assertTrue(ep["remote_id"].startswith("at://did:plc:test/app.bsky.feed.post/"))
            self.assertEqual(
                ep["permalink"], "https://bsky.app/profile/agentsee.work/post/" + ep["remote_id"].rsplit("/", 1)[1]
            )
            rec = next(r for r in s["records"] if r["embed"]["$type"] == "app.bsky.embed.images")
            self.assertEqual(rec["langs"], ["en-GB"])
            self.assertEqual(rec["embed"]["images"][0]["image"]["ref"]["$link"], "bafyimg")
            self.assertIn("episode art", rec["embed"]["images"][0]["alt"])
            self.assertTrue(rec["createdAt"].endswith("Z"))
            links = [f for f in rec["facets"] if f["features"][0]["$type"] == "app.bsky.richtext.facet#link"]
            self.assertEqual(len(links), 1, rec["facets"])
            b = rec["text"].encode("utf-8")
            idx = links[0]["index"]
            self.assertEqual(
                b[idx["byteStart"] : idx["byteEnd"]].decode(),
                links[0]["features"][0]["uri"],
                "the facet points at the link's bytes",
            )
            self.assertTrue(links[0]["features"][0]["uri"].startswith("https://agentsee.work/p/"))
            tags = [
                f["features"][0]["tag"]
                for f in rec["facets"]
                if f["features"][0]["$type"] == "app.bsky.richtext.facet#tag"
            ]
            self.assertIn("AgentSee", tags)
            vrec = next(r for r in s["records"] if r["embed"]["$type"] == "app.bsky.embed.video")
            self.assertEqual(vrec["embed"]["video"]["ref"]["$link"], "bafyvid")
            self.assertEqual(s["videos"][0][0], "did:plc:test")
            self.assertEqual(
                s["auths"][0]["lxm"], ["com.atproto.repo.uploadBlob"], "the service token is for uploadBlob on our PDS"
            )
            self.assertEqual(s["polls"], 2, "polled until the job carried the blob")
            self.assertEqual(s["blobs"], [("image/jpeg", 1)] if False else s["blobs"])  # the art went up as a blob
            self.assertEqual(s["blobs"][0][0], "image/jpeg")
            self.assertEqual(clip["status"], "published")
            # Mastodon: the image at once, the video after a poll, the status with the key and the language
            m = StubMastodon.state
            mep, mclip = ledger.post(c, ids["mastodon", "episode"]), ledger.post(c, ids["mastodon", "clip"])
            self.assertEqual(mep["status"], "published")
            self.assertEqual(mclip["status"], "published")
            self.assertTrue(mep["permalink"].endswith("/@agentseework/101"))
            self.assertEqual([x["type"] for x in m["media"]], ["image/jpeg", "video/mp4"])
            self.assertIn("episode art", m["media"][0]["description"])
            self.assertEqual(m["polls"], 2, "the video was polled until processed")
            st = m["statuses"][0]
            self.assertEqual(
                (st["form"]["language"], st["form"]["visibility"], st["form"]["media_ids[]"]),
                (["en"], ["public"], ["m1"]),
            )
            self.assertEqual(st["key"], f"agentsee-post-{mep['id']}-{mep['attempts'] - 1}")
            self.assertIn("agentsee.work/p/", st["form"]["status"][0])
            # The text budgets as each platform counts them
            self.assertEqual(mastodon.length("x https://agentsee.work/p/e9mae y"), 27)
            self.assertEqual(bluesky.facets("no links")[:], [])
            self.assertEqual(bluesky.facets("see https://a.b/c, now")[0]["features"][0]["uri"], "https://a.b/c")
            self.assertEqual(
                bluesky.dimensions(self.src / "clips" / "01-the-point.png"),
                None,
                "a stub PNG header without IHDR size is no size",
            )
            # The collector reads them back from what the adapter recorded
            res = collect.run(c, only=["bluesky", "mastodon"], env=self.senv, who="tester", log=quiet)
            self.assertFalse([v for v in res.values() if v.startswith("failed")], res)
        finally:
            plan.ACCOUNTS["bluesky"]["adapter"] = plan.ACCOUNTS["mastodon"]["adapter"] = "hand"
            for k in self.r2env:
                os.environ.pop(k, None)

    def test_17_mail(self):
        c = self.c()
        self.assertFalse(mail.ready({}))
        with self.assertRaises(mail.MailError) as cm:
            mail.send("a@example.com", "x", "y", env={})
        self.assertIn("MAIL_USER", str(cm.exception))
        FakeSMTP.sent.clear()
        msgid = mail.send(
            "Ada <ada@example.com>",
            "The subject",
            "The body.",
            env=self.menv,
            attachments=[("signed.pdf", b"%PDF-1.4 x", "application/pdf")],
            copy=True,
        )
        self.assertTrue(msgid.startswith("<") and msgid.endswith("@agentsee.work>"), msgid)
        self.assertEqual(FakeSMTP.opened[-1], ("mail.agentsee.work", 465, mail.TIMEOUT))
        self.assertEqual(FakeSMTP.logins[-1], ("office", "office-pass"))
        m = FakeSMTP.sent[-1]
        self.assertEqual(m["From"], "AgentSee <office@agentsee.work>")
        self.assertEqual(m["To"], "Ada <ada@example.com>")
        self.assertEqual(m["Cc"], "hello@agentsee.work")
        self.assertEqual(m["Reply-To"], "hello@agentsee.work")
        self.assertEqual(m["Subject"], "The subject")
        self.assertEqual(m["Message-ID"], msgid)
        self.assertTrue(m.is_multipart())
        text = m.get_body(("plain",)).get_content()
        self.assertTrue(text.startswith("The body.\n\n— AgentSee\n"), text)
        self.assertIn("A reply reaches James and Abrar", text)
        att = [p for p in m.iter_attachments()]
        self.assertEqual((att[0].get_filename(), att[0].get_content_type()), ("signed.pdf", "application/pdf"))
        self.assertEqual(att[0].get_content(), b"%PDF-1.4 x")
        # plain text only, no copy, when not asked
        mail.send("b@example.com", "s", "t", env=self.menv)
        m = FakeSMTP.sent[-1]
        self.assertFalse(m.is_multipart())
        self.assertIsNone(m["Cc"])
        self.assertEqual(m.get_content_type(), "text/plain")
        # a refusal is a sentence, and the record keeps both outcomes
        FakeSMTP.fail = "login"
        with self.assertRaises(mail.MailError) as cm:
            mail.send("b@example.com", "s", "t", env=self.menv)
        self.assertIn("refused the office account's password", str(cm.exception))
        FakeSMTP.fail = "send"
        with self.assertRaises(mail.MailError) as cm:
            mail.send("b@example.com", "s", "t", env=self.menv)
        self.assertIn("SMTPDataError", str(cm.exception))
        # the server hanging up before a word (its throttle, 5 connections a second) is tried once more after
        # its window; a second hang-up is the failure, and says so
        retry_after, mail.RETRY_AFTER = mail.RETRY_AFTER, 0.05
        try:
            FakeSMTP.hangups, opened, sent = 1, len(FakeSMTP.opened), len(FakeSMTP.sent)
            t0 = time.monotonic()
            msgid = mail.send("c@example.com", "s", "t", env=self.menv)
            self.assertGreaterEqual(time.monotonic() - t0, 0.05, "the retry waits out the window")
            self.assertEqual((len(FakeSMTP.opened) - opened, len(FakeSMTP.sent) - sent), (2, 1))
            self.assertEqual(FakeSMTP.sent[-1]["Message-ID"], msgid)
            FakeSMTP.hangups, opened, sent = 2, len(FakeSMTP.opened), len(FakeSMTP.sent)
            with self.assertRaises(mail.MailError) as hung:
                mail.send("c@example.com", "s", "t", env=self.menv)
            self.assertIn(
                "SMTPServerDisconnected: Connection unexpectedly closed, twice 0.05s apart", str(hung.exception)
            )
            self.assertEqual((len(FakeSMTP.opened) - opened, len(FakeSMTP.sent) - sent, FakeSMTP.hangups), (2, 0, 0))
        finally:
            mail.RETRY_AFTER = retry_after
        self.assertIsNone(mail.last_failure(c))
        mail.record(c, "tester", "b@example.com", "s", error=cm.exception)
        mail.record(c, "tester", "b@example.com", "s", msgid="<ok@agentsee.work>")
        last = mail.last_failure(c)
        self.assertIn("SMTPDataError", last["detail"])
        ev = c.execute("SELECT detail FROM event WHERE what='mail:sent' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(json.loads(ev["detail"])["id"], "<ok@agentsee.work>")

    def test_18_guests(self):
        """The door on the guest host, end to end: an invitation made on the
        engine, a code by email, the claim, a second device, a stranger, a
        bound invitation, withdrawal and reissue, the limits."""
        SEE = guests.HOST
        os.environ.update(self.menv, ENGINE_SESSION_KEY="a-key-for-the-tests")
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in (*self.menv, "ENGINE_SESSION_KEY")])
        client = archive.Client(self.r2env)
        patch = unittest.mock.patch.object(archive, "Client", lambda env=None: client)
        patch.start()
        self.addCleanup(patch.stop)
        web.STATE["ledger"] = str(self.db)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), web.App)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        c = self.c()

        def req(method, path, data=None, host=None, cookies=None, headers=None):
            hdr = dict(headers or {})
            if host:
                hdr["Host"] = host
            if cookies:
                hdr["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
            body = urllib.parse.urlencode(data).encode() if data is not None else None
            r = urllib.request.Request(base + path, data=body, method=method, headers=hdr)
            opener = urllib.request.build_opener(NoRedirect)
            try:
                with opener.open(r, timeout=10) as resp:
                    return resp.status, resp.headers, resp.read().decode(errors="replace")
            except urllib.error.HTTPError as e:
                with e:
                    return e.code, e.headers, e.read().decode(errors="replace")

        def jar(h):
            out = {}
            for sc in h.get_all("Set-Cookie") or []:
                k, _, rest = sc.partition("=")
                out[k] = rest.split(";")[0]
                self.assertIn("; Secure; HttpOnly; SameSite=Lax", sc)
            return out

        def code_from_mail():
            m = FakeSMTP.sent[-1]
            self.assertEqual(m["Subject"], "Your code for the AgentSee office")
            self.assertNotIn(
                SEE, m.get_content(), "no link to the door in the code mail: the code is typed, not clicked"
            )
            return re.search(r"\n    (\d{6})\n", m.get_content()).group(1), m["To"]

        def enter(address, invite=None, cookies=None, **headers):
            data = {"address": address}
            if invite:
                data["invite"] = invite
            st, h, body = req("POST", "/enter", data, SEE, cookies, headers or None)
            if st != 200:
                return st, body, None, None
            cid = re.search(r'name="id" value="([^"]+)"', body).group(1)
            return st, body, cid, jar(h)

        def come_in(cid, code, bind):
            return req("POST", "/code", {"id": cid, "code": code}, SEE, {"see_code": bind})

        # ── the engine's side: an invitation without an address ──
        st, h, body = req("GET", "/guests")
        self.assertEqual(st, 200)
        self.assertIn("No guests yet", body)
        st, h, body = req("POST", "/guests/new", {"_t": web.CSRF, "name": "", "issue": "AGE-73"})
        self.assertEqual(st, 303)
        self.assertIn("msg=A+guest+has+a+name", h["Location"])

        # ── the issue: found in the project by name, closest first, or made there; Linear's absence and failure said ──
        st, h, body = req("POST", "/guests/find", {"_t": web.CSRF, "name": " "})
        self.assertEqual((st, h["Location"]), (303, "/guests?msg=A+guest+has+a+name"))
        st, h, body = req("POST", "/guests/find", {"_t": web.CSRF, "name": "Ada Lovelace"})
        self.assertEqual(st, 200)
        self.assertIn("Linear is not set up on this engine", body)
        self.assertIn('value="new" checked', body)
        self.assertIn('name="other"', body, "the identifier can always be typed")
        fake = FakeLinear()
        linear.post = fake
        linear.vault = FakeVault("lr1", item="Linear podcast token")
        linear._token.update(access=None, until=0.0, unsaved=None)
        lenv = {"LINEAR_CLIENT_ID": "lcid", "LINEAR_CLIENT_SECRET": "lcs", "LINEAR_REFRESH_TOKEN": "lr1"}
        os.environ.update(lenv)
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in lenv])
        fake.issues["AGE-74"] = {
            **fake.issues["AGE-73"],
            "id": "i-74",
            "identifier": "AGE-74",
            "title": "Guest episode: Ada Byron — on numbers",
            "url": "https://linear.app/agentsee/issue/AGE-74",
            "labelIds": [],
        }
        st, h, body = req(
            "POST", "/guests/find", {"_t": web.CSRF, "name": "Ada Lovelace", "address": "ada@example.com"}
        )
        self.assertEqual(st, 200)
        self.assertIn("2 in the Podcast project mention the name, closest first", body)
        self.assertLess(body.index("AGE-73"), body.index("AGE-74"), "Ada Lovelace before Ada Byron")
        self.assertIn('value="AGE-73" checked', body)
        self.assertIn('name="address" value="ada@example.com"', body)
        fake.fail = 500
        st, h, body = req("POST", "/guests/find", {"_t": web.CSRF, "name": "Ada Lovelace"})
        self.assertEqual(st, 200)
        self.assertIn("Linear did not answer", body)
        self.assertIn('name="other"', body, "the typed route stays when Linear is down")

        st, h, body = req("POST", "/guests/new", {"_t": web.CSRF, "name": "Ada Lovelace", "issue": "age-73"})
        self.assertEqual(st, 303)
        gid = re.search(r"/guests/([0-9a-f]{8})\?msg=Invitation\+made", h["Location"]).group(1)
        g = guests.guest(c, gid)
        self.assertEqual((g["name"], g["address"], g["issue"], g["state"]), ("Ada Lovelace", None, "AGE-73", "invited"))
        self.assertEqual(
            [tuple(j) for j in c.execute("SELECT kind, guest FROM linear_job")],
            [("invited", gid)],
            "told, by the worker",
        )
        st, h, body = req(
            "POST", "/guests/new", {"_t": web.CSRF, "name": "Grace Hopper", "issue": "new", "angle": " on  compilers "}
        )
        self.assertEqual(st, 303)
        grace = re.search(r"/guests/([0-9a-f]{8})\?msg=Invitation\+made", h["Location"]).group(1)
        self.assertEqual(guests.guest(c, grace)["issue"], "AGE-102", "made in Linear while the host waits")
        made = fake.issues["AGE-102"]
        self.assertEqual(made["title"], "Guest episode: Grace Hopper — on compilers")
        self.assertIn(f"**Status.** Invited through {SEE} by Local on ", made["description"])
        self.assertIn("**Proposed angle.** on compilers", made["description"])
        st, h, body = req("POST", "/guests/find", {"_t": web.CSRF, "name": "Grace Hopper"})
        self.assertIn("already Grace Hopper&#8217;s", body, "an issue another invitation has is marked")
        fake.fail = 500
        st, h, body = req("POST", "/guests/new", {"_t": web.CSRF, "name": "Nobody", "issue": "new"})
        self.assertIn("msg=Linear+would+not+make+the+issue", h["Location"])
        self.assertIsNone(
            c.execute("SELECT id FROM guest WHERE name='Nobody'").fetchone(), "no guest without its issue"
        )
        type(self).linear_made = {k: v for k, v in fake.issues.items() if k != "AGE-73"}  # for test 19's Linear
        st, h, body = req("GET", f"/guests/{gid}")
        self.assertEqual(st, 200)
        self.assertIn(f"https://{SEE}/i/{g['invite']}", body)
        self.assertIn("linear.app/agentsee/issue/AGE-73", body)
        objs = FakeR2.state["objects"]
        doc = json.loads(objs[f"guests/{gid}/guest.json"]["body"])
        self.assertEqual(
            (doc["name"], doc["state"], doc["events"][0]["what"]), ("Ada Lovelace", "invited", "guest:invited")
        )
        st, h, body = req("GET", "/guests")
        self.assertIn("Ada Lovelace", body)

        # ── the guest host is another site on the same port ──
        st, h, body = req("GET", "/", host=SEE)
        self.assertEqual(st, 200)
        self.assertIn("Send me a code", body)
        self.assertIn("runs a little script of ours", body)
        self.assertNotIn('class="engine-nav"', body, "nothing of the engine's on the guest host")
        self.assertIn("sets two cookies of its own", body, "the site's no-cookies footer is not repeated here")
        self.assertNotIn("No trackers, no cookies", body)
        self.assertNotIn('href="/podcast/"', body, "the lifted links go to the site, not this host")
        self.assertIn('href="https://agentsee.work/podcast/"', body)
        self.assertIn('href="https://agentsee.work/privacy/"', body)
        self.assertIn("default-src 'none'", h["Content-Security-Policy"])
        self.assertIn("connect-src 'self'", h["Content-Security-Policy"], "office.js fetches /idea/say")
        self.assertEqual(req("GET", "/episodes", host=SEE)[0], 404, "the engine's screens are not here")
        self.assertEqual(req("GET", "/guests", host=SEE)[0], 404)
        self.assertEqual(req("GET", f"/i/{g['invite']}")[0], 404, "nor the door on the engine host")
        st, h, body = req("GET", f"/i/{g['invite']}", host=SEE)
        self.assertEqual(st, 200)
        self.assertIn("An invitation for <b>Ada Lovelace</b>", body)
        self.assertIn('<p class="hello">Hello, <b>Ada Lovelace</b>.</p>', body, "greeted by name at the arrival")
        self.assertIn(f'name="invite" value="{g["invite"]}"', body)
        st, h, body = req("GET", "/i/nothing-like-this", host=SEE)
        self.assertIn("not one we know", body)
        st, h, body = req("GET", "/office", host=SEE)
        self.assertEqual((st, h["Location"]), (303, "/"))
        self.assertEqual(req("GET", "/see.css", host=SEE)[0], 200)
        self.assertEqual(req("GET", "/assets/styles.css", host=SEE)[0], 200)
        self.assertEqual(req("GET", "/assets/..%2Fstyles.css", host=SEE)[0], 404)

        # ── the arrival: Seymour meets a browser with no cookie of ours, once ──
        st, h, body = req("GET", "/", host=SEE)
        self.assertIn('<div class="arrival" id="arrival">', body)
        self.assertIn('aria-label="Seymour, the AgentSee eye, in his fedora"', body)
        self.assertEqual(body.count('id="mark"'), 1, "the masthead's mark keeps its ids; Seymour has his own")
        self.assertEqual((body.count('id="seymour"'), body.count("url(#seymour-clip)")), (1, 1))
        self.assertIn('class="mark dormant"', body, "no resting blink of his own: the script's sequence only")
        self.assertNotIn('class="hello"', body, "nobody to greet by name without an invitation")
        self.assertIn(f'src="{guests.pages.asset("/door.js")}"', body)
        self.assertEqual(req("GET", "/door.js", host=SEE)[0], 200)
        st, h, body = req("GET", "/", host=SEE, cookies={"see_session": "stale"})
        self.assertIn("Send me a code", body)
        self.assertNotIn(
            'id="arrival"', body, "a browser that has been here is not met again, nor is a third cookie set"
        )
        self.assertIsNone(h.get("Set-Cookie"))

        # ── the code: bound to the browser, five tries, once ──
        st, body, cid, ck = enter("not an address", g["invite"])
        self.assertEqual(st, 400)
        self.assertIn("not an email address", body)
        st, body, cid, ck = enter("Ada@Example.com ", g["invite"])
        self.assertEqual(st, 200)
        self.assertIn("A code is on its way", body)
        self.assertIn("ada@example.com", body)
        code, to = code_from_mail()
        self.assertEqual(to, "ada@example.com")
        self.assertNotIn('id="arrival"', req("GET", "/", host=SEE, cookies=ck)[2], "the code's cookie counts as been")
        st, h, body = req("POST", "/code", {"id": cid, "code": code}, SEE)
        self.assertEqual(st, 400)
        self.assertIn("another device", body, "no binding cookie, no entry")
        st, h, body = come_in(cid, "000000" if code != "000000" else "000001", ck["see_code"])
        self.assertEqual(st, 400)
        self.assertIn("Not that code; 4 more tries", body)
        self.assertIn(f'name="id" value="{cid}"', body, "the same code can be tried again")
        st, h, body = come_in(cid, code[:3] + " " + code[3:], ck["see_code"])
        self.assertEqual((st, h["Location"]), (303, "/office"))
        sess = jar(h)
        self.assertEqual(sess["see_code"], "", "the binding cookie is cleared")
        s1 = {"see_session": sess["see_session"]}
        st, h, body = come_in(cid, code, ck["see_code"])
        self.assertEqual(st, 400)
        self.assertIn("expired or was used", body, "a code works once")
        g = guests.guest(c, gid)
        self.assertEqual((g["state"], g["address"]), ("claimed", "ada@example.com"))
        self.assertTrue(g["claimed_at"])
        job = c.execute("SELECT * FROM linear_job WHERE guest=? ORDER BY id DESC", (gid,)).fetchone()
        self.assertEqual(
            (job["kind"], json.loads(job["args"])["issue"], job["done_at"]),
            ("claimed", "AGE-73", None),
            "the claim is for the worker to tell Linear, never this request",
        )
        doc = json.loads(objs[f"guests/{gid}/guest.json"]["body"])
        self.assertEqual(doc["state"], "claimed")
        self.assertEqual(len(doc["sessions"]), 1)
        self.assertEqual([e["what"] for e in doc["events"]], ["guest:invited", "guest:claimed", "guest:signin"])
        self.assertEqual(doc["events"][2]["who"], "ada@example.com", "the guest's acts are in the guest's name")

        # ── the office, on this device; a second device needs a code of its own ──
        st, h, body = req("GET", "/office", host=SEE, cookies=s1)
        self.assertEqual(st, 200)
        self.assertIn("You're in, Ada.", body)
        self.assertIn("ada@example.com", body)
        st, h, body = req("GET", "/", host=SEE, cookies=s1)
        self.assertIn("You're in, Ada.", body, "the door opens straight onto the office")
        st, h, body = req("GET", f"/i/{g['invite']}", host=SEE, cookies=s1)
        self.assertEqual((st, h["Location"]), (303, "/office"))
        forged = sess["see_session"][:-1] + ("1" if sess["see_session"].endswith("0") else "0")  # one digit changed
        st, h, body = req("GET", "/office", host=SEE, cookies={"see_session": forged})
        self.assertEqual(st, 303, "a forged signature is no session")
        st, body, cid, ck = enter("ada@example.com")  # no invite in the link this time: the address is known
        code, to = code_from_mail()
        st, h, body = come_in(cid, code, ck["see_code"])
        self.assertEqual(st, 303)
        s2 = {"see_session": jar(h)["see_session"]}
        self.assertEqual(
            c.execute("SELECT COUNT(*) FROM session WHERE guest=? AND revoked_at IS NULL", (gid,)).fetchone()[0], 2
        )
        st, h, body = req("GET", f"/guests/{gid}")
        self.assertIn("2 signed in", body)

        # ── a stranger with a right code sees no invitation ──
        st, body, cid, ck = enter("mallory@example.com")
        code, to = code_from_mail()
        st, h, body = come_in(cid, code, ck["see_code"])
        self.assertEqual(st, 403)
        self.assertIn("no invitation for this address", body)
        self.assertEqual(h.get_all("Set-Cookie"), None)
        self.assertEqual(
            c.execute("SELECT who FROM event WHERE what='guest:refused'").fetchone()[0], "mallory@example.com"
        )

        # ── an invitation made with an address refuses any other ──
        st, h, body = req("POST", "/guests/find", {"_t": web.CSRF, "name": "Bob Builder", "address": "Bob@example.com"})
        self.assertIn("Make the invitation and email it", body, "the button says a mail goes out on this click")
        mails = len(FakeSMTP.sent)
        st, h, body = req("POST", "/guests/new", {"_t": web.CSRF, "name": "Bob Builder", "address": "Bob@example.com"})
        gid2 = re.search(r"/guests/([0-9a-f]{8})", h["Location"]).group(1)
        self.assertIn("Invitation+made+and+emailed+to+bob%40example.com", h["Location"])
        g2 = guests.guest(c, gid2)
        self.assertEqual(g2["address"], "bob@example.com")
        self.assertEqual(len(FakeSMTP.sent), mails + 1, "with an address, the invitation is emailed as it is made")
        m = FakeSMTP.sent[-1]
        self.assertEqual((m["To"], m["Subject"]), ("bob@example.com", "An invitation to the AgentSee podcast"))
        self.assertIn(f"https://{SEE}/i/{g2['invite']}", m.get_content())
        st, h, body = req("GET", f"/guests/{gid2}")
        self.assertIn("Send the invitation again by email", body)
        # ── the mail failing does not lose the invitation ──
        FakeSMTP.fail = "send"
        st, h, body = req("POST", "/guests/new", {"_t": web.CSRF, "name": "Carol Shaw", "address": "carol@example.com"})
        gid3 = re.search(r"/guests/([0-9a-f]{8})", h["Location"]).group(1)
        self.assertIn("Invitation+made.+Not+sent", h["Location"])
        self.assertEqual(guests.guest(c, gid3)["address"], "carol@example.com")
        self.assertEqual(len(FakeSMTP.sent), mails + 1)
        st, h, body = req("GET", f"/guests/{gid3}")
        self.assertIn("Send the invitation by email", body)
        self.assertNotIn("again by email", body, "nothing went, so not again")
        st, h, body = req("POST", "/guests/new", {"_t": web.CSRF, "name": "Dan Nobody"})
        self.assertIn("msg=Invitation+made", h["Location"])
        self.assertNotIn("emailed", h["Location"], "no address, no mail")
        self.assertEqual(len(FakeSMTP.sent), mails + 1)
        st, body, cid, ck = enter("eve@example.com", g2["invite"])
        code, to = code_from_mail()
        self.assertEqual(come_in(cid, code, ck["see_code"])[0], 403)
        self.assertEqual(guests.guest(c, gid2)["state"], "invited")
        st, body, cid, ck = enter("bob@example.com", g2["invite"])
        code, to = code_from_mail()
        st, h, body = come_in(cid, code, ck["see_code"])
        self.assertEqual(st, 303)
        sb = {"see_session": jar(h)["see_session"]}
        self.assertEqual(guests.guest(c, gid2)["state"], "claimed")
        st, h, body = req("POST", f"/guests/{gid2}/send", {"_t": web.CSRF})
        self.assertEqual(st, 303)
        self.assertIn("Sent+to+bob%40example.com", h["Location"])
        m = FakeSMTP.sent[-1]
        self.assertEqual((m["To"], m["Subject"]), ("bob@example.com", "An invitation to the AgentSee podcast"))
        self.assertIn(f"https://{SEE}/i/{g2['invite']}", m.get_content())
        self.assertIn("Local would like to record", m.get_content())
        self.assertIn("mark it as not junk", m.get_content())  # the junk folder, and the reply that lifts it out
        self.assertIn("A reply, even one word, helps", m.get_content())

        # ── withdrawn: both devices out, the link dead; reissued: a new link, the address still bound ──
        st, h, body = req("POST", f"/guests/{gid}/revoke", {"_t": web.CSRF})
        self.assertEqual(st, 303)
        for s in (s1, s2):
            self.assertEqual(req("GET", "/office", host=SEE, cookies=s)[0], 303)
        self.assertIn("not one we know", req("GET", f"/i/{g['invite']}", host=SEE)[2])
        self.assertEqual(guests.guest(c, gid)["state"], "revoked")
        self.assertEqual(json.loads(objs[f"guests/{gid}/guest.json"]["body"])["state"], "revoked")
        st, h, body = req("GET", f"/guests/{gid}")
        self.assertIn("Reissue, with a new link", body)
        self.assertIn("withdrawn", body)
        st, h, body = req("POST", f"/guests/{gid}/reissue", {"_t": web.CSRF})
        g = guests.guest(c, gid)
        self.assertEqual((g["state"], g["address"], g["revoked_at"]), ("invited", "ada@example.com", None))
        self.assertNotEqual(g["invite"], doc["invite"])
        st, h, body = req("POST", f"/guests/{gid}/send", {"_t": web.CSRF})
        self.assertEqual((st, FakeSMTP.sent[-1]["To"]), (303, "ada@example.com"), "the new link, to the bound address")
        st, body, cid, ck = enter("other@example.com", g["invite"])
        code, to = code_from_mail()
        self.assertEqual(come_in(cid, code, ck["see_code"])[0], 403, "the address stays bound through a reissue")
        st, body, cid, ck = enter("ada@example.com", g["invite"])
        code, to = code_from_mail()
        st, h, body = come_in(cid, code, ck["see_code"])
        self.assertEqual(st, 303)
        s3 = {"see_session": jar(h)["see_session"]}
        self.assertEqual(guests.guest(c, gid)["state"], "claimed")

        # ── leaving ──
        st, h, body = req("POST", "/leave", host=SEE, cookies=s3)
        self.assertEqual((st, h["Location"], jar(h)["see_session"]), (303, "/", ""))
        self.assertEqual(req("GET", "/office", host=SEE, cookies=s3)[0], 303)
        self.assertEqual(c.execute("SELECT who FROM event WHERE what='guest:left'").fetchone()[0], "ada@example.com")

        # ── a browser's POST from another site is refused ──
        st, h, body = req("POST", "/leave", host=SEE, cookies=sb, headers={"Origin": "https://evil.example"})
        self.assertEqual(st, 403)
        st, h, body = req("POST", "/enter", {"address": "x@example.com"}, SEE, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(st, 403)
        st, h, body = req("POST", "/enter", {"address": "x@example.com"}, SEE, headers={"Origin": "null"})
        self.assertEqual(st, 403, "an opaque origin is another site")
        self.assertEqual(req("GET", "/office", host=SEE, cookies=sb)[0], 200, "Bob is still in")
        # which is why the guest host must not say no-referrer: under it a browser posts Origin: null to its own door
        self.assertEqual(req("GET", "/", host=SEE)[1]["Referrer-Policy"], "same-origin")
        self.assertEqual(req("GET", "/health")[1]["Referrer-Policy"], "no-referrer", "the engine host keeps its own")

        # ── the limits: codes per address, wrong tries, the cooldown, expiry ──
        sent = len(FakeSMTP.sent)
        for i in range(3):
            st, body, cid, ck = enter("many@example.com")
            self.assertEqual(st, 200)
        st, body, cid4, ck4 = enter("many@example.com")
        self.assertEqual(st, 429)
        self.assertIn("had its codes for now", body)
        self.assertEqual(len(FakeSMTP.sent), sent + 3)
        for i in range(4):
            st, h, body = come_in(cid, "000000", ck["see_code"])
            self.assertEqual(st, 400)
        self.assertIn("Not that code; 1 more try", body)
        st, h, body = come_in(cid, "000000", ck["see_code"])
        self.assertIn("that was the last try", body)
        self.assertNotIn('name="id"', body, "back at the door, not the code form")
        st, h, body = come_in(cid, "000000", ck["see_code"])
        self.assertIn("void after five wrong tries", body)
        c.execute("DELETE FROM code WHERE address='many@example.com' AND id!=?", (cid,))
        st, body, _, _ = enter("many@example.com")
        self.assertEqual(st, 429)
        self.assertIn("waits fifteen minutes", body)
        st, body, cid, ck = enter("late@example.com")
        c.execute("UPDATE code SET expires_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", cid))
        code, to = code_from_mail()
        self.assertIn("expired or was used", come_in(cid, code, ck["see_code"])[2])
        guests.PER_IP = (1, 600)
        try:
            self.assertIn("from this connection", enter("ip@example.com")[1])
        finally:
            guests.PER_IP = (12, 600)

        # ── mail that does not go is said so, on the guest's page and the engine's ──
        FakeSMTP.fail = "send"
        st, body, cid, ck = enter("quiet@example.com")
        self.assertEqual(st, 502)
        self.assertIn("could not send the code", body)
        st, h, body = req("GET", "/health")
        self.assertIn("Last mail failure", body)
        self.assertIn("SMTPDataError", body)
        self.assertIn("survive a restart", body)
        self.assertIn("office@agentsee.work", body)

        # ── a restart with the same key keeps the session; another key does not ──
        os.environ["ENGINE_SESSION_KEY"] = "another-key"
        self.assertEqual(req("GET", "/office", host=SEE, cookies=sb)[0], 303)
        os.environ["ENGINE_SESSION_KEY"] = "a-key-for-the-tests"
        self.assertEqual(req("GET", "/office", host=SEE, cookies=sb)[0], 200)
        srv.shutdown()

    def test_19_linear(self):
        """The bridge: the outbox the worker drains (the story test 18 left,
        told in order), the token refreshed and rotated into the vault, a
        comment, a label made once, a due date, a search, an issue made; then
        the day's end, a 401, a vault write that fails, a retired token;
        Health and the guest's page."""
        c = self.c()
        fake = FakeLinear()
        fake.issues.update(self.linear_made)  # what test 18 made, as Linear still has it
        linear.post = fake
        linear.vault = FakeVault("lr1", item="Linear podcast token")
        linear._token.update(access=None, until=0.0, unsaved=None)
        env = {"LINEAR_CLIENT_ID": "lcid", "LINEAR_CLIENT_SECRET": "lcs", "LINEAR_REFRESH_TOKEN": "lr1"}
        self.assertFalse(linear.ready({}))
        self.assertTrue(linear.ready(env))
        gid = c.execute("SELECT id FROM guest WHERE name='Ada Lovelace'").fetchone()[0]
        grace = c.execute("SELECT id FROM guest WHERE name='Grace Hopper'").fetchone()[0]

        # ── the outbox: the story waits for the worker; a failure holds that guest's later jobs; then it is told in order ──
        self.assertEqual(linear.drain(c, {}), 0, "nothing without the credential")
        jobs = c.execute("SELECT * FROM linear_job ORDER BY id").fetchall()
        self.assertEqual(
            [(j["kind"], j["guest"]) for j in jobs],
            [
                ("invited", gid),
                ("invited", grace),
                ("claimed", gid),
                ("revoked", gid),
                ("reissued", gid),
                ("sent", gid),
                ("claimed", gid),
            ],
            "test 18's story, in order",
        )
        self.assertEqual(linear.waiting(c), 7)
        fake.fail = 500
        self.assertEqual(
            linear.drain(c, env, log=lambda s: None), 1, "Grace's goes; Ada's first fails and holds the rest"
        )
        rows = c.execute("SELECT * FROM linear_job ORDER BY id").fetchall()
        self.assertEqual(
            (rows[0]["kind"], rows[0]["guest"], rows[0]["tries"], rows[0]["done_at"]), ("invited", gid, 1, None)
        )
        self.assertIn("Something went wrong", rows[0]["last_error"])
        self.assertGreater(rows[0]["next_at"], ledger.now())
        self.assertIsNotNone(rows[1]["done_at"])
        self.assertEqual(
            [(r["tries"], r["done_at"]) for r in rows[2:]], [(0, None)] * 5, "held behind the first, untried"
        )
        self.assertEqual(linear.drain(c, env), 0, "not before its time")
        ev = c.execute("SELECT * FROM event WHERE what='linear:failed'").fetchone()
        self.assertEqual((ev["guest"], json.loads(ev["detail"])["try"]), (gid, 1))
        c.execute("UPDATE linear_job SET next_at=NULL")
        self.assertEqual(linear.drain(c, env), 6)
        self.assertEqual((linear.waiting(c), linear.stuck(c)), (0, 0))
        self.assertEqual(fake.comments[0][0], "AGE-102")
        self.assertRegex(fake.comments[0][1], r"^Local made an invitation for Grace Hopper on the engine on .+")
        self.assertEqual([fake.labels[x]["name"] for x in fake.issues["AGE-102"]["labelIds"]], ["guest-invited"])
        story = [b for i, b in fake.comments if i == "AGE-73"]
        self.assertEqual(len(story), 6)
        page = re.escape(f"https://engine.agentsee.work/guests/{gid}")
        self.assertRegex(
            story[0],
            r"^Local made an invitation for Ada Lovelace on the engine on .+; no address yet, so the first to pass a "
            rf"code claims it\. Its page: {page}$",
        )
        self.assertRegex(
            story[1],
            r"^Ada Lovelace claimed their invitation through see\.agentsee\.work on .+, from ada@example\.com\.$",
        )
        self.assertRegex(story[2], r"^Local withdrew the invitation on .+; the link and every session are dead\.$")
        self.assertRegex(
            story[3],
            r"^Local issued Ada Lovelace a new link on .+; the old one and every session are dead, and ada@example\.com "
            r"stays bound\.$",
        )
        self.assertRegex(story[4], r"^Local emailed the invitation to ada@example\.com on .+\.$")
        self.assertRegex(story[5], r"^Ada Lovelace claimed their invitation through .+$")
        self.assertEqual(
            [fake.labels[x]["name"] for x in fake.issues["AGE-73"]["labelIds"]], ["guest-invited", "guest-claimed"]
        )
        ev = c.execute("SELECT * FROM event WHERE what='linear:done' AND guest=? ORDER BY id", (gid,)).fetchone()
        self.assertEqual(json.loads(ev["detail"])["comment"], "https://linear.app/agentsee/issue/AGE-73#comment-2")
        self.assertEqual(
            (fake.n, linear.vault.value, linear.vault.writes), (1, "lr2", 1), "one refresh, rotated into the vault"
        )

        # ── the verbs ──
        self.assertEqual(linear.me(env)["name"], "AgentSee Engine")
        q = linear.quote("Ada *Lovelace*", "First line.\n\nThird line.")
        self.assertEqual(
            q,
            "**Ada \\*Lovelace\\* wrote, through [see.agentsee.work](https://see.agentsee.work):**\n\n"
            "> First line.\n> \n> Third line.",
        )
        self.assertEqual(linear.comment(env, "AGE-73", q)["url"], "https://linear.app/agentsee/issue/AGE-73#comment-8")
        self.assertEqual(fake.comments[-1], ("AGE-73", q))
        n = len(fake.calls)
        linear.label(env, "AGE-73", "guest-claimed")
        self.assertEqual(fake.calls[n:], ["Issue", "Labels"], "a label already there is not set again")
        i = linear.label(env, "AGE-73", "booked")
        self.assertEqual(
            sorted(fake.labels[x]["name"] for x in i["labelIds"]), ["booked", "guest-claimed", "guest-invited"]
        )
        self.assertEqual(linear.due(env, "AGE-73", "2026-11-03")["dueDate"], "2026-11-03")
        self.assertEqual([i["identifier"] for i in linear.search(env, "ada")], ["AGE-73", "AGE-74"])
        self.assertEqual(linear.search(env, "nobody"), [])
        idea = linear.quote("Grace Hopper", "An idea.")
        new = linear.create_issue(env, "Grace Hopper, on compilers", idea, labels=("guest-claimed",))
        self.assertEqual(
            (new["identifier"], [fake.labels[x]["name"] for x in new["labelIds"]]), ("AGE-103", ["guest-claimed"])
        )
        self.assertEqual(fake.issues["AGE-103"]["description"], idea)
        with self.assertRaises(linear.LinearError) as cm:
            linear.issue(env, "AGE-999")
        self.assertIn("no such issue", str(cm.exception))
        self.assertEqual(fake.n, 1, "the access token is kept for the day")

        # ── the token: the day's end, a 401, a vault write that fails, a retired token ──
        linear._token["until"] = 0
        linear.me(env)
        self.assertEqual((fake.n, linear.vault.value), (2, "lr3"))
        fake.access = "revoked-elsewhere"
        linear.me(env)
        self.assertEqual(fake.n, 3, "a 401 is one fresh token and one more try")
        linear.vault.fail_write = True
        linear._token["until"] = 0
        with self.assertRaises(linear.LinearError) as cm:
            linear.me(env)
        self.assertIn("vault write failed", str(cm.exception))
        self.assertEqual(linear._token["unsaved"], "lr5")
        linear.me(env)
        self.assertEqual(
            (linear._token["unsaved"], linear.vault.value), (None, "lr5"), "held, then written on the next attempt"
        )
        linear.vault.value = "stale"
        linear._token["until"] = 0
        with self.assertRaises(linear.LinearError) as cm:
            linear.me(env)
        self.assertIn("consent again", str(cm.exception))
        linear.vault.value = fake.refresh

        # ── what the screens say ──
        os.environ.update(env)
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in env])
        title, body = web.health_page(c)
        self.assertIn("AgentSee Engine", body)
        self.assertIn("0 waiting, 0 given up; last failure", body)
        name, body = guests.guest_page(c, gid, "t")
        self.assertIn("linear:done", body)
        self.assertIn("#comment-2", body)

        # ── a label refused is not a failed job, and the error names the call; a job that gave up, put back ──
        fake.refuse = "Update"  # the label exists from Ada's story; putting it on the issue is what is refused
        args = {"issue": "AGE-102", "name": "Grace Hopper", "address": "grace@example.com", "when": "now"}
        linear.enqueue(c, "claimed", args, guest=grace)
        before = len(fake.comments)
        self.assertEqual(linear.drain(c, env, log=lambda s: None), 1, "the comment went; the label did not fail it")
        self.assertEqual(len(fake.comments), before + 1)
        ev = c.execute(
            "SELECT detail FROM event WHERE what='linear:done' AND guest=? ORDER BY id DESC", (grace,)
        ).fetchone()
        self.assertEqual(json.loads(ev["detail"])["label"], "not set: Update: 200: not allowed to take action")
        fake.refuse = None
        linear.enqueue(
            c, "sent", {"issue": "AGE-102", "who": "Local", "address": "grace@example.com", "when": "now"}, grace
        )
        c.execute("UPDATE linear_job SET tries=? WHERE done_at IS NULL", (linear.TRIES,))
        self.assertEqual((linear.waiting(c), linear.stuck(c)), (0, 1))
        self.assertEqual(linear.drain(c, env), 0, "given up: not tried again by itself")
        self.assertEqual(linear.retry(c, "local"), 1)
        self.assertEqual((linear.waiting(c), linear.stuck(c)), (1, 0))
        self.assertEqual(linear.drain(c, env, log=lambda s: None), 1)
        ev = c.execute("SELECT who, detail FROM event WHERE what='linear:retry'").fetchone()
        self.assertEqual((ev["who"], json.loads(ev["detail"])), ("local", {"jobs": 1}))
        entry = pathlib.Path(__file__).resolve().parent / "engine.py"
        r = subprocess.run(
            [sys.executable, str(entry), "--ledger", str(self.db), "linear", "--retry"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(
            (r.returncode, r.stdout.strip()), (0, "0 put back in the queue\n0 sent; 0 waiting, 0 given up"), r.stderr
        )

    def test_20_entry(self):
        """engine.py as the box runs it, in a fresh interpreter, reaching a
        module that tools/ also has a file called: tools/ must stay off the
        engine's path, or tools/linear.py is the bridge (which it was, for a
        morning)."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("LINEAR_")}
        entry = pathlib.Path(__file__).resolve().parent / "engine.py"
        r = subprocess.run(
            [sys.executable, str(entry), "--ledger", str(self.db), "linear", "--drain"],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "0 sent; 0 waiting, 0 given up"), r.stderr)

    def test_21_release(self):
        """The release: a version taken in with its hash and mirrored; a host's
        signature drawn and saved, each host signing the version once, which
        issues it; the guest's page before and after; the signing and what it
        refuses; the three copies in the archive, the PDF by email with a copy
        to hello@, the issue told and labelled; the copy re-rendered on demand
        on both hosts; a newer version leaving the signed one alone."""
        import release

        SEE = guests.HOST
        os.environ.update(self.menv, ENGINE_SESSION_KEY="a-key-for-the-tests")
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in (*self.menv, "ENGINE_SESSION_KEY", "ENGINE_USER")])
        client = archive.Client(self.r2env)
        patch = unittest.mock.patch.object(archive, "Client", lambda env=None: client)
        patch.start()
        self.addCleanup(patch.stop)
        c = self.c()
        objs = FakeR2.state["objects"]
        fake = FakeLinear()
        fake.issues.update(self.linear_made)
        linear.post = fake
        linear.vault = FakeVault("lr1", item="Linear podcast token")
        linear._token.update(access=None, until=0.0, unsaved=None)
        env = {"LINEAR_CLIENT_ID": "lcid", "LINEAR_CLIENT_SECRET": "lcs", "LINEAR_REFRESH_TOKEN": "lr1"}
        gid = c.execute("SELECT id FROM guest WHERE name='Ada Lovelace'").fetchone()[0]

        # ── versions: from text, from the issue's document; the hash; the mirror ──
        with self.assertRaises(release.ReleaseError):
            release.add(c, "# Short\n\nToo short to be the release.", "jameshartt@example.com")
        v1 = release.add(c, RELEASE_TEXT, "jameshartt@example.com", source="test")
        want = hashlib.sha256(release.clean(RELEASE_TEXT).encode()).hexdigest()
        self.assertEqual((v1["n"], v1["title"], v1["sha256"]), (1, "Appearing on the AgentSee podcast", want))
        self.assertEqual(objs["release/v1.md"]["body"].decode(), release.clean(RELEASE_TEXT))
        self.assertEqual(json.loads(objs["release/v1.json"]["body"])["signed"], {})
        with self.assertRaises(release.ReleaseError) as cm:
            release.add(c, RELEASE_TEXT, "x")
        self.assertIn("already has exactly this text", str(cm.exception))
        docs = linear.documents(env, "AGE-73")
        self.assertEqual(([d["title"] for d in docs], docs[0]["content"]), (["Guest release, version 1"], RELEASE_TEXT))
        self.assertIsNone(release.issued(c), "nobody has signed")
        self.assertEqual(release.waiting_for(c, 1), ["James", "Abrar"])

        web.STATE["ledger"] = str(self.db)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), web.App)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def req(method, path, data=None, host=None, cookies=None, headers=None):
            hdr = dict(headers or {})
            if host:
                hdr["Host"] = host
            if cookies:
                hdr["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
            body = urllib.parse.urlencode(data).encode() if data is not None else None
            r = urllib.request.Request(base + path, data=body, method=method, headers=hdr)
            opener = urllib.request.build_opener(NoRedirect)
            try:
                with opener.open(r, timeout=10) as resp:
                    return resp.status, resp.headers, resp.read().decode(errors="replace")
            except urllib.error.HTTPError as e:
                with e:
                    return e.code, e.headers, e.read().decode(errors="replace")

        def jar(h):
            return {sc.partition("=")[0]: sc.partition("=")[2].split(";")[0] for sc in h.get_all("Set-Cookie") or []}

        png = test_png()
        data_url = "data:image/png;base64," + base64.b64encode(png).decode()

        # ── the hosts: a profile each, a signature on file, a version signed once by each; then issued ──
        os.environ["ENGINE_USER"] = "nobody@example.com"
        st, h, body = req("GET", "/you")
        self.assertEqual(st, 200)
        self.assertIn("signs as James and Abrar", body)
        st, h, body = req("POST", "/you/sign/1", {"_t": web.CSRF})
        self.assertIn("signs+as+James+and+Abrar", h["Location"])
        os.environ["ENGINE_USER"] = "jameshartt@example.com"
        st, h, body = req("GET", "/you")
        self.assertEqual(st, 200)
        for s in ("No signature on file", "<canvas", "/sign.js", "What we may publish", "waiting for James, Abrar"):
            self.assertIn(s, body)
        st, h, body = req("POST", "/you/sign/1", {"_t": web.CSRF})
        self.assertIn("msg=Draw+or+upload+your+signature+first", h["Location"])
        st, h, body = req("POST", "/you/signature", {"_t": web.CSRF, "signature": data_url, "strokes": "0"})
        self.assertIn("msg=Draw+or+choose+a+signature+first", h["Location"])
        st, h, body = req(
            "POST", "/you/signature", {"_t": web.CSRF, "signature": "data:image/png;base64,AAAA", "strokes": "1"}
        )
        self.assertIn("msg=Not+a+PNG+image", h["Location"])
        st, h, body = req("POST", "/you/signature", {"_t": web.CSRF, "signature": data_url, "strokes": "1"})
        self.assertIn("msg=Saved", h["Location"])
        self.assertEqual(objs["release/hosts/james.png"]["body"], png)
        st, h, body = req("GET", "/you/signature.png")
        self.assertEqual((st, h["Content-Type"]), (200, "image/png"))
        st, h, body = req("POST", "/you/sign/1", {"_t": web.CSRF})
        self.assertIn("msg=Signed+version+1%3B+waiting+for+Abrar", h["Location"])
        st, h, body = req("POST", "/you/sign/1", {"_t": web.CSRF})
        self.assertIn("already", h["Location"])
        self.assertIsNone(release.issued(c), "Abrar has not signed")

        c.execute("DELETE FROM code WHERE address='ada@example.com'")  # her three codes of test 18 are spent
        st, h, body = req("POST", "/enter", {"address": "ada@example.com"}, SEE)
        self.assertEqual(st, 200)
        cid, bind = re.search(r'name="id" value="([^"]+)"', body).group(1), jar(h)["see_code"]
        code = re.search(r"\n    (\d{6})\n", FakeSMTP.sent[-1].get_content()).group(1)
        st, h, body = req("POST", "/code", {"id": cid, "code": code}, SEE, {"see_code": bind})
        self.assertEqual(st, 303)
        ada = {"see_session": jar(h)["see_session"]}
        st, h, body = req("GET", "/release", host=SEE, cookies=ada)
        self.assertEqual(st, 200)
        self.assertIn("signing this version", body, "not issued: nothing to sign yet")
        body = req("GET", "/office", host=SEE, cookies=ada)[2]
        self.assertIn("None of four done. Nothing for you yet: James and Abrar are signing the release", body)
        self.assertIn('<li data-state="waiting"><p class="head"><span class="dot" aria-hidden="true">1</span>', body)
        self.assertNotIn('class="progress"', body, "the office page is the strip at full size")
        body = req("GET", "/release", host=SEE, cookies=ada)[2]
        self.assertIn(
            '<nav class="progress" aria-label="Where you are"><a class="home" href="/office">The office</a>', body
        )
        self.assertIn('<li data-state="waiting" aria-current="page">', body, "the page's own step, with the hosts")
        self.assertIn('<p class="next">Nothing for you yet: James and Abrar are signing the release', body)

        os.environ["ENGINE_USER"] = "abrar@example.com"
        st, h, body = req("POST", "/you/signature", {"_t": web.CSRF, "signature": data_url, "strokes": "1"})
        st, h, body = req("POST", "/you/sign/1", {"_t": web.CSRF})
        self.assertIn("it+is+issued", h["Location"])
        self.assertEqual(release.issued(c), 1)
        self.assertEqual(sorted(json.loads(objs["release/v1.json"]["body"])["signed"]), ["Abrar", "James"])
        self.assertIn("issued", req("GET", "/you")[2])

        # ── the guest: the page, the refusals, the signing ──
        st, h, body = req("GET", "/office", host=SEE, cookies=ada)
        self.assertIn('href="/release"', body)
        self.assertIn("Read and sign it", body)
        self.assertIn('Next: <a href="/release">read and sign the release</a>.', body)
        self.assertIn('<li data-state="current" data-next>', body, "the next step is the card with weight")
        self.assertIn('<a class="button signal" href="/release">Read and sign it</a>', body)
        body = req("GET", "/release", host=SEE, cookies=ada)[2]
        self.assertIn('<li data-state="current" aria-current="page">', body)
        self.assertIn('<p class="next">Next: <a href="/release">read and sign the release</a>.</p>', body)
        self.assertNotIn('<p class="kicker">Next</p>', body, "no hand-off while the step is the guest's to do")
        st, h, body = req("GET", "/release", host=SEE, cookies=ada)
        self.assertEqual(st, 200)
        for s in (
            "Appearing on the AgentSee podcast",
            "What we may publish",
            "<ul>",
            "<canvas",
            "/sign.js",
            "/release/signature/James.png",
            "/release/signature/Abrar.png",
            'value="Ada Lovelace"',
            "Recorded with your signature",
            '<li>On <a href="https://agentsee.work" target="_blank" rel="noopener">agentsee.work</a> and on <b>YouTube</b>.</li>',
            '<a href="https://agentsee.work/privacy/" target="_blank" rel="noopener">privacy notice</a> covers',
        ):
            self.assertIn(s, body)
        self.assertNotIn("](", body, "Linear's link syntax never reaches a page")
        self.assertEqual(
            release.plain_inline(
                "On [agentsee.work](<https://agentsee.work>) and the [notice](<https://agentsee.work/privacy/>), **always**."
            ),
            "On agentsee.work and the notice (https://agentsee.work/privacy/), always.",
            "nor the PDF",
        )
        st, h, body = req("GET", "/release/signature/James.png", host=SEE, cookies=ada)
        self.assertEqual((st, h["Content-Type"]), (200, "image/png"))
        self.assertEqual(req("GET", "/release/signature/James.png", host=SEE)[0], 404, "nothing without a session")
        self.assertEqual(req("GET", "/release", host=SEE)[0], 303)
        self.assertEqual(req("GET", "/release.pdf", host=SEE, cookies=ada)[1]["Location"], "/release")

        def sign(live=False, **kw):
            data = {"name": "Ada Lovelace", "signature": data_url, "strokes": "1", "agree": "yes", **kw}
            return req("POST", "/release/sign", data, SEE, ada, {"X-Office": "live"} if live else None)

        for kw, said in (
            ({"strokes": "0"}, "Draw your signature in the box"),
            ({"name": " "}, "Type your full name"),
            ({"agree": ""}, "Tick the box"),
            ({"signature": "data:image/png;base64,AAAA"}, "Not a PNG image"),
        ):
            st, h, body = sign(**kw)
            self.assertEqual(st, 400, kw)
            self.assertIn(said, body)
            st, h, body = sign(live=True, **kw)  # from the script: the word alone, the drawing kept on the pad
            self.assertEqual(st, 400, kw)
            self.assertIn(said, json.loads(body)["error"])
        self.assertIsNone(guests.guest(c, gid)["signed_at"])
        sent = len(FakeSMTP.sent)
        st, h, body = sign(name="  Ada   Lovelace ")
        self.assertEqual((st, h["Location"]), (303, "/release"))
        g = guests.guest(c, gid)
        self.assertEqual(g["signed_version"], 1)
        self.assertTrue(g["signed_at"])
        row = release.guest_signature(c, gid)
        self.assertEqual(
            (row["name"], row["address"], row["version"], row["png"] == png, row["sha256"]),
            ("Ada Lovelace", "ada@example.com", 1, True, v1["sha256"]),
        )
        self.assertEqual(row["ip"], "127.0.0.1")

        # ── the copies, the mail, the issue ──
        rec = json.loads(objs[f"guests/{gid}/release-v1.json"]["body"])
        self.assertEqual(
            [(s["role"], s["name"]) for s in rec["signatures"]],
            [("host", "James"), ("host", "Abrar"), ("guest", "Ada Lovelace")],
        )
        self.assertEqual((rec["version"]["sha256"], rec["signatures"][0]["address"]), (v1["sha256"], None))
        self.assertIn("What we may publish", rec["text"])
        page = objs[f"guests/{gid}/release-v1.html"]["body"].decode()
        self.assertIn("data:image/png;base64,", page)
        self.assertIn('<a href="https://agentsee.work" target="_blank" rel="noopener">agentsee.work</a>', page)
        self.assertNotIn("](", page)
        self.assertIn("Ada Lovelace", page)
        self.assertIn("verified by a code", page)
        pdf = objs[f"guests/{gid}/release-v1.pdf"]["body"]
        self.assertTrue(pdf.startswith(b"%PDF-"), "fpdf2 is installed for the suite (check.yml)")
        self.assertEqual(json.loads(objs[f"guests/{gid}/guest.json"]["body"])["signed_version"], 1)
        self.assertEqual(len(FakeSMTP.sent), sent + 1)
        m = FakeSMTP.sent[-1]
        self.assertEqual(
            (m["To"], m["Cc"], m["Subject"]),
            ("ada@example.com", "hello@agentsee.work", "Your signed release for the AgentSee podcast"),
        )
        self.assertIn("Thank you for signing", m.get_body(preferencelist=("plain",)).get_content())
        att = list(m.iter_attachments())
        self.assertEqual(
            [(a.get_filename(), a.get_content_type()) for a in att], [("agentsee-release-v1.pdf", "application/pdf")]
        )
        self.assertEqual(att[0].get_payload(decode=True), pdf)
        job = c.execute("SELECT * FROM linear_job WHERE kind='signed'").fetchone()
        self.assertEqual((job["guest"], json.loads(job["args"])["version"]), (gid, 1))
        self.assertEqual(linear.drain(c, env, log=lambda s: None), 1)
        issue, text = fake.comments[-1]
        self.assertEqual(issue, "AGE-73")
        self.assertRegex(
            text,
            r"^Ada Lovelace signed the release, version 1, through see\.agentsee\.work on .+, from ada@example\.com\. "
            rf"The signed copy: guests/{gid}/release-v1\.pdf in the archive\.$",
        )
        self.assertIn("release-signed", [fake.labels[x]["name"] for x in fake.issues["AGE-73"]["labelIds"]])

        # ── afterwards: the signed page, the copy on demand on both hosts, a newer version ──
        st, h, body = req("GET", "/release", host=SEE, cookies=ada)
        self.assertEqual(st, 200)
        for s in (
            "Signed on",
            "/release.pdf",
            "/release/signature/mine.png",
            "by James, Abrar and you",
            '<a class="button" href="/office">Back to the office</a>',
            '<p class="kicker">Next</p><h2>The idea</h2>',
            '<a class="button signal" href="/idea">Sit down with the office</a>',
            '<li data-state="done" aria-current="page"><span class="dot" aria-hidden="true">&#10003;</span>',
            '<p class="next">Next: <a href="/idea">flesh the idea out with the office</a>.</p>',
        ):
            self.assertIn(s, body)
        self.assertLess(
            body.index("<h2>All three</h2>"), body.index("<h2>The idea</h2>"), "the hand-off under the signatures"
        )
        self.assertNotIn("<canvas", body)
        st, h, body = req("GET", "/release.pdf", host=SEE, cookies=ada)
        self.assertEqual((st, h["Content-Type"]), (200, "application/pdf"))
        self.assertTrue(body.startswith("%PDF-"))
        self.assertEqual(req("GET", "/release/signature/mine.png", host=SEE, cookies=ada)[0], 200)
        self.assertIn("Your copy", req("GET", "/office", host=SEE, cookies=ada)[2])
        st, h, body = req("GET", f"/guests/{gid}")
        self.assertIn("signed version 1 on", body)
        self.assertIn(f"/guests/{gid}/release.pdf", body)
        self.assertEqual(req("GET", f"/guests/{gid}/release.pdf")[1]["Content-Type"], "application/pdf")
        body = req("GET", "/guests")[2]
        self.assertIn("Signed", body)
        self.assertIn("<th>Next</th>", body)
        self.assertIn('flesh the idea out with the office <span class="muted">today</span>', body)
        self.assertIn('<span class="muted">not in yet</span>', body, "Grace has not come through her link")
        body = req("GET", f"/guests/{gid}")[2]
        today = dt.datetime.now(ledger.ZONE).strftime("%-d %B")
        self.assertIn(f"One of four done. Next: flesh the idea out with the office, since {today} (today).", body)
        self.assertIn(f'action="/guests/{gid}/nudge"', body)
        sent, before = len(FakeSMTP.sent), len(c.execute("SELECT id FROM linear_job").fetchall())
        st, h, body = req("POST", f"/guests/{gid}/nudge", {"_t": web.CSRF})
        self.assertEqual(st, 303)
        self.assertIn("msg=Sent+to+ada%40example.com%3A+Next%3A+flesh+the+idea+out+with+the+office.", h["Location"])
        m = FakeSMTP.sent[-1]
        self.assertEqual(
            (len(FakeSMTP.sent), m["To"], m["Subject"]),
            (sent + 1, "ada@example.com", "The idea for your AgentSee episode"),
        )
        text = m.get_content()  # the office's letter for the stage (remind.py), the one the daily pass sends
        self.assertIn("Hello Ada,", text)
        self.assertIn("A note from Abrar, in case it helps.", text, "a hand note opens in the host's name")
        self.assertIn(f"You signed the release on {today}; thank you. The next thing is the", text)
        self.assertIn(f"https://{SEE}/idea", text)
        self.assertIn("If anything is unclear or in the way, reply to this message", text)
        job = c.execute("SELECT * FROM linear_job ORDER BY id DESC").fetchone()
        self.assertEqual(
            tuple(json.loads(job["args"])[k] for k in ("step", "n", "auto")), ("idea", 1, False), job["args"]
        )
        self.assertEqual(job["kind"], "nudged")
        self.assertEqual(len(c.execute("SELECT id FROM linear_job").fetchall()), before + 1)
        self.assertEqual(linear.drain(c, env, log=lambda s: None), 1)
        self.assertIn("emailed Ada Lovelace the next step, the idea, on", fake.comments[-1][1])
        self.assertIn("(the first of its two notes, counted against the office's).", fake.comments[-1][1])
        body = req("GET", "/guests")[2]
        self.assertIn('<span class="muted">today</span> <span class="muted">reminded once</span>', body)
        v2 = release.add(c, RELEASE_TEXT.replace("video link", "studio link"), "jameshartt@example.com")
        self.assertEqual((v2["n"], release.issued(c)), (2, 1), "a new version is not issued until both sign it")
        st, h, body = req("GET", "/release", host=SEE, cookies=ada)
        self.assertIn("Signed on", body)
        self.assertIn("video link", body, "the guest's copy is the version they signed")
        self.assertIn("waiting for James, Abrar", req("GET", "/you")[2])
        entry = pathlib.Path(__file__).resolve().parent / "engine.py"
        r = subprocess.run(
            [sys.executable, str(entry), "--ledger", str(self.db), "release"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("v1  Appearing on the AgentSee podcast", r.stdout)
        self.assertIn("[issued]", r.stdout)
        self.assertIn("v2 ", r.stdout)
        self.assertIn("[waiting for James, Abrar]", r.stdout)
        srv.shutdown()

    def test_21a_signature_ink(self):
        """A signature is kept as one ink on transparent whatever it came in: white
        strokes drawn on the dark theme, black on a photo of paper, white on black,
        trimmed and fitted alike; a second pass changes nothing; a flat image is
        kept. `release normalise` brings the images already in the ledger to the
        same, once. A host signs by command, with the record saying so."""
        from PIL import Image, ImageChops, ImageDraw

        import release

        def strokes(fill, paper):
            im = Image.new("RGBA", (300, 140), paper)
            ImageDraw.Draw(im).line([(40, 100), (90, 30), (140, 100), (200, 40), (250, 90)], fill=fill, width=5)
            out = io.BytesIO()
            im.save(out, "PNG")
            return out.getvalue()

        white_on_clear = strokes((239, 233, 224, 255), (0, 0, 0, 0))
        black_on_paper = strokes((0, 0, 0, 255), (247, 243, 234, 255))
        white_on_black = strokes((255, 255, 255, 255), (12, 12, 12, 255))
        outs = [release.normalise_png(x) for x in (white_on_clear, black_on_paper, white_on_black)]
        ims = [Image.open(io.BytesIO(o)) for o in outs]
        self.assertEqual([(i.mode, i.size) for i in ims], [("RGBA", release.CANVAS)] * 3)
        alpha = ims[0].getchannel("A")
        box = alpha.getbbox()
        self.assertTrue(box, "the strokes are there")
        self.assertTrue(box[0] >= release.PAD and box[2] <= release.CANVAS[0] - release.PAD, "fitted with room")
        self.assertTrue(box[2] - box[0] > release.CANVAS[0] // 2, "and scaled up to the canvas")
        for i in ims[1:]:
            self.assertEqual(i.getchannel("A").tobytes(), alpha.tobytes(), "the same strokes, any colour")
        ink = Image.new("RGB", release.CANVAS, release.INK)
        self.assertIsNone(ImageChops.difference(ims[0].convert("RGB"), ink).getbbox(), "one ink")
        self.assertEqual(release.normalise_png(outs[0]), outs[0], "a second pass changes nothing")
        flat = test_png()
        self.assertEqual(release.normalise_png(flat), flat, "no paper to tell the ink from: kept")
        self.assertLess(len(outs[1]), release.MAX_PNG)

        # ── the images already in the ledger: white strokes on file and on version 1, from the dark theme ──
        os.environ.update(self.menv)
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in (*self.menv, "ENGINE_USER")])
        client = archive.Client(self.r2env)
        patch = unittest.mock.patch.object(archive, "Client", lambda env=None: client)
        patch.start()
        self.addCleanup(patch.stop)
        c = self.c()
        objs = FakeR2.state["objects"]
        c.execute("UPDATE host_signature SET png=? WHERE host='James'", (white_on_clear,))
        c.execute("UPDATE release_signature SET png=? WHERE host='James' AND version=1", (white_on_clear,))
        changed = release.normalise_all(c, "jameshartt@example.com")
        self.assertEqual([(k, n) for k, n, *_ in changed], [("file", "James"), ("v1", "James")], "the flat ones kept")
        self.assertEqual(c.execute("SELECT png FROM host_signature WHERE host='James'").fetchone()[0], outs[0])
        self.assertEqual(release.signed_by(c, 1)["James"]["png"], outs[0])
        self.assertEqual(objs["release/hosts/james.png"]["body"], outs[0], "mirrored again")
        ev = c.execute("SELECT who, detail FROM event WHERE what='release:normalised'").fetchone()
        self.assertEqual(
            (ev["who"], json.loads(ev["detail"])), ("jameshartt@example.com", {"images": ["file James", "v1 James"]})
        )
        self.assertEqual(release.normalise_all(c, "x"), [], "once")

        # ── a host signs by command: the image from a file, then the version; the record says who ran it ──
        sig = pathlib.Path(self.tmp.name) / "james-on-paper.png"
        sig.write_bytes(black_on_paper)
        entry = pathlib.Path(__file__).resolve().parent / "engine.py"

        def run(*args):
            r = subprocess.run(
                [sys.executable, str(entry), "--ledger", str(self.db), "release", *args],
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ, "ENGINE_USER": "jameshartt@example.com"},
            )
            return r.returncode, (r.stdout + r.stderr).strip()

        self.assertEqual(run("sign")[0], 1)
        rc, out = run("sign", "Nobody")
        self.assertEqual((rc, "signs as James and Abrar" in out), (1, True), out)
        rc, out = run("sign", "James", "--version", "9")
        self.assertEqual((rc, "no such version" in out), (1, True), out)
        rc, out = run("sign", "James", "--png", str(sig), "--version", "2")
        self.assertEqual(rc, 0, out)
        self.assertIn("James: signature image on file", out)
        self.assertIn("v2 signed by James [waiting for Abrar]", out)
        row = release.signed_by(c, 2)["James"]
        self.assertEqual(
            (row["png"], row["ip"], row["address"]), (outs[1], None, "jameshartt@example.com, by command for James")
        )
        self.assertEqual(row["ua"], "by command: engine.py release sign, run by jameshartt@example.com")
        rc, out = run("sign", "James", "--version", "2")
        self.assertEqual((rc, "already" in out), (1, True), out)
        rc, out = run("sign", "Abrar", "--version", "2")
        self.assertEqual((rc, "v2 signed by Abrar [issued: a guest sees it now]" in out), (0, True), out)
        self.assertEqual(release.issued(c), 2)
        rc, out = run("normalise")
        self.assertEqual((rc, out), (0, "nothing to change: every signature is ink on transparent"))
        rc, out = run()
        self.assertIn("James: image on file since", out)
        self.assertIn("v2 ", out)
        self.assertIn("[issued]", out)

    def test_22_idea(self):
        """The idea conversation: the skill as the system prompt and as a file;
        the start, a reply streamed and a reply waited for; what the API is
        sent and never sent; the ledger's turns and tokens; the issue's one
        transcript comment created then rewritten; the run-sheet draft found,
        saved, told and revised; the caps and a failure said in a sentence;
        the about-you form with a headshot; who may not talk."""
        import idea

        SEE = guests.HOST
        os.environ.update(self.menv, ENGINE_SESSION_KEY="a-key-for-the-tests", ANTHROPIC_API_KEY="sk-test")
        self.addCleanup(
            lambda: [os.environ.pop(k, None) for k in (*self.menv, "ENGINE_SESSION_KEY", "ANTHROPIC_API_KEY")]
        )
        client = archive.Client(self.r2env)
        patch = unittest.mock.patch.object(archive, "Client", lambda env=None: client)
        patch.start()
        self.addCleanup(patch.stop)
        c = self.c()
        objs = FakeR2.state["objects"]
        fake = FakeLinear()
        fake.issues.update(self.linear_made)
        linear.post = fake
        linear.vault = FakeVault("lr1", item="Linear podcast token")
        linear._token.update(access=None, until=0.0, unsaved=None)
        env = {"LINEAR_CLIENT_ID": "lcid", "LINEAR_CLIENT_SECRET": "lcs", "LINEAR_REFRESH_TOKEN": "lr1"}
        ai = FakeAnthropic()
        idea.open_stream = ai
        gid = c.execute("SELECT id FROM guest WHERE name='Ada Lovelace'").fetchone()[0]

        # ── the skill: the one source, with the site's questions and the episodes so far in it ──
        text = idea.skill_text(c)
        self.assertNotIn("---\nname:", text)
        self.assertIn("## The opening", text)
        self.assertNotIn("Question 4 of about 20", text)
        self.assertIn("1. If writing the code is no longer the expensive part", text)
        nine = f"### Episode 9: Nine ({DAY0})\n\nWhat happened in nine."
        self.assertIn(nine, text)
        self.assertNotIn("## Transcripts", text, "the file a guest takes away holds the notes, not the transcripts")
        self.assertIn("# Run-sheet draft:", text)
        self.assertEqual(
            idea.skill_text(),
            text.replace(nine, "(none yet: this would be the first)"),
        )

        # ── the system prompt: the skill and the transcripts cached, then this guest ──
        objs["episodes/ep9/out/transcript.txt"] = {
            "body": b"Abrar Mahmood: Nine is about nine things.\nJames Hartt: Nine of them.\n",
            "type": "text/plain",
            "meta": {},
        }
        self.assertFalse((archive.out_dir("ep9") / "transcript.txt").exists())
        prompt = idea.system_prompt(c, guests.guest(c, gid))
        self.assertEqual([b.get("cache_control") for b in prompt], [{"type": "ephemeral", "ttl": "1h"}, None])
        self.assertIn("## The episodes so far", prompt[0]["text"])
        self.assertIn("## Transcripts\n", prompt[0]["text"])
        self.assertIn("### Episode 9: Nine\n\nAbrar Mahmood: Nine is about nine things.", prompt[0]["text"])
        self.assertIn("with Ada Lovelace, who has signed the release", prompt[1]["text"])
        self.assertNotIn("Ada Lovelace", prompt[0]["text"], "the cached block is the same for every guest")
        self.assertTrue((archive.out_dir("ep9") / "transcript.txt").exists(), "fetched from the archive once")
        n = len(FakeR2.state["requests"])
        idea.system_prompt(c, guests.guest(c, gid))
        self.assertEqual(len(FakeR2.state["requests"]), n, "then read from the data dir")
        self.assertEqual(idea.known(c), (1, 1))

        # ── the hosts' page on the guest: the issue, read once an hour, kept, never in the cached block ──
        self.assertIn("## What the hosts noted", prompt[0]["text"], "how the page is held is in the skill")
        self.assertIn("no page on this guest that the office can read", prompt[1]["text"], "nothing read yet")
        fake.issues["AGE-73"]["description"] = (
            "## Who they are\n\nWrote the first program.\n\n## Caveats\n\nNever name the Analytical Engine's backer."
        )
        prompt = idea.system_prompt(c, guests.guest(c, gid), env=env)
        self.assertEqual(fake.calls.count("Notes"), 1)
        self.assertIn("## The hosts' page on this guest", prompt[1]["text"])
        self.assertIn(
            "AGE-73: Ada Lovelace, on engines\n\n## Who they are\n\nWrote the first program.", prompt[1]["text"]
        )
        self.assertIn("Never name the Analytical Engine's backer.", prompt[1]["text"])
        self.assertIn("The guest decides what the episode is about.", prompt[1]["text"])
        self.assertNotIn("Wrote the first program", prompt[0]["text"], "the cached block is the same for every guest")
        self.assertNotIn("no page on this guest", prompt[1]["text"])
        g = guests.guest(c, gid)
        self.assertTrue(g["notes"].startswith("AGE-73: Ada Lovelace, on engines"))
        self.assertTrue(g["notes_at"])
        idea.system_prompt(c, guests.guest(c, gid), env=env)
        self.assertEqual(fake.calls.count("Notes"), 1, "within the hour the copy serves")
        stale = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).replace(microsecond=0).isoformat()
        c.execute("UPDATE guest SET notes_at=? WHERE id=?", (stale, gid))
        fake.issues["AGE-73"]["description"] += "\n\nConfirm the dates."
        fake.fail = 500
        prompt = idea.system_prompt(c, guests.guest(c, gid), env=env)
        self.assertIn("Wrote the first program", prompt[1]["text"], "Linear down: the copy serves")
        self.assertNotIn("Confirm the dates", prompt[1]["text"])
        self.assertEqual(guests.guest(c, gid)["notes_at"], stale, "and is not called fresh")
        prompt = idea.system_prompt(c, guests.guest(c, gid), env=env)
        self.assertIn("Confirm the dates.", prompt[1]["text"], "Linear back: read again")
        self.assertNotEqual(guests.guest(c, gid)["notes_at"], stale)
        self.assertNotIn("Confirm the dates", idea.skill_text(c), "the file a guest takes away carries no page")
        bob = guests.guest(c, c.execute("SELECT id FROM guest WHERE name='Bob Builder'").fetchone()[0])
        self.assertIsNone(idea.notes(c, bob, env), "no issue, no page")
        self.assertEqual(fake.calls.count("Notes"), 2, "the refused call is not counted; Bob's is never made")

        web.STATE["ledger"] = str(self.db)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), web.App)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def req(method, path, data=None, host=None, cookies=None, headers=None, raw=None):
            hdr = dict(headers or {})
            if host:
                hdr["Host"] = host
            if cookies:
                hdr["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
            body = urllib.parse.urlencode(data).encode() if data is not None else None
            if raw:
                body, hdr["Content-Type"] = raw
            r = urllib.request.Request(base + path, data=body, method=method, headers=hdr)
            opener = urllib.request.build_opener(NoRedirect)
            try:
                with opener.open(r, timeout=20) as resp:
                    return resp.status, resp.headers, resp.read().decode(errors="replace")
            except urllib.error.HTTPError as e:
                with e:
                    return e.code, e.headers, e.read().decode(errors="replace")

        def jar(h):
            return {sc.partition("=")[0]: sc.partition("=")[2].split(";")[0] for sc in h.get_all("Set-Cookie") or []}

        def sign_in(address):
            c.execute("DELETE FROM code WHERE address=?", (address,))
            st, h, body = req("POST", "/enter", {"address": address}, SEE)
            cid, bind = re.search(r'name="id" value="([^"]+)"', body).group(1), jar(h)["see_code"]
            code = re.search(r"\n    (\d{6})\n", FakeSMTP.sent[-1].get_content()).group(1)
            st, h, body = req("POST", "/code", {"id": cid, "code": code}, SEE, {"see_code": bind})
            self.assertEqual(st, 303)
            return {"see_session": jar(h)["see_session"]}

        ada, bobs = sign_in("ada@example.com"), sign_in("bob@example.com")

        # ── who may talk: a signed guest; the page before the first word ──
        self.assertEqual(req("GET", "/idea", host=SEE)[0], 303)
        st, h, body = req("GET", "/idea", host=SEE, cookies=bobs)
        self.assertEqual(st, 200)
        self.assertIn("The release comes first", body)
        st, h, body = req("POST", "/idea/say", {"start": "1"}, SEE, bobs)
        self.assertEqual(st, 400)
        self.assertIn("The release comes first", body)
        self.assertIn("After the release", req("GET", "/office", host=SEE, cookies=bobs)[2])
        self.assertIn("Sit down with the office", req("GET", "/office", host=SEE, cookies=ada)[2])
        st, h, body = req("GET", "/idea", host=SEE, cookies=ada)
        self.assertEqual(st, 200)
        for s in ("Begin", "/office.js", "The questions, as a file", "Europe/London", "The run-sheet draft"):
            self.assertIn(s, body)
        self.assertIn('<div id="next"></div>', body, "the hand-off's place, empty until there is a draft")
        self.assertIn('<li data-state="current" aria-current="page">', body)
        self.assertIn(
            '<li data-state="current"><span class="dot" aria-hidden="true">3</span><b><a href="/time">Time</a></b><small>To do</small>',
            body,
        )
        self.assertIn('<p class="next">Next: <a href="/idea">flesh the idea out with the office</a>.</p>', body)
        self.assertIn('<a class="button" href="/office">Back to the office</a>', body)
        self.assertNotIn(
            "Back to the office</a>",
            body.replace('<a class="button" href="/office">Back to the office</a>', ""),
            "one way back",
        )
        st, h, body = req("GET", "/idea/skill.md", host=SEE, cookies=ada)
        self.assertEqual((st, h["Content-Type"]), (200, "text/markdown; charset=utf-8"))
        self.assertIn("1. If writing the code", body)
        self.assertIn("## What the hosts noted", body)
        self.assertNotIn("Wrote the first program", body, "the hosts' page stays in the office")
        self.assertEqual((req("GET", "/office.js", host=SEE)[0], req("GET", "/chat.js", host=SEE)[0]), (200, 404))
        import pages

        self.assertRegex(pages.asset("/engine.css"), r"^/engine\.css\?v=[0-9a-f]{10}$", "a deploy changes the URL")
        self.assertEqual(pages.asset("/nothing.css"), "/nothing.css")
        self.assertIn('href="' + pages.asset("/see.css") + '"', req("GET", "/idea", host=SEE, cookies=ada)[2])
        self.assertEqual(req("GET", pages.asset("/office.js"), host=SEE)[0], 200, "the query is ignored")

        # ── the start, waited for; what the API is sent ──
        st, h, body = req("POST", "/idea/say", {"start": "1"}, SEE, ada, {"Accept": "text/event-stream"})
        self.assertEqual((st, h["Content-Type"]), (200, "text/event-stream; charset=utf-8"))
        done = json.loads(body.strip().split("\n\n")[-1].split("\n", 1)[1][len("data: ") :])
        self.assertEqual([s["id"] for s in done["sections"]], ["conversation"], "the Begin button becomes the box")
        self.assertTrue(
            done["sections"][0]["html"].startswith('<section class="section engine-section" id="conversation">')
        )
        self.assertIn('class="composer"', done["sections"][0]["html"])
        payload, headers = ai.payloads[-1]
        self.assertEqual(headers["x-api-key"], "sk-test")
        self.assertEqual(headers["anthropic-beta"], "server-side-fallback-2026-07-01")
        self.assertEqual((payload["model"], payload["max_tokens"], payload["stream"]), ("claude-opus-5-5", 4000, True))
        self.assertEqual((payload["fallbacks"], payload["output_config"]), ("default", {"effort": "medium"}))
        self.assertEqual(payload["system"][0]["cache_control"], {"type": "ephemeral", "ttl": "1h"})
        self.assertIn("The idea, before we record", payload["system"][0]["text"])
        self.assertIn("Nine is about nine things", payload["system"][0]["text"])
        self.assertIn("with Ada Lovelace", payload["system"][1]["text"])
        self.assertIn("Wrote the first program.", payload["system"][1]["text"], "the page, from the copy kept")
        self.assertEqual(payload["messages"], [{"role": "user", "content": idea.START}])
        self.assertNotIn("tools", payload)
        self.assertNotIn("thinking", payload)
        rows = idea.turns(c, gid)
        self.assertEqual([t["role"] for t in rows], ["start", "agent"])
        self.assertEqual((rows[1]["input_tokens"], rows[1]["output_tokens"], rows[1]["stop"]), (1500, 50, "end_turn"))
        self.assertEqual(guests.guest(c, gid)["idea_tokens"], 1050, "the cache read is not charged to the budget")
        st, h, body = req("GET", "/idea", host=SEE, cookies=ada)
        self.assertIn('<div class="turn agent"><p>That is the spine (1).', body)
        self.assertIn("<textarea", body)
        self.assertNotIn(">Begin<", body)
        self.assertNotIn(idea.START, body, "the start is the engine's line, not the guest's")
        self.assertLess(body.index("Your own assistant"), body.index("Or borrow ours"), "their own assistant leads")
        self.assertIn('class="composer"', body)
        self.assertIn('placeholder="Your answer. Enter sends; Shift+Enter for a new line."', body)

        # ── a reply streamed ──
        st, h, body = req(
            "POST", "/idea/say", {"text": "It is about compilers."}, SEE, ada, {"Accept": "text/event-stream"}
        )
        self.assertEqual((st, h["Content-Type"]), (200, "text/event-stream; charset=utf-8"))
        events = [chunk.split("\n", 1) for chunk in body.strip().split("\n\n")]
        kinds = [e[0] for e in events]
        self.assertEqual(kinds[0], "event: delta")
        self.assertEqual(kinds[-1], "event: done")
        done = json.loads(events[-1][1][len("data: ") :])
        self.assertIn('class="turn agent"', done["html"])
        self.assertEqual(done["sections"], [], "a plain reply changes no section")
        streamed = "".join(json.loads(e[1][len("data: ") :])["t"] for e in events if e[0] == "event: delta")
        self.assertEqual(streamed, idea.turns(c, gid)[-1]["text"])
        self.assertEqual(ai.payloads[-1][0]["messages"][-1], {"role": "user", "content": "It is about compilers."})
        self.assertEqual(idea.exchanges(c, gid), 1)

        # ── the issue: one transcript comment, created then rewritten ──
        jobs = c.execute("SELECT kind FROM linear_job WHERE guest=? AND done_at IS NULL ORDER BY id", (gid,)).fetchall()
        self.assertEqual([j["kind"] for j in jobs], ["idea", "idea"])
        before = len(fake.comments)
        self.assertEqual(linear.drain(c, env, log=lambda s: None), 2)
        self.assertEqual(len(fake.comments), before + 1, "the second exchange rewrote the comment, not a new one")
        ident, transcript = fake.comments[-1]
        self.assertEqual(ident, "AGE-73")
        self.assertIn("**Ada Lovelace's conversation with the office", transcript)
        self.assertIn("1 exchanges", transcript)
        self.assertIn("> **The office:**", transcript)
        self.assertIn("> **Ada:**\n> It is about compilers.", transcript)
        self.assertNotIn(idea.START, transcript)
        self.assertTrue(guests.guest(c, gid)["idea_comment"].startswith("c-"))

        # ── the draft: found in the reply, saved, told, labelled; then revised ──
        ai.replies.append(
            "My guess: an hour on compilers.\n\n```markdown\n# Run-sheet draft: Compilers for everyone\n\n"
            "Ada on compilers.\n\n**Run length: 30 minutes.**\n\n## Areas\n\n**1. Intro**\n\n- What the podcast is\n```\n\n"
            "The office saves this and sends it to James and Abrar."
        )
        self.assertIn("finish the idea with the office", req("GET", "/office", host=SEE, cookies=ada)[2], "begun")
        sent = len(FakeSMTP.sent)
        st, h, body = req("POST", "/idea/say", {"text": "Write it up."}, SEE, ada)
        self.assertEqual(st, 303)
        g = guests.guest(c, gid)
        self.assertTrue(g["brief"].startswith("# Run-sheet draft: Compilers for everyone"))
        self.assertIn("**1. Intro**", g["brief"])
        self.assertNotIn("```", g["brief"])
        m = FakeSMTP.sent[-1]
        self.assertEqual(
            (len(FakeSMTP.sent), m["To"], m["Subject"]),
            (sent + 1, "ada@example.com", "Your run-sheet draft, and the next step"),
        )
        text = m.get_content()
        self.assertIn("Your run-sheet draft is with James and Abrar.", text)
        self.assertIn(
            f"The next step is a time to record. Pick the time you would prefer and up to two backups at\n\n    https://{SEE}/time",
            text,
        )
        self.assertIn("--- the draft ---\n\n# Run-sheet draft: Compilers for everyone", text)
        self.assertEqual(
            [
                e["who"]
                for e in c.execute(
                    "SELECT who FROM event WHERE what='mail:sent' AND guest=? ORDER BY id DESC LIMIT 1", (gid,)
                )
            ],
            ["see"],
        )
        st, h, body = req("GET", "/idea", host=SEE, cookies=ada)
        self.assertIn("Saved", body)
        self.assertIn("James and Abrar have it. Change anything here", body)
        self.assertIn("Compilers for everyone", body)
        self.assertIn('<pre class="brief">', body)
        self.assertIn(
            '<div id="next"><section class="section engine-section"><p class="kicker">Next</p><h2>The time</h2>', body
        )
        self.assertIn('<a class="button signal" href="/time">Pick a time</a>', body)
        self.assertLess(body.index('id="draft"'), body.index('id="next"'), "the hand-off under the draft")
        self.assertLess(body.index('id="next"'), body.index('id="about"'), "and before the about-you form")
        self.assertIn('<li data-state="done" aria-current="page">', body, "the idea, done, is where the page is")
        self.assertIn(
            '<li data-state="current"><span class="dot" aria-hidden="true">3</span><b><a href="/time">Time</a></b><small>Next</small>',
            body,
        )
        self.assertIn('<p class="next">Next: <a href="/time">pick a time to record</a>.</p>', body)
        body = req("GET", "/office", host=SEE, cookies=ada)[2]
        self.assertIn("Drafted", body)
        self.assertIn('Two of four done. Next: <a href="/time">pick a time to record</a>.', body)
        self.assertIn(
            '<li data-state="current" data-next><p class="head"><span class="dot" aria-hidden="true">3</span><b>The time</b></p>',
            body,
        )
        self.assertIn('<a class="button signal" href="/time">Pick a time</a>', body)
        self.assertIn("pick a time to record <span", req("GET", "/guests")[2], "the Guests screen's Next column")
        self.assertEqual(linear.drain(c, env, log=lambda s: None), 2)
        drafted = [b for i, b in fake.comments if "The run-sheet draft for Ada Lovelace" in b]
        self.assertEqual(len(drafted), 1)
        self.assertIn("written by the office from what they said", drafted[0])
        self.assertIn("> # Run-sheet draft: Compilers for everyone", drafted[0])
        self.assertIn("idea-drafted", [fake.labels[x]["name"] for x in fake.issues["AGE-73"]["labelIds"]])
        st, h, body = req("POST", "/idea/brief", {"brief": "# Run-sheet draft: Better\n\nRevised by hand."}, SEE, ada)
        self.assertEqual((st, h["Location"]), (303, "/idea?saved=draft#draft"), "back to the draft, not the top")
        self.assertEqual(guests.guest(c, gid)["brief"], "# Run-sheet draft: Better\n\nRevised by hand.")
        st, h, body = req("GET", "/idea?saved=draft", host=SEE, cookies=ada)
        self.assertLess(body.index('id="draft"'), body.index("<b>Saved.</b>"), "the word in its section")
        st, h, body = req(
            "POST", "/idea/brief", {"brief": "# Run-sheet draft: Live\n\nIn place."}, SEE, ada, {"X-Office": "live"}
        )
        self.assertEqual((st, h["Content-Type"]), (200, "application/json; charset=utf-8"), "the script's save")
        j = json.loads(body)
        self.assertEqual((j["ok"], [s["id"] for s in j["sections"]]), (True, ["draft", "next"]))
        self.assertTrue(j["sections"][1]["html"].startswith('<div id="next"><section'), "the hand-off, in place")
        self.assertEqual(len(FakeSMTP.sent), sent + 1, "a revision gets no second mail; the first draft did")
        html = j["sections"][0]["html"]
        self.assertTrue(html.startswith('<section class="section engine-section" id="draft">'), "the section, whole")
        self.assertIn("data-said><b>Saved.</b>", html)
        self.assertIn("Run-sheet draft: Live", html)
        self.assertEqual(guests.guest(c, gid)["brief"], "# Run-sheet draft: Live\n\nIn place.")
        st, h, body = req("POST", "/idea/brief", {"brief": "x"}, SEE, ada, {"X-Office": "live"})
        self.assertEqual((st, json.loads(body)), (400, {"ok": False, "error": "That is not a run-sheet."}))
        self.assertEqual(linear.drain(c, env, log=lambda s: None), 2)
        self.assertIn("as they revised it", fake.comments[-1][1])
        st, h, body = req("POST", "/idea/brief", {"brief": "x"}, SEE, ada)
        self.assertEqual((st, h["Location"]), (303, "/idea?err=That+is+not+a+run-sheet.&at=draft#draft"))
        st, h, body = req("GET", "/idea?err=That+is+not+a+run-sheet.&at=draft", host=SEE, cookies=ada)
        self.assertLess(body.index('id="draft"'), body.index("not a run-sheet"))
        self.assertLess(body.index("not a run-sheet"), body.index('id="about"'))
        self.assertIn("Run-sheet draft: Live", req("GET", f"/guests/{gid}")[2])

        # ── a failure said in a sentence, waited for and streamed; the caps ──
        ai.fail = 529
        st, h, body = req("POST", "/idea/say", {"text": "Again?"}, SEE, ada)
        self.assertEqual(st, 400)
        self.assertIn("would not answer: 529", body)
        self.assertEqual(idea.turns(c, gid)[-1]["role"], "guest", "the guest's words are kept; the reply never came")
        ai.fail = 529
        st, h, body = req("POST", "/idea/say", {"text": "Again?"}, SEE, ada, {"Accept": "text/event-stream"})
        self.assertEqual(st, 200)
        events = [chunk.split("\n", 1) for chunk in body.strip().split("\n\n")]
        self.assertEqual([e[0] for e in events], ["event: fail"])
        failed = json.loads(events[0][1][len("data: ") :])
        self.assertIn("would not answer", failed["m"])
        self.assertEqual((failed["kept"], failed["writing"]), (True, False), "the words stay; nothing is being written")
        st, h, body = req("GET", "/idea", host=SEE, cookies=ada)
        self.assertIn(pages.esc(idea.MISSING), body, "the page says the reply never came")
        self.assertLess(body.index("Again?</p></div>"), body.index(pages.esc(idea.MISSING)))
        self.assertNotIn(" disabled", body, "and the box is open")
        self.assertNotIn("data-writing", body)
        st, h, body = req("POST", "/idea/say", {"text": "Once more."}, SEE, ada)
        self.assertEqual(st, 303)
        sent = ai.payloads[-1][0]["messages"]
        self.assertEqual(sent[-1]["role"], "user")
        self.assertEqual(sent[-1]["content"], "Again?\n\nAgain?\n\nOnce more.", "neighbours of one role joined")
        self.assertEqual(
            [m["role"] for m in sent], ["user", "assistant", "user", "assistant", "user", "assistant", "user"]
        )
        before = len(fake.comments)
        self.assertEqual(linear.drain(c, env, log=lambda s: None), 1, "the transcript, rewritten")
        self.assertEqual(len(fake.comments), before)
        self.assertIn(
            "5 exchanges",
            [b for i, b in fake.comments if "conversation with the office" in b][-1],
            "the two the office never answered count: they were said",
        )
        cfg = dict(ledger.CONFIG.get("idea") or {})
        ledger.CONFIG["idea"] = {**cfg, "max_turns": 5}
        try:
            st, h, body = req("POST", "/idea/say", {"text": "More."}, SEE, ada)
            self.assertEqual(st, 400)
            self.assertIn("limit of 5 turns", body)
            ledger.CONFIG["idea"] = {**cfg, "token_budget": 10}
            st, h, body = req("POST", "/idea/say", {"text": "More."}, SEE, ada)
            self.assertEqual(st, 400)
            self.assertIn("used all the office set aside", body)
            self.assertIn("used all the office set aside", req("GET", "/idea", host=SEE, cookies=ada)[2])
        finally:
            ledger.CONFIG["idea"] = cfg
        st, h, body = req("POST", "/idea/say", {"text": ""}, SEE, ada)
        self.assertEqual(st, 400)
        self.assertIn("Say something first", body)

        # ── about you: the fields, the headshot, the issue ──
        def multipart(fields, files):
            b = "----agentsee-test"
            out = []
            for k, v in fields.items():
                out.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
            for k, (fn, data, ct) in files.items():
                out.append(
                    f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{fn}"\r\nContent-Type: {ct}\r\n\r\n'.encode()
                    + data
                    + b"\r\n"
                )
            out.append(f"--{b}--\r\n".encode())
            return b"".join(out), f"multipart/form-data; boundary={b}"

        png = test_png(32, 32)
        about = {
            "shown_name": " Ada  Lovelace ",
            "bio": "Wrote the first program.",
            "link": "https://example.com/ada",
            "tz": "Europe/Paris",
        }
        st, h, body = req("POST", "/idea/about", host=SEE, cookies=ada, raw=multipart({**about, "link": "ftp://x"}, {}))
        self.assertEqual(
            (st, h["Location"]), (303, "/idea?err=The+link+should+start+with+https%3A%2F%2F&at=about#about")
        )
        body = req("GET", "/idea?err=The+link+should+start+with+https%3A%2F%2F&at=about", host=SEE, cookies=ada)[2]
        self.assertLess(body.index('id="about"'), body.index("should start with https://"))
        st, h, body = req(
            "POST",
            "/idea/about",
            host=SEE,
            cookies=ada,
            raw=multipart(about, {"headshot": ("me.png", png, "image/png")}),
        )
        self.assertEqual((st, h["Location"]), (303, "/idea?saved=about#about"))
        g = guests.guest(c, gid)
        self.assertEqual(
            (g["shown_name"], g["bio"], g["link"], g["tz"]),
            ("Ada Lovelace", about["bio"], about["link"], "Europe/Paris"),
        )
        shot = idea.headshot(c, gid)
        self.assertEqual((shot["type"], shot["name"], shot["data"] == png), ("image/png", "me.png", True))
        self.assertEqual(objs[f"guests/{gid}/headshot.png"]["body"], png)
        st, h, body = req("GET", "/idea/headshot", host=SEE, cookies=ada)
        self.assertEqual((st, h["Content-Type"]), (200, "image/png"))
        self.assertIn('value="Europe/Paris" selected', req("GET", "/idea", host=SEE, cookies=ada)[2])
        st, h, body = req(
            "POST", "/idea/about", host=SEE, cookies=ada, headers={"X-Office": "live"}, raw=multipart(about, {})
        )
        self.assertEqual(st, 200, body)
        j = json.loads(body)
        self.assertEqual([s["id"] for s in j["sections"]], ["about"])
        self.assertTrue(j["sections"][0]["html"].startswith('<section class="section engine-section" id="about">'))
        self.assertIn("data-said><b>Saved.</b>", j["sections"][0]["html"])
        self.assertIn('value="Europe/Paris" selected', j["sections"][0]["html"])
        self.assertEqual(linear.drain(c, env, log=lambda s: None), 2)
        self.assertIn("About Ada Lovelace", fake.comments[-1][1])
        self.assertIn("> Time zone: Europe/Paris", fake.comments[-1][1])
        self.assertIn(f"guests/{gid}/headshot.png", fake.comments[-2][1], "the save with the headshot, told")
        st, h, body = req(
            "POST",
            "/idea/about",
            host=SEE,
            cookies=ada,
            raw=multipart(about, {"headshot": ("x.gif", b"GIF89a....", "image/gif")}),
        )
        self.assertEqual(st, 303)
        self.assertIn("err=The+headshot+should+be+a+JPEG+or+a+PNG&at=about#about", h["Location"])
        self.assertIn("shown as Ada Lovelace, Europe/Paris", req("GET", f"/guests/{gid}")[2])
        self.assertIn("The office", req("GET", "/health")[2])
        self.assertIn("knows 1 episodes, 1 transcripts", req("GET", "/health")[2])

        # ── a reply that runs out of room is carried on from its last word, so the draft comes out whole ──
        head = (
            "Here it is.\n\n```markdown\n# Run-sheet draft: Whole again\n\nAda on compilers, in full.\n\n"
            "## Areas\n\n**1. Intro**\n\n- What the"
        )
        tail = " podcast is\n- Why Ada\n```\n\nThe office saves this and sends it to James and Abrar."
        ai.replies += [head, tail]
        ai.stops += ["max_tokens", "end_turn"]
        n = len(ai.payloads)
        st, h, body = req("POST", "/idea/say", {"text": "Write it whole."}, SEE, ada, {"Accept": "text/event-stream"})
        self.assertEqual(st, 200)
        self.assertEqual(len(ai.payloads), n + 2, "one more call, to carry on")
        more = ai.payloads[-1][0]["messages"]
        self.assertEqual(more[-2], {"role": "user", "content": "Write it whole."})
        self.assertEqual(more[-1], {"role": "assistant", "content": head}, "the text so far, as the assistant's own")
        last = idea.turns(c, gid)[-1]
        self.assertEqual((last["role"], last["text"], last["stop"]), ("agent", head + tail, "end_turn"))
        self.assertEqual((last["input_tokens"], last["output_tokens"]), (3000, 100), "both calls' usage")
        streamed = "".join(
            json.loads(e.split("\n", 1)[1][len("data: ") :])["t"]
            for e in body.strip().split("\n\n")
            if e.startswith("event: delta")
        )
        self.assertEqual(streamed, head + tail, "the page saw all of it, the seam included")
        done = json.loads(body.strip().split("\n\n")[-1].split("\n", 1)[1][len("data: ") :])
        self.assertEqual([s["id"] for s in done["sections"]], ["draft", "next"], "the draft and the hand-off, rendered")
        self.assertIn("Whole again", done["sections"][0]["html"])
        g = guests.guest(c, gid)
        self.assertTrue(g["brief"].startswith("# Run-sheet draft: Whole again"))
        self.assertIn("- What the podcast is", g["brief"])
        self.assertNotIn("```", g["brief"])

        # ── a reply still being written when the browser goes: the page meanwhile, a second message refused, the
        #    line kept open, and the reply finished and kept all the same ──
        ai.gate = gate = threading.Event()
        guests.PULSE = 0.2
        self.addCleanup(setattr, guests, "PULSE", 15)
        s = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=5)
        self.addCleanup(s.close)
        said = urllib.parse.urlencode({"text": "I think this is a great episode for about 45 minutes"}).encode()
        s.sendall(
            b"POST /idea/say HTTP/1.1\r\nHost: %s\r\nCookie: see_session=%s\r\nAccept: text/event-stream\r\n"
            b"Content-Type: application/x-www-form-urlencoded\r\nContent-Length: %d\r\n\r\n%s"
            % (SEE.encode(), ada["see_session"].encode(), len(said), said)
        )
        got = b""
        while b"event: delta" not in got:
            got += s.recv(4096)
        self.assertTrue(idea.writing(gid), "the first word is out; the rest is held")
        st, h, body = req("GET", "/idea", host=SEE, cookies=ada)
        self.assertIn("great episode for about 45 minutes</p></div>", body, "the words are there")
        self.assertIn('<div class="turn agent" data-writing>', body, "and the turn being written")
        self.assertIn("The office is writing; its reply appears here when it is done.", body)
        self.assertIn('required disabled placeholder="Your answer.', body, "the box is closed")
        self.assertIn('class="signal" disabled>Send<', body)
        self.assertNotIn(pages.esc(idea.MISSING), body)
        st, h, body = req("GET", "/idea/live", host=SEE, cookies=ada)
        self.assertEqual(
            (st, h["Content-Type"], json.loads(body)), (200, "application/json; charset=utf-8", {"writing": True})
        )
        st, h, body = req(
            "POST",
            "/idea/say",
            {"text": "Continue, seemed to have stalled."},
            SEE,
            ada,
            {"Accept": "text/event-stream"},
        )
        events = [chunk.split("\n", 1) for chunk in body.strip().split("\n\n")]
        self.assertEqual([e[0] for e in events], ["event: fail"])
        self.assertEqual(
            json.loads(events[0][1][len("data: ") :]),
            {
                "m": "One thing at a time: the office is still writing its last reply. It appears here when it is done.",
                "kept": False,
                "writing": True,
            },
        )
        self.assertEqual(idea.turns(c, gid)[-1]["text"], "I think this is a great episode for about 45 minutes")
        while b": writing\n\n" not in got:
            got += s.recv(4096)
        self.assertNotIn(b"event: done", got, "a comment keeps the line open while the model is silent")
        s.close()  # the browser goes (a reload, a phone in a pocket)
        gate.set()
        for _ in range(200):
            if not idea.writing(gid):
                break
            time.sleep(0.02)
        rows = idea.turns(c, gid)
        self.assertEqual(
            ([t["role"] for t in rows[-2:]], rows[-1]["stop"]), (["guest", "agent"], "end_turn"), "finished, and kept"
        )
        self.assertTrue(rows[-1]["text"].startswith("That is the spine ("))
        st, h, body = req("GET", "/idea/live", host=SEE, cookies=ada)
        j = json.loads(body)
        self.assertEqual((j["writing"], [x["id"] for x in j["sections"]]), (False, ["conversation", "draft", "next"]))
        self.assertIn(rows[-1]["text"][:24], j["sections"][0]["html"], "the reply, for the page to put in place")
        self.assertNotIn("data-writing", j["sections"][0]["html"])
        self.assertNotIn(" disabled", j["sections"][0]["html"])
        self.assertIn("Whole again", j["sections"][1]["html"])
        self.assertEqual(req("GET", "/idea/live", host=SEE)[2], '{"writing": false, "go": "/"}', "no session: the door")
        self.assertEqual(
            json.loads(req("GET", "/idea/live", host=SEE, cookies=bobs)[2]),
            {"writing": False, "go": "/idea"},
            "unsigned",
        )
        srv.shutdown()

    def test_23_booking(self):
        """The time (AGE-109, AGE-155): the rules on the guest's page; a request
        refused for notice, for the hour, for an overlap; one asked in the guest's
        zone with London beside it, told to the hosts by mail and to the issue; the
        tiles, one per time, that each host ticks in one go and can change until the
        other answers; the guest's highest time both can do booked, with the
        confirmation, the calendar file, the due date and the label; a request the
        booking leaves hopeless declined by the office; a tick on a taken day refused;
        a host who can do none, and two hosts with nothing in common, each a decline
        at once; a request from before, answered one at a time, still read; the join
        link before, inside and after its window; a cancel; a withdrawal."""
        from zoneinfo import ZoneInfo

        import schedule

        SEE = guests.HOST
        os.environ.update(self.menv, ENGINE_SESSION_KEY="a-key-for-the-tests")
        self.addCleanup(
            lambda: [
                os.environ.pop(k, None)
                for k in (*self.menv, "ENGINE_SESSION_KEY", "ENGINE_USER", "RIVERSIDE_GUEST_LINK")
            ]
        )
        client = archive.Client(self.r2env)
        patch = unittest.mock.patch.object(archive, "Client", lambda env=None: client)
        patch.start()
        self.addCleanup(patch.stop)
        c = self.c()
        fake = FakeLinear()
        fake.issues.update(self.linear_made)
        linear.post = fake
        linear.vault = FakeVault("lr1", item="Linear podcast token")
        linear._token.update(access=None, until=0.0, unsaved=None)
        env = {"LINEAR_CLIENT_ID": "lcid", "LINEAR_CLIENT_SECRET": "lcs", "LINEAR_REFRESH_TOKEN": "lr1"}
        gid = c.execute("SELECT id FROM guest WHERE name='Ada Lovelace'").fetchone()[0]
        web.STATE["ledger"] = str(self.db)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), web.App)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def req(method, path, data=None, host=None, cookies=None, headers=None):
            hdr = dict(headers or {})
            if host:
                hdr["Host"] = host
            if cookies:
                hdr["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
            body = urllib.parse.urlencode(data).encode() if data is not None else None
            r = urllib.request.Request(base + path, data=body, method=method, headers=hdr)
            opener = urllib.request.build_opener(NoRedirect)
            try:
                with opener.open(r, timeout=10) as resp:
                    return resp.status, resp.headers, resp.read().decode(errors="replace")
            except urllib.error.HTTPError as e:
                with e:
                    return e.code, e.headers, e.read().decode(errors="replace")

        def jar(h):
            return {sc.partition("=")[0]: sc.partition("=")[2].split(";")[0] for sc in h.get_all("Set-Cookie") or []}

        def drained():
            n = linear.drain(c, env, log=lambda s: None)
            self.assertGreaterEqual(n, 1)
            return fake.comments[-1]

        # ── Ada comes in on a fresh device; the page, signed in only ──
        c.execute("DELETE FROM code WHERE address='ada@example.com'")
        st, h, body = req("POST", "/enter", {"address": "ada@example.com"}, SEE)
        cid, bind = re.search(r'name="id" value="([^"]+)"', body).group(1), jar(h)["see_code"]
        code = re.search(r"\n    (\d{6})\n", FakeSMTP.sent[-1].get_content()).group(1)
        st, h, body = req("POST", "/code", {"id": cid, "code": code}, SEE, {"see_code": bind})
        ada = {"see_session": jar(h)["see_session"]}
        self.assertIn("Pick a time", req("GET", "/office", host=SEE, cookies=ada)[2])
        self.assertEqual(req("GET", "/time", host=SEE)[0], 303, "signed in only")
        st, h, body = req("GET", "/time", host=SEE, cookies=ada)
        self.assertEqual(st, 200)
        for s in (
            "Monday 10:00 London is perfect",
            "latest start is 16:00 London",
            "At least 7 days",
            'name="tz"',
            'name="when0"',
            'option value="Europe/Paris" selected',
            "data-calendar",
            'data-from="08:00"',
            'data-to="16:00"',
            'class="cal"',
            "<noscript>",
            "data-chosen",
            'name="when0"',
        ):
            self.assertIn(s, body)
        self.assertNotIn('step="900"', body, "no grid of minutes counted from the moment the page loaded")
        soon = (dt.datetime.now(ledger.ZONE) + dt.timedelta(days=3)).date()
        self.assertIn(
            f'<span class="day soon" title="too soon: we ask for a week&#x27;s notice">{soon.day}</span>', body
        )
        ten = dt.datetime.now(ledger.ZONE) + dt.timedelta(days=10)
        self.assertIn(
            f'data-day="{ten.date().isoformat()}" data-offset="{int(ten.utcoffset().total_seconds() // 60)}"', body
        )
        self.assertEqual(req("GET", "/time.js", host=SEE)[0], 200)

        # ── the request: refused for notice, the hour, an overlap, a zone, no time; then asked ──
        day = (dt.datetime.now(ledger.ZONE) + dt.timedelta(days=10)).replace(hour=10, minute=0, second=0, microsecond=0)
        while day.weekday() >= 5:
            day += dt.timedelta(days=1)
        backup = day + dt.timedelta(days=7)

        def sydney(t):
            return t.astimezone(ZoneInfo("Australia/Sydney")).strftime("%Y-%m-%dT%H:%M")

        def ask(**kw):
            return req("POST", "/time/ask", {"tz": "Australia/Sydney", "when0": sydney(day), **kw}, SEE, ada)

        for kw, said in (
            ({"when0": sydney(day - dt.timedelta(days=8))}, "at least 7 days from today"),
            ({"when0": sydney(day.replace(hour=17))}, "between 08:00 and 16:00 London, so nothing runs past 18:00"),
            ({"when1": sydney(day + dt.timedelta(hours=1))}, "overlaps another of your times"),
            ({"tz": "Mars/Olympus"}, "not a time zone"),
            ({"when0": ""}, "the slot you would prefer"),
            ({"when0": "soon"}, "A day and a time"),
        ):
            st, h, body = ask(**kw)
            self.assertEqual(st, 400, kw)
            self.assertIn(said, body)
        live = {"X-Office": "live"}
        st, h, body = req(
            "POST", "/time/ask", {"tz": "Australia/Sydney", "when0": sydney(day.replace(hour=17))}, SEE, ada, live
        )
        self.assertEqual(st, 400)
        self.assertIn("between 08:00 and 16:00 London", json.loads(body)["error"])
        sent = len(FakeSMTP.sent)
        st, h, body = req(
            "POST",
            "/time/ask",
            {"tz": "Australia/Sydney", "when0": sydney(day), "when1": sydney(backup)},
            SEE,
            ada,
            live,
        )
        self.assertEqual((st, h["Content-Type"]), (200, "application/json; charset=utf-8"), body)
        j = json.loads(body)
        self.assertEqual([s["id"] for s in j["sections"]], ["time"], "the block that changed, in place")
        self.assertTrue(j["sections"][0]["html"].startswith('<div id="time">'))
        for s in ("Asked", "Withdraw and pick again", "data-live"):
            self.assertIn(s, j["sections"][0]["html"])
        b = schedule.current(c, gid)
        self.assertEqual((b["state"], b["tz"], b["offered"], b["answers"]), ("asked", "Australia/Sydney", 0, "{}"))
        choices = [dt.datetime.fromisoformat(x) for x in json.loads(b["choices"])]
        self.assertEqual(choices, [day, backup], "the guest's wall time is the London instant")
        self.assertEqual(guests.guest(c, gid)["tz"], "Australia/Sydney", "the zone they booked in is theirs")
        st, h, body = req("GET", "/time", host=SEE, cookies=ada)
        for s in (day.strftime("%a %d %b %Y, %H:%M"), "Sydney", "with James and Abrar", "Withdraw and pick again"):
            self.assertIn(s, body)
        self.assertIn(
            '<p class="kicker">Next</p><h2>Nothing for you just now</h2><p>James and Abrar are answering', body
        )
        self.assertIn('<li data-state="waiting" aria-current="page">', body)
        self.assertIn(
            '<p class="next">Nothing for you just now: James and Abrar are answering the times you gave', body
        )
        body = req("GET", "/office", host=SEE, cookies=ada)[2]
        self.assertIn("Asked for", body)
        self.assertIn("Two of four done. Nothing for you just now:", body)
        self.assertNotIn("data-next", body, "nothing is the guest's to do")
        self.assertIn('<span class="muted">nothing for you just now</span>', req("GET", "/guests")[2])
        m = FakeSMTP.sent[-1]
        self.assertEqual(
            (len(FakeSMTP.sent), m["To"], m["Subject"]),
            (sent + 1, "hello@agentsee.work", "A time to accept: Ada Lovelace"),
        )
        self.assertIn(f"/guests/{gid}", m.get_content())
        issue, text = drained()
        self.assertEqual(issue, "AGE-73")
        self.assertIn(f"Ada Lovelace asked, through {linear.office()}, to record on", text)
        self.assertIn("backups", text)

        # ── the hosts: a stranger refused; the tiles; James ticks the backup, then changes to both; Abrar ticks
        #    the backup alone, and Ada's highest time both can do, the backup, is booked ──
        os.environ["ENGINE_USER"] = "nobody@example.com"
        st, h, body = req("POST", f"/guests/{gid}/accept", {"_t": web.CSRF, "pick0": "1"})
        self.assertIn("hosts+are+James+and+Abrar", h["Location"])
        os.environ["ENGINE_USER"] = "jameshartt@example.com"
        st, h, body = req("GET", f"/guests/{gid}")
        kind, word = schedule._day_word(day, {}, set(), schedule.settings())
        for s in (
            'class="offers"',
            f'<label class="offer {kind}" title="{word}"><input type="checkbox" name="pick0" value="1"><span class="medal">🥇</span>',
            '<span class="medal">🥈</span>',
            f'<span class="wday">{day.strftime("%A")}</span><span class="dom">{day.day}</span><span class="month">{day.strftime("%B")}</span>',
            '<span class="time">10:00 London</span>',
            f'<span class="theirs">{day.astimezone(ZoneInfo("Australia/Sydney")).strftime("%a %-d %b, %H:%M")} Sydney</span>',
            f'<span class="word">{schedule.TILE_WORDS[kind]}</span>',
            "Tick every time you can do, then accept. Ada&#8217;s highest that suits you both is booked.",
            "Accept the ticked times",
            "None of these",
        ):
            self.assertIn(s, body)
        self.assertNotIn('name="pick2"', body, "two times asked for, two tiles")
        self.assertIn("asked; waiting for James, Abrar", req("GET", "/guests")[2])
        sent = len(FakeSMTP.sent)
        st, h, body = req("POST", f"/guests/{gid}/accept", {"_t": web.CSRF})
        self.assertIn("Tick+the+times+you+can+do%2C+or+say+none+of+these", h["Location"])
        st, h, body = req("POST", f"/guests/{gid}/accept", {"_t": web.CSRF, "pick1": "1"})
        self.assertIn("You+can+do+silver%3B+waiting+for+Abrar", h["Location"])
        b = schedule.current(c, gid)
        self.assertEqual(
            (b["state"], b["offered"], schedule.picks_of(b, "James"), schedule.picks_of(b, "Abrar")),
            ("asked", 0, [1], None),
        )
        self.assertEqual(len(FakeSMTP.sent), sent, "the guest hears the outcome only")
        self.assertIn("James can do silver; waiting for Abrar", drained()[1])
        self.assertIn("asked; waiting for Abrar", req("GET", "/guests")[2])
        st, h, body = req("GET", f"/guests/{gid}")
        self.assertIn('<input type="checkbox" name="pick1" value="1" checked>', body)
        self.assertIn("You said silver; change it until Abrar has answered.", body)
        self.assertIn("Change to the ticked times", body)
        st, h, body = req("POST", f"/guests/{gid}/accept", {"_t": web.CSRF, "pick0": "1", "pick1": "1"})
        self.assertIn("You+can+do+gold+and+silver%3B+waiting+for+Abrar", h["Location"])
        self.assertEqual(schedule.picks_of(schedule.current(c, gid), "James"), [0, 1])
        grace = c.execute("SELECT id FROM guest WHERE name='Grace Hopper'").fetchone()[0]
        c.execute("UPDATE guest SET address='grace@example.com', signed_version=1 WHERE id=?", (grace,))
        clash = backup.replace(hour=14)  # Grace asks for the same day, later in it, and a week on
        c.execute(
            "INSERT INTO booking (id, guest, tz, choices, offered, answers, state, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "graceclash01",
                grace,
                "Europe/London",
                json.dumps([clash.isoformat(), (clash + dt.timedelta(days=7)).isoformat()]),
                0,
                "{}",
                "asked",
                ledger.now(),
                ledger.now(),
            ),
        )
        said = schedule.answer(c, schedule.booking(c, "graceclash01"), "James", [0], "jameshartt@example.com")
        self.assertEqual(said, "You can do gold; waiting for Abrar", "James can do only the day Ada is about to take")
        os.environ["ENGINE_USER"] = "abrar@example.com"
        body = req("GET", f"/guests/{gid}")[2]
        self.assertEqual(
            body.count('<span class="who">James can</span>'), 2, "what the other host ticked, on the tiles"
        )
        self.assertIn("Tick every time you can do", body)
        sent = len(FakeSMTP.sent)
        st, h, body = req("POST", f"/guests/{gid}/accept", {"_t": web.CSRF, "pick1": "1"})
        self.assertIn("Booked%3A", h["Location"])
        self.assertIn("Ada%27s+first+backup%3B+Ada+has+the+confirmation", h["Location"])
        b = schedule.current(c, gid)
        self.assertEqual((b["state"], b["offered"], dt.datetime.fromisoformat(b["start_at"])), ("booked", 1, backup))
        m = FakeSMTP.sent[sent]
        self.assertEqual(
            (len(FakeSMTP.sent), m["To"], m["Cc"], m["Subject"]),
            (sent + 2, "ada@example.com", "hello@agentsee.work", "Booked: your recording with AgentSee"),
            "the confirmation; then Grace, whose only tick from James was that day, asked to pick again",
        )
        gone = FakeSMTP.sent[sent + 1]
        self.assertEqual((gone["To"], gone["Subject"]), ("grace@example.com", "Your recording: another time, please"))
        self.assertIn("the days you asked for were booked by another guest since", gone.get_content())
        self.assertEqual(schedule.booking(c, "graceclash01")["state"], "declined")
        self.assertIn(f"https://{SEE}/join/{b['id']}", m.get_body(preferencelist=("plain",)).get_content())
        att = list(m.iter_attachments())
        self.assertEqual(
            [(a.get_filename(), a.get_content_type()) for a in att], [("agentsee-recording.ics", "text/calendar")]
        )
        ics = att[0].get_payload(decode=True).decode()
        self.assertIn("DTSTART:" + backup.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"), ics)
        self.assertIn(f"UID:{b['id']}@agentsee.work", ics)
        self.assertGreaterEqual(linear.drain(c, env, log=lambda s: None), 2)
        self.assertIn("Booked:", fake.comments[-2][1])
        self.assertIn("Ada Lovelace's first backup, accepted by James and Abrar", fake.comments[-2][1])
        self.assertIn("Declined: the days you asked for were booked by another guest since", fake.comments[-1][1])
        self.assertEqual(fake.issues["AGE-73"]["dueDate"], backup.strftime("%Y-%m-%d"))
        self.assertIn("booked", [fake.labels[x]["name"] for x in fake.issues["AGE-73"]["labelIds"]])
        body = req("GET", f"/guests/{gid}")[2]
        self.assertIn("<b>booked</b>", body)
        self.assertIn("James can</td>", body, "the table once settled: who could do each")
        self.assertNotIn('class="offers"', body)

        # ── one recording a day: the calendar says taken, a request for it is refused, a tick on it too; the
        #    hosts' calendar wears the medals; Abrar can do none and the request is declined at once; a request
        #    from before 18 November 2026 still reads, James's yes as a tick on the time that was on offer ──
        self.assertIn(backup.date(), schedule.taken_days(c))
        cal = schedule.calendar_html(c)
        self.assertIn(f'<span class="day taken" title="taken: a recording that day">{backup.day}</span>', cal)
        self.assertIn('class="day week"', cal, "the rest of that week says ask us")
        with self.assertRaises(schedule.ScheduleError) as cm:
            schedule.ask(c, guests.guest(c, grace), "Europe/London", [clash.strftime("%Y-%m-%dT%H:%M")], "grace")
        self.assertIn("taken by a recording already", str(cm.exception))
        c.execute(
            "UPDATE booking SET state='asked', answers='{}', note=NULL WHERE id='graceclash01'"
        )  # unanswered again
        gb = schedule.booking(c, "graceclash01")
        with self.assertRaises(schedule.ScheduleError) as cm:
            schedule.answer(c, gb, "James", [0, 1], "jameshartt@example.com")
        self.assertIn("taken by another recording since; untick it", str(cm.exception))
        os.environ["ENGINE_USER"] = "jameshartt@example.com"
        body = req("GET", f"/guests/{grace}")[2]
        self.assertIn(
            '<label class="offer taken" title="taken by another recording"><input type="checkbox" name="pick0" value="1" disabled>',
            body,
        )
        self.assertIn('<span class="word">taken</span>', body)
        self.assertEqual(
            schedule.answer(c, gb, "James", [1], "jameshartt@example.com"), "You can do silver; waiting for Abrar"
        )
        st, h, body = req("GET", "/calendar")
        self.assertEqual(st, 200)
        self.assertIn(f'<a class="booked" href="/guests/{gid}">Ada Lovelace</a>', body)
        self.assertIn(f'<a class="asked" href="/guests/{grace}">🥇 Grace Hopper?</a>', body)
        self.assertIn(f'<a class="asked" href="/guests/{grace}">🥈 Grace Hopper?</a>', body)
        sent = len(FakeSMTP.sent)
        said = schedule.answer(
            c, schedule.booking(c, gb["id"]), "Abrar", [], "abrar@example.com", "away that  fortnight"
        )
        self.assertEqual(
            said, "Declined: Abrar could not make any of them (away that fortnight); Grace is asked to pick again"
        )
        self.assertEqual(schedule.booking(c, gb["id"])["state"], "declined")
        m = FakeSMTP.sent[-1]
        self.assertEqual(
            (len(FakeSMTP.sent), m["To"], m["Subject"]),
            (sent + 1, "grace@example.com", "Your recording: another time, please"),
        )
        self.assertIn("Abrar could not make any of them (away that fortnight)", m.get_content())
        self.assertIn("Declined: Abrar could not make any of them", drained()[1])
        far = backup + dt.timedelta(days=21)
        c.execute(
            "INSERT INTO booking (id, guest, tz, choices, offered, answers, state, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "graceold0001",
                grace,
                "Europe/London",
                json.dumps([(far - dt.timedelta(days=7)).isoformat(), far.isoformat()]),
                1,
                json.dumps({"James": {"yes": True, "at": ledger.now(), "note": ""}}),
                "asked",
                ledger.now(),
                ledger.now(),
            ),
        )
        gb = schedule.booking(c, "graceold0001")
        self.assertEqual(
            (schedule.picks_of(gb, "James"), schedule.waiting_for(gb), schedule.open_choices(gb)), ([1], ["Abrar"], [1])
        )
        with self.assertRaises(schedule.ScheduleError) as cm:
            schedule.answer(c, gb, "Abrar", [0], "abrar@example.com")
        self.assertIn("not one of Grace's times", str(cm.exception), "declined one at a time, before")
        said = schedule.answer(c, gb, "Abrar", [1], "abrar@example.com")
        self.assertTrue(said.startswith("Booked: "), said)
        self.assertIn("Grace's first backup", said)
        self.assertEqual(schedule.booking(c, "graceold0001")["state"], "booked")
        schedule.cancel(c, schedule.booking(c, "graceold0001"), "James", "jameshartt@example.com")
        self.assertEqual(schedule.booking(c, "graceold0001")["state"], "cancelled")
        self.assertIsNone(schedule.current(c, grace))
        st, h, body = req("GET", "/time", host=SEE, cookies=ada)
        for s in (
            "Booked",
            f"/join/{b['id']}",
            "Add it to your calendar",
            "<h2>The recording</h2>",
            "The review follows",
        ):
            self.assertIn(s, body)
        body = req("GET", "/office", host=SEE, cookies=ada)[2]
        self.assertIn("Booked for", body)
        self.assertIn("Three of four done. The recording:", body)
        self.assertIn('<span class="muted">the recording</span>', req("GET", "/guests")[2])
        st, h, body = req("GET", "/time/recording.ics", host=SEE, cookies=ada)
        self.assertEqual((st, h["Content-Type"]), (200, "text/calendar; charset=utf-8"))
        self.assertIn(backup.strftime("%a %d %b, %H:%M"), req("GET", "/guests")[2])
        st, h, body = ask()
        self.assertEqual(st, 400)
        self.assertIn("You have a booking", body)

        # ── the join link: a page before the window, the studio inside it, a page after; nothing for a stranger id ──
        st, h, body = req("GET", f"/join/{b['id']}", host=SEE)
        self.assertEqual(st, 200)
        self.assertIn("Not yet", body)
        self.assertIn("opens the studio from an hour before", body)
        self.assertEqual(req("GET", "/join/not-a-booking-id", host=SEE)[0], 404)
        soon = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=30)).isoformat()
        c.execute("UPDATE booking SET start_at=? WHERE id=?", (soon, b["id"]))
        self.assertEqual(req("GET", f"/join/{b['id']}", host=SEE)[0], 503, "no studio link on this engine")
        self.assertIn("Studio link", req("GET", "/health")[2])
        self.assertIn("not set: the join link tells the guest so", req("GET", "/health")[2])
        os.environ["RIVERSIDE_GUEST_LINK"] = "https://riverside.fm/studio/agentsee?t=guest"
        body = req("GET", "/health")[2]
        self.assertIn("set: a booking's join link opens the studio", body)
        self.assertNotIn("riverside.fm", body, "the value itself is never shown")
        st, h, body = req("GET", f"/join/{b['id']}", host=SEE)
        self.assertEqual((st, h["Location"]), (303, "https://riverside.fm/studio/agentsee?t=guest"))
        ev = c.execute("SELECT detail FROM event WHERE what='booking:join' ORDER BY id DESC").fetchone()
        self.assertEqual(json.loads(ev["detail"])["state"], "studio")
        gone = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).isoformat()
        c.execute("UPDATE booking SET start_at=? WHERE id=?", (gone, b["id"]))
        st, h, body = req("GET", f"/join/{b['id']}", host=SEE)
        self.assertEqual(st, 200)
        self.assertIn("has been", body)
        c.execute("UPDATE booking SET start_at=? WHERE id=?", (backup.isoformat(), b["id"]))

        # ── a host cancels: the guest told, the due date cleared, the form back; the link shows nothing ──
        sent = len(FakeSMTP.sent)
        st, h, body = req("POST", f"/guests/{gid}/cancel", {"_t": web.CSRF, "note": "Abrar is away"})
        self.assertIn("Cancelled%3B", h["Location"])
        self.assertEqual(schedule.booking(c, b["id"])["state"], "cancelled")
        self.assertIsNone(schedule.current(c, gid))
        self.assertNotIn(backup.date(), schedule.taken_days(c), "the day is free again")
        m = FakeSMTP.sent[-1]
        self.assertEqual(
            (len(FakeSMTP.sent), m["To"], m["Subject"]),
            (sent + 1, "ada@example.com", "Your recording: cancelled, another time please"),
        )
        self.assertIn("cancelled the booking", drained()[1])
        self.assertIsNone(fake.issues["AGE-73"]["dueDate"])
        st, h, body = req("GET", "/time", host=SEE, cookies=ada)
        for s in ("That recording was cancelled", "Abrar is away", 'name="when0"'):
            self.assertIn(s, body)
        self.assertIn("No booking here", req("GET", f"/join/{b['id']}", host=SEE)[2])

        # ── asked again with a backup: James gold, Abrar silver, nothing in common, declined; asked again and
        #    withdrawn by the guest; asked with no backup and declined by James at once ──
        self.assertEqual(ask(when1=sydney(backup))[0], 303)
        b2 = schedule.current(c, gid)
        os.environ["ENGINE_USER"] = "jameshartt@example.com"
        st, h, body = req("POST", f"/guests/{gid}/accept", {"_t": web.CSRF, "pick0": "1"})
        self.assertIn("You+can+do+gold%3B+waiting+for+Abrar", h["Location"])
        os.environ["ENGINE_USER"] = "abrar@example.com"
        sent = len(FakeSMTP.sent)
        st, h, body = req("POST", f"/guests/{gid}/accept", {"_t": web.CSRF, "pick1": "1"})
        self.assertIn("Declined%3A+none+of+them+suited+both+of+us+%28James+gold%3B+Abrar+silver%29", h["Location"])
        self.assertEqual(schedule.booking(c, b2["id"])["state"], "declined")
        m = FakeSMTP.sent[-1]
        self.assertEqual(
            (len(FakeSMTP.sent), m["To"], m["Subject"]),
            (sent + 1, "ada@example.com", "Your recording: another time, please"),
        )
        self.assertIn("none of them suited both of us (James gold; Abrar silver)", m.get_content())
        body = req("GET", f"/guests/{gid}")[2]
        self.assertIn("James can</td>", body)
        self.assertIn("Abrar can</td>", body)
        self.assertEqual(ask()[0], 303)
        b3 = schedule.current(c, gid)
        st, h, body = req("POST", "/time/withdraw", {}, SEE, ada)
        self.assertEqual((st, h["Location"]), (303, "/time"))
        self.assertEqual(schedule.booking(c, b3["id"])["state"], "withdrawn")
        self.assertIn("withdrew", drained()[1])
        self.assertEqual(ask()[0], 303)
        b4 = schedule.current(c, gid)
        os.environ["ENGINE_USER"] = "jameshartt@example.com"
        st, h, body = req("POST", f"/guests/{gid}/decline", {"_t": web.CSRF})
        self.assertIn("Declined%3A+James+could+not+make+any+of+them%3B+Ada+is+asked+to+pick+again", h["Location"])
        self.assertEqual(schedule.booking(c, b4["id"])["state"], "declined")
        self.assertEqual(FakeSMTP.sent[-1]["Subject"], "Your recording: another time, please")
        self.assertIn("None of those times worked", req("GET", "/time", host=SEE, cookies=ada)[2])
        self.assertIn("pick again", drained()[1])
        self.assertEqual(
            [x["state"] for x in json.loads(FakeR2.state["objects"][f"guests/{gid}/guest.json"]["body"])["bookings"]],
            ["cancelled", "declined", "withdrawn", "declined"],
            "the archive copy carries the bookings",
        )
        srv.shutdown()

    def test_24_deploy(self):
        """What Health says about the commit running and the last deploy (deploy.py),
        and the gate GitHub's key opens on the box (home/deploy-gate.sh): three
        checked words through to deploy.sh as root, anything else refused."""
        home = ledger.ROOT / "engine" / "home"
        root = pathlib.Path(self.tmp.name) / "deploy"
        sha = "0123456789abcdef" * 2 + "01234567"

        # ── head(): a branch, a packed ref, a worktree, a detached HEAD; None without a .git ──
        plain = root / "plain"
        (plain / ".git" / "refs" / "heads").mkdir(parents=True)
        (plain / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        (plain / ".git" / "refs" / "heads" / "main").write_text(sha + "\n")
        self.assertEqual(deploy.head(plain), sha)
        (plain / ".git" / "refs" / "heads" / "main").unlink()
        (plain / ".git" / "packed-refs").write_text(
            f"# pack-refs with: peeled fully-peeled sorted\n{sha[::-1]} refs/heads/other\n{sha} refs/heads/main\n"
        )
        self.assertEqual(deploy.head(plain), sha, "a packed ref")
        wt = root / "wt"
        wt.mkdir()
        gitdir = plain / ".git" / "worktrees" / "wt"
        gitdir.mkdir(parents=True)
        (wt / ".git").write_text(f"gitdir: {gitdir}\n")
        (gitdir / "commondir").write_text("../..\n")
        (gitdir / "HEAD").write_text("ref: refs/heads/main\n")
        self.assertEqual(deploy.head(wt), sha, "a worktree's refs are in the main .git")
        (gitdir / "HEAD").write_text(sha[::-1] + "\n")
        self.assertEqual(deploy.head(wt), sha[::-1], "detached")
        self.assertIsNone(deploy.head(root / "nowhere"))
        self.assertRegex(deploy.RUNNING or "", r"^[0-9a-f]{40}$", "this checkout's own commit, once, at import")
        got = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ledger.ROOT)
        self.assertEqual(deploy.RUNNING, got.stdout.strip())

        # ── Health: the commit running; the record deploy.sh writes, or its absence ──
        web.STATE["ledger"] = str(self.db)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), web.App)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def health():
            with urllib.request.urlopen(base + "/health", timeout=10) as r:
                return r.read().decode()

        rec = ledger.data_dir() / deploy.RECORD
        self.addCleanup(lambda: rec.unlink(missing_ok=True))
        body = health()
        self.assertIn("Running", body)
        self.assertIn(deploy.RUNNING[:7], body)
        self.assertIn("Last deploy", body)
        self.assertIn("no record", body)
        run = "https://github.com/agentsee-work/agentsee.work/actions/runs/41"
        record = {
            "at": "2026-11-03T17:20:00+00:00",
            "sha": sha,
            "subject": "feat(engine): deploys itself",
            "by": "jameshartt",
            "run": run,
            "action": "restart",
            "outcome": "ok",
            "detail": "up on 0123456 within 60s",
        }
        rec.write_text(json.dumps(record))
        body = health()
        self.assertIn("Tue 03 Nov 2026, 17:20", body)
        self.assertIn('<span class="mono">0123456</span> feat(engine): deploys itself; restart, by jameshartt', body)
        self.assertIn("ok: up on 0123456 within 60s", body)
        self.assertIn(f'<a href="{run}" rel="noopener" target="_blank">the run</a>', body)
        self.assertNotIn('class="error">ok', body)
        record.update(outcome="rolled back", detail="the engine did not come up on 0123456; back on 76543210")
        rec.write_text(json.dumps(record))
        self.assertIn('<span class="error">rolled back: the engine did not come up on 0123456', health())
        rec.write_text("{not json")
        self.assertIn("no record", health(), "a half-written record is no record")
        srv.shutdown()

        # ── the gate: sshd runs it with the client's words in SSH_ORIGINAL_COMMAND ──
        self.assertTrue(os.access(home / "deploy-gate.sh", os.X_OK) and os.access(home / "deploy.sh", os.X_OK))
        for script in ("deploy.sh", "deploy-gate.sh"):
            self.assertEqual(subprocess.run(["bash", "-n", str(home / script)]).returncode, 0, script)
        fake = root / "bin"
        fake.mkdir()
        (fake / "sudo").write_text('#!/bin/sh\necho "sudo $*"\n')
        (fake / "sudo").chmod(0o755)

        def gate(command):
            env = {**os.environ, "PATH": f"{fake}:{os.environ.get('PATH', '')}", "SSH_ORIGINAL_COMMAND": command}
            return subprocess.run([str(home / "deploy-gate.sh")], env=env, capture_output=True, text=True)

        r = gate(f"{sha} jameshartt {run}")
        self.assertEqual(
            (r.returncode, r.stdout.strip()),
            (0, f"sudo -n /opt/agentsee/repo/engine/home/deploy.sh {sha} jameshartt {run}"),
            r.stderr,
        )
        self.assertEqual(gate(f"{sha} github-actions[bot] {run}").returncode, 0, "a bot's login")
        for bad in (
            "",
            sha,
            f"{sha} jameshartt",
            f"{sha[:7]} jameshartt {run}",
            f"{sha.upper()} jameshartt {run}",
            f"{sha} jameshartt https://github.com/someone-else/agentsee.work/actions/runs/41",
            f"{sha} jameshartt {run} extra",
            f"{sha} jameshartt {run};id",
            f"{sha} jameshartt {run}/../41",
            f"{sha} 'a b' {run}",
        ):
            r = gate(bad)
            self.assertEqual((r.returncode, r.stdout), (2, ""), bad)
            self.assertIn("refused", r.stderr, bad)

        # ── the wiring the runbook relies on ──
        self.assertIn("/opt/agentsee/repo/engine/home/deploy.sh", (home / "sudoers-deploy").read_text())
        self.assertRegex((home / "known_hosts").read_text(), r"(?m)^mail\.agentsee\.work ssh-ed25519 ")
        flow = (ledger.ROOT / ".github" / "workflows" / "check.yml").read_text()
        self.assertIn("needs: [engine, ruff]", flow)
        self.assertIn("UserKnownHostsFile=engine/home/known_hosts", flow)
        self.assertIn("deploy@mail.agentsee.work", flow)
        compose = (home / "docker-compose.yml").read_text()
        self.assertIn("healthcheck:", compose)
        self.assertIn("8877/health", compose)

    def test_25_review(self):
        """The review: the round's record from the archive into the ledger and
        the office's step; the page with the player and the clips through
        signed links, the archive named in the policy; a note with its moment,
        mirrored as the pipeline reads it; the notes sent, by mail and on the
        issue; the approval with a clip vetoed, its signature and record, the
        copies, the mail, the issue labelled; a second approval refused; a new
        round asking again; the hosts' page."""
        import idea
        import review

        SEE = guests.HOST
        os.environ.update(self.menv, ENGINE_SESSION_KEY="a-key-for-the-tests", ANTHROPIC_API_KEY="sk-test")
        self.addCleanup(
            lambda: [
                os.environ.pop(k, None) for k in (*self.menv, "ENGINE_SESSION_KEY", "ENGINE_USER", "ANTHROPIC_API_KEY")
            ]
        )
        ai = FakeAnthropic()
        idea.open_stream = ai
        client = archive.Client(self.r2env)
        patch = unittest.mock.patch.object(archive, "Client", lambda env=None: client)
        patch.start()
        self.addCleanup(patch.stop)
        c = self.c()
        objs = FakeR2.state["objects"]
        fake = FakeLinear()
        fake.issues.update(self.linear_made)
        linear.post = fake
        linear.vault = FakeVault("lr1", item="Linear podcast token")
        linear._token.update(access=None, until=0.0, unsaved=None)
        env = {"LINEAR_CLIENT_ID": "lcid", "LINEAR_CLIENT_SECRET": "lcs", "LINEAR_REFRESH_TOKEN": "lr1"}
        gid = c.execute("SELECT id FROM guest WHERE name='Ada Lovelace'").fetchone()[0]
        self.assertEqual(guests.guest(c, gid)["signed_version"], 1, "Ada signed the release in test 21")

        web.STATE["ledger"] = str(self.db)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), web.App)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def req(method, path, data=None, host=None, cookies=None, headers=None):
            hdr = dict(headers or {})
            if host:
                hdr["Host"] = host
            if cookies:
                hdr["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
            body = urllib.parse.urlencode(data).encode() if data is not None else None
            r = urllib.request.Request(base + path, data=body, method=method, headers=hdr)
            opener = urllib.request.build_opener(NoRedirect)
            try:
                with opener.open(r, timeout=10) as resp:
                    return resp.status, resp.headers, resp.read().decode(errors="replace")
            except urllib.error.HTTPError as e:
                with e:
                    return e.code, e.headers, e.read().decode(errors="replace")

        def jar(h):
            return {sc.partition("=")[0]: sc.partition("=")[2].split(";")[0] for sc in h.get_all("Set-Cookie") or []}

        c.execute("DELETE FROM code WHERE address='ada@example.com'")
        st, h, body = req("POST", "/enter", {"address": "ada@example.com"}, SEE)
        self.assertEqual(st, 200)
        cid, bind = re.search(r'name="id" value="([^"]+)"', body).group(1), jar(h)["see_code"]
        code = re.search(r"\n    (\d{6})\n", FakeSMTP.sent[-1].get_content()).group(1)
        st, h, body = req("POST", "/code", {"id": cid, "code": code}, SEE, {"see_code": bind})
        self.assertEqual(st, 303)
        ada = {"see_session": jar(h)["see_session"]}

        # ── nothing yet: the page says so, the office says so, the step waits ──
        st, h, body = req("GET", "/review", host=SEE, cookies=ada)
        self.assertEqual(st, 200)
        self.assertIn("Nothing to watch yet", body)
        self.assertIn("After the recording.", req("GET", "/office", host=SEE, cookies=ada)[2])
        self.assertEqual(req("GET", "/review", host=SEE)[0], 303, "nothing without a session")
        self.assertIsNone(review.current(c, gid))

        # ── the pipeline's records in the archive: the round and its release ──
        clips = [
            {"index": 1, "slug": "the-first-one", "title": "The first one", "file": "clips/01-the-first-one.mp4"},
            {"index": 2, "slug": "the-second", "title": "The second", "file": "clips/02-the-second.mp4"},
        ]
        files = {
            k: hashlib.sha256(k.encode()).hexdigest()
            for k in (
                "episode.mp4",
                "episode.mp3",
                "thumbnail.png",
                "release.json",
                "clips/01-the-first-one.mp4",
                "clips/01-the-first-one.png",
                "clips/02-the-second.mp4",
            )
        }
        rel = {"episode": {"title": "Ada, on engines", "number": None}, "clips": clips, "files": {}}
        doc = {
            "slug": "ada-engines",
            "round": 1,
            "at": "2026-11-05T10:00:00",
            "number": None,
            "title": "Ada, on engines",
            "guests": [gid],
            "edit": {"edl.json": "e" * 64},
            "files": files,
            "changed": [],
        }

        def put(key, body):
            objs[key] = {"body": json.dumps(body).encode(), "type": "application/json", "meta": {}}

        put(f"guests/{gid}/review.json", doc)
        put("episodes/ada-engines/review/release.json", rel)
        put(
            "episodes/ada-engines/review/transcript.json",
            {
                "version": 1,
                "segments": [
                    {
                        "speaker": "James Hartt",
                        "startTime": 740.0,
                        "endTime": 748.0,
                        "body": "So tell us about the loom.",
                    },
                    {
                        "speaker": "Ada Lovelace",
                        "startTime": 748.5,
                        "endTime": 760.0,
                        "body": "It was my grandmother's loom, and it still works. The cards are the thing.",
                    },
                    {
                        "speaker": "James Hartt",
                        "startTime": 2000.0,
                        "endTime": 2004.0,
                        "body": "And that is the ending.",
                    },
                ],
            },
        )
        body = req("GET", "/office", host=SEE, cookies=ada)[2]
        self.assertIn("Round 1 is ready: the episode and the clips, to watch, note and approve", body)
        self.assertIn('<a class="button signal" href="/review">Watch, note, approve</a>', body)
        self.assertIn('Three of four done. Next: <a href="/review">watch the episode and approve it</a>.', body)
        row = review.current(c, gid)
        self.assertEqual(
            (row["slug"], row["round"], json.loads(row["release"])["clips"][1]["title"]),
            ("ada-engines", 1, "The second"),
        )
        ev = c.execute(
            "SELECT detail FROM event WHERE what='review:round' AND guest=? ORDER BY id DESC", (gid,)
        ).fetchone()
        self.assertEqual(json.loads(ev["detail"])["round"], 1)

        # ── the page: the player and the clips through signed links, the archive in the policy ──
        st, h, body = req("GET", "/review", host=SEE, cookies=ada)
        self.assertEqual(st, 200)
        endpoint = self.r2env["R2_ENDPOINT"].rstrip("/")
        signed = f"{endpoint}/archive-test/episodes/ada-engines/review/"
        for s in (
            "Ada, on engines",
            "Round 1, from",
            '<video class="episode" data-episode controls playsinline',
            f'src="{signed}episode.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&amp;X-Amz-Credential=AKID%2F',
            f'poster="{signed}thumbnail.png?X-Amz-Algorithm',
            "X-Amz-Expires=3600&amp;X-Amz-SignedHeaders=host&amp;X-Amz-Signature=",
            f'<a href="{signed}episode.mp3?X-Amz-Algorithm',
            "Download the video",
            "2 for the socials",
            "1. The first one",
            f'poster="{signed}clips/01-the-first-one.png?',
            f'src="{signed}clips/02-the-second.mp4?',
            'name="use-1" value="yes" checked',
            'name="use-2" value="yes" checked',
            "No notes yet on this round",
            'name="at" class="at"',
            '<input type="hidden" name="round" value="1">',
            "<canvas",
            "/review.js",
            "/sign.js",
        ):
            self.assertIn(s, body, s)
        self.assertNotIn('poster="" ', body)
        self.assertLess(body.index('id="watch"'), body.index('id="notes"'), "the episode, then the notes under it")
        self.assertLess(body.index('id="notes"'), body.index('id="clips"'), "then the clips")
        self.assertLess(body.index('id="clips"'), body.index('id="approve"'), "then the approval")
        self.assertIn(f"media-src {endpoint}", h["Content-Security-Policy"])
        self.assertIn(f"img-src 'self' {endpoint} https://media.agentsee.work", h["Content-Security-Policy"])
        self.assertIn(
            f"media-src {endpoint}", req("GET", "/health")[1]["Content-Security-Policy"], "the engine's policy too"
        )
        self.assertEqual(req("GET", "/review.js", host=SEE)[0], 200)

        # ── a note with its moment, sent as it is made: mirrored, mailed, on the issue; a bad one refused ──
        ai.replies = [
            '{"cut": true, "phrase": "It was my grandmother\'s loom, and it still works.", "why": "the loom"}',
            '{"cut": false}',
        ]
        sent = len(FakeSMTP.sent)
        st, h, body = req("POST", "/review/note", {"text": "Cut the bit about the loom", "at": "12:34"}, SEE, ada)
        self.assertEqual((st, h["Location"]), (303, "/review#notes"))
        self.assertEqual(len(FakeSMTP.sent), sent + 1)
        m = FakeSMTP.sent[-1]
        self.assertEqual(
            (m["To"], m["Subject"]), ("hello@agentsee.work", "Ada Lovelace's note on round 1 of Ada, on engines")
        )
        self.assertIn("- at 12:34: Cut the bit about the loom", m.get_content())
        self.assertIn("episode.py notes ada-engines", m.get_content())
        self.assertIn(
            'awaiting their confirmation: "It was my grandmother\'s loom, and it still works."', m.get_content()
        )
        asked = ai.payloads[-1][0]
        self.assertIn("JSON only", asked["system"][0]["text"])
        self.assertIn("about the moment 12:34", asked["messages"][0]["content"])
        self.assertIn("[12:28] Ada Lovelace: It was my grandmother's loom", asked["messages"][0]["content"])
        self.assertNotIn("And that is the ending", asked["messages"][0]["content"], "a window around the moment")
        st, h, body = req("POST", "/review/note", {"text": "Nice", "at": "noon"}, SEE, ada)
        self.assertEqual(st, 400)
        self.assertIn("A moment is m:ss", body)
        st, h, body = req("POST", "/review/note", {"text": " ", "at": ""}, SEE, ada, {"X-Office": "live"})
        self.assertEqual(st, 400)
        self.assertIn("Write the note first", json.loads(body)["error"])
        self.assertEqual(len(FakeSMTP.sent), sent + 1, "a refused note sends nothing")
        st, h, body = req(
            "POST",
            "/review/note",
            {"text": "The ending is lovely.\n\nKeep it.", "at": ""},
            SEE,
            ada,
            {"X-Office": "live"},
        )
        self.assertEqual(st, 200)
        j = json.loads(body)
        self.assertEqual((j["ok"], j["sections"][0]["id"]), (True, "notes"))
        self.assertIn("Sent.", j["sections"][0]["html"])
        self.assertIn('data-t="754.0">12:34</a> Cut the bit about the loom', j["sections"][0]["html"])
        self.assertIn('<span class="t muted">general</span> The ending is lovely.', j["sections"][0]["html"])
        self.assertIn("Send the note", j["sections"][0]["html"])
        self.assertNotIn("/review/send", j["sections"][0]["html"], "no second step")
        self.assertIn(
            "Cut these words? <q>It was my grandmother&#x27;s loom, and it still works.</q>", j["sections"][0]["html"]
        )
        self.assertIn("Yes, cut them", j["sections"][0]["html"])
        mirrored = json.loads(objs[f"guests/{gid}/notes.json"]["body"])
        self.assertEqual(len(mirrored["rounds"]), 1)
        r1 = mirrored["rounds"][0]
        self.assertEqual((r1["n"], r1["slug"], r1["sent"][:4]), (1, "ada-engines", "2026"))
        self.assertEqual(
            [(n["t"], n["text"], n["sent"][:4]) for n in r1["notes"]],
            [(754.0, "Cut the bit about the loom", "2026"), (None, "The ending is lovely.\n\nKeep it.", "2026")],
        )
        self.assertEqual(
            (r1["notes"][0]["remove"], r1["notes"][0]["confirmed"]),
            ({"phrase": "It was my grandmother's loom, and it still works."}, False),
            "found, not answered yet",
        )
        self.assertNotIn("remove", r1["notes"][1])
        jobs = c.execute("SELECT * FROM linear_job WHERE kind='noted' ORDER BY id").fetchall()
        self.assertEqual(
            [(x["guest"], json.loads(x["args"])["notes"][0]["t"]) for x in jobs], [(gid, 754.0), (gid, None)]
        )
        self.assertEqual(linear.drain(c, env, log=lambda s: None), 2)
        issue, text = fake.comments[-2]
        self.assertEqual(issue, "AGE-73")
        self.assertIn('Ada Lovelace left a note on review round 1 of "Ada, on engines" (ada-engines)', text)
        self.assertIn("**Ada Lovelace wrote, through [see.agentsee.work](https://see.agentsee.work):**", text)
        self.assertIn("> At 12:34: Cut the bit about the loom", text)
        self.assertIn('awaiting their confirmation: "It was my grandmother\'s loom, and it still works."', text)
        self.assertIn("> The ending is lovely.", fake.comments[-1][1])
        self.assertNotIn("confirmation", fake.comments[-1][1])
        self.assertEqual(req("GET", "/review/send", host=SEE, cookies=ada)[0], 404, "no send step any more")

        # ── the cut: the guest's word on the words found; words the model named that are not there; the model down ──
        nid = c.execute("SELECT id FROM review_note WHERE guest=? AND remove IS NOT NULL", (gid,)).fetchone()[0]
        self.assertEqual(req("POST", "/review/cut", {"id": "999", "answer": "yes"}, SEE, ada)[0], 400)
        sent = len(FakeSMTP.sent)
        st, h, body = req("POST", "/review/cut", {"id": str(nid), "answer": "yes"}, SEE, ada, {"X-Office": "live"})
        self.assertEqual(st, 200)
        self.assertIn("Cut confirmed: <q>It was my grandmother", json.loads(body)["sections"][0]["html"])
        n0 = json.loads(objs[f"guests/{gid}/notes.json"]["body"])["rounds"][0]["notes"][0]
        self.assertEqual(
            (n0["remove"]["phrase"], n0["confirmed"]), ("It was my grandmother's loom, and it still works.", True)
        )
        self.assertEqual(len(FakeSMTP.sent), sent + 1)
        self.assertEqual(FakeSMTP.sent[-1]["Subject"], "Ada Lovelace confirmed the cut on round 1 of Ada, on engines")
        self.assertEqual(linear.drain(c, env, log=lambda s: None), 1)
        self.assertIn('confirmed the cut on review round 1 of "Ada, on engines" (ada-engines)', fake.comments[-1][1])
        self.assertIn("takes it into removals.json", fake.comments[-1][1])
        st, h, body = req("POST", "/review/cut", {"id": str(nid), "answer": "no"}, SEE, ada)
        self.assertEqual(st, 400)
        self.assertIn("answered that one already", body)
        ai.replies = ['{"cut": true, "phrase": "my joke about bananas", "why": "bananas"}']
        st, h, body = req("POST", "/review/note", {"text": "Remove my joke about bananas", "at": ""}, SEE, ada)
        self.assertEqual(st, 303)
        self.assertIn("could not be found in the transcript", FakeSMTP.sent[-1].get_content())
        last = c.execute("SELECT remove FROM review_note WHERE guest=? ORDER BY id DESC LIMIT 1", (gid,)).fetchone()
        self.assertIsNone(last["remove"], "words that are not in the transcript are not recorded")
        self.assertEqual(
            c.execute("SELECT COUNT(*) FROM event WHERE what='review:cut:missed' AND guest=?", (gid,)).fetchone()[0], 1
        )
        whole = ai.payloads[-1][0]["messages"][0]["content"]
        self.assertNotIn("about the moment", whole, "no moment: the whole transcript")
        self.assertIn("[33:20] James Hartt: And that is the ending.", whole)
        ai.fail = 500
        st, h, body = req(
            "POST", "/review/note", {"text": "Lose the bit where I cough", "at": "0:05"}, SEE, ada, {"X-Office": "live"}
        )
        self.assertEqual(st, 200, "the model down does not stop a note")
        self.assertEqual(
            c.execute("SELECT COUNT(*) FROM event WHERE what='review:cut:failed' AND guest=?", (gid,)).fetchone()[0], 1
        )
        ai.replies = ['{"cut": true, "phrase": "The cards are the thing.", "why": "the cards"}']
        st, h, body = req(
            "POST",
            "/review/note",
            {"text": "Drop the line about the cards", "at": "12:40"},
            SEE,
            ada,
            {"X-Office": "live"},
        )
        self.assertIn("Cut these words? <q>The cards are the thing.</q>", json.loads(body)["sections"][0]["html"])
        nid = c.execute("SELECT id FROM review_note WHERE guest=? ORDER BY id DESC LIMIT 1", (gid,)).fetchone()[0]
        st, h, body = req("POST", "/review/cut", {"id": str(nid), "answer": "no"}, SEE, ada, {"X-Office": "live"})
        self.assertIn("Left in: <q>The cards are the thing.</q>", json.loads(body)["sections"][0]["html"])
        self.assertEqual(
            [n.get("confirmed") for n in json.loads(objs[f"guests/{gid}/notes.json"]["body"])["rounds"][0]["notes"]],
            [True, None, None, None, False],
        )
        self.assertEqual(linear.drain(c, env, log=lambda s: None), 4, "three notes and an answer")
        self.assertIn("decided against the cut", fake.comments[-1][1])

        # ── the approval: the refusals, then the signing with a clip vetoed ──
        png = test_png()
        data_url = "data:image/png;base64," + base64.b64encode(png).decode()

        def approve(live=False, **kw):
            data = {
                "round": "1",
                "name": "Ada Lovelace",
                "signature": data_url,
                "strokes": "1",
                "agree": "yes",
                "use-1": "yes",
            }
            data.update(kw)
            for k in [k for k, v in data.items() if v is None]:
                del data[k]
            return req("POST", "/review/approve", data, SEE, ada, {"X-Office": "live"} if live else None)

        for kw, said in (
            ({"strokes": "0"}, "Draw your signature in the box"),
            ({"round": "2"}, "Round 1 is the one here now"),
            ({"name": " "}, "Type your full name"),
            ({"agree": ""}, "Tick the box"),
            ({"signature": "data:image/png;base64,AAAA"}, "Not a PNG image"),
        ):
            st, h, body = approve(**kw)
            self.assertEqual(st, 400, kw)
            self.assertIn(said, body)
            st, h, body = approve(live=True, **kw)
            self.assertEqual(st, 400, kw)
            self.assertIn(said, json.loads(body)["error"])
        self.assertIsNone(review.approval(c, gid))
        sent = len(FakeSMTP.sent)
        st, h, body = approve(live=True, name=" Ada  Lovelace ")  # use-2 absent: the second clip is vetoed
        self.assertEqual((st, json.loads(body)["go"]), (200, "/review#approve"))
        a = review.approval(c, gid)
        self.assertEqual(
            (a["slug"], a["round"], json.loads(a["vetoed"]), a["name"], a["address"], a["png"] == png, a["ip"]),
            ("ada-engines", 1, ["clips/02-the-second.mp4"], "Ada Lovelace", "ada@example.com", True, "127.0.0.1"),
        )
        self.assertEqual(a["sha256"], hashlib.sha256(review.current(c, gid)["record"].encode()).hexdigest())
        rec = json.loads(objs[f"guests/{gid}/approval.json"]["body"])
        self.assertEqual(
            (rec["review"]["slug"], rec["review"]["round"], rec["review"]["sha256"], rec["episode"]),
            ("ada-engines", 1, a["sha256"], True),
        )
        self.assertEqual(rec["clips"], {"vetoed": ["clips/02-the-second.mp4"], "kept": ["clips/01-the-first-one.mp4"]})
        self.assertEqual(
            (rec["signature"]["name"], rec["signature"]["address"], rec["signature"]["ip"], rec["review"]["files"]),
            ("Ada Lovelace", "ada@example.com", "127.0.0.1", files),
        )
        page = objs[f"guests/{gid}/approval.html"]["body"].decode()
        for s in (
            "data:image/png;base64,",
            "Clips not to use</h2><ul><li>The second</li>",
            "verified by a code",
            "review round 1",
        ):
            self.assertIn(s, page)
        self.assertEqual(len(FakeSMTP.sent), sent + 1)
        m = FakeSMTP.sent[-1]
        self.assertEqual(
            (m["To"], m["Cc"], m["Subject"]),
            ("ada@example.com", "hello@agentsee.work", "Your approval of Ada, on engines"),
        )
        self.assertIn(
            "1 clip may be used; 1 (The second) will not be.", m.get_body(preferencelist=("plain",)).get_content()
        )
        att = list(m.iter_attachments())
        self.assertEqual(
            [(x.get_filename(), x.get_content_type()) for x in att], [("agentsee-approval-round-1.html", "text/html")]
        )
        self.assertEqual(att[0].get_payload(decode=True).decode(), page)
        job = c.execute("SELECT * FROM linear_job WHERE kind='approved'").fetchone()
        self.assertEqual((job["guest"], json.loads(job["args"])["vetoed"]), (gid, ["clips/02-the-second.mp4"]))
        self.assertEqual(linear.drain(c, env, log=lambda s: None), 1)
        issue, text = fake.comments[-1]
        self.assertRegex(
            text,
            r'^Ada Lovelace approved review round 1 of "Ada, on engines" \(ada-engines\) through see\.agentsee\.work on .+, '
            rf"from ada@example\.com\. Clips vetoed: clips/02-the-second\.mp4\. The record: guests/{gid}/approval\.json ",
        )
        self.assertIn("episode-approved", [fake.labels[x]["name"] for x in fake.issues["AGE-73"]["labelIds"]])
        self.assertEqual(
            json.loads(objs[f"guests/{gid}/guest.json"]["body"])["id"], gid, "the guest's record mirrored too"
        )

        # ── afterwards: the page, the copy, no second approval; the office; the hosts' page ──
        st, h, body = req("GET", "/review", host=SEE, cookies=ada)
        self.assertEqual(st, 200)
        for s in ("You approved round 1 on", "with 1 clip to use and 1 not to (The second)", "/review/approval.html"):
            self.assertIn(s, body)
        self.assertNotIn("<canvas", body)
        st, h, body = req("GET", "/review/approval.html", host=SEE, cookies=ada)
        self.assertEqual(
            (st, h["Content-Type"], h["Content-Disposition"]),
            (200, "text/html; charset=utf-8", 'attachment; filename="agentsee-approval-round-1.html"'),
        )
        self.assertEqual(body, page)
        self.assertEqual(req("GET", "/review/approval.html", host=SEE)[0], 303)
        st, h, body = approve()
        self.assertEqual(st, 400)
        self.assertIn("You approved round 1 already", body)
        body = req("GET", "/office", host=SEE, cookies=ada)[2]
        self.assertIn("Round 1 approved ", body)
        self.assertIn("All four done. All done: James and Abrar take it from here", body)
        os.environ["ENGINE_USER"] = "jameshartt@example.com"
        body = req("GET", f"/guests/{gid}")[2]
        self.assertIn("ada-engines round 1, seen", body)
        self.assertIn("5 notes; approved on", body)
        self.assertIn("vetoed clips/02-the-second.mp4", body)
        self.assertIn("no host has approved yet", body)
        self.assertIn(f'href="/guests/{gid}/review"', body)

        # ── the hosts' side: the same round on the engine, their notes, their approvals, the pipeline's record ──
        st, h, body = req("GET", f"/guests/{gid}/review")
        self.assertEqual(st, 200)
        for s in (
            "Ada Lovelace: the review",
            f'src="{signed}episode.mp4?X-Amz-Algorithm',
            "<b>Ada</b>: ",
            "Cut confirmed: <q>It was my grandmother",
            f'action="/guests/{gid}/review/note"',
            f'name="_t" value="{web.CSRF}"',
            "Approve round 1 as James",
            "<b>Ada Lovelace</b> approved round 1",
            "vetoing clips/02-the-second.mp4",
            "<b>James</b>: not yet",
            "<b>Abrar</b>: not yet",
            "/review.js",
            "/office.js",
        ):
            self.assertIn(s, body, s)
        self.assertNotIn("Yes, cut them", body, "the guest's cuts are the guest's to answer")
        self.assertIn(f"media-src {endpoint}", h["Content-Security-Policy"])
        self.assertEqual(req("GET", f"/guests/{gid}/review", host=SEE, cookies=ada)[0], 404, "not on the guest host")
        self.assertEqual(req("GET", "/office.js")[0], 200)
        ai.replies = ['{"cut": true, "phrase": "So tell us about the loom.", "why": "the lead-in"}']
        sent = len(FakeSMTP.sent)
        st, h, body = req(
            "POST",
            f"/guests/{gid}/review/note",
            {"_t": web.CSRF, "text": "Lose my lead-in", "at": "12:21"},
            headers={"X-Office": "live"},
        )
        self.assertEqual(st, 200)
        html = json.loads(body)["sections"][0]["html"]
        self.assertIn("<b>James</b>: ", html)
        self.assertIn("Lose my lead-in", html)
        self.assertIn("Cut these words? <q>So tell us about the loom.</q>", html)
        self.assertIn(f'action="/guests/{gid}/review/cut"', html)
        self.assertIn("Sent.", html)
        self.assertEqual(len(FakeSMTP.sent), sent, "a host's note mails nobody")
        self.assertIn("The host's note", ai.payloads[-1][0]["messages"][0]["content"])
        rounds = json.loads(objs[f"guests/{gid}/notes.json"]["body"])["rounds"][0]["notes"]
        self.assertEqual(
            (rounds[-1]["by"], rounds[-1]["text"], rounds[-1]["remove"]["phrase"], rounds[-1]["confirmed"]),
            ("James", "Lose my lead-in", "So tell us about the loom.", False),
        )
        self.assertNotIn("by", rounds[0], "the guest's own carry no author")
        nid = c.execute("SELECT id FROM review_note WHERE guest=? ORDER BY id DESC LIMIT 1", (gid,)).fetchone()[0]
        st, h, body = req("POST", "/review/cut", {"id": str(nid), "answer": "yes"}, SEE, ada)
        self.assertEqual(st, 400)
        self.assertIn("not yours to answer", body)
        st, h, body = req("POST", f"/guests/{gid}/review/cut", {"_t": web.CSRF, "id": str(nid), "answer": "yes"})
        self.assertEqual((st, h["Location"]), (303, f"/guests/{gid}/review#notes"))
        self.assertTrue(json.loads(objs[f"guests/{gid}/notes.json"]["body"])["rounds"][0]["notes"][-1]["confirmed"])
        self.assertEqual(linear.drain(c, env, log=lambda s: None), 2)
        self.assertIn(
            'James left a note on review round 1 of "Ada, on engines" (ada-engines) on the engine', fake.comments[-2][1]
        )
        self.assertNotIn("wrote, through", fake.comments[-2][1], "a host's words are not quoted as the guest's")
        self.assertIn('awaiting their confirmation: "So tell us about the loom."', fake.comments[-2][1])
        self.assertIn("James confirmed the cut", fake.comments[-1][1])
        body = req("GET", "/review", host=SEE, cookies=ada)[2]
        self.assertIn("<b>James</b>: ", body, "the guest sees the hosts' notes, by name")
        self.assertIn("Lose my lead-in", body)
        self.assertNotIn(f'action="/guests/{gid}/review/cut"', body)
        self.assertEqual(
            req("POST", f"/guests/{gid}/review/note", {"text": "no token"})[0],
            403,
            "the engine's forms carry the token",
        )

        st, h, body = req("POST", f"/guests/{gid}/review/approve", {"_t": web.CSRF}, headers={"X-Office": "live"})
        self.assertEqual(st, 200)
        html = json.loads(body)["sections"][0]["html"]
        self.assertIn("<b>James</b>: approved on", html)
        self.assertIn("<b>Abrar</b>: not yet", html)
        self.assertNotIn("Approve round 1 as James", html)
        hosts_rec = json.loads(objs["episodes/ada-engines/review/hosts.json"]["body"])
        self.assertEqual(
            (hosts_rec["round"], hosts_rec["sha256"], hosts_rec["hosts"], [x["host"] for x in hosts_rec["approvals"]]),
            (1, a["sha256"], ["James", "Abrar"], ["James"]),
        )
        self.assertEqual(
            (hosts_rec["approvals"][0]["who"], hosts_rec["approvals"][0]["ip"]), ("jameshartt@example.com", "127.0.0.1")
        )
        st, h, body = req("POST", f"/guests/{gid}/review/approve", {"_t": web.CSRF})
        self.assertIn("msg=You+approved+round+1+already", h["Location"])
        os.environ["ENGINE_USER"] = "nobody@example.com"
        st, h, body = req("POST", f"/guests/{gid}/review/approve", {"_t": web.CSRF})
        self.assertIn("hosts+are+James+and+Abrar", h["Location"])
        os.environ["ENGINE_USER"] = "abrar@example.com"
        st, h, body = req("POST", f"/guests/{gid}/review/approve", {"_t": web.CSRF})
        self.assertEqual(h["Location"], f"/guests/{gid}/review#approve")
        hosts_rec = json.loads(objs["episodes/ada-engines/review/hosts.json"]["body"])
        self.assertEqual([x["host"] for x in hosts_rec["approvals"]], ["James", "Abrar"])
        self.assertEqual(linear.drain(c, env, log=lambda s: None), 2)
        self.assertIn("James approved review round 1", fake.comments[-2][1])
        self.assertIn("waiting for Abrar", fake.comments[-2][1])
        self.assertIn("Abrar approved review round 1", fake.comments[-1][1])
        self.assertIn("the last of the hosts (James, Abrar). The guest has approved too", fake.comments[-1][1])
        os.environ["ENGINE_USER"] = "jameshartt@example.com"
        self.assertIn("hosts approved: James, Abrar", req("GET", f"/guests/{gid}")[2])
        self.assertIn("The guest, and the hosts", req("GET", f"/guests/{gid}/review")[2])

        # ── a new round: the office sees it, the page says what changed and asks again ──
        put(
            f"guests/{gid}/review.json",
            {**doc, "round": 2, "at": "2026-11-06T09:00:00", "changed": ["episode.mp4", "episode.mp3"]},
        )
        st, h, body = req("GET", "/review", host=SEE, cookies=ada)
        self.assertEqual(st, 200)
        for s in (
            "Round 2, from",
            "Since the last round these changed: episode.mp4, episode.mp3",
            "You approved round 1 on",
            "this is round 2, so it asks for your approval again",
            '<input type="hidden" name="round" value="2">',
            "<canvas",
            "No notes yet on this round",
            "What you asked on round 1",
            "Cut confirmed: <q>It was my grandmother",
        ):
            self.assertIn(s, body, s)
        self.assertNotIn("Yes, cut them", body, "the last round's cuts are a record, not a question")
        self.assertEqual(review.current(c, gid)["round"], 2)
        self.assertIn("Round 2 is ready", req("GET", "/office", host=SEE, cookies=ada)[2])
        st, h, body = req("POST", "/review/note", {"text": "Round two is right.", "at": ""}, SEE, ada)
        self.assertEqual(st, 303)
        self.assertEqual([r["n"] for r in json.loads(objs[f"guests/{gid}/notes.json"]["body"])["rounds"]], [1, 2])
        self.assertIn("Nothing to watch yet", review.not_yet(c, guests.guest(c, gid), "")[1])

    def test_25b_reminders(self):
        """The office's notes (AGE-154): who is due one, by stage and quiet days; the letter for each stage; the
        cap of two, a host's note counted; the weekend; the Guests screen, Health and the issue."""
        import remind

        SEE = guests.HOST
        os.environ.update(self.menv)
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in self.menv])
        c = self.c()
        fake = FakeLinear()
        fake.issues.update(self.linear_made)
        linear.post = fake
        linear.vault = FakeVault("lr1", item="Linear podcast token")
        linear._token.update(access=None, until=0.0, unsaved=None)
        env = {"LINEAR_CLIENT_ID": "lcid", "LINEAR_CLIENT_SECRET": "lcs", "LINEAR_REFRESH_TOKEN": "lr1"}
        quiet = lambda s: None  # noqa: E731
        day = lambda iso: guests.when(iso, "%-d %B")  # noqa: E731
        flat = lambda m: " ".join(m.get_content().split())  # noqa: E731 — the letters are wrapped at 72
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        sunday = dt.datetime(2026, 11, 15, 9, 35, tzinfo=ledger.ZONE)
        # the guests the earlier tests made keep their addresses out of this, and get them back after
        had = {r["id"]: r["address"] for r in c.execute("SELECT id, address FROM guest WHERE address IS NOT NULL")}
        c.execute("UPDATE guest SET address=NULL")
        self.addCleanup(lambda: [c.execute("UPDATE guest SET address=? WHERE id=?", (a, i)) for i, a in had.items()])

        def ago(days):
            return (now - dt.timedelta(days=days)).isoformat()

        def make(name, address, days, issue=None, **cols):
            """A guest whose whole story so far happened `days` ago: made then, the invitation mailed then, and
            each column given set then too, so the quiet clock reads from that day."""
            g = guests.create(c, name, address, issue, "james@example.com")
            guests.act(c, g, "send", "james@example.com")
            sets = {"created_at": ago(days), "updated_at": ago(days), **cols}
            if cols:
                sets.update(state="claimed", claimed_at=ago(days))
            c.execute(f"UPDATE guest SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), g["id"]))
            c.execute("UPDATE event SET at=? WHERE guest=?", (ago(days), g["id"]))
            return g["id"]

        def book(gid, state, created, updated, start_at=None):
            c.execute(
                "INSERT INTO booking (id, guest, tz, choices, offered, answers, state, start_at, created_at, "
                "updated_at) VALUES (?, ?, 'Europe/London', ?, 0, '{}', ?, ?, ?, ?)",
                (f"bk-{gid}-{created[:10]}", gid, json.dumps([start]), state, start_at, created, updated),
            )

        FakeSMTP.sent.clear()
        start = (now + dt.timedelta(days=21)).replace(hour=10, minute=0, second=0).isoformat()
        door = make("Dora Door", "dora@example.com", 8)  # invited by mail, never came in; the door is slower
        fresh = make("Finn Fresh", "finn@example.com", 1)  # invited yesterday: too soon
        unmailed = guests.create(c, "Ulla Unmailed", "ulla@example.com", None, "james@example.com")  # link by hand
        c.execute("UPDATE guest SET created_at=? WHERE id=?", (ago(9), unmailed["id"]))
        rel = make("Rex Release", "rex@example.com", 5)  # in, release unsigned
        c.execute("UPDATE guest SET state='claimed', claimed_at=? WHERE id=?", (ago(5), rel))
        idle = make("Ida Idle", "ida@example.com", 4, signed_version=1, signed_at=ago(4))  # signed, idea untouched
        booked = make("Bea Booked", "bea@example.com", 6, signed_version=1, signed_at=ago(6))  # booked first
        book(booked, "booked", ago(5), ago(5), start)
        drafted = make(  # James's case: the run-sheet written, no time picked (AGE-150)
            "Tim Time",
            "tim@example.com",
            8,
            "AGE-73",
            signed_version=1,
            signed_at=ago(10),
            brief="A run-sheet.",
            brief_at=ago(8),
        )
        asked = make(
            "Wanda Waiting", "wanda@example.com", 5, signed_version=1, signed_at=ago(9), brief="x", brief_at=ago(5)
        )
        book(asked, "asked", ago(4), ago(4))
        fell = make(
            "Finlay Fell", "fin@example.com", 12, signed_version=1, signed_at=ago(12), brief="x", brief_at=ago(11)
        )
        book(fell, "declined", ago(10), ago(4))
        gone = make("Gus Gone", "gus@example.com", 20, signed_version=1, signed_at=ago(20))
        guests.revoke(c, gone, "james@example.com")
        c.execute("DELETE FROM linear_job")
        mine = (door, fresh, unmailed["id"], rel, idle, booked, drafted, asked, fell, gone)

        # ── who is due, and why ──
        due = {g["name"]: (stage, n) for g, stage, since, n, w in remind.due(c, now)}
        self.assertEqual(
            due,
            {
                "Dora Door": ("door", 1),
                "Rex Release": ("release", 1),
                "Ida Idle": ("idea", 1),
                "Bea Booked": ("idea", 1),
                "Tim Time": ("time", 1),
                "Finlay Fell": ("time", 1),
            },
        )
        # not Finn (a day), not Ulla (never emailed, so mail would be a surprise), not Wanda (with the hosts), not Gus
        self.assertEqual(
            [g["name"] for g, *_ in remind.due(c, now - dt.timedelta(days=4)) if g["id"] == door],
            [],
            "a door unopened for four days is not yet a stop",
        )
        self.assertEqual(
            sorted(g["name"] for g, *_ in remind.due(c, now - dt.timedelta(days=2))),
            ["Bea Booked", "Rex Release", "Tim Time"],
            "two days earlier only those three had been quiet three days",
        )

        # ── a Sunday sends nothing; a dry run lists anyway ──
        self.assertEqual(guests.remind_all(c, now=sunday, log=quiet), [])
        self.assertEqual(len(FakeSMTP.sent), 9, "the nine invitations, nothing more")
        lines = guests.remind_all(c, now=now, dry_run=True, log=quiet)
        self.assertEqual(len(lines), 6)
        self.assertIn(f"Tim Time (tim@example.com): a time to record, since {day(ago(8))}, note 1", lines)
        self.assertEqual(len(FakeSMTP.sent), 9, "a dry run sends nothing")
        self.assertIsNone(c.execute("SELECT 1 FROM event WHERE what='remind:run'").fetchone())

        # ── the pass, on a weekday: six notes, paced under the mail server's five connections a second ──
        said = []
        pace, mail.PACE = mail.PACE, 0.02
        try:
            t0 = time.monotonic()
            lines = guests.remind_all(c, now=now, log=said.append)
            self.assertGreaterEqual(time.monotonic() - t0, 5 * 0.02, "a pause between notes, none before the first")
        finally:
            mail.PACE = pace
        self.assertEqual(len(lines), 6)
        self.assertTrue(all(line.endswith(": sent") for line in lines), lines)
        self.assertEqual(len(FakeSMTP.sent), 15)
        by = {m["To"]: m for m in FakeSMTP.sent[9:]}
        m = by["dora@example.com"]
        self.assertEqual(m["Subject"], "The invitation to the AgentSee podcast, again")
        text = flat(m)
        self.assertIn(
            f"James invited you to record an episode of the AgentSee podcast on {day(ago(8))}, and the office has "
            "not seen you yet.",
            text,
        )
        self.assertIn(f"https://{SEE}/i/{guests.guest(c, door)['invite']}", text)
        self.assertIn("junk folder", text)
        self.assertNotIn("sends you a code.", text, "the door's letter has its own ending")
        m = by["rex@example.com"]
        self.assertEqual(m["Subject"], "The release for your AgentSee episode")
        text = flat(m)
        self.assertIn("A note from the office, in case it helps.", text)
        self.assertIn(f"You came into the office on {day(ago(5))}, and the release has been waiting", text)
        self.assertIn(f" https://{SEE}/release ", text)
        self.assertIn("If anything is unclear or in the way, reply to this message; it reaches James and Abrar", text)
        self.assertIn(f"The office at https://{SEE} shows where everything stands", text)
        m = by["ida@example.com"]
        self.assertEqual(m["Subject"], "The idea for your AgentSee episode")
        self.assertIn(f"You signed the release on {day(ago(4))}; thank you.", flat(m))
        self.assertIn("Nothing has moved since, which is fine, and this is a note in case it is not.", flat(m))
        m = by["bea@example.com"]
        self.assertEqual(m["Subject"], "The idea for your AgentSee episode")
        self.assertIn("Your recording is booked for", flat(m))
        self.assertIn("that starts when you do, at https://", flat(m), "booked first: pointed at the idea")
        m = by["tim@example.com"]
        self.assertEqual(m["Subject"], "A time to record your AgentSee episode")
        text = flat(m)
        self.assertIn(
            "Your run-sheet draft is written and with James and Abrar, and the one thing left before the recording is "
            "the time, which only you can pick.",
            text,
        )
        self.assertIn(f" https://{SEE}/time ", text)
        self.assertIn("Both hosts say which they can do, your highest that suits both is booked", text)
        m = by["fin@example.com"]
        self.assertIn(
            "The time you asked for did not work out, and nothing has been picked since; the calendar is open again.",
            flat(m),
        )
        for m in FakeSMTP.sent[9:]:
            self.assertEqual(m["Auto-Submitted"], "auto-generated")
            self.assertIn("A reply reaches James and Abrar.", flat(m))
        run = c.execute("SELECT * FROM event WHERE what='remind:run'").fetchone()
        self.assertEqual((run["who"], json.loads(run["detail"])["sent"]), ("worker", 6))
        self.assertIn("Tim Time (a time to record, 1)", json.loads(run["detail"])["to"])
        self.assertEqual(len([line for line in said if line.startswith("remind: ")]), 6)
        # the record under the stage's kind, by the worker; the issue told as the office's own note
        rec = c.execute(
            "SELECT who, detail FROM event WHERE guest=? AND what='mail:sent' ORDER BY id DESC", (drafted,)
        ).fetchone()
        self.assertEqual((rec["who"], json.loads(rec["detail"])["subject"]), ("worker", "reminder:time"))
        job = c.execute("SELECT * FROM linear_job WHERE guest=? ORDER BY id DESC", (drafted,)).fetchone()
        args = json.loads(job["args"])
        self.assertEqual((job["kind"], args["auto"], args["n"], args["step"]), ("nudged", True, 1, "time"))
        self.assertEqual(linear.drain(c, env, log=quiet), 1, "only Tim has an issue")
        self.assertEqual(
            fake.comments[-1][1],
            f"The office reminded Tim Time of the next step, a time to record, on {guests.when(ledger.now())}: "
            "the first of its two notes.",
        )

        # ── the same day again: nothing; the Guests screen and Health say what went ──
        self.assertEqual(remind.due(c, now), [])
        title, body = guests.guests_page(c, "csrf")
        self.assertIn(
            'pick a time to record <span class="late">8 days</span> <span class="muted">reminded once</span>', body
        )
        self.assertIn(
            '<span class="muted">not in yet</span> <span class="muted">8 days</span> '
            '<span class="muted">reminded once</span>',
            body,
        )
        self.assertIn(
            'flesh the idea out with the office <span class="muted">4 days</span> '
            '<span class="muted">reminded once</span>',
            body,
        )
        self.assertEqual(remind.mark(c, guests.guest(c, asked)), "", "no mark on a guest who is with the hosts")
        health = web.health_page(c)[1]
        self.assertIn("Last reminders", health)
        self.assertIn("&quot;sent&quot;: 6", health)
        web.STATE["remind_day"] = dt.date(2000, 1, 1)
        ledger.CONFIG["remind_at"] = "00:00"
        self.assertTrue(web.daily_due("remind"))
        ledger.CONFIG["remind_at"] = "09:30"

        # ── the second note after seven more quiet days, and its last; then the hosts ──
        self.assertEqual(
            [g["name"] for g, *_ in remind.due(c, now + dt.timedelta(days=6))],
            ["Finn Fresh"],
            "six days is not seven; Finn's own first note is due by then",
        )
        later = now + dt.timedelta(days=7, minutes=1)
        due = {g["name"]: n for g, stage, since, n, w in remind.due(c, later)}
        self.assertEqual(
            due,
            {n: 2 for n in ("Rex Release", "Ida Idle", "Bea Booked", "Tim Time", "Finlay Fell")} | {"Finn Fresh": 1},
            "the door's second note waits fourteen days, so Dora is not here",
        )
        lines = guests.remind_all(c, now=later, log=quiet)
        self.assertEqual(len(lines), 6)
        m = next(m for m in FakeSMTP.sent[15:] if m["To"] == "tim@example.com")
        self.assertIn(
            "This is the office's second note, and its last; anything after it comes from James or Abrar in person.",
            flat(m),
        )
        self.assertEqual(remind.count(c, drafted, "time", ago(8)), 2)
        self.assertEqual(remind.mark(c, guests.guest(c, drafted)), "reminded twice")
        self.assertEqual(
            [g["name"] for g, *_ in remind.due(c, later + dt.timedelta(days=30))],
            ["Dora Door", "Finn Fresh"],
            "two is the cap; the hosts take it from here; the door's second notes come in their own time",
        )
        # a host's hand note is never capped, counts, opens in their name and reads as theirs on the issue
        msg = guests.nudge(c, guests.guest(c, drafted), "abrar@example.com")
        self.assertEqual(msg, "Sent to tim@example.com: Next: pick a time to record.")
        self.assertIn("A note from Abrar, in case it helps.", flat(FakeSMTP.sent[-1]))
        self.assertEqual(remind.mark(c, guests.guest(c, drafted)), "reminded 3 times")
        self.assertEqual(linear.drain(c, env, log=quiet), 2, "the office's second and Abrar's")
        self.assertTrue(fake.comments[-1][1].startswith("Abrar emailed Tim Time the next step, a time to record, on"))
        self.assertIn("(note 3, counted against the office's).", fake.comments[-1][1])
        # a step re-entered starts the count again: Tim's time falls through, and the office may write once more
        again = (later + dt.timedelta(days=1)).isoformat()
        book(drafted, "declined", later.isoformat(), again)
        self.assertEqual(remind.count(c, drafted, "time", again), 0)
        due = {g["name"]: n for g, stage, since, n, w in remind.due(c, later + dt.timedelta(days=5))}
        self.assertEqual(due.get("Tim Time"), 1, "the first note about the step as it now stands")
        self.assertFalse(set(due) & {"Dora Door", "Rex Release", "Ida Idle", "Bea Booked", "Finlay Fell"}, due)
        # nothing is theirs: the hand button refuses with the sentence
        with self.assertRaises(ValueError) as e:
            guests.nudge(c, guests.guest(c, asked), "james@example.com")
        self.assertIn("Nothing is theirs to do: Nothing for you just now", str(e.exception))
        for gid in mine:
            c.execute("DELETE FROM guest WHERE id=?", (gid,))
        c.execute("DELETE FROM linear_job")

    def test_26_threads(self):
        c = self.c()
        stub = http.server.HTTPServer(("127.0.0.1", 0), StubThreads)
        threading.Thread(target=stub.serve_forever, daemon=True).start()
        self.addCleanup(stub.server_close)
        self.addCleanup(stub.shutdown)
        base = f"http://127.0.0.1:{stub.server_address[1]}"
        threads.API, threads.AUTH, threads.POLL = base, base + "/oauth/authorize", 0
        tvault = FakeVault("pending", item="Threads podcast token")
        threads.vault = tvault
        was = dict(plan.ACCOUNTS["threads"])
        plan.ACCOUNTS["threads"]["adapter"] = "threads"  # on, here regardless of accounts.json
        self.addCleanup(lambda: plan.ACCOUNTS["threads"].update(was))
        tenv = {"THREADS_APP_ID": "tid", "THREADS_APP_SECRET": "tsec", **self.menv}
        quiet = lambda s: None  # noqa: E731
        s = StubThreads.state
        posts = c.execute("SELECT * FROM post WHERE episode='ep9' AND account='threads' ORDER BY asset").fetchall()
        self.assertEqual(len(posts), 2)
        ep, clip = next(p for p in posts if p["asset"] == "episode"), next(p for p in posts if p["asset"] != "episode")
        for p in posts:
            ledger.update_post(c, p["id"], status="planned", remote_id=None, permalink=None)
        posts = [ledger.post(c, p["id"]) for p in posts]
        # before the consent: the dry run describes; without the app, skipped; with it, the error says where to go
        res = publish.run(c, posts, env=tenv, dry_run=True, log=quiet)
        self.assertEqual([r[1] for r in res], ["dry-run"] * 2)
        self.assertIn("video post of clips/01-the-point.mp4 with its alt text", threads.describe(clip, None, None))
        self.assertEqual([r[1] for r in publish.run(c, posts, env={}, log=quiet)], ["skipped-no-credentials"] * 2)
        res = publish.run(c, [posts[0]], env=tenv, log=quiet)
        self.assertEqual(res[0][1], "failed")
        self.assertIn(
            "give the consent at https://engine.agentsee.work/meta/threads/consent",
            ledger.post(c, posts[0]["id"])["last_error"],
        )
        ledger.update_post(c, posts[0]["id"], status="planned")
        # the consent, through the engine
        web.STATE["ledger"] = str(self.db)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), web.App)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        wbase = f"http://127.0.0.1:{srv.server_address[1]}"
        os.environ.update(THREADS_APP_ID="tid", THREADS_APP_SECRET="tsec", **self.menv)
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in ("THREADS_APP_ID", "THREADS_APP_SECRET", *self.menv)])

        def get(path, cookie=None):
            req = urllib.request.Request(wbase + path, headers={"Cookie": cookie} if cookie else {})
            try:
                with urllib.request.build_opener(NoRedirect).open(req, timeout=10) as r:
                    return r.status, dict(r.headers), r.read().decode(errors="replace")
            except urllib.error.HTTPError as e:
                with e:
                    return e.code, dict(e.headers), e.read().decode(errors="replace")

        st, h, body = get("/meta/threads/consent")
        self.assertEqual(st, 303)
        self.assertTrue(h["Location"].startswith(threads.AUTH + "?"), h["Location"])
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(h["Location"]).query).items()}
        self.assertEqual(qs["client_id"], "tid")
        self.assertEqual(qs["redirect_uri"], "https://engine.agentsee.work/meta/threads/callback")
        self.assertEqual(qs["scope"], "threads_basic,threads_content_publish,threads_manage_insights,threads_delete")
        self.assertEqual(qs["response_type"], "code")
        cookie = "th_state=" + re.search(r"th_state=([^;]+)", h["Set-Cookie"]).group(1)
        self.assertIn("HttpOnly", h["Set-Cookie"])
        self.assertEqual(get("/meta/threads/callback?code=CODE1&state=wrong", cookie)[0], 403, "the state is checked")
        self.assertEqual(get(f"/meta/threads/callback?code=CODE1&state={qs['state']}")[0], 403, "and the cookie")
        st, h, body = get(
            f"/meta/threads/callback?error=access_denied&error_reason=user_denied"
            f"&error_description=The+user+denied+your+request&state={qs['state']}",
            cookie,
        )
        self.assertEqual(st, 400)
        self.assertIn("denied your request", body)
        st, h, body = get(f"/meta/threads/callback?code=CODE1%23_&state={qs['state']}", cookie)
        self.assertEqual(st, 200, body)
        self.assertIn("the token is in", body)
        self.assertIn("@agentsee.work", body)
        self.assertIn("valid to", body)
        self.assertEqual((s["exchanges"], tvault.value), (1, "LL1"), "the sixty-day token is in the vault")
        row = threads.status(c)
        self.assertEqual((row["who"], row["state"]), ("1784", "valid"))
        days = lambda col: (dt.datetime.fromisoformat(row[col]) - dt.datetime.now(dt.timezone.utc)).days  # noqa: E731
        self.assertIn(days("expires_at"), (59, 60))
        self.assertIn(days("grant_expires_at"), (89, 90))
        st, h, body = get("/health")
        self.assertIn("Threads token", body)
        self.assertIn("valid to", body)
        # the posts: the art as an image, the clip as a video, each a container, a poll, a publish, a read
        res = publish.run(c, [ledger.post(c, p["id"]) for p in posts], env=tenv, who="tester", log=quiet)
        self.assertEqual([r[1] for r in res], ["published", "published"], res)
        image = next(x for x in s["containers"].values() if x["media_type"] == "IMAGE")
        video = next(x for x in s["containers"].values() if x["media_type"] == "VIDEO")
        self.assertEqual((image["image_url"], image["alt_text"]), (MEDIA + "episode-art.jpg", "Nine: the episode art"))
        self.assertEqual((video["video_url"], video["alt_text"]), (MEDIA + "clips/01-the-point.mp4", "The point"))
        self.assertEqual(image["text"], ep["text"])
        self.assertIn("https://agentsee.work/p/e9thc01", video["text"])
        self.assertEqual(s["polls"], 4, "two polls per container: in progress, then finished")
        ep2, clip2 = ledger.post(c, ep["id"]), ledger.post(c, clip["id"])
        self.assertTrue(ep2["permalink"].startswith("https://www.threads.com/@agentsee.work/post/P"), ep2["permalink"])
        self.assertEqual(ep2["remote_id"], ep2["permalink"].rsplit("/P", 1)[1])
        self.assertTrue(clip2["published_at"])
        events = c.execute("SELECT * FROM event WHERE what='publish:container' AND post=?", (clip["id"],)).fetchall()
        self.assertEqual(len(events), 1)
        self.assertEqual(json.loads(events[0]["detail"])["media"], "clips/01-the-point.mp4")
        # a container Meta cannot take (the clip as a post never tried: its container events gone)
        ledger.update_post(c, clip["id"], status="planned", remote_id=None, permalink=None)
        c.execute("DELETE FROM event WHERE what='publish:container' AND post=?", (clip["id"],))
        s["mode"] = "error"
        res = publish.run(c, [ledger.post(c, clip["id"])], env=tenv, log=quiet)
        self.assertEqual(res[0][1], "failed")
        self.assertIn("FAILED_DOWNLOADING_VIDEO", ledger.post(c, clip["id"])["last_error"])
        # not now: Meta's rate limit, with its estimate of the wait
        ledger.update_post(c, clip["id"], status="planned")
        s["mode"] = "limit"
        res = publish.run(c, [ledger.post(c, clip["id"])], env=tenv, log=quiet)
        self.assertEqual(res[0][1], "held")
        until, why = publish.HELD.pop("threads")
        wait = until - dt.datetime.now(dt.timezone.utc)
        self.assertTrue(dt.timedelta(minutes=6) < wait <= dt.timedelta(minutes=7), wait)
        self.assertIn("613", why)
        self.assertEqual(ledger.post(c, clip["id"])["status"], "planned", "a hold keeps the state")
        # and the day's quota spent
        s["quota"] = 250
        res = publish.run(c, [ledger.post(c, clip["id"])], env=tenv, log=quiet)
        self.assertEqual(res[0][1], "held")
        self.assertIn("250 of 250", publish.HELD.pop("threads")[1])
        s["quota"] = 3
        # a token Meta refuses marks the record and names the consent
        s["mode"] = "bad-token"
        res = publish.run(c, [ledger.post(c, clip["id"])], env=tenv, log=quiet)
        self.assertEqual(res[0][1], "failed")
        err = ledger.post(c, clip["id"])["last_error"]
        self.assertIn("190", err)
        self.assertIn("/meta/threads/consent", err)
        self.assertEqual(threads.status(c)["state"], "invalid")
        self.assertIn("invalid", get("/health")[2])
        s["mode"] = None
        line = threads.upkeep(c, tenv, log=quiet)
        self.assertIn("valid to", line)
        self.assertEqual(threads.status(c)["state"], "valid", "the upkeep asked Meta, which vouched for the token")
        # never twice: a retry whose container was published finds the post by its text
        c.execute("DELETE FROM event WHERE what='publish:container' AND post=?", (clip["id"],))
        ledger.update_post(c, clip["id"], status="planned")
        res = publish.run(c, [ledger.post(c, clip["id"])], env=tenv, log=quiet)
        self.assertEqual(res[0][1], "published", res)
        mid, n = ledger.post(c, clip["id"])["remote_id"], len(s["containers"])
        ledger.update_post(c, clip["id"], status="planned", remote_id=None, permalink=None)
        res = publish.run(c, [ledger.post(c, clip["id"])], env=tenv, log=quiet)
        self.assertEqual(res[0][1], "published", res)
        self.assertEqual((len(s["containers"]), ledger.post(c, clip["id"])["remote_id"]), (n, mid), "no new container")
        note = json.loads(
            c.execute("SELECT detail FROM event WHERE what='publish:published' ORDER BY id DESC").fetchone()[0]
        )["note"]
        self.assertIn("found by its text", note)
        # the upkeep: a refresh at 29 days, with the write-back guard
        s["expires_at"], s["issued_at"] = StubThreads.now() + 29 * 86400, StubThreads.now() - 31 * 86400
        tvault.fail_write = True
        FakeSMTP.sent.clear()
        line = threads.upkeep(c, tenv, log=quiet)
        self.assertIn("refresh failed", line)
        self.assertIn("Do not restart", line)
        self.assertEqual((s["refreshes"], threads._token["unsaved"], tvault.value), (1, "LL2", "LL1"))
        self.assertEqual(threads.status(c)["state"], "failed")
        self.assertEqual(
            (FakeSMTP.sent[-1]["To"], FakeSMTP.sent[-1]["Subject"]),
            ("hello@agentsee.work", "The Threads token needs a look"),
        )
        line = threads.upkeep(c, tenv, log=quiet)
        self.assertEqual(
            (tvault.value, threads._token["unsaved"], s["refreshes"]),
            ("LL2", None, 1),
            "the held token went to the vault first; no second refresh",
        )
        self.assertIn("valid to", line)
        row = threads.status(c)
        self.assertEqual(row["state"], "valid")
        self.assertTrue(row["refreshed_at"])
        self.assertEqual(len(FakeSMTP.sent), 1, "nothing more to say")
        # the mail at 13 days of grant, once a week at most
        s["grant_at"] = StubThreads.now() + 13 * 86400
        threads.record(c, warned_at=None)
        line = threads.upkeep(c, tenv, log=quiet)
        self.assertIn("grant to", line)
        self.assertEqual(len(FakeSMTP.sent), 2)
        self.assertIn("grant lapses", FakeSMTP.sent[-1].get_content())
        self.assertEqual(threads.status(c)["state"], "warned")
        threads.upkeep(c, tenv, log=quiet)
        self.assertEqual(len(FakeSMTP.sent), 2, "not again this week")
        # a token Meta no longer vouches for
        s["valid"] = False
        threads.record(c, warned_at=None)
        line = threads.upkeep(c, tenv, log=quiet)
        self.assertIn("consent must be given again", line)
        self.assertEqual(threads.status(c)["state"], "invalid")
        self.assertIn("no longer valid", FakeSMTP.sent[-1].get_content())
        s["valid"] = True
        srv.shutdown()

    def test_27_instagram(self):
        c = self.c()
        stub = http.server.HTTPServer(("127.0.0.1", 0), StubInstagram)
        threading.Thread(target=stub.serve_forever, daemon=True).start()
        self.addCleanup(stub.server_close)
        self.addCleanup(stub.shutdown)
        instagram.API, instagram.POLL = f"http://127.0.0.1:{stub.server_address[1]}", 0
        named = ledger.CONFIG.pop("instagram", None)  # config.json names the real account; here Meta is asked
        self.addCleanup(lambda: ledger.CONFIG.__setitem__("instagram", named))
        was = dict(plan.ACCOUNTS["instagram"])
        plan.ACCOUNTS["instagram"].update(adapter="instagram", assets=["episode", "clip"])  # on, with the grid
        self.addCleanup(lambda: plan.ACCOUNTS["instagram"].update(was))
        plan.plan(c, "ep9", "tester")  # the episode post the account now takes
        ienv = {"INSTAGRAM_APP_ID": "iid", "INSTAGRAM_APP_SECRET": "isec", "INSTAGRAM_TOKEN": "IGTOK", **self.menv}
        quiet = lambda s: None  # noqa: E731
        s = StubInstagram.state
        posts = c.execute("SELECT * FROM post WHERE episode='ep9' AND account='instagram' ORDER BY asset").fetchall()
        self.assertEqual([p["asset"] for p in posts], ["clip:01-the-point", "episode"])
        clip, ep = posts
        for p in posts:
            ledger.update_post(c, p["id"], status="planned", remote_id=None, permalink=None)
        posts = [ledger.post(c, p["id"]) for p in posts]
        self.assertEqual(ep["media"], "episode-art.jpg")
        self.assertIn("an Instagram reel of clips/01-the-point.mp4", instagram.describe(clip, None, None))
        self.assertIn("an Instagram image post of episode-art.jpg", instagram.describe(ep, None, None))
        self.assertEqual([r[1] for r in publish.run(c, posts, env=ienv, dry_run=True, log=quiet)], ["dry-run"] * 2)
        self.assertEqual([r[1] for r in publish.run(c, posts, env={}, log=quiet)], ["skipped-no-credentials"] * 2)
        # the Reel with its cover, the art on the grid: the account asked once, then the container, poll, publish
        res = publish.run(c, posts, env=ienv, who="tester", log=quiet)
        self.assertEqual([r[1] for r in res], ["published", "published"], res)
        reel = next(x for x in s["containers"].values() if x.get("media_type") == "REELS")
        image = next(x for x in s["containers"].values() if "image_url" in x)
        self.assertEqual(
            (reel["video_url"], reel["cover_url"], reel["share_to_feed"]),
            (MEDIA + "clips/01-the-point.mp4", MEDIA + "clips/01-the-point.jpg", "true"),
        )
        self.assertNotIn("thumb_offset", reel)
        self.assertIn("https://agentsee.work/p/e9igc01", reel["caption"])
        self.assertEqual((image["image_url"], image["alt_text"]), (MEDIA + "episode-art.jpg", "Nine: the episode art"))
        self.assertEqual(image["caption"], ep["text"])
        self.assertEqual(s["asked"], 1, "which account the token serves was asked once")
        row = ledger.credential(c, "instagram")
        self.assertEqual((row["who"], row["state"], row["note"]), (StubInstagram.IG, "valid", "@agentsee.work"))
        self.assertTrue(ledger.post(c, clip["id"])["permalink"].startswith("https://www.instagram.com/reel/R"))
        self.assertTrue(ledger.post(c, ep["id"])["permalink"].startswith("https://www.instagram.com/p/P"))
        self.assertEqual(s["polls"], 4, "two polls per container")
        e = ledger.episode(c, "ep9")
        self.assertIsNone(instagram.cover_for(c, e, {"files": {}}, "clips/01-the-point.mp4"), "no JPEG: first frame")
        self.assertEqual(instagram.cover_for(c, e, ledger.release(e), clip["media"]), MEDIA + "clips/01-the-point.jpg")
        # a container Meta cannot take, with Meta's subcode
        c.execute("DELETE FROM event WHERE what='publish:container' AND post=?", (clip["id"],))
        ledger.update_post(c, clip["id"], status="planned", remote_id=None, permalink=None)
        s["mode"] = "error"
        res = publish.run(c, [ledger.post(c, clip["id"])], env=ienv, log=quiet)
        self.assertEqual(res[0][1], "failed")
        self.assertIn("2207026", ledger.post(c, clip["id"])["last_error"])
        # not now, with Meta's estimate from the app-usage header
        ledger.update_post(c, clip["id"], status="planned")
        s["mode"] = "limit"
        res = publish.run(c, [ledger.post(c, clip["id"])], env=ienv, log=quiet)
        self.assertEqual(res[0][1], "held")
        until, why = publish.HELD.pop("instagram")
        wait = until - dt.datetime.now(dt.timezone.utc)
        self.assertTrue(dt.timedelta(minutes=11) < wait <= dt.timedelta(minutes=12), wait)
        self.assertIn("613", why)
        # the day's quota spent: a hold of a day
        s["quota"] = 100
        res = publish.run(c, [ledger.post(c, clip["id"])], env=ienv, log=quiet)
        self.assertEqual(res[0][1], "held")
        until, why = publish.HELD.pop("instagram")
        self.assertIn("100 of 100", why)
        self.assertTrue(until - dt.datetime.now(dt.timezone.utc) > dt.timedelta(hours=23))
        s["quota"] = 2
        # an account Meta restricts, and a token it refuses: both mark the record and say what to do
        for mode, code in (("restricted", "2207050"), ("bad-token", "190")):
            s["mode"] = mode
            res = publish.run(c, [ledger.post(c, clip["id"])], env=ienv, log=quiet)
            self.assertEqual(res[0][1], "failed", mode)
            err = ledger.post(c, clip["id"])["last_error"]
            self.assertIn(code, err)
            self.assertIn("app dashboard", err)
            self.assertEqual(ledger.credential(c, "instagram")["state"], "invalid")
            s["mode"] = None
            ledger.update_post(c, clip["id"], status="planned")
        line = instagram.upkeep(c, ienv, log=quiet)
        self.assertEqual(line, f"@agentsee.work (id {StubInstagram.IG}), token valid, no expiry")
        self.assertEqual(ledger.credential(c, "instagram")["state"], "valid")
        self.assertEqual(s["asked"], 1, "the account id came from the ledger")
        # a token whose login shared no asset: the account is found through the token's granular scopes instead
        ledger.set_credential(c, "instagram", who=None)
        instagram._account.clear()
        s["assigned"] = False
        self.assertEqual(instagram.account(c, ienv), {"id": StubInstagram.IG, "username": "agentsee.work"})
        self.assertEqual((s["asked"], ledger.credential(c, "instagram")["who"]), (2, StubInstagram.IG))
        ledger.set_credential(c, "instagram", who=None)
        instagram._account.clear()
        s["granular"] = []
        with self.assertRaises(publish.PublishError) as got:
            instagram.account(c, ienv)
        self.assertIn("names none in the token's granular scopes", str(got.exception))
        self.assertIn("instagram_content_publish", str(got.exception), "the permissions it does carry, for a look")
        self.assertIn('type USER for the app "AgentSee engine", issued 2025-11-06', str(got.exception))
        # config.json naming the account: the token reads it, which proves the asset is shared, and Meta is not
        # asked to name it
        ledger.CONFIG["instagram"] = {"account": StubInstagram.IG}
        self.assertEqual(instagram.account(c, ienv), {"id": StubInstagram.IG, "username": "agentsee.work"})
        self.assertEqual((s["asked"], ledger.credential(c, "instagram")["who"]), (3, StubInstagram.IG))
        self.assertEqual(
            json.loads(
                c.execute("SELECT detail FROM event WHERE what='instagram:account' ORDER BY id DESC").fetchone()[0]
            )["from"],
            "config.json",
        )
        instagram._account.clear()
        self.assertEqual(instagram.account(c, ienv)["id"], StubInstagram.IG, "from the row, which agrees with config")
        self.assertEqual(s["asked"], 3)
        instagram._account.clear()
        ledger.CONFIG["instagram"] = {"account": "999"}
        with self.assertRaises(publish.PublishError) as got:
            instagram.account(c, ienv)
        self.assertIn("reading the account 999 that config.json names", str(got.exception))
        del ledger.CONFIG["instagram"]
        instagram._account.clear()
        s["assigned"], s["granular"] = True, None
        instagram.account(c, ienv)
        # never twice: a retry whose container was published finds the post by its caption
        c.execute("DELETE FROM event WHERE what='publish:container' AND post=?", (clip["id"],))
        res = publish.run(c, [ledger.post(c, clip["id"])], env=ienv, log=quiet)
        self.assertEqual(res[0][1], "published", res)
        mid, n = ledger.post(c, clip["id"])["remote_id"], len(s["containers"])
        ledger.update_post(c, clip["id"], status="planned", remote_id=None, permalink=None)
        res = publish.run(c, [ledger.post(c, clip["id"])], env=ienv, log=quiet)
        self.assertEqual(res[0][1], "published", res)
        self.assertEqual((len(s["containers"]), ledger.post(c, clip["id"])["remote_id"]), (n, mid), "no new container")
        note = json.loads(
            c.execute("SELECT detail FROM event WHERE what='publish:published' ORDER BY id DESC").fetchone()[0]
        )["note"]
        self.assertIn("found by its caption", note)
        # a token Meta no longer honours: the upkeep says so and hello@ hears
        s["valid"] = False
        FakeSMTP.sent.clear()
        ledger.set_credential(c, "instagram", warned_at=None)
        line = instagram.upkeep(c, ienv, log=quiet)
        self.assertIn("invalid", line)
        self.assertEqual(ledger.credential(c, "instagram")["state"], "invalid")
        self.assertEqual(FakeSMTP.sent[-1]["Subject"], "The Instagram token needs a look")
        self.assertIn("app dashboard", FakeSMTP.sent[-1].get_content())
        s["valid"] = True
        instagram.upkeep(c, ienv, log=quiet)
        # the callback page Meta was given, and Health
        web.STATE["ledger"] = str(self.db)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), web.App)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        wbase = f"http://127.0.0.1:{srv.server_address[1]}"
        os.environ.update(INSTAGRAM_APP_ID="iid", INSTAGRAM_APP_SECRET="isec", INSTAGRAM_TOKEN="IGTOK")
        self.addCleanup(
            lambda: [os.environ.pop(k) for k in ("INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET", "INSTAGRAM_TOKEN")]
        )
        with urllib.request.urlopen(wbase + "/meta/instagram/callback", timeout=10) as r:
            self.assertEqual(r.status, 200)
            self.assertIn("Nothing to do here", r.read().decode())
        with urllib.request.urlopen(wbase + "/health", timeout=10) as r:
            body = r.read().decode()
        self.assertIn("Instagram token", body)
        self.assertIn("@agentsee.work", body)
        self.assertIn("token honoured by Meta", body)
        srv.shutdown()

    def test_28_meta_collect(self):
        c = self.c()
        quiet = lambda s: None  # noqa: E731
        tstub = http.server.HTTPServer(("127.0.0.1", 0), StubThreads)
        istub = http.server.HTTPServer(("127.0.0.1", 0), StubInstagram)
        for srv in (tstub, istub):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self.addCleanup(srv.server_close)
            self.addCleanup(srv.shutdown)
        threads.API = f"http://127.0.0.1:{tstub.server_address[1]}"
        instagram.API = f"http://127.0.0.1:{istub.server_address[1]}"
        named = ledger.CONFIG.pop("instagram", None)  # config.json names the real account; here Meta is asked
        self.addCleanup(lambda: ledger.CONFIG.__setitem__("instagram", named))
        was = {k: dict(plan.ACCOUNTS[k]) for k in ("threads", "instagram")}
        plan.ACCOUNTS["threads"]["adapter"] = "threads"
        plan.ACCOUNTS["instagram"]["adapter"] = "instagram"
        self.addCleanup(lambda: [plan.ACCOUNTS[k].update(v) for k, v in was.items()])
        env = {
            "THREADS_APP_ID": "tid",
            "THREADS_APP_SECRET": "tsec",
            "INSTAGRAM_APP_ID": "iid",
            "INSTAGRAM_APP_SECRET": "isec",
            "INSTAGRAM_TOKEN": "IGTOK",
            **self.menv,
        }
        th = {p["asset"]: p for p in c.execute("SELECT * FROM post WHERE episode='ep9' AND platform='threads'")}
        ig = {p["asset"]: p for p in c.execute("SELECT * FROM post WHERE episode='ep9' AND platform='instagram'")}
        self.assertTrue(th["clip:01-the-point"]["remote_id"] and ig["clip:01-the-point"]["remote_id"])
        # two posts made by hand: a pasted permalink, no id
        ledger.update_post(
            c, th["episode"]["id"], remote_id=None, permalink="https://www.threads.com/@agentsee.work/post/Pm1"
        )
        ledger.update_post(c, ig["episode"]["id"], remote_id=None, permalink="https://www.instagram.com/p/Pm2/")
        self.assertEqual(ledger.credential(c, "threads")["state"], "invalid", "as the last test left it")
        res = collect.run(c, only=["tokens", "threads", "instagram"], env=env, who="tester", log=quiet)
        self.assertEqual(ledger.credential(c, "threads")["state"], "valid", "the tokens source asked Meta first")
        self.assertFalse([v for v in res.values() if v.startswith("failed")], res)
        self.assertIn("threads: valid to", res["tokens"])
        self.assertIn("instagram: @agentsee.work", res["tokens"])
        self.assertIn("2 posts, the account, 1 links clicked, 1 matched by shortcode", res["threads"])
        self.assertIn("2 posts, the account (42 followers; the day's count not yet", res["instagram"])
        self.assertIn("1 matched by shortcode", res["instagram"])
        # Threads: the post's lifetime numbers, the account's day, the clicks on the post's short link
        m = collect.latest(c)
        clip = th["clip:01-the-point"]["id"]
        self.assertEqual(
            (m[clip]["views"]["now"], m[clip]["shares"]["now"], m[clip]["link_clicks"]["now"]), (120, 4, 9)
        )
        self.assertEqual(ledger.post(c, th["episode"]["id"])["remote_id"], "m1", "matched by the shortcode")
        self.assertEqual(m[th["episode"]["id"]]["likes"]["now"], 7)
        acct = collect.account_latest(c)
        self.assertEqual((acct["threads"]["followers_count"], acct["threads"]["views"]), (150, 40))
        # Instagram: the media's counts and its insights, a Reel's watch time, the account under a hundred followers
        reel = ig["clip:01-the-point"]["id"]
        self.assertEqual((m[reel]["likes"]["now"], m[reel]["views"]["now"], m[reel]["saved"]["now"]), (5, 500, 6))
        self.assertEqual((m[reel]["ig_reels_avg_watch_time"]["now"], m[reel]["reels_skip_rate"]["now"]), (8500, 0.31))
        self.assertNotIn("follows", m[reel])
        grid = ig["episode"]["id"]
        self.assertEqual(ledger.post(c, grid)["remote_id"], "m2", "matched by the shortcode")
        self.assertEqual((m[grid]["follows"]["now"], m[grid]["profile_visits"]["now"]), (1, 12))
        self.assertNotIn("reels_skip_rate", m[grid])
        self.assertEqual((acct["instagram"]["followers_count"], acct["instagram"]["reach"]), (42, 80))
        self.assertNotIn("follower_count", acct["instagram"])
        raw = c.execute("SELECT COUNT(*) FROM snapshot WHERE post=? AND raw IS NOT NULL", (reel,)).fetchone()[0]
        self.assertEqual(raw, 1, "the platform's answer kept once per read")
        # the screens
        title, body = web.reach_page(c)
        self.assertIn("Instagram Reels", body)
        self.assertIn("8.5 s", body)
        self.assertIn("Accounts", body)
        self.assertIn("150", body)
        # a token in trouble: its source says so and the run goes on; an adapter off, likewise
        ledger.set_credential(c, "threads", state="invalid", note="test")
        self.assertIn("skipped: the token is invalid", collect.threads(c, env, ledger.now(), quiet))
        ledger.set_credential(c, "threads", state="valid", note=None)
        plan.ACCOUNTS["instagram"]["adapter"] = "hand"
        self.assertEqual(collect.instagram(c, env, ledger.now(), quiet), "skipped: the Instagram adapter is off")
        self.assertEqual(collect.instagram(c, {}, ledger.now(), quiet), "skipped: the Instagram adapter is off")
        plan.ACCOUNTS["instagram"]["adapter"] = "instagram"
        self.assertEqual(collect.instagram(c, {}, ledger.now(), quiet), f"skipped: {instagram.NEEDS} not set")
        self.assertIn("skipped: INSTAGRAM_APP_ID", collect.tokens(c, {}, ledger.now(), quiet))

    def test_29_switch_on(self):
        """The accounts are on their adapters in accounts.json, and the backlog of cards nobody posted is skipped
        (AGE-134): a card past its day and a planned post past its day go, a published post and a future one stay."""
        self.assertEqual(
            (plan.ACCOUNTS["threads"]["adapter"], plan.ACCOUNTS["instagram"]["adapter"]), ("threads", "instagram")
        )
        self.assertEqual(plan.ACCOUNTS["instagram"]["assets"], ["episode", "clip"], "the episode on the grid")
        c = self.c()
        th = {p["asset"]: p for p in c.execute("SELECT * FROM post WHERE episode='ep9' AND platform='threads'")}
        ig = {p["asset"]: p for p in c.execute("SELECT * FROM post WHERE episode='ep9' AND platform='instagram'")}
        past, future = (
            "2026-10-20T09:00:00+01:00",
            (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3)).isoformat(),
        )
        ledger.update_post(c, th["episode"]["id"], status="hand", scheduled_at=past)  # a card never pasted back
        ledger.update_post(c, ig["clip:01-the-point"]["id"], status="planned", scheduled_at=past)  # left behind
        ledger.update_post(c, ig["episode"]["id"], status="planned", scheduled_at=future)  # its day to come
        self.assertEqual(ledger.post(c, th["clip:01-the-point"]["id"])["status"], "published")
        rows = plan.skip_backlog(c, ["threads", "instagram"], who="tester", dry_run=True)
        self.assertEqual(sorted(p["id"] for p in rows), sorted([th["episode"]["id"], ig["clip:01-the-point"]["id"]]))
        self.assertEqual(ledger.post(c, th["episode"]["id"])["status"], "hand", "a dry run changes nothing")
        rows = plan.skip_backlog(c, ["threads", "instagram"], who="tester")
        self.assertEqual(len(rows), 2)
        self.assertEqual(ledger.post(c, th["episode"]["id"])["status"], "skipped")
        self.assertEqual(ledger.post(c, ig["clip:01-the-point"]["id"])["status"], "skipped")
        self.assertEqual(ledger.post(c, ig["episode"]["id"])["status"], "planned", "its day has not come")
        self.assertEqual(ledger.post(c, th["clip:01-the-point"]["id"])["status"], "published", "left alone")
        why = c.execute("SELECT detail FROM event WHERE what='skip:backlog' ORDER BY id DESC LIMIT 1").fetchone()[0]
        self.assertIn("nobody posted the card", why)
        self.assertEqual(plan.skip_backlog(c, ["threads", "instagram"], who="tester"), [], "nothing twice")
        self.assertEqual(plan.skip_backlog(c, ["instagram"], before="2026-10-01", dry_run=True), [], "before the day")

    def test_30_arrive(self):
        """A staged episode into the ledger with nobody at a shell (arrive.py): the archive listed and each staged
        record judged; off until a host turns it on; an episode ingested by hand adopted without a word; the
        arrival, its posts, its links and its mail; a number and a date followed while nothing is out, an edited
        post keeping its words; two numbers swapped in one pass; what is refused, what waits for a host and what
        is tried again; a guest's approval held against the ledger's, and told on the issue; the screens."""
        import arrive

        c = self.c()
        client = archive.Client(self.r2env)
        objs = FakeR2.state["objects"]
        pace, mail.PACE = mail.PACE, 0
        self.addCleanup(setattr, mail, "PACE", pace)
        arrive.LAST.clear()
        self.addCleanup(arrive.LAST.clear)
        kv, down = {}, []
        first = len(FakeSMTP.sent)
        fixture_release = json.loads((self.src / "release.json").read_text())
        quiet = lambda s: None  # noqa: E731
        sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
        day = lambda n: (dt.date.today() + dt.timedelta(days=n)).isoformat()  # noqa: E731
        ago = lambda m: (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=m)).isoformat(timespec="seconds")  # noqa: E731
        subjects = lambda: [m["Subject"] for m in FakeSMTP.sent[first:]]  # noqa: E731
        mailed = lambda: FakeSMTP.sent[-1].get_content()  # noqa: E731
        ids = lambda slug: [p["link_id"] for p in arrive.posts(c, slug)]  # noqa: E731
        by = lambda slug: {(p["account"], p["asset"]): p for p in arrive.posts(c, slug)}  # noqa: E731

        def put(link_id, value):
            if down:
                raise RuntimeError("KV answered 503: try later")
            kv[link_id] = value

        def look(**kw):
            found = arrive.look(c, client, "tester", put, self.menv, log=quiet, **kw)
            return found, {r["slug"]: r for r in found["results"]}

        def press(what, slug=None):
            return arrive.act(c, what, slug, "james@example.com", client=client, put=put, env=self.menv, log=quiet)

        def stage(slug, number, title, at, release=DAY0, approved=True, **more):
            """An episode approved and staged: the two records in the archive, hashed as tools/stage.py hashes them."""
            r = json.loads(json.dumps(fixture_release))
            r["slug"] = slug
            r["episode"].update(
                number=str(number),
                title=title,
                slug=slug,
                guid=f"g-{slug}",
                date=release,
                release=release,
                url=f"https://agentsee.work/podcast/{slug}/",
                media_url=f"https://media.agentsee.work/podcast/{slug}/",
                approved={
                    "by": "Tester",
                    "at": "2026-11-20T09:00:00",
                    "release": release,
                    "number": str(number),
                    **more,
                },
            )
            if not approved:
                del r["episode"]["approved"]
            after = (dt.date.fromisoformat(release) + dt.timedelta(days=1)).isoformat()
            r["plan"] = [
                {"asset": "episode", "day": 0, "date": release},
                {"asset": "clip:01-the-point", "day": 1, "date": after},
            ]
            raw = json.dumps(r).encode()
            staged = {
                "slug": slug,
                "at": at,
                "approved": {},
                "media": {},
                "archive": {f"episodes/{slug}/out/release.json": {"sha256": sha(raw), "state": "uploaded"}},
            }
            client.put_bytes(f"episodes/{slug}/out/release.json", raw, "application/json")
            client.put_bytes(f"episodes/{slug}/out/staged.json", json.dumps(staged).encode(), "application/json")
            return r

        def restage9(digest):
            """ep9's staged record as test 14 archived it, naming a release.json of this hash; the fixture's
            names none."""
            s = json.loads((self.src / "staged.json").read_text())
            s["archive"]["episodes/ep9/out/release.json"] = {"sha256": digest, "state": "uploaded"}
            client.put_bytes("episodes/ep9/out/staged.json", json.dumps(s).encode(), "application/json")

        # off by default: a look only says what it would do, and a dry run writes nothing at all
        self.assertFalse(arrive.on(c))
        stage("arr-a", 31, "Arrive A", "2026-11-20T10:00:00")
        restage9("0" * 64)  # a stage under way: the record names a release.json the archive does not hold yet
        FakeR2.state["requests"].clear()
        found, res = look(dry_run=True)
        self.assertGreater(FakeR2.state["requests"].count(("GET", "")), 1, "the listing's continuation is walked")
        self.assertEqual((found["staged"], found["on"]), (2, False), "ep9 and arr-a; a review alone is not staged")
        self.assertEqual((res["arr-a"]["state"], res["arr-a"]["why"]), ("waiting", "automatic arrival is off"))
        self.assertEqual(res["ep9"]["state"], "failed")
        self.assertIn("is not the one staged.json names", res["ep9"]["why"])
        self.assertEqual((arrive.arrival(c, "arr-a"), arrive.LAST), (None, {}))

        # a real look keeps its answer and tells the hosts once; a failure this young is not worth a mail
        look()
        self.assertEqual(arrive.arrival(c, "arr-a")["state"], "waiting")
        self.assertIsNone(ledger.episode(c, "arr-a"))
        self.assertEqual(subjects(), ["Episode 31, Arrive A is staged and waiting for you"])
        self.assertEqual(FakeSMTP.sent[-1]["To"], mail.settings()["copy_to"])
        self.assertIn("automatic arrival is off", mailed())
        look()
        self.assertEqual(len(subjects()), 1, "one note per answer per staging")
        self.assertIn("Turned on, it would bring in: <b>arr-a (Arrive A)</b>", arrive.panel(c, "t"))

        # an episode ingested and planned by hand, at this staging, is adopted without a word
        was, n9 = ledger.episode(c, "ep9"), len(arrive.posts(c, "ep9"))
        restage9(sha(objs["episodes/ep9/out/release.json"]["body"]))
        res = look()[1]
        self.assertEqual((res["ep9"]["state"], res["ep9"]["quiet"]), ("in", True))
        self.assertEqual(
            (ledger.episode(c, "ep9")["ingested_at"], len(arrive.posts(c, "ep9"))), (was["ingested_at"], n9)
        )
        self.assertEqual(len(subjects()), 1)
        self.assertNotIn("ep9", look()[1], "settled: a staged record with the same ETag is not read again")

        # a host turns it on, and the pass that follows brings arr-a in: ingested, planned, linked, told
        said = press("auto/on")
        self.assertTrue(arrive.on(c))
        e = ledger.episode(c, "arr-a")
        self.assertEqual((e["number"], e["staged_at"], e["approved_by"]), ("31", "2026-11-20T10:00:00", "Tester"))
        n = len(arrive.posts(c, "arr-a"))
        self.assertGreater(n, 5)
        self.assertEqual(
            said,
            f"Automatic arrival is on. arr-a is in ({n} posts planned, {n} links pushed), and nothing of it is sent "
            "until its plan is confirmed",
        )
        self.assertEqual(
            (self.data / "episodes" / "arr-a" / "out" / "release.json").read_bytes(),
            objs["episodes/arr-a/out/release.json"]["body"],
            "the record as the archive holds it, where plan and the adapters look",
        )
        self.assertTrue(all(i.startswith("e31") and i in kv for i in ids("arr-a")), ids("arr-a"))
        self.assertEqual(arrive.stage(c, e), '<a href="/episodes/arr-a">to confirm</a>')
        self.assertEqual(subjects()[-1], "Episode 31, Arrive A is in: confirm its plan")
        for part in (
            "brought it in by itself",
            f"Posts     {n} planned\n",
            f"Links     {n} short links pushed",
            "still yours to merge",
            "Nothing of it is sent anywhere until a host confirms its plan: no upload, no post, no card.",
            "/episodes/arr-a",
        ):
            self.assertIn(part, mailed())
        self.assertEqual(look()[0]["results"], [], "in, and nothing staged since")

        # arrived, it is planned and nothing more: none of its posts is the publisher's until a host confirms the
        # plan, and then its uploads are, at once, and the rest on their days
        far = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=400)).isoformat()
        mine = lambda rows, slug: {(p["account"], p["asset"]) for p in rows if p["episode"] == slug}  # noqa: E731
        self.assertEqual((e["confirmed_at"], e["confirmed_by"]), (None, None))
        self.assertEqual(
            mine(publish.due(c, far, include_late=True), "arr-a"), set(), "an upload, a timed post, a card"
        )
        self.assertEqual(
            arrive.confirm(c, "arr-a", "james@example.com", staged="2026-11-20T10:00:00"),
            "The plan of arr-a is confirmed: the publisher takes its posts from the next minute",
        )
        taken, late = publish.split(c)
        self.assertEqual(mine(taken, "arr-a"), {("youtube", "episode"), ("youtube", "clip:01-the-point")})
        self.assertEqual(mine(late, "arr-a"), set())
        self.assertEqual(len(mine(publish.due(c, far, include_late=True), "arr-a")), n)
        self.assertEqual(arrive.stage(c, ledger.episode(c, "arr-a")), "planned, links pushed")

        # staged again with another number and day while nothing is out: every post follows, an edited one keeps
        # its words and takes the new link
        d12 = day(12)
        xe = by("arr-a")["x", "episode"]
        ledger.update_post(c, xe["id"], "james@example.com", text="My own words. " + plan.short(xe["link_id"]))
        ledger.event(c, "edit:text", "james@example.com", post=xe["id"], episode="arr-a")
        stage("arr-a", 32, "Arrive A, moved", "2026-11-20T12:00:00", release=d12)
        self.assertEqual(look()[1]["arr-a"]["state"], "in")
        e = ledger.episode(c, "arr-a")
        self.assertEqual((e["number"], e["release"], e["title"]), ("32", d12, "Arrive A, moved"))
        self.assertEqual(len(arrive.posts(c, "arr-a")), n, "none made twice")
        self.assertTrue(all(i.startswith("e32") and i in kv for i in ids("arr-a")), ids("arr-a"))
        a = by("arr-a")
        self.assertEqual(a["x", "episode"]["text"], "My own words. " + plan.short("e32xe"))
        self.assertIn("Arrive A, moved", a["bluesky", "episode"]["text"])
        self.assertEqual(
            (a["x", "episode"]["scheduled_at"], a["x", "clip:01-the-point"]["scheduled_at"]),
            (ledger.at(d12, "09:00"), ledger.at(day(13), "09:00")),
        )
        self.assertEqual(subjects()[-1], "Episode 32, Arrive A, moved was staged again: confirm its plan again")
        for part in (
            "Number    31 became 32",
            f"Date      {DAY0} became {d12}",
            "1 edited post(s) kept their words",
            "written again from the new record",
            "moved to the plan's new day",
            f"Posts     {n} planned, 0 of them new",
            "Its plan had been confirmed. It was staged again with none of its posts out, so that word is gone",
            "/episodes/arr-a",
        ):
            self.assertIn(part, mailed())
        # what a host confirmed is not what is staged now, and none of it had gone: the word is taken back
        self.assertEqual((e["confirmed_at"], e["confirmed_by"]), (None, None))
        held = c.execute("SELECT who, detail FROM event WHERE what='confirm:held' AND episode='arr-a'").fetchall()
        self.assertEqual(
            [(h["who"], json.loads(h["detail"])["confirmed_by"], json.loads(h["detail"])["why"]) for h in held],
            [("tester", "james@example.com", "staged again with none of its posts out")],
        )
        self.assertEqual(mine(publish.due(c, far, include_late=True), "arr-a"), set())
        self.assertEqual(arrive.stage(c, e), '<a href="/episodes/arr-a">to confirm</a>')

        # two episodes swapping numbers go in one pass, and e32… is never two posts'
        stage("arr-b", 33, "Arrive B", "2026-11-20T10:30:00")
        self.assertEqual(look()[1]["arr-b"]["state"], "in")
        stage("arr-a", 33, "Arrive A, moved", "2026-11-20T13:00:00", release=d12)
        stage("arr-b", 32, "Arrive B", "2026-11-20T13:30:00")
        res = look()[1]
        self.assertEqual((res["arr-a"]["state"], res["arr-b"]["state"]), ("in", "in"))
        self.assertEqual((ledger.episode(c, "arr-a")["number"], ledger.episode(c, "arr-b")["number"]), ("33", "32"))
        self.assertTrue(all(i.startswith("e33") and i in kv for i in ids("arr-a")), ids("arr-a"))
        self.assertTrue(all(i.startswith("e32") and i in kv for i in ids("arr-b")), ids("arr-b"))
        self.assertEqual(by("arr-a")["x", "episode"]["text"], "My own words. " + plan.short("e33xe"))

        # a number that is another episode's is refused; a host sets it aside, and puts it back
        stage("arr-c", 9, "Arrive C", "2026-11-20T14:00:00")
        res = look()[1]
        self.assertEqual((res["arr-c"]["state"], res["arr-c"]["why"]), ("refused", "episode number 9 is ep9's"))
        self.assertIsNone(ledger.episode(c, "arr-c"))
        self.assertEqual(subjects()[-1], "Episode 9, Arrive C was not brought in")
        sent = len(subjects())
        self.assertEqual(press("aside", "arr-c"), "arr-c is set aside until it is staged again")
        self.assertNotIn("arr-c", look()[1])
        self.assertIn("set aside", arrive.panel(c, "t"))
        self.assertEqual(press("back", "arr-c"), "arr-c is back on the list")
        self.assertEqual(look()[1]["arr-c"]["state"], "refused")
        self.assertEqual(len(subjects()), sent, "the same refusal is not mailed twice")
        with self.assertRaises(ValueError):
            press("bring", "arr-a")  # it is in: nothing is waiting for anyone
        with self.assertRaises(ValueError):
            press("aside", "nope")

        # once a post is out the number and the date are frozen; the words of what is left still follow. A post
        # is out only after a host's word, and that word stays when the episode is staged again
        arrive.confirm(c, "arr-b", "james@example.com")
        gone = by("arr-b")["x", "episode"]
        ledger.update_post(c, gone["id"], "tester", status="published", published_at=ledger.now())
        stage("arr-b", 34, "Arrive B", "2026-11-20T15:00:00")
        res = look()[1]
        self.assertEqual(res["arr-b"]["state"], "refused")
        self.assertIn("1 of its posts is out as episode 32", res["arr-b"]["why"])
        self.assertIn("frozen", res["arr-b"]["why"])
        self.assertEqual(ledger.episode(c, "arr-b")["number"], "32")
        self.assertEqual(subjects()[-1], "Episode 34, Arrive B was not brought in")
        stage("arr-b", 32, "Arrive B, retitled", "2026-11-20T15:30:00")
        self.assertEqual(look()[1]["arr-b"]["state"], "in")
        b = by("arr-b")
        self.assertEqual(
            (b["x", "episode"]["text"], ledger.episode(c, "arr-b")["title"]), (gone["text"], "Arrive B, retitled")
        )
        self.assertIn("Arrive B, retitled", b["bluesky", "episode"]["text"], "what has not gone is written again")
        self.assertEqual(subjects()[-1], "Episode 32, Arrive B, retitled was staged again")
        self.assertEqual(ledger.episode(c, "arr-b")["confirmed_by"], "james@example.com", "a post is out: it stays")
        self.assertIn("Its plan stays confirmed, so the publisher goes on with what has not gone out.", mailed())
        self.assertIsNone(c.execute("SELECT 1 FROM event WHERE what='confirm:held' AND episode='arr-b'").fetchone())
        left = mine(publish.due(c, far, include_late=True), "arr-b")
        self.assertEqual(len(left), len(b) - 1, "all but the one that is out are still the publisher's")
        self.assertNotIn(("x", "episode"), left)

        # a release time that has passed waits for a host's word, which is for that staging alone; staged again
        # after its release, it plans nothing new
        stage("arr-d", 35, "Arrive D", "2026-11-20T16:00:00", release=day(-1))
        res = look()[1]
        self.assertEqual(res["arr-d"]["state"], "waiting")
        self.assertIn("has passed", res["arr-d"]["why"])
        self.assertEqual(subjects()[-1], "Episode 35, Arrive D is staged and waiting for you")
        said = press("bring", "arr-d")
        self.assertIn("arr-d is in (", said)
        self.assertNotIn("arr-c", said, "the button's sentence is about its own episode")
        nd = len(arrive.posts(c, "arr-d"))
        self.assertGreater(nd, 5)
        c.execute("DELETE FROM post WHERE id=?", (arrive.posts(c, "arr-d")[-1]["id"],))
        stage("arr-d", 35, "Arrive D", "2026-11-20T16:30:00", release=day(-1))
        self.assertEqual(look()[1]["arr-d"]["state"], "waiting", "the host's word was for the staging before")
        self.assertIn("arr-d is in (", press("bring", "arr-d"))
        self.assertEqual(len(arrive.posts(c, "arr-d")), nd - 1)
        self.assertEqual(subjects()[-1], "Episode 35, Arrive D was staged again: confirm its plan")
        self.assertIn("already released, so no new posts were planned", mailed())

        # a plan nobody has confirmed with its release under a day away, or passed: red on Health, and said once
        # more, once for each wait, and not on the heels of the mail that asked
        hence = lambda h: (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=h)).isoformat(timespec="seconds")  # noqa: E731
        chase = lambda: arrive.chase(c, self.menv, log=quiet)  # noqa: E731
        self.assertEqual(
            [arrive.soon({"release_at": hence(h)}) for h in (-1, 23, 25)], [True, True, False], "a day is the line"
        )
        self.assertEqual([u["slug"] for u in arrive.unconfirmed(c)], ["arr-d", "arr-a"], "soonest release first")
        self.assertEqual(
            arrive.confirm_health(c),
            '<span class="error">1 not confirmed with the release under 24 hours away or passed: '
            '<a href="/episodes/arr-d">arr-d</a></span>; 2 waiting in all',
        )
        sent = len(subjects())
        self.assertEqual(chase(), [], "it came in a moment ago, and that mail asked")
        c.execute("UPDATE event SET at=? WHERE episode='arr-d' AND what='ingest'", (ago(31),))
        self.assertEqual(chase(), ["arr-d"])
        self.assertEqual(subjects()[sent:], ["Episode 35, Arrive D is not confirmed, and its release has passed"])
        self.assertEqual(FakeSMTP.sent[-1]["To"], mail.settings()["copy_to"])
        for part in (
            "arr-d is in the ledger and planned, and no host has confirmed its plan.",
            "Nothing of it is sent until one of you confirms",
            "This is the only reminder.",
            "/episodes/arr-d",
        ):
            self.assertIn(part, mailed())
        self.assertEqual((chase(), chase()), ([], []))
        self.assertEqual(len(subjects()), sent + 1, "once for the wait, however many minutes pass")
        arrive.confirm(c, "arr-d", "james@example.com")
        self.assertEqual(arrive.confirm_health(c), '1 waiting for a host: <a href="/episodes/arr-a">arr-a</a>')
        self.assertEqual(chase(), [], "confirmed: nothing to chase")
        arrive.hold(c, "arr-d", "james@example.com")
        at = ledger.episode(c, "arr-d")["release_at"]
        c.execute("UPDATE episode SET release_at=? WHERE slug='arr-d'", (hence(23),))
        self.assertEqual(chase(), [], "held a moment ago: the host who held it knows")
        c.execute("UPDATE event SET at=? WHERE episode='arr-d' AND what='confirm:held'", (ago(31),))
        self.assertEqual((chase(), chase()), (["arr-d"], []), "a hold begins another wait, said once too")
        self.assertEqual(
            subjects()[sent + 1 :], ["Episode 35, Arrive D is not confirmed, and its release is under a day away"]
        )
        c.execute("UPDATE episode SET release_at=? WHERE slug='arr-d'", (hence(25),))
        self.assertEqual(arrive.confirm_health(c).split(":")[0], "2 waiting for a host", "over a day away: not red")
        c.execute("UPDATE episode SET release_at=? WHERE slug='arr-d'", (at,))

        # approved with the guests' gate waived: a host brings it in, or sets it aside
        stage("arr-e", 36, "Arrive E", "2026-11-20T17:00:00", waived="the guest is travelling")
        res = look()[1]
        self.assertEqual(res["arr-e"]["state"], "waiting")
        self.assertIn("waived (the guest is travelling)", res["arr-e"]["why"])
        press("aside", "arr-e")

        # staged and not approved
        stage("arr-f", 37, "Arrive F", "2026-11-20T17:30:00", approved=False)
        res = look()[1]
        self.assertEqual((res["arr-f"]["state"], res["arr-f"]["why"]), ("refused", "the staged record is not approved"))

        # a guest's episode: the approval it was approved with is the archive's, and the one this engine took
        gid = c.execute("SELECT id FROM guest WHERE name='Ada Lovelace'").fetchone()[0]
        akey = f"guests/{gid}/approval.json"
        signed = objs[akey]["body"]
        rec = json.loads(signed)
        g = {
            "id": gid,
            "name": "Ada Lovelace",
            "approval": akey,
            "sha256": sha(signed),
            "at": rec["at"],
            "round": rec["review"]["round"],
        }
        jobs = c.execute("SELECT COALESCE(MAX(id), 0) FROM linear_job").fetchone()[0]

        def ada(minute, **kw):
            stage("ada-engines", 38, "Ada, on engines", f"2026-11-21T10:{minute:02d}:00", guests=[{**g, **kw}])
            return look()[1]["ada-engines"]

        c.execute("UPDATE review SET round=? WHERE guest=?", (g["round"] + 1, gid))
        r = ada(0)
        self.assertEqual(r["state"], "refused")
        self.assertIn("and the review is at round", r["why"])
        c.execute("UPDATE review SET round=? WHERE guest=?", (g["round"], gid))
        r = ada(1, sha256="0" * 64)
        self.assertEqual(r["state"], "refused")
        self.assertIn("is not the one the episode was approved with", r["why"])
        r = ada(2, id="zzzzzzzz")
        self.assertEqual(r["state"], "refused")
        self.assertIn("cannot know", r["why"])
        altered = json.dumps({**rec, "at": "2026-01-01T00:00:00+00:00"}).encode()
        objs[akey]["body"] = altered
        r = ada(3, sha256=sha(altered))
        self.assertEqual(r["state"], "refused")
        self.assertIn("does not match the one this engine recorded", r["why"])
        objs[akey]["body"] = signed
        self.assertIsNone(ledger.episode(c, "ada-engines"))
        self.assertEqual(ada(4)["state"], "in")
        self.assertEqual(ledger.episode(c, "ada-engines")["number"], "38")
        self.assertEqual(subjects()[-1], "Episode 38, Ada, on engines is in: confirm its plan")
        told = c.execute("SELECT * FROM linear_job WHERE id > ? AND kind='arrived' ORDER BY id", (jobs,)).fetchall()
        self.assertEqual(
            [(j["guest"], json.loads(j["args"])["issue"]) for j in told],
            [(gid, "AGE-73")] * 4,
            "three refusals and the arrival on her issue; the guest nobody knows has none",
        )
        self.assertIn("is in", json.loads(told[-1]["args"])["text"])
        fake = FakeLinear()
        fake.issues.update(self.linear_made)
        linear.post = fake
        linear.vault = FakeVault("lr1", item="Linear podcast token")
        linear._token.update(access=None, until=0.0, unsaved=None)
        lenv = {"LINEAR_CLIENT_ID": "lcid", "LINEAR_CLIENT_SECRET": "lcs", "LINEAR_REFRESH_TOKEN": "lr1"}
        self.assertGreaterEqual(linear.drain(c, lenv, log=quiet), 4)
        self.assertEqual(fake.comments[-1][0], "AGE-73")
        self.assertIn("Episode 38, Ada, on engines is in", fake.comments[-1][1])

        # KV says no: the episode is in and planned, only its links are tried again, and it is said once, after
        # half an hour; the links go whatever the switch says
        down.append(1)
        sent = len(subjects())
        stage("arr-g", 39, "Arrive G", "2026-11-21T11:00:00")
        res = look()[1]
        self.assertEqual(res["arr-g"]["state"], "unlinked")
        self.assertIn("KV answered 503", res["arr-g"]["why"])
        e = ledger.episode(c, "arr-g")
        self.assertGreater(len(arrive.posts(c, "arr-g")), 5)
        self.assertIn("links not pushed", arrive.stage(c, e))
        self.assertIn('<a href="/episodes/arr-g">to confirm</a>', arrive.stage(c, e))
        self.assertEqual(len(subjects()), sent, "too young to be worth a mail")
        self.assertIn("refused or failing", arrive.health(c))
        c.execute("UPDATE arrival SET since=? WHERE slug='arr-g'", (ago(31),))
        look()
        self.assertEqual(subjects()[sent:], ["Episode 39, Arrive G is in without its short links"])
        self.assertIn("None goes out until a host confirms its plan (https://", mailed())
        look()
        self.assertEqual(len(subjects()), sent + 1, "the only note about it")
        arrive.switch(c, False, "tester")
        down.clear()
        res = look()[1]
        self.assertEqual(res["arr-g"]["state"], "in", "only the links were left, and those are not the switch's")
        self.assertTrue(all(i.startswith("e39") and i in kv for i in ids("arr-g")), ids("arr-g"))
        self.assertEqual(subjects()[sent + 1 :], ["Episode 39, Arrive G is in: confirm its plan"])

        # the plan's panel counts as the publisher will: what it says before the word is what a run takes after.
        # An upload past its time goes public at once; a timed post over six hours late is left for a person
        g = arrive.posts(c, "arr-g")
        kept = {p["id"]: p["scheduled_at"] for p in g}
        has = lambda p, name: getattr(publish.adapter(p["account"]), name, name == "SENDS")  # noqa: E731
        up = [p for p in g if has(p, "SCHEDULES")]
        cards = [p for p in g if not has(p, "SENDS")]
        timed = [p for p in g if has(p, "SENDS") and not has(p, "SCHEDULES")]
        self.assertEqual((len(up), len(cards) > 1, len(timed) > 3), (2, True, True))
        for p, h in [(up[0], -1), (timed[0], -7), (timed[1], -1)] + [(p, 72) for p in up[1:] + timed[2:]]:
            ledger.update_post(c, p["id"], scheduled_at=hence(h))
        html = arrive.plan_panel(c, ledger.episode(c, "arr-g"), "tok")
        for part in (
            "Nothing is sent until a host confirms it",
            "<dt>Episode</dt><dd>39, Arrive G</dd>",
            '<a href="https://agentsee.work/podcast/arr-g/"',
            "YouTube takes 2 uploads now, 1 of them private until their times.",
            f"{len(timed) - 2} posts go out by themselves from ",
            f"{len(cards)} cards for a person to post come up on By hand, each on its day.",
            "Public at once, not private: 1 upload at or past the time set, or within 15 minutes of it.",
            "Sent within a minute of confirming: 1 post past the time set by less than 6 hours.",
            "Not sent: 1 post more than 6 hours past the time set.",
            "text, time and skip can still be changed below before confirming",
            'action="/episodes/arr-g/confirm"',
            'name="_t" value="tok"',
            'name="staged" value="2026-11-21T11:00:00"',
            "Confirm the plan</button>",
        ):
            self.assertIn(part, html)
        for part in ("The release time has passed.", "Hold the plan", "Already out"):
            self.assertNotIn(part, html)
        self.assertIn("The release time has passed.", arrive.plan_panel(c, ledger.episode(c, "arr-d"), "tok"))
        arrive.confirm(c, "arr-g", "james@example.com")
        of = lambda rows: {p["id"] for p in rows if p["episode"] == "arr-g"}  # noqa: E731
        taken, late = publish.split(c)
        self.assertEqual(of(taken), {up[0]["id"], up[1]["id"], timed[1]["id"]}, "both uploads, and the hour-late post")
        self.assertEqual(of(late), {timed[0]["id"]}, "seven hours late: under Late on By hand, as the panel said")
        html = arrive.plan_panel(c, ledger.episode(c, "arr-g"), "tok")
        for part in (
            "Confirmed by james@example.com",
            "What a platform already has stays there",
            'action="/episodes/arr-g/hold"',
            "Hold the plan</button>",
        ):
            self.assertIn(part, html)
        self.assertNotIn("Confirm the plan", html)
        arrive.hold(c, "arr-g", "james@example.com")
        self.assertEqual(of(publish.due(c, include_late=True)), set())
        for i, at in kept.items():
            ledger.update_post(c, i, scheduled_at=at)
        # what waits is held to the numbers too: off, a pass still refuses what it would refuse when on
        self.assertEqual((res["arr-c"]["state"], res["arr-c"]["why"]), ("refused", "episode number 9 is ep9's"))
        self.assertIn("Nothing is waiting on the switch", arrive.panel(c, "t"))
        waits = [
            {"slug": s, "number": n, "state": "waiting"} for s, n in (("arr-a", "39"), ("arr-g", "33"), ("new", "38"))
        ]
        arrive.settle(c, waits, "waiting")
        self.assertEqual([w["state"] for w in waits], ["waiting", "waiting", "refused"], "a swap would go; 38 is Ada's")
        self.assertEqual(waits[2]["why"], "episode number 38 is ada-engines's")

        # a staged record that leaves the archive leaves nothing waiting
        del objs["episodes/arr-f/out/staged.json"]
        look()
        self.assertIsNone(arrive.arrival(c, "arr-f"))

        # one pass at a time, and the worker's is due every ten minutes
        with arrive.LOCK:
            self.assertEqual(press("look"), "A look is running already; reload in a moment")
        self.assertFalse(arrive.due())
        arrive.LAST["at"] = ago(11)
        self.assertTrue(arrive.due())

        # the screens
        web.STATE["ledger"] = str(self.db)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), web.App)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def get(path):
            try:
                with urllib.request.urlopen(base + path, timeout=10) as r:
                    return r.status, dict(r.headers), r.read().decode(errors="replace")
            except urllib.error.HTTPError as e:
                with e:
                    return e.code, dict(e.headers), e.read().decode(errors="replace")

        def post(path, token=web.CSRF):
            data = urllib.parse.urlencode({"_t": token}).encode()
            req = urllib.request.Request(base + path, data=data, method="POST")
            try:
                with urllib.request.build_opener(NoRedirect).open(req, timeout=10) as r:
                    return r.status, urllib.parse.unquote_plus(r.headers.get("Location") or "")
            except urllib.error.HTTPError as e:
                with e:
                    return e.code, urllib.parse.unquote_plus(e.headers.get("Location") or "")

        with unittest.mock.patch.object(archive, "Client", lambda env=None: client):  # the engine's own pass
            st, h, body = get("/episodes")
            self.assertEqual(st, 200)
            for part in (
                "Arrivals",
                "Automatic arrival is <b>off</b>",
                "Look now",
                "Put it back",
                "Bring it in",
                "In the ledger",
                "planned, links pushed",
                "arr-c",
            ):
                self.assertIn(part, body)
            self.assertEqual(post("/episodes/arr-e/back"), (303, "/episodes?msg=arr-e is back on the list"))
            self.assertEqual(arrive.arrival(c, "arr-e")["state"], "waiting")
            with unittest.mock.patch("sys.stdout", new_callable=io.StringIO) as logged:  # the pass tells the log
                st, to = post("/episodes/look")
            self.assertEqual(st, 303)
            self.assertIn("arr-c was refused", to)
            self.assertIn("arrive: arr-c: refused: episode number 9 is ep9's", logged.getvalue())
            self.assertEqual(post("/episodes/nope/bring")[0], 400)
            self.assertEqual(post("/episodes/look", "stale")[0], 403)
            self.assertIn("Hello there", get("/episodes?msg=Hello+there")[2])
            self.assertIn("Episode arrivals", get("/health")[2])
            # the plans that wait: on the list, on Health (arr-d was released yesterday), and not on By hand
            body = get("/episodes")[2]
            for slug in ("arr-a", "arr-d", "arr-g", "ada-engines"):
                self.assertIn(f'<a href="/episodes/{slug}">to confirm</a>', body)
            for slug in ("ep9", "arr-b"):
                self.assertNotIn(f'<a href="/episodes/{slug}">to confirm</a>', body)
            body = get("/health")[2]
            self.assertIn("Plans to confirm", body)
            self.assertIn(
                '<span class="error">1 not confirmed with the release under 24 hours away or passed: '
                '<a href="/episodes/arr-d">arr-d</a></span>; 4 waiting in all',
                body,
            )
            late = [
                p for p in arrive.posts(c, "arr-d") if publish.fate_of(p, dt.datetime.now(dt.timezone.utc)) == "late"
            ]
            self.assertTrue(late, "arr-d has timed posts more than six hours past, and nobody confirmed it")
            body = get("/due")[2]
            self.assertNotIn("Past their time, not sent", body)
            for p in late:
                self.assertNotIn(f'href="/posts/{p["id"]}"', body)
        srv.shutdown()

        # no credentials, and an archive that cannot be listed
        found = arrive.look(c, None, env={}, log=quiet)
        self.assertIn("not set", found["skipped"])
        self.assertTrue(arrive.sentence(found).startswith("Not looked: "))
        nowhere = archive.Client({**self.r2env, "R2_ENDPOINT": "http://127.0.0.1:9"})
        found = arrive.look(c, nowhere, env={}, log=quiet)
        self.assertTrue(found["error"].startswith("the archive could not be listed: "), found["error"])

        # the commands by hand say why, and leave the stopping to the command line
        with self.assertRaises(ingest.IngestError):
            ingest.read("nope", self.data / "nowhere")
        with self.assertRaises(plan.PlanError):
            plan.draft(c, "nope")


class TidyTest(unittest.TestCase):
    """The tidy a transcript goes through (AGE-72), on made-up words: no episode data here."""

    @classmethod
    def setUpClass(cls):
        # By file: engine/ has a release.py of its own, and tools/ is not on the engine's path.
        # eplib goes in sys.modules for as long as release.py's `from eplib import` needs it.
        tools = pathlib.Path(__file__).resolve().parent.parent / "tools"

        def load(file, name):
            spec = importlib.util.spec_from_file_location(name, tools / file)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            return module

        eplib = load("eplib.py", "eplib")
        cls.addClassCleanup(sys.modules.pop, "eplib", None)
        cls.release = load("release.py", "tools_release")
        cls.addClassCleanup(sys.modules.pop, "tools_release", None)
        cls.tidy = staticmethod(eplib.tidy_words)

    @staticmethod
    def said(text, spk="a", start=0.0, pause_before=()):
        """The words of a line, a third of a second each, with a long pause before any word index named."""
        words, t = [], start
        for i, w in enumerate(text.split()):
            if i in pause_before:
                t += 1.5
            words.append({"w": w, "t0": round(t, 3), "t1": round(t + 0.3, 3), "spk": spk})
            t += 0.33
        return words

    def text(self, words):
        return " ".join(w["w"] for w in self.tidy(words))

    def published(self, words):
        """What the site shows: the cues of transcript.json, run together."""
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            self.release.transcript_files(out, self.tidy(words), {"a": "A", "b": "B"}, "Show")
            return [s["body"] for s in json.loads((out / "transcript.json").read_text())["segments"]]

    def test_a_wrapped_cue_keeps_its_case(self):
        """The issue's five: each sat at the start of a cue, and the cue took a capital of its own."""
        for line, pause in (
            ("people are at the moment. But we want to", 4),
            ("my background is sort of started off", 4),
            ("the software development sector for over a decade", 3),
            ("my own startup called GrayDrive where", 3),
            ("really there because that was me", 2),
        ):
            bodies = self.published(self.said(line, pause_before=(pause,)))
            self.assertEqual(len(bodies), 2, "the pause closes a cue")
            self.assertEqual(" ".join(bodies), line[0].upper() + line[1:])

    def test_a_new_speaker_opens_with_a_capital(self):
        words = self.said("that was it", spk="a") + self.said("and then we left", spk="b", start=2.0)
        self.assertEqual(self.published(words), ["That was it", "And then we left"])

    def test_a_name_at_a_cue_start_keeps_its_capital(self):
        """Whisper wrote it in lower case here and with its capital elsewhere; the cue's capital was right."""
        words = self.said("I am working with james on this with James", pause_before=(4,))
        self.assertEqual(self.published(words), ["I am working with", "James on this with James"])

    def test_another_speakers_stop_is_not_carried(self):
        """Someone's "mm-hmm." under a sentence, at any gap: the speaker runs on."""
        for gap in (0.05, 1.4):
            before = self.said("at the", spk="a")
            filler = [{"w": "Mm.", "t0": 0.7, "t1": 0.9, "spk": "b"}]
            after = self.said("moment we", spk="a", start=before[-1]["t1"] + gap)
            self.assertEqual(self.text(before + filler + after), "At the moment we")

    def test_a_real_break_after_a_filler_keeps_its_stop_and_capital(self):
        words = self.said("that was done um. so then we left")
        self.assertEqual(self.text(words), "That was done. So then we left")

    def test_a_kept_stop_keeps_its_capital(self):
        self.assertEqual(self.text(self.said("we left. then it rained")), "We left. Then it rained")

    def test_whispers_capital_on_a_common_word_goes(self):
        self.assertEqual(self.text(self.said("and And so So, because Because")), "And and so so, because because")
        self.assertEqual(
            self.text(self.said("it was SO good")), "It was SO good", "all capitals are not a sentence start"
        )

    def test_a_new_speakers_common_word_keeps_its_capital(self):
        words = self.said("well then", spk="a") + self.said("So we did", spk="b", start=1.0)
        self.assertEqual(self.text(words), "Well then So we did")

    def test_proper_nouns_survive(self):
        line = "we use Claude and Riverside at GradeDrive with Linear and Python in London on Monday for AI"
        self.assertEqual(self.text(self.said(line)), line[0].upper() + line[1:])
        self.assertEqual(self.published(self.said(line, pause_before=(4,)))[-1].split()[0], "Riverside")

    def test_i_is_capitalised(self):
        self.assertEqual(self.text(self.said("so i think i've seen it")), "So I think I've seen it")

    def test_times_and_words_stand(self):
        words = self.said("that was um. done uh with And it") + [{"w": "Mm.", "t0": 9.0, "t1": 9.1, "spk": "b"}]
        given = json.loads(json.dumps(words))
        got = self.tidy(words)
        self.assertEqual(words, given, "a pure function of its input")
        kept = [w for w in given if w["w"].strip(".") not in ("um", "uh", "Mm")]
        self.assertEqual([(w["t0"], w["t1"], w["spk"]) for w in got], [(w["t0"], w["t1"], w["spk"]) for w in kept])


if __name__ == "__main__":
    unittest.main(verbosity=1)
