#!/usr/bin/env python3
"""
What the one-time consents share. Not a tool; youtube.py and x.py import it.

A consent is OAuth's authorization-code grant with PKCE, run once by a person
at a terminal. The script opens a loopback port, sends the browser to the
platform's consent page, catches the redirect back, swaps the code for tokens
and prints the refresh token for the vault. This module is the part of that
which does not depend on the platform: the port, the state check, the PKCE
pair, the token request and the two pages the browser ends on.
"""

import base64
import hashlib
import http.server
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from eplib import die

DONE_PAGE = """<!doctype html><meta charset="utf-8"><title>AgentSee: done</title>
<body style="font: 18px/1.5 system-ui; margin: 3rem auto; max-width: 32rem">
<h1>{heading}</h1><p>{text}</p><p>You can close this tab.</p>"""


def pkce():
    """A fresh verifier and its S256 challenge."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def basic(user, password):
    """The Authorization header a confidential client sends the token endpoint."""
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def post_form(url, fields, headers=None):
    data = urllib.parse.urlencode(fields).encode()
    h = {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
    req = urllib.request.Request(url, data=data, method="POST", headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            j = json.loads(body)
            body = f"{j.get('error')}: {j.get('error_description', '')}".strip(": ")
        except ValueError:
            pass
        die(f"{url} answered {e.code}: {body}")


def get_json(url, authorization, fatal=True):
    """A GET with an Authorization header ("Bearer ..." or an OAuth 1.0a
    header). With fatal=False an HTTP error is printed and None comes back."""
    req = urllib.request.Request(url, headers={"Authorization": authorization})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        msg = f"{url} answered {e.code}: {e.read().decode(errors='replace')[:300]}"
        if fatal:
            die(msg)
        print(f"  warning: {msg}")
        return None


class Catch(http.server.BaseHTTPRequestHandler):
    """One request: the platform's redirect back to the loopback, carrying the code."""
    who = "The platform"
    path = "/"
    state = None
    result = None

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != Catch.path:
            self.send_error(404)
            return
        q = urllib.parse.parse_qs(u.query)
        if q.get("state", [None])[0] != Catch.state:
            self.reply(400, "Not this one", "The state did not match; the terminal will say more.")
            Catch.result = {"error": "state mismatch (a stale or foreign redirect)"}
        elif "error" in q:
            self.reply(400, "Not authorised", f"{Catch.who} said: {q['error'][0]}")
            Catch.result = {"error": q["error"][0]}
        elif "code" in q:
            self.reply(200, "Authorised", "The terminal has the rest.")
            Catch.result = {"code": q["code"][0]}
        else:
            self.send_error(400)

    def reply(self, status, heading, text):
        body = DONE_PAGE.format(heading=heading, text=text).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def listen(who, port=0, path="/"):
    """Open the loopback port; back come the server and the redirect URL.
    Port 0 takes any free one, for platforms that let a loopback redirect go
    unregistered; the rest want a fixed port that is on the app's list."""
    Catch.who, Catch.path, Catch.result = who, path, None
    Catch.state = secrets.token_urlsafe(24)
    try:
        server = http.server.HTTPServer(("127.0.0.1", port), Catch)
    except OSError as e:
        die(f"cannot listen on 127.0.0.1:{port}: {e.strerror}")
    return server, f"http://127.0.0.1:{server.server_address[1]}{path}"


def wait(server, timeout):
    """Serve until the redirect lands or the clock runs out; the code, or the error."""
    deadline = time.time() + timeout
    server.timeout = 5
    while Catch.result is None and time.time() < deadline:
        server.handle_request()
    server.server_close()
    if Catch.result is None:
        die(f"no redirect arrived in {timeout} s")
    return Catch.result
