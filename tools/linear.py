#!/usr/bin/env python3
"""
Linear for the podcast engine: the one-time consent that makes the engine
"AgentSee Engine".

    op run --env-file=tools/linear.env -- ./tools/linear.py auth

`auth` opens Linear's consent page for the OAuth application AgentSee Engine
(AGE-115, step 3) with actor=app, so the tokens act as the application and
not as the person who approves, and asks for read, write, issues:create and
comments:create. The browser lands on a loopback port this script opens for
the occasion (PORT below; http://127.0.0.1:PORT/callback has to be the
app's callback URL, character for character), the code is swapped for
tokens, and the refresh token is printed once, for the vault item "Linear
podcast token" with username "AgentSee Engine". Nothing is written to disk:
the app's client id and secret come from the vault through `op run`.

Linear's access token lasts a day and its refresh token rotates on each
use, so the vault copy is the live one and the engine writes each
replacement back (engine/linear.py). Do not run this while the engine is up
with a token of its own: a fresh consent can retire the engine's, and its
next refresh would fail.
"""

import argparse
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from consent import Catch, listen, post_form, wait  # noqa: E402
from eplib import die  # noqa: E402

AUTH_URL = "https://linear.app/oauth/authorize"
TOKEN_URL = "https://api.linear.app/oauth/token"
API = "https://api.linear.app/graphql"
SCOPES = ["read", "write", "issues:create", "comments:create"]
PORT = 8795
CALLBACK = "/callback"
ENV = "tools/linear.env"


def client():
    cid, secret = os.environ.get("LINEAR_CLIENT_ID"), os.environ.get("LINEAR_CLIENT_SECRET")
    if not cid or not secret:
        die(f"LINEAR_CLIENT_ID and LINEAR_CLIENT_SECRET are not set: run under op run --env-file={ENV}")
    return cid, secret


def viewer(access):
    """Who the token acts as, from Linear; None if it would not say."""
    body = json.dumps({"query": "{ viewer { id name } }"}).encode()
    req = urllib.request.Request(
        API, data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {access}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return (json.load(r).get("data") or {}).get("viewer")
    except (urllib.error.URLError, ValueError) as e:
        print(f"  warning: Linear would not say whose token this is: {e}")
        return None


def auth(a):
    cid, secret = client()
    server, redirect = listen("Linear", a.port, CALLBACK)
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code",
        "scope": ",".join(SCOPES), "state": Catch.state, "actor": "app", "prompt": "consent",
    })
    print(f"Listening on {redirect} for Linear's redirect. That exact URL has to be the "
          f"application's callback URL, or Linear will refuse.")
    print("Approve as yourself, a workspace admin; with actor=app the token acts as AgentSee Engine, not as you.")
    if a.no_browser or not webbrowser.open(url):
        print("Open this in a browser on this machine:\n")
        print(url, "\n")
    result = wait(server, a.timeout)
    if "error" in result:
        err = result["error"]
        if err == "access_denied":
            die("you cancelled on the consent page")
        die(f"Linear's redirect carried an error: {err}")

    tok = post_form(TOKEN_URL, {
        "code": result["code"], "redirect_uri": redirect, "client_id": cid,
        "client_secret": secret, "grant_type": "authorization_code",
    })
    refresh = tok.get("refresh_token")
    if not refresh:
        die("Linear returned no refresh token; the engine needs one. Check the application's settings and run again")
    granted = {s for s in re.split(r"[ ,]+", tok.get("scope", "").strip()) if s}
    missing = [s for s in SCOPES if s not in granted]
    if missing:
        print("  warning: not every scope was granted; the engine will fail at the first step that needs one of these:")
        for s in missing:
            print(f"    {s}")

    print()
    who = viewer(tok["access_token"])
    if who:
        print(f"Linear says the token acts as: {who.get('name')} ({who.get('id')})")
        if who.get("name") != "AgentSee Engine":
            print("  warning: that is not the application. Was actor=app on the consent page's URL?")
    print("\nFile this in 1Password, Engineering vault, as an API Credential named")
    print("\"Linear podcast token\": credential = the token below, username = AgentSee Engine.")
    print("The engine retires it on first use and writes the replacement to the same")
    print("item. It is shown once and written nowhere here; close this terminal afterwards.\n")
    print(refresh)
    print()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("auth", help="the consent once in a browser, as the application; print the refresh token to file")
    p.add_argument("--no-browser", action="store_true", help="print the URL instead of opening it")
    p.add_argument("--timeout", type=int, default=300, help="seconds to wait for the redirect")
    p.add_argument("--port", type=int, default=PORT,
                   help=f"the loopback port; http://127.0.0.1:PORT{CALLBACK} must be the application's "
                        f"callback URL (default {PORT})")
    p.set_defaults(fn=auth)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
