#!/usr/bin/env python3
"""Add matchOn keys that `stalwart-cli snapshot` cannot infer.

Run over every snapshot, before committing:

    stalwart-cli ... snapshot --output plan.json ... && ./add-matchon.py plan.json

WHY THIS EXISTS
---------------
snapshot warns that some object types have no label property, and for those
`apply` matches by VALUE — so an object whose fields changed is CREATED rather
than updated. On a rebuild that means duplicate accounts, which is a bad way to
discover the difference between a plan and a backup.

Account needs name AND domainId: `james` exists on agentsee.work, and nothing
stops a `james` on another domain later. Matching on name alone would quietly
merge two different people.

Types with a natural label already carry one from snapshot (`name`, `selector`,
`emailAddress`, `description`) and are left alone.

AND IT SORTS THE KEYS
---------------------
`snapshot` does not emit object keys in a stable order between runs. Each line
here is one JSON object holding up to thirty sub-objects, so a key that moved
rewrites the whole line, and `git diff` reported 11 of 12 lines changed for a
snapshot that was semantically identical to the committed one — verified field
by field, zero differences.

A diff that large with nothing in it is worse than no diff at all: the next
snapshot that *does* contain real drift will look exactly the same, and nobody
reads 11 unreadable lines twice. Sorting makes the serialisation canonical, so
a diff means a change. `@type` still sorts first — `@` is 0x40, below the
letters.
"""
import json
import sys

MATCH_ON = {
    "Account": ["name", "domainId"],
}


def main(path: str) -> int:
    out, changed = [], 0
    for line in open(path):
        if not line.strip():
            continue
        obj = json.loads(line)
        want = MATCH_ON.get(obj.get("object"))
        if want and obj.get("matchOn") != want:
            obj = {**obj, "matchOn": want}
            changed += 1
        # sort_keys is load-bearing — see the module docstring.
        out.append(json.dumps(obj, separators=(",", ":"), sort_keys=True))

    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")
    print(f"{path}: added matchOn to {changed} object type(s)")

    # A type that stops needing this is fine; a type that still warns and is not
    # listed here is not, so say so rather than passing silently.
    missing = [
        o["object"]
        for o in (json.loads(l) for l in open(path) if l.strip())
        if o.get("object") in MATCH_ON and not o.get("matchOn")
    ]
    if missing:
        print("FAILED to set matchOn on:", ", ".join(missing), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: add-matchon.py <plan.json>")
    sys.exit(main(sys.argv[1]))
