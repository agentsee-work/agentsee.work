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
            # Rebuild the dict so matchOn sits where snapshot puts it, keeping
            # the diff between successive snapshots readable.
            obj = {
                **{k: v for k, v in obj.items() if k != "value"},
                "matchOn": want,
                "value": obj["value"],
            }
            changed += 1
        out.append(json.dumps(obj, separators=(",", ":")))

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
