#!/usr/bin/env python3
"""
YouTube for the podcast. Today: the one-time authorisation. Upload and the
Analytics reads join when the publisher is built.

    op run --env-file=tools/youtube-auth.env -- ./tools/youtube.py auth

`auth` runs Google's consent once, in a browser, for the brand channel, and
prints the refresh token to file in the vault as "YouTube podcast token". It
holds nothing on disk: the client comes from the vault through `op run`, the
browser lands on a loopback port this script opens for the occasion, and the
token is printed once and never written. Afterwards everything that talks to
YouTube resolves tools/youtube.env, which names the token too.

What it asks for: upload and manage videos, captions, thumbnails and
playlists (youtube.upload, youtube.force-ssl) and read the channel's
analytics (yt-analytics.readonly). The OAuth client has to be of type
"Desktop app": that is the type Google lets redirect to a loopback address
without registering it. A refresh token from a consent screen still in
"Testing" dies after seven days, and this script says so if it sees one.
"""

import argparse
import os
import pathlib
import sys
import urllib.parse
import webbrowser

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from consent import Catch, get_json, listen, pkce, post_form, wait  # noqa: E402
from eplib import die  # noqa: E402

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
CHANNEL = "@agentseework"
ENV_FILE = "tools/youtube-auth.env"


def client():
    cid, secret = os.environ.get("YOUTUBE_CLIENT_ID"), os.environ.get("YOUTUBE_CLIENT_SECRET")
    if not cid or not secret:
        die(f"YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET are not set: run under "
            f"`op run --env-file={ENV_FILE}`")
    if not cid.endswith(".apps.googleusercontent.com"):
        print(f"  warning: the client id does not end in .apps.googleusercontent.com "
              f"({len(cid)} chars); check the vault item's username is the Client ID")
    return cid, secret


def auth(a):
    cid, secret = client()
    server, redirect = listen("Google")
    verifier, challenge = pkce()
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
        "state": Catch.state, "code_challenge": challenge, "code_challenge_method": "S256",
    })
    print(f"Listening on {redirect} for Google's redirect.")
    print("When Google asks which account, pick the brand channel, "
          f"{CHANNEL}, not the personal account that owns it.")
    if a.no_browser or not webbrowser.open(url):
        print("Open this in a browser on this machine:\n")
        print(url, "\n")
    result = wait(server, a.timeout)
    if "error" in result:
        err = result["error"]
        if err == "access_denied":
            die("you cancelled, or the Google account is not allowed to use this app "
                "(a consent screen in Testing only admits listed test users)")
        die(f"Google's redirect carried an error: {err}")

    tok = post_form(TOKEN_URL, {
        "code": result["code"], "client_id": cid, "client_secret": secret,
        "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier,
    })
    refresh = tok.get("refresh_token")
    if not refresh:
        die("Google returned no refresh token. That happens when the client is not a "
            "Desktop app, or the consent was skipped; create a Desktop client and run again")
    granted = set(tok.get("scope", "").split())
    missing = [s for s in SCOPES if s not in granted]
    if missing:
        print("  warning: not every scope was granted (untick nothing on the consent page):")
        for s in missing:
            print(f"    {s}")
    if "refresh_token_expires_in" in tok:
        days = int(tok["refresh_token_expires_in"]) // 86400
        print(f"  warning: this refresh token expires in {days} days: the consent screen is "
              f"still in Testing. Set it to In production and run again; do not file this one.")

    ch = get_json(CHANNELS_URL + "?" + urllib.parse.urlencode({"part": "snippet", "mine": "true"}),
                  f"Bearer {tok['access_token']}")
    items = ch.get("items") or []
    if not items:
        die("the token works but the account has no YouTube channel: the wrong account "
            "was picked at the consent step; run again and choose the brand channel")
    sn = items[0]["snippet"]
    handle = sn.get("customUrl", "")
    print(f"\nAuthorised: {sn['title']} ({handle or 'no handle'})  id {items[0]['id']}")
    if handle.lower() != CHANNEL:
        print(f"  warning: that is not {CHANNEL}. Run again and pick the brand channel "
              f"at the account chooser; do not file this token.")
    print("\nFile this in 1Password, Engineering vault, as an API Credential named")
    print("\"YouTube podcast token\": credential = the token below, username = the channel id.")
    print("It is shown once and written nowhere; close this terminal afterwards.\n")
    print(refresh)
    print()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("auth", help="consent once in a browser; print the refresh token to file")
    p.add_argument("--no-browser", action="store_true", help="print the URL instead of opening it")
    p.add_argument("--timeout", type=int, default=300, help="seconds to wait for the redirect")
    p.set_defaults(fn=auth)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
