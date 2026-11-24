#!/usr/bin/env python3
"""
The pipeline's end of the guest office (AGE-92): what the engine wrote
about a guest into the archive, read back on the workstation.

    ./tools/episode.py notes ep4        # the guest's confirmed cuts into removals.json; name, line, link into the notes
    ./tools/episode.py approve ep4 --release 2026-11-24   # the gate, the number, the numbered render, the record
    ./tools/episode.py claims           # who holds which of Seymour's poses and chains, for the site

Both wrap this in `op run` with tools/stage.env, as `stage` does, because
the archive is where the office's records are, under guests/<id>/:
guest.json (the guest, with the name as they want it shown, a line and a
link), notes.json (their review notes, each cut confirmed as the words to
remove), approval.json (their approval of a review round, signed) and
claim.json (the pose or chain of Seymour's they hold, AGE-159).
episode.json names an episode's guests by their ids on the engine's Guests
screen ("guests": [...]); an episode that names none is the hosts' own
and needs no record.

A guest's Seymour is the eye of their episode. `notes` and `approve` write
the claim into episode.json as "seymour" ({"kind", "name", "owner"}), from
claim.json as it stands that day, or from the approval when it carries one;
the cards, the art, the thumbnail and the clips are drawn with it
(show.face). With two guests it is the first one episode.json names who
holds one. Once the episode is approved, `notes` leaves it alone: a swap
made afterwards shows on the site and in later episodes, and reaches this
one only if it is approved again. Both commands, and `claims`, also bring
the whole board (seymour/claims.json in the archive) to
build/seymour/claims.json, which tools/site.py and `eyeanim.py --export`
read.

Approval is where the episode number is minted (AGE-112). A review render
carries none, so a guest who never approves leaves no hole: the next
number is one more than the highest on a staged episode in the archive or
an approved one here; `--number N` overrides that when plans changed, and
a number on another staged episode is refused. The render is then redone
with the number on the title card, in the mp3's tag, on the art and in
the notes (`episode.py run --from cards`, unless `--no-render` says that
was done), the approval goes into episode.json with every deliverable's
hash and each guest's approval and its hash, and the release date is set
for the plan. `stage` checks the same records once more before anything
reaches the media host.
"""

import argparse
import datetime as dt
import os
import pathlib
import subprocess
import sys

from eplib import EPISODES, ROOT, die, ep_dir, load_json, save_json
from stage import boto, connect, get_json, guests_of, hosts_of, review_record, unapproved

TOOLS = pathlib.Path(__file__).resolve().parent
BOARD = ROOT / "build" / "seymour" / "claims.json"  # the claims as the workstation last saw them (eyeanim.CLAIMS)
BOARD_KEY = "seymour/claims.json"  # where the engine mirrors them (engine/seymour.py)


def minted(s3, bucket):
    """Every number on a staged episode in the archive, with its slug."""
    nums = {}
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="episodes/", Delimiter="/")
    for page in pages:
        for cp in page.get("CommonPrefixes", []):
            slug = cp["Prefix"][len("episodes/") :].rstrip("/")
            r, _ = get_json(s3, bucket, f"episodes/{slug}/out/release.json")
            n = ((r or {}).get("episode") or {}).get("number")
            if n not in (None, ""):
                nums[int(n)] = slug
    return nums


def local_numbers():
    """Every number on an episode approved on this workstation, staged yet or not."""
    nums = {}
    for p in sorted(EPISODES.glob("*/episode.json")):
        c = load_json(p)
        if c.get("approved") and c.get("number") not in (None, ""):
            nums[int(c["number"])] = c.get("slug") or p.parent.name
    return nums


def choose_number(cfg, taken, wanted=None):
    """The episode's number: the one asked for, else the one it has, else the next free one. One
    that another episode holds is refused, whichever way it was asked."""
    slug = cfg["slug"]
    n = wanted if wanted not in (None, "") else cfg.get("number")
    if n in (None, ""):
        return max(taken, default=-1) + 1
    n = int(n)
    other = taken.get(n)
    if other and other != slug:
        die(f"episode {n} is {other}; this is {slug}. --number picks another, or leave it to the next free one")
    return n


def fill_guests(cfg, guests):
    """notes.guests from the office's records: a guest the notes do not name yet is added with the
    name as they want it shown, their line and their link; one the notes name keeps what was written."""
    notes = cfg.setdefault("notes", {})
    have = {g.get("name") for g in notes.get("guests", [])}
    added = []
    for g in guests:
        if g["name"] in have or g["guest"].get("name") in have:
            continue
        entry = {"name": g["name"]}
        if g["guest"].get("bio"):
            entry["who"] = g["guest"]["bio"]
        if g["guest"].get("link"):
            entry["href"] = g["guest"]["link"]
        notes.setdefault("guests", []).append(entry)
        added.append(g["name"])
    return added


def claim_of(guests):
    """The episode's Seymour from the office's records: the claim of the first guest named who holds one, as
    {"kind", "name", "owner": {"id", "name"}}; None when no guest of the episode holds one. A guest's claim.json
    is what they hold today (a swap rewrites it, a release empties it); a guest with none on file but a pick in
    their signed approval gets that one."""
    for g in guests:
        c = g.get("claim")
        if c is None:
            c = (g.get("approval") or {}).get("seymour")
        if c and c.get("kind") in ("pose", "chain") and c.get("name"):
            return {"kind": c["kind"], "name": c["name"], "owner": {"id": g["id"], "name": g["name"]}}
    return None


def fill_claim(cfg, guests):
    """episode.json's "seymour" brought up to the office's records. Returns (changed, a line to print)."""
    had, now = cfg.get("seymour"), claim_of(guests)
    if not guests or had == now:  # the hosts' own episode has no record to read: what episode.json says stands
        return False, None
    if now:
        cfg["seymour"] = now
        return True, f"seymour: the {now['kind']} {now['name']}, {now['owner']['name']}'s" + (
            f" (was the {had.get('kind')} {had.get('name')})" if had else ""
        )
    del cfg["seymour"]
    return True, f"seymour: none now (was the {had.get('kind')} {had.get('name')})"


def fetch_board(s3, bucket, path=None):
    """The whole board of claims from the archive to build/seymour/claims.json; the document, or None when the
    engine has mirrored none yet (nothing is written then)."""
    doc, _ = get_json(s3, bucket, BOARD_KEY)
    if doc is not None:
        save_json(path or BOARD, doc)
    return doc


def seed_removals(d, guests):
    """The cuts a guest confirmed in review, into removals.json as the phrases cut.py takes; one
    that is there already is left alone."""
    path = d / "removals.json"
    rems = load_json(path) if path.exists() else []
    have = {r.get("phrase") for r in rems if r.get("phrase")}
    added = []
    for g in guests:
        for rnd in (g["notes"] or {}).get("rounds") or []:
            for note in rnd.get("notes") or []:
                cut = note.get("remove") or {}
                phrase = cut.get("phrase")
                if not phrase or not note.get("confirmed") or phrase in have:
                    continue
                entry = {
                    "phrase": phrase,
                    "why": f"{g['name']}, review round {rnd.get('n')}: {(note.get('text') or '')[:120]}",
                }
                if cut.get("near") is not None:
                    entry["near"] = cut["near"]
                rems.append(entry)
                have.add(phrase)
                added.append(phrase)
    if added:
        save_json(path, rems)
    return added


def vetoed_clips(d, guests):
    """The clips that stand and the titles of those a guest vetoed; nothing changed. A veto names the
    clip as the review's release.json does: its file, its slug, its title or its index."""
    path = d / "clips.json"
    if not path.exists():
        return [], []
    clips = load_json(path)
    vetoed = set()
    for g in guests:
        for v in ((g["approval"] or {}).get("clips") or {}).get("vetoed") or []:
            vetoed.add(str(v))
    if not vetoed:
        return clips, []
    keep, dropped = [], []
    for i, c in enumerate(clips, 1):
        slug = c.get("slug") or f"clip-{i:02d}"
        names = {str(i), slug, f"{i:02d}-{slug}", f"clips/{i:02d}-{slug}.mp4", c.get("title") or ""}
        if names & vetoed:
            dropped.append(c.get("title") or slug)
        else:
            keep.append(c)
    return keep, dropped


def drop_vetoed(d, guests):
    """A clip the guest vetoed is dropped from clips.json, not re-cut, and every rendered clip
    goes with it so the render that follows has only the clips that stand."""
    keep, dropped = vetoed_clips(d, guests)
    if dropped:
        save_json(d / "clips.json", keep)
        for p in (d / "out" / "clips").glob("*"):
            if p.is_file():
                p.unlink()
    return dropped


def notes_cmd(a, d, cfg, guests):
    if not guests:
        die('episode.json names no guest ("guests": [ids from the engine\'s Guests screen]): nothing to read')
    added = fill_guests(cfg, guests)
    if added:
        save_json(d / "episode.json", cfg)
        print(f"notes.guests: added {', '.join(added)}")
    if cfg.get("approved"):
        changed, line = fill_claim(dict(cfg), guests)
        if changed:
            print(f"{line}: not taken, the episode is approved; `episode.py approve {a.slug}` again would take it")
    else:
        changed, line = fill_claim(cfg, guests)
        if changed:
            save_json(d / "episode.json", cfg)
            print(
                f"{line}. The cards, the art and the clips carry it from the next `episode.py run {a.slug} --from cards`"
            )
    phrases = seed_removals(d, guests)
    for g in guests:
        rounds = (g["notes"] or {}).get("rounds") or []
        n_notes = sum(len(r.get("notes") or []) for r in rounds)
        print(
            f"{g['name']}: {len(rounds)} round(s) of notes, {n_notes} note(s)"
            + (f"; approved round {(g['approval'].get('review') or {}).get('round')}" if g["approval"] else "")
        )
    if phrases:
        print(
            f"removals.json: {len(phrases)} cut(s) added. Re-cut (`episode.py run {a.slug} --from cut`), "
            "then `episode.py review` so the guest sees it"
        )
    else:
        print("removals.json: nothing new")
    return 0


def approve_cmd(a, d, cfg, guests, s3, buckets):
    out = d / "out"
    hosts = hosts_of(cfg, s3, buckets["archive"]) if cfg.get("guests") else None
    why = unapproved(guests, d, cfg, hosts)
    known = {g["name"] for g in guests} | {g["guest"].get("name") for g in guests}
    for g in (cfg.get("notes") or {}).get("guests") or []:
        if g.get("name") not in known:
            why.append(
                f"{g.get('name')} is in notes.guests with no record in the office: "
                'episode.json "guests" names nobody by that name'
            )
    waived = None
    if why:
        for w in why:
            print(f"  gate: {w}")
        if not a.despite_guests:
            die("the office's records do not clear this episode; --despite-guests waives that, and the record says so")
        waived = why
        print("  waived with --despite-guests")
    for g in guests:
        if g["approval"]:
            print(
                f"  guest: {g['name']} approved round {(g['approval'].get('review') or {}).get('round')} "
                f"at {str(g['approval'].get('at', ''))[:16]}"
            )
    for h in (hosts or {}).get("approvals") or []:
        print(f"  host: {h.get('host')} approved round {(hosts or {}).get('round')} at {str(h.get('at', ''))[:16]}")

    taken = {**local_numbers(), **minted(s3, buckets["archive"])}
    n = choose_number(cfg, taken, a.number)
    had = cfg.get("number")
    cfg["number"] = str(n)
    print(
        f"  episode {n}"
        + (
            f", was {had}"
            if had not in (None, "") and str(had) != str(n)
            else ""
            if had not in (None, "")
            else ", minted" + (f" (taken: {', '.join(str(k) for k in sorted(taken))})" if taken else "")
        )
    )
    added = fill_guests(cfg, guests)
    if added:
        print(f"  notes.guests: added {', '.join(added)}")
    # The guest's Seymour as they hold it today: the render below draws the cards, the art and the clips with it.
    _, line = fill_claim(cfg, guests)
    if line:
        print(f"  {line}")

    def drawn(rel):  # the pose or chain the render in hand was drawn with
        s = (rel.get("episode") or {}).get("seymour") or {}
        return (s.get("kind"), s.get("name"))

    wanted = ((cfg.get("seymour") or {}).get("kind"), (cfg.get("seymour") or {}).get("name"))
    if a.dry_run:
        keep, would_drop = vetoed_clips(d, guests)
        rel = load_json(out / "release.json")
        rendered = (rel.get("episode") or {}).get("number")
        print(f"  clips vetoed: {', '.join(would_drop) if would_drop else 'none'}; {len(keep)} would stand")
        print(f"  render needed: {'yes' if str(rendered) != str(n) or would_drop or drawn(rel) != wanted else 'no'}")
        print("dry run: nothing written, nothing rendered")
        return 0
    dropped = drop_vetoed(d, guests)
    if dropped:
        print(f"  clips vetoed and dropped: {', '.join(dropped)}")
    cfg["release"] = a.release or dt.date.today().isoformat()
    save_json(d / "episode.json", cfg)

    rel = load_json(out / "release.json")
    rendered = (rel.get("episode") or {}).get("number")
    if str(rendered) != str(n) or dropped or drawn(rel) != wanted:
        if a.no_render and not dropped:
            if str(rendered) != str(n):
                what = f"number {rendered!r}, not {n}"
            else:
                what = f"another Seymour ({drawn(rel)[1] or 'none'}, not {wanted[1] or 'none'})"
            die(f"the render carries {what}: drop --no-render, or `episode.py run {a.slug} --from cards` first")
        print(f"── the numbered render: episode.py run {a.slug} --from cards", flush=True)
        rc = subprocess.call([str(TOOLS / "episode.py"), "run", a.slug, "--from", "cards"])
        if rc:
            die(f"the render failed ({rc})")
    # The release once more, so the notes carry the guests and the plan counts
    # from the release date; then the hashes are of what will be staged.
    rc = subprocess.call([str(TOOLS / "release.py"), a.slug, "--no-art"])
    if rc:
        die(f"release failed ({rc})")
    rel = load_json(out / "release.json")
    if rel.get("warnings") and not a.despite_warnings:
        for w in rel["warnings"]:
            print(f"  warning: {w}")
        die("the release has warnings: fix the cause, or pass --despite-warnings")
    by = (
        a.by
        or subprocess.run(["git", "config", "user.name"], capture_output=True, text=True).stdout.strip()
        or os.environ.get("USER", "?")
    )
    approved = {
        "by": by,
        "at": dt.datetime.now().isoformat(timespec="seconds"),
        "release": cfg["release"],
        "number": str(n),
        "review": (review_record(d) or {}).get("round"),
        "guests": [
            {
                "id": g["id"],
                "name": g["name"],
                "approval": f"guests/{g['id']}/approval.json",
                "sha256": g["approval_sha256"],
                "at": (g["approval"] or {}).get("at"),
                "round": ((g["approval"] or {}).get("review") or {}).get("round"),
            }
            for g in guests
        ],
        "files": {k: v["sha256"] for k, v in rel["files"].items()},
    }
    if cfg.get("seymour"):
        approved["seymour"] = cfg["seymour"]
    if hosts:
        approved["hosts"] = [
            {"host": h.get("host"), "at": h.get("at"), "who": h.get("who")} for h in hosts.get("approvals") or []
        ]
    if waived:
        approved["waived"] = waived
    cfg["approved"] = approved
    save_json(d / "episode.json", cfg)
    who = "; ".join(f"{g['name']} ({g['id']})" for g in guests)
    print(
        f"approved by {by}, release {cfg['release']}, episode {n}, {len(rel['files'])} files recorded"
        + (f", with the approval of {who}" if guests and not waived else "")
        + (" (guest records waived)" if waived else ""),
        flush=True,
    )
    # release.json carries the approval too, as it always has.
    return subprocess.call([str(TOOLS / "release.py"), a.slug, "--no-art"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    nt = sub.add_parser("notes", help="the guest's confirmed cuts into removals.json; name, line, link into the notes")
    nt.add_argument("slug")
    sub.add_parser("claims", help="who holds which of Seymour's poses and chains, from the archive, for the site")
    ok = sub.add_parser("approve", help="the guests' approvals, the episode number, the numbered render, the record")
    ok.add_argument("slug")
    ok.add_argument("--release", help="YYYY-MM-DD the episode goes out; default today")
    ok.add_argument("--by", help="who approved it; default the git user")
    ok.add_argument("--number", help="this number rather than the next free one, when plans changed")
    ok.add_argument(
        "--no-render",
        action="store_true",
        help="the render already carries the number; do not run cards, assemble, clips and release",
    )
    ok.add_argument("--despite-warnings", action="store_true", help="approve although the release has warnings")
    ok.add_argument(
        "--despite-guests",
        action="store_true",
        help="approve although a guest's record does not clear it; the record says so",
    )
    ok.add_argument(
        "--dry-run",
        action="store_true",
        help="the gate, the number, the clips that would go; nothing written, nothing rendered",
    )
    a = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)
    boto3 = boto(__file__)
    if a.cmd == "claims":
        s3, buckets = connect(boto3)
        doc = fetch_board(s3, buckets["archive"])
        if doc is None:
            print(f"no {BOARD_KEY} in the archive yet: nobody has claimed a pose or a chain")
            return 0
        for cl in doc.get("claims") or []:
            print(f"  {cl.get('kind'):<5} {cl.get('name'):<14} {cl.get('owner')}  since {str(cl.get('at') or '')[:10]}")
        print(
            f"{BOARD.relative_to(ROOT)}: {len(doc.get('claims') or [])} claimed; `tools/site.py` puts the names on the site"
        )
        return 0
    d = ep_dir(a.slug)
    cfg = load_json(d / "episode.json")
    if a.cmd == "approve" and not (d / "out" / "release.json").exists():
        die("no out/release.json: run the pipeline first")
    s3, buckets = connect(boto3, a.slug)
    guests = guests_of(cfg, s3, buckets["archive"])
    fetch_board(s3, buckets["archive"])
    if a.cmd == "notes":
        return notes_cmd(a, d, cfg, guests)
    return approve_cmd(a, d, cfg, guests, s3, buckets)


if __name__ == "__main__":
    sys.exit(main())
