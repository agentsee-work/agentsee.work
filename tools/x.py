#!/usr/bin/env python3
"""
X for the podcast. Today: the credentials. Posting, the media uploads and
the metrics reads join when the publisher is built.

    op run --env-file=tools/x.env -- ./tools/x.py check
    op run --env-file=tools/x-auth.env -- ./tools/x.py auth

`check` says what the two vault items hold, without printing them, and asks
X whose token it is. The item is the same shape (username + credential) for
every kind of credential X hands out, so this is how to tell them apart:

  OAuth 2.0   What the Developer Console generates for the account that owns
              the app, and what `auth` below prints. X stamps the kind into
              the token itself: decoded, an access token ends ":at:1" and
              lives two hours; a refresh token ends ":rt:1" and is the one to
              file, with the account id as username. A refresh token is
              single use: each refresh returns a new one and retires the old,
              so the vault item is the live copy and whatever refreshes it
              writes the new token back before doing anything else.
              `check --refresh` proves one and prints its replacement to file.
  OAuth 1.0a  "Access Token and Secret", from the same Keys and tokens page.
              The token starts with the account id and a hyphen; requests are
              signed with the API Key and Secret. Neither expires nor rotates;
              the permission level is fixed when generated.
  Bearer      The app-only token, "AAAA...": reads public data, cannot post.

`auth` runs X's OAuth 2.0 consent once, in a browser that is logged in as the
show's account, and prints the refresh token to file in the vault. It holds
nothing on disk: the app's client comes from the vault through `op run`, the
browser lands on a loopback port this script opens for the occasion, and the
token is printed once and never written. It asks for tweet.read,
tweet.write, users.read, media.write and offline.access. X checks the
callback URL against the app's list character for character, so the port is
fixed (PORT below) and http://127.0.0.1:PORT/callback has to be on that list
in the Developer Console, under User authentication settings; the app has to
be a confidential client (type "Web App, Automated App or Bot"), the kind
that gets a Client Secret.
"""

import argparse
import base64
import hashlib
import hmac
import os
import pathlib
import re
import secrets
import sys
import time
import urllib.parse
import webbrowser

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from consent import Catch, basic, get_json, listen, pkce, post_form, wait  # noqa: E402
from eplib import die  # noqa: E402

AUTH_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
ME_URL = "https://api.x.com/2/users/me"
SCOPES = ["tweet.read", "tweet.write", "users.read", "media.write", "offline.access"]
HANDLE = "agentseework"
PORT = 8794
CALLBACK = "/callback"
AUTH_ENV = "tools/x-auth.env"
ENV = "tools/x.env"


def env(*names, env_file):
    vals = [os.environ.get(n) for n in names]
    if not all(vals):
        die(f"{' / '.join(names)} are not all set: run under `op run --env-file={env_file}`")
    return vals


def app_kind(app_id):
    """What the app item's username is: an OAuth 2.0 Client ID is base64 of
    "<id>:1:ci"; the OAuth 1.0a API Key is 25 letters and digits."""
    if decoded(app_id).endswith(":1:ci"):
        return "client"
    if re.fullmatch(r"[A-Za-z0-9]{20,30}", app_id):
        return "api-key"
    return "unknown"


def decoded(s):
    """X's OAuth 2.0 ids and tokens are base64 of "<...>:1:ci", ":at:1", ":rt:1"."""
    for dec in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            return dec(s + "=" * (-len(s) % 4)).decode()
        except (ValueError, UnicodeDecodeError):
            continue
    return ""


def token_kind(secret):
    if secret.startswith("AAAA"):
        return "bearer"
    d = decoded(secret)
    if d.endswith(":at:1"):
        return "access"
    if d.endswith(":rt:1"):
        return "refresh"
    return "unknown"


def client():
    cid, secret = env("X_APP_ID", "X_APP_SECRET", env_file=AUTH_ENV)
    if app_kind(cid) != "client":
        print(f"  warning: the app item's username does not look like an OAuth 2.0 Client ID "
              f"({len(cid)} chars); it should be the Client ID from the app's \"OAuth 2.0 "
              f"Client ID and Client Secret\" box, not the API Key")
    return cid, secret


def oauth1(method, url, params, app_id, app_secret, token, token_secret, nonce=None, stamp=None):
    """The Authorization header for one OAuth 1.0a request (RFC 5849,
    HMAC-SHA1), which is how an Access Token and Secret pair signs."""
    q = lambda s: urllib.parse.quote(str(s), safe="-._~")  # noqa: E731
    o = {"oauth_consumer_key": app_id, "oauth_nonce": nonce or secrets.token_hex(16),
         "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": str(stamp or int(time.time())),
         "oauth_token": token, "oauth_version": "1.0"}
    pairs = "&".join(f"{q(k)}={q(v)}" for k, v in sorted({**params, **o}.items()))
    base = "&".join([method.upper(), q(url), q(pairs)])
    key = f"{q(app_secret)}&{q(token_secret)}".encode()
    o["oauth_signature"] = base64.b64encode(hmac.new(key, base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{q(k)}="{q(v)}"' for k, v in sorted(o.items()))


def whose(me):
    """Print who a users/me answer names; True when it is the show's account."""
    who = (me or {}).get("data") or {}
    if not who:
        return False
    print(f"For: {who.get('name')} (@{who.get('username')})  id {who.get('id')}")
    if who.get("username", "").lower() != HANDLE:
        print(f"  warning: that is not @{HANDLE}; this credential posts as the wrong account")
    return True


def check(a):
    app_id, app_secret, tok, tok_secret = env("X_APP_ID", "X_APP_SECRET", "X_TOKEN", "X_TOKEN_SECRET",
                                              env_file=ENV)
    kind = app_kind(app_id)
    print({"client": "X podcast app: an OAuth 2.0 Client ID and Client Secret.",
           "api-key": "X podcast app: an OAuth 1.0a API Key and Secret.",
           "unknown": f"X podcast app: username of {len(app_id)} chars that is neither an OAuth 2.0 "
                      f"Client ID nor an API Key."}[kind])
    m = re.fullmatch(r"(\d+)-[A-Za-z0-9]+", tok)
    if m:
        print(f"X podcast token: an OAuth 1.0a Access Token and Secret, for account id {m.group(1)}. "
              f"It neither expires nor rotates; its permission level was fixed when it was "
              f"generated, so regenerate it if the app's permissions change.")
        if kind != "api-key":
            die("signing with it needs the API Key and Secret (Keys and tokens, the pair above "
                "the OAuth 2.0 box) in X podcast app, which holds something else")
        print("Asking X whose it is (one user read, a cent on pay-per-use)...")
        me = get_json(ME_URL, oauth1("GET", ME_URL, {}, app_id, app_secret, tok, tok_secret), fatal=False)
        if not whose(me):
            die("X did not answer; with no credits on the developer account yet, that is the "
                "expected failure, and the pair is fine to keep")
        print("Works. tools/x.env is what the publisher runs under.")
        return 0
    t = token_kind(tok_secret)
    if t == "bearer":
        die("the credential is the app's Bearer Token: app-only, it reads public data and cannot "
            "post as the account. File the OAuth 2.0 refresh token, or run `auth`")
    if t == "access":
        print("X podcast token: an OAuth 2.0 access token. X issues those for two hours, so it is "
              "dead by tonight; the vault needs the refresh token that came with it (the one "
              "that decodes to ...:rt:1), with the account id as username.")
        print("Asking X whose it is while it lasts (one user read, a cent on pay-per-use)...")
        if not whose(get_json(ME_URL, f"Bearer {tok_secret}", fatal=False)):
            print("  X did not answer: the two hours are up, or there are no credits yet.")
        die("replace the credential with the refresh token; if the console showed none, "
            "run `auth` and file what it prints")
    if t == "refresh":
        acct = (f"for account id {tok}" if tok.isdigit() else
                "(the username is not the account id; nothing reads it, but the id is the "
                "useful thing to keep there)")
        print(f"X podcast token: an OAuth 2.0 refresh token {acct}.")
        if kind != "client":
            die("refreshing with it needs the OAuth 2.0 Client ID and Client Secret in "
                "X podcast app, which holds something else")
        if not a.refresh:
            print("Not tried: a refresh retires it and issues another. Run `check --refresh` to "
                  "prove it now; the replacement is printed to file. Otherwise the publisher's "
                  "first refresh is the test.")
            return 0
        print("Refreshing (this retires the token in the vault)...")
        tok2 = post_form(TOKEN_URL, {"grant_type": "refresh_token", "refresh_token": tok_secret,
                                     "client_id": app_id},
                         headers={"Authorization": basic(app_id, app_secret)})
        new = tok2.get("refresh_token")
        granted = set(tok2.get("scope", "").split())
        missing = [sc for sc in SCOPES if sc not in granted]
        if missing:
            print("  warning: the token lacks scopes the publisher needs: " + ", ".join(missing))
        if not whose(get_json(ME_URL, f"Bearer {tok2['access_token']}", fatal=False)):
            print("  X refreshed it but would not say for whom (no credits yet?); the pair works.")
        if not new:
            die("X refreshed but sent no new refresh token; the one in the vault may still be "
                "live. Run `auth` and file what it prints")
        print("\nWorks. X has retired the token in the vault and issued this one. Put it in the")
        print("credential field of \"X podcast token\" now, before anything else runs:\n")
        print(new)
        print()
        return 0
    die(f"X podcast token: username of {len(tok)} chars and credential of {len(tok_secret)} chars, "
        f"a shape this script does not know. Expected the account id and an OAuth 2.0 refresh "
        f"token, or an Access Token and Secret (username <account id>-..., credential its secret)")


def auth(a):
    cid, secret = client()
    server, redirect = listen("X", a.port, CALLBACK)
    verifier, challenge = pkce()
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": cid, "redirect_uri": redirect,
        "scope": " ".join(SCOPES), "state": Catch.state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    print(f"Listening on {redirect} for X's redirect. That exact URL has to be on the "
          f"app's callback list, or X will say something went wrong.")
    print(f"X authorises whoever the browser is logged in as, with no account chooser: "
          f"be logged in to x.com as @{HANDLE} first (a private window is the easy way).")
    if a.no_browser or not webbrowser.open(url):
        print("Open this in a browser on this machine:\n")
        print(url, "\n")
    result = wait(server, a.timeout)
    if "error" in result:
        err = result["error"]
        if err == "access_denied":
            die("you cancelled on the consent page")
        die(f"X's redirect carried an error: {err}")

    tok = post_form(TOKEN_URL, {
        "code": result["code"], "grant_type": "authorization_code", "client_id": cid,
        "redirect_uri": redirect, "code_verifier": verifier,
    }, headers={"Authorization": basic(cid, secret)})
    refresh = tok.get("refresh_token")
    if not refresh:
        die("X returned no refresh token: offline.access was not granted. Run again and "
            "authorise everything the consent page lists")
    granted = set(tok.get("scope", "").split())
    missing = [s for s in SCOPES if s not in granted]
    if missing:
        print("  warning: not every scope was granted; the publisher will fail at the first "
              "step that needs one of these:")
        for s in missing:
            print(f"    {s}")

    # Whose token this is. One user read, a cent on pay-per-use; it fails with
    # no credits on the account, and then the token is printed unconfirmed.
    print()
    if not whose(get_json(ME_URL, f"Bearer {tok['access_token']}", fatal=False)):
        print(f"Authorised, but X would not say for which account (the read above needs "
              f"credits on the developer account). File it only if the consent page named "
              f"@{HANDLE}; the username field is then the account id from the console.")
    print("\nFile this in 1Password, Engineering vault, as an API Credential named")
    print("\"X podcast token\": credential = the token below, username = the account id.")
    print("X retires it on first use and hands back a new one, which the publisher")
    print("writes to the same item. It is shown once and written nowhere here;")
    print("close this terminal afterwards.\n")
    print(refresh)
    print()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check", help="say what the vault holds for X, and whose token it is")
    p.add_argument("--refresh", action="store_true",
                   help="prove an OAuth 2.0 refresh token by using it; prints the replacement to file")
    p.set_defaults(fn=check)
    p = sub.add_parser("auth", help="OAuth 2.0 consent once in a browser; print the refresh token to file")
    p.add_argument("--no-browser", action="store_true", help="print the URL instead of opening it")
    p.add_argument("--timeout", type=int, default=300, help="seconds to wait for the redirect")
    p.add_argument("--port", type=int, default=PORT,
                   help=f"the loopback port; http://127.0.0.1:PORT{CALLBACK} must be on the "
                        f"app's callback list (default {PORT})")
    p.set_defaults(fn=auth)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
