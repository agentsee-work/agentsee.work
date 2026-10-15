"""
The one credential the engine writes: X's refresh token, which X replaces
on every use (see publish/x.py). Reads and writes go through the 1Password
CLI in this process, which has a session whenever the engine runs under
`op run`: a signed-in terminal today, a service account on the VPS later.
The write is a round trip through the item's JSON on stdin, never a value
on the command line, where other processes could read it; and it is read
back before it counts as done.
"""

import json
import subprocess


class VaultError(Exception):
    pass


def op(args, stdin=None):
    try:
        r = subprocess.run(["op", *args], input=stdin, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise VaultError("the op command is not on this machine") from None
    except subprocess.TimeoutExpired:
        raise VaultError("op did not answer within 60 s") from None
    if r.returncode != 0:
        lines = [x for x in r.stderr.strip().splitlines() if x.strip()]
        raise VaultError((lines[-1] if lines else "op failed")[:300])
    return r.stdout


def read(vault, item, field):
    return op(["read", f"op://{vault}/{item}/{field}", "--no-newline"])


def write(vault, item, field, value):
    j = json.loads(op(["item", "get", item, "--vault", vault, "--format", "json"]))
    hit = [f for f in j.get("fields", []) if f.get("id") == field or f.get("label") == field]
    if len(hit) != 1:
        raise VaultError(f"{item} has {len(hit)} fields called {field}, not one")
    hit[0]["value"] = value
    op(["item", "edit", item, "--vault", vault], stdin=json.dumps(j))
    if read(vault, item, field) != value:
        raise VaultError(f"{item} did not keep the new {field}")
