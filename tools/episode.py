#!/usr/bin/env python3
"""
One episode, start to finish.

    ./tools/episode.py new ep0 ~/Downloads/timeline.zip --number 0 \\
        --title "Introductions and the premise" \\
        --names 6ac4c0cd="James Hartt",6ac4c0ce="Abrar Mahmood"
    ./tools/episode.py run ep0                 # everything after ingest
    ./tools/episode.py run ep0 --from cut      # re-run from a stage
    ./tools/episode.py run ep0 --only assemble --fast
    ./tools/episode.py status ep0

The stages, each its own tool and each re-runnable on its own:

    ingest      the Riverside package -> manifest.json          (ingest.py)
    transcribe  words with timing, per speaker                  (transcribe.py)
    fillers     the ums Whisper left out, per track, with erm   (fillers.py)
    align       exact word boundaries by forced alignment       (align.py)
    cut         the edit, as data: edl.json                     (cut.py)
    cards       animated title and end cards, chapter cards     (cards.py)
    assemble    the mix, the video, captions, chapters, notes   (assemble.py)
    clips       vertical clips, if clips.json exists            (clips.py)

`new` writes build/episodes/<slug>/episode.json — the episode's words: title,
number, hosts, thumbnail text — and runs ingest. The later tools read that
file so the title is typed once. Extra arguments for a stage go after `--`:

    ./tools/episode.py run ep0 --only cut -- --slate-in rolling --max-pause 2
"""

import argparse
import datetime as dt
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from eplib import EPISODES, ROOT, die, load_json, save_json  # noqa: E402

TOOLS = pathlib.Path(__file__).resolve().parent
STAGES = ["ingest", "transcribe", "fillers", "align", "cut", "cards", "assemble", "clips"]
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
    die(f"unknown stage {stage}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new", help="write episode.json and ingest")
    n.add_argument("slug")
    n.add_argument("package")
    n.add_argument("--title", required=True)
    n.add_argument("--number", required=True)
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
    a = ap.parse_args()

    if a.cmd == "new":
        d = EPISODES / a.slug
        cfg = {"slug": a.slug, "title": a.title, "number": a.number,
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
                  ("clips.json", "clip list"), ("out/clips", "clips")]
        print(f"{a.slug}: {cfg.get('title', '?')} (episode {cfg.get('number', '?')})")
        for f, label in checks:
            print(f"  {'done' if (d / f).exists() else '    '}  {label:<18} {f}")
        return 0

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
