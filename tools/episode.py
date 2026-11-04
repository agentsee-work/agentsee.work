#!/usr/bin/env python3
"""
One episode, start to finish.

    ./tools/episode.py new ep4 ~/Downloads/timeline.zip --guest k3v9x2 \\
        --title "What a regulator wants to see" \\
        --names 6ac4c0cd="James Hartt",6ac4c0ce="Abrar Mahmood",9b1f22aa="Ada Lovelace"
    ./tools/episode.py run ep4                 # everything after ingest
    ./tools/episode.py run ep4 --from cut      # re-run from a stage
    ./tools/episode.py run ep4 --only assemble --fast
    ./tools/episode.py status ep4
    ./tools/episode.py review ep4                         # the render to the archive, for the guest to see
    ./tools/episode.py notes ep4                          # the guest's confirmed cuts into removals.json
    ./tools/episode.py approve ep4 --release 2026-11-24   # the guest and the user have approved: the number, the record
    ./tools/episode.py stage ep4                          # archive + public media, via op run

The stages, each its own tool and each re-runnable on its own:

    ingest      the Riverside package -> manifest.json          (ingest.py)
    transcribe  words with timing, per speaker                  (transcribe.py)
    fillers     the ums Whisper left out, per track, with erm   (fillers.py)
    align       exact word boundaries by forced alignment       (align.py)
    cut         the edit, as data: edl.json                     (cut.py)
    cards       animated title and end cards, chapter cards     (cards.py)
    assemble    the mix, the video, captions, chapters, notes   (assemble.py)
    clips       vertical clips, if clips.json exists            (clips.py)
    release     notes, chapters, transcript, art: the manifest  (release.py)
                a publisher needs, from what was made

An episode has no number until it is approved (AGE-112): the render a guest
reviews carries none on its title card or its art, so a guest who never
approves leaves no hole in the numbering. `review` puts that render in the
archive, and nowhere public, for the office to show the guest. `notes`
brings the guest's confirmed cuts back as removals.json, and the name, line
and link they gave into the notes. `approve` (office.py) reads each guest's
approval from the archive and refuses without one, mints the next number
(or takes `--number N` when plans changed), redoes the render with it,
records who approved the episode and when in episode.json with the hash of
every deliverable and of each guest's approval, and sets the release date
the plan counts from. `stage` then puts the approved episode in the right
spot (stage.py: the archive bucket and the public media bucket), checking
the same records once more. `review`, `notes`, `approve` and `stage` wrap
themselves in `op run` with tools/stage.env so the R2 token comes from the
vault and nowhere else.

`new` writes build/episodes/<slug>/episode.json — the episode's words: title,
hosts, thumbnail text, the guests' ids from the engine's Guests screen — and
runs ingest. The later tools read that file so the title is typed once.
Extra arguments for a stage go after `--`:

    ./tools/episode.py run ep4 --only cut -- --slate-in rolling --max-pause 2
"""

import argparse
import datetime as dt
import os
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from eplib import EPISODES, ROOT, die, load_json, save_json  # noqa: E402

TOOLS = pathlib.Path(__file__).resolve().parent
STAGES = ["ingest", "transcribe", "fillers", "align", "cut", "cards", "assemble", "clips", "release"]
# with erm installed: transcribe is skipped, align also merges the aligned
# words into the transcript, and clips render alongside assemble


def stage_cmd(stage, slug, cfg, extra):
    d = EPISODES / slug
    if stage == "ingest":
        return [TOOLS / "ingest.py", slug, cfg["package"], "--names", cfg["names"], *extra]
    erm = (EPISODES.parent / "venv-erm" / "bin" / "erm").exists()
    if stage == "transcribe":
        # with erm the verbatim transcript, aligned, is the transcript, and
        # Whisper need not run a third time; the merge happens after align
        if erm:
            return None
        return [TOOLS / "transcribe.py", slug, *extra]
    if stage == "fillers":
        if not erm:
            return None
        return [TOOLS / "fillers.py", slug, *extra]
    if stage == "align":
        if not erm:
            return None
        return [[TOOLS / "align.py", slug, *extra],
                [TOOLS / "transcribe.py", slug, "--merge-only", "--from-aligned"]]
    if stage == "cut":
        cmd = [TOOLS / "cut.py", slug, *extra]
        if (d / "chapters.json").exists() and "--chapters" not in extra:
            cmd += ["--chapters", d / "chapters.json"]
        return cmd
    if stage == "cards":
        return [TOOLS / "cards.py", slug, *extra]
    if stage == "assemble":
        return [TOOLS / "assemble.py", slug, *extra]
    if stage == "clips":
        if not (d / "clips.json").exists():
            return None
        return [TOOLS / "clips.py", slug, *extra]
    if stage == "release":
        return [TOOLS / "release.py", slug, *extra]
    die(f"unknown stage {stage}")


def with_vault(cmd):
    """The command under `op run` with tools/stage.env, unless the R2 credentials are in the
    environment already (a shell that did that itself)."""
    if os.environ.get("AWS_SECRET_ACCESS_KEY"):
        return cmd
    if not shutil.which("op"):
        die("the 1Password CLI (op) is not on PATH; tools/stage.env needs it")
    return ["op", "run", "--env-file", TOOLS / "stage.env", "--", *cmd]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new", help="write episode.json and ingest")
    n.add_argument("slug")
    n.add_argument("package")
    n.add_argument("--title", required=True)
    n.add_argument("--number", help="the episode number, when it is known already; approve mints one otherwise")
    n.add_argument("--guest", action="append", default=[], metavar="ID",
                   help="a guest's id from the engine's Guests screen; once per guest")
    n.add_argument("--names", required=True, help="id-prefix=name pairs")
    n.add_argument("--thumb", help="thumbnail words, three max; default is the title")
    n.add_argument("--hosts", help="credits line; default is the show's hosts")
    n.add_argument("--date", help="YYYY-MM-DD, default today")
    r = sub.add_parser("run", help="run the stages after ingest")
    r.add_argument("slug")
    r.add_argument("--from", dest="from_", choices=STAGES, help="start here")
    r.add_argument("--only", choices=STAGES)
    r.add_argument("--fast", action="store_true", help="pass --fast to the encoders")
    r.add_argument("extra", nargs="*", help="arguments for the stage, after --")
    s = sub.add_parser("status", help="what exists for an episode")
    s.add_argument("slug")
    rv = sub.add_parser("review", help="the render as it stands to the archive, for the guest to review; nothing public")
    rv.add_argument("slug")
    rv.add_argument("--dry-run", action="store_true", help="list what would go; no credentials")
    nt = sub.add_parser("notes", help="the guest's confirmed cuts into removals.json; their name, line and link into the notes")
    nt.add_argument("slug")
    ok = sub.add_parser("approve", help="the guests' approvals, the episode number, the numbered render, the record")
    ok.add_argument("slug")
    ok.add_argument("--release", help="YYYY-MM-DD the episode goes out; default today")
    ok.add_argument("--by", help="who approved it; default the git user")
    ok.add_argument("--number", help="this number rather than the next free one, when plans changed")
    ok.add_argument("--no-render", action="store_true",
                    help="the render already carries the number; do not run cards, assemble, clips and release")
    ok.add_argument("--despite-warnings", action="store_true",
                    help="approve although the release has warnings")
    ok.add_argument("--despite-guests", action="store_true",
                    help="approve although a guest's record does not clear it; the record says so")
    st = sub.add_parser("stage", help="upload the approved episode: archive and public media")
    st.add_argument("slug")
    st.add_argument("--dry-run", action="store_true", help="list what would go; no credentials")
    st.add_argument("--no-source", action="store_true", help="stage without the Riverside package")
    a = ap.parse_args()

    if a.cmd == "new":
        d = EPISODES / a.slug
        cfg = {"slug": a.slug, "title": a.title, "number": a.number,
               "guests": a.guest or None,
               "thumb": a.thumb or a.title, "hosts": a.hosts,
               "date": a.date or dt.date.today().isoformat(),
               "package": str(pathlib.Path(a.package).expanduser().resolve()),
               "names": a.names}
        cfg = {k: v for k, v in cfg.items() if v is not None}
        save_json(d / "episode.json", cfg)
        print(f"{(d / 'episode.json').relative_to(ROOT)}")
        return subprocess.call([str(c) for c in stage_cmd("ingest", a.slug, cfg, [])])

    d = EPISODES / a.slug
    if not d.exists():
        die(f"no episode {a.slug}; `episode.py new` first")
    cfg = load_json(d / "episode.json") if (d / "episode.json").exists() else {}

    if a.cmd == "status":
        checks = [("manifest.json", "ingest"), ("transcript/words.json", "transcribe"),
                  ("edl.json", "cut"), ("cards/title.mp4", "cards"),
                  ("out/episode.mp3", "assemble (audio)"), ("out/episode.mp4", "assemble (video)"),
                  ("clips.json", "clip list"), ("out/clips", "clips"),
                  ("out/release.json", "release"), ("out/review.json", "review"),
                  ("out/staged.json", "staged")]
        number = f"episode {cfg['number']}" if cfg.get("number") is not None else "no number until approved"
        guests = ", ".join(cfg.get("guests") or []) or "none"
        print(f"{a.slug}: {cfg.get('title', '?')} ({number}; guests: {guests})")
        for f, label in checks:
            print(f"  {'done' if (d / f).exists() else '    '}  {label:<18} {f}")
        rv = d / "out" / "review.json"
        if rv.exists():
            r = load_json(rv)
            print(f"  review round {r.get('round')} sent {str(r.get('at', ''))[:16]}")
        ok = cfg.get("approved")
        print(f"  approved by {ok['by']} on {ok['at'][:10]}, release {ok['release']}" if ok
              else "  not approved")
        return 0

    if a.cmd == "review":
        cmd = [TOOLS / "stage.py", a.slug, "--review"] + (["--dry-run"] if a.dry_run else [])
        if not a.dry_run:
            cmd = with_vault(cmd)
        return subprocess.call([str(c) for c in cmd])

    if a.cmd == "notes":
        return subprocess.call([str(c) for c in with_vault([TOOLS / "office.py", "notes", a.slug])])

    if a.cmd == "approve":
        if not (d / "out" / "release.json").exists():
            die("no out/release.json: run the pipeline first")
        cmd = [TOOLS / "office.py", "approve", a.slug]
        if a.release:
            cmd += ["--release", a.release]
        if a.by:
            cmd += ["--by", a.by]
        if a.number:
            cmd += ["--number", a.number]
        for flag in ("no_render", "despite_warnings", "despite_guests"):
            if getattr(a, flag):
                cmd.append("--" + flag.replace("_", "-"))
        return subprocess.call([str(c) for c in with_vault(cmd)])

    if a.cmd == "stage":
        cmd = [TOOLS / "stage.py", a.slug] + (["--dry-run"] if a.dry_run else []) \
            + (["--no-source"] if a.no_source else [])
        if not a.dry_run:
            cmd = with_vault(cmd)
        return subprocess.call([str(c) for c in cmd])

    stages = [a.only] if a.only else STAGES[1:]
    if a.from_ and not a.only:
        stages = STAGES[STAGES.index(a.from_):]
    def cmds_for(st):
        extra = list(a.extra)
        if a.fast and st in ("assemble", "clips"):
            extra.append("--fast")
        if a.fast and st == "cards":
            extra.append("--preview")
        got = stage_cmd(st, a.slug, cfg, extra)
        if got is None:
            return None
        return got if isinstance(got[0], list) else [got]

    skipped_why = {"clips": "no clips.json", "transcribe": "erm's verbatim transcript is used instead"}
    i = 0
    while i < len(stages):
        st = stages[i]
        cmds = cmds_for(st)
        if cmds is None:
            print(f"── {st}: skipped ({skipped_why.get(st, 'no build/venv-erm')})")
            i += 1
            continue
        # assemble and clips do not depend on each other: clips run
        # alongside, their output held back until assemble has finished
        side = None
        if st == "assemble" and i + 1 < len(stages) and stages[i + 1] == "clips":
            side_cmds = cmds_for("clips")
            if side_cmds:
                print("── assemble, with clips alongside")
                side = subprocess.Popen([str(c) for c in side_cmds[0]], stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True)
            else:
                print("── assemble")
                print("── clips: skipped (no clips.json)")
            i += 1
        else:
            print(f"── {st}")
        for cmd in cmds:
            rc = subprocess.call([str(c) for c in cmd])
            if rc != 0:
                if side:
                    side.kill()
                die(f"{st} failed ({rc})")
        if side:
            out, _ = side.communicate()
            print("── clips")
            print(out, end="")
            if side.returncode != 0:
                die(f"clips failed ({side.returncode})")
        i += 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
