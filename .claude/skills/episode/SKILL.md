---
name: episode
description: Turn a Riverside recording of the AgentSee podcast into the published deliverables (mp3, mp4, captions, chapters, clips) with tools/episode.py. Use when the user has a new timeline.zip from Riverside, asks to assemble, cut, re-render or clip an episode, or asks what state an episode is in.
---

# Producing an episode

Everything is scripted under `tools/`; read `docs/PODCAST-PIPELINE.md` once.
Output goes to `build/episodes/<slug>/` and is never committed.

## Steps

1. **Ingest.** The user downloads the Riverside package (Export all → Premiere
   Pro → Export → Timeline). Then:
   `./tools/episode.py new <slug> <zip> --number N --title "..." --names <id>=<Full Name>,...`
   Track ids are the file prefixes inside the zip; if the names are not known,
   run ingest without them, look at a frame from each video, and ask. Read
   ingest's warnings: if it says Riverside's kept stretches do not line up
   across tracks, the offsets are wrong and nothing downstream is worth
   running until they are right (episode 1 had one voice 1.5 s late).
2. **Run.** `./tools/episode.py run <slug>` does fillers → align (which also merges the
   aligned verbatim words into the transcript) → cut → cards → assemble, with clips alongside
   assemble when `clips.json` exists, then release. Fillers need `build/venv-erm`, align `build/venv-align`
   (GPU torch; falls back to venv-erm's CPU build), and only without erm does the plain
   transcription (`build/venv-transcribe`) run. See the doc for the three venvs.
3. **Choose the chapters by reading the transcript.** This is a judgement, not
   a rule: read `build/episodes/<slug>/transcript/transcript.txt` end to end,
   decide where each section genuinely begins (the sentence that opens the new
   thought, not the reaction to the last one), and write `chapters.json` with
   the words quoted: `{"title": ..., "at": "<first words of the section>",
   "after": "<last words of the section before>"}`. Quote five or more words
   so the phrase is unique; whatever is said between `after` and `at` (a "nice,
   cool", a handover question) is dropped and the card goes there. The first
   chapter needs only a title; a marker without a card takes `"card": false`.
   `cut.py` refuses a phrase it cannot find or finds twice. Never fall back to
   a time (`t0`): the seam-finder landed cards mid-sentence.
4. **Read the edit before trusting it.** `./tools/cut.py <slug> --show` and
   the transcript. Check the head and tail are the real start and end of the
   show; if not, put `slate_in`/`slate_out` (a spoken phrase) or `start`/`end`
   (seconds) in the `cut` block of `episode.json`, where `cut.py` reads its
   defaults, re-cut, then `episode.py run <slug> --from assemble`. A flag on
   the command line is lost the next time the stage runs; episode 1's slate
   was. Read `align.py`'s ghost list
   (words Whisper wrote into silence) and `fillers.py`'s recovered words; both
   should look right.
5. **If the user has review notes** ("remove the 'nice' at the start of section two",
   "the cut after X is odd"): put editorial cuts in `removals.json` (and `keep: true` spans for cuts they did not want), re-cut, and
   if the setting itself is in doubt run `./tools/variants.py <slug>` and point
   them at `out/variants/README.txt` and the mp3s; each variant's `.txt` lists
   its cuts at the time they occur. When they choose, run
   `./tools/variants.py <slug> --choose <tag>`: it records the settings in
   `episode.json` so `cut.py` reproduces that edit, then re-run cut, assemble
   and clips. Episode 0 chose `tightest`.
6. **Check the QC.** `out/qc.json`: every deliverable within 1 LU of -16 LUFS
   and under -1 dBTP, chapters present in the mp3, caption count sane. Look at
   `out/contact.png`.
7. **Clips.** `./tools/clips.py <slug> --propose 8`, read the candidates and
   the transcript, choose 3 to 5 passages that stand alone (a claim and a
   reason, ending on a line), write `clips.json`, run `./tools/clips.py <slug>`.
   Look at each clip's poster PNG. A clip's `title` is also the first line of
   its social post; give it a `hook` when the post should open differently.
8. **Write the notes, then release.** Put a `notes` block in `episode.json`,
   from the transcript: `summary` (two or three sentences in the show's
   register: what happened and what was learned, no claims the episode does
   not make), `links` (what was named and deserves one: the site's own issue
   on the subject first, then the tools and services), `guests` if any
   (`name`, `who`, `href`), `questions` (the numbers of the ten questions the
   episode touched; their text is read from the site), and `tags`. Then
   `./tools/release.py <slug>` (`episode.py run` does it last). Read
   `out/notes.md` and `out/youtube.txt` as a listener would; the summary is
   the one piece of writing that reaches every platform, and the user
   approves it. The release warns about anything a platform would reject
   (chapter spacing, lengths, a clip too long for a soundbite); fix the
   cause, not the warning.
   When the user says the assets are approved, and not before:
   `./tools/episode.py approve <slug> --release YYYY-MM-DD`, then
   `./tools/episode.py stage <slug>` to put the masters in the archive and
   the public files on the media host (it asks 1Password for the token).
   `stage.py <slug> --dry-run` shows what would go without credentials.
9. **Report** what was rendered, the measured numbers, the release's
   warnings, and anything the transcript suggests should be re-cut. Do not
   publish anything.

## Rules

- Never edit files under `build/episodes/*/source/`; they are the masters.
- Re-render from the data (edl.json, clips.json) rather than hand-editing media.
- Levels are measured, not predicted; quote the numbers from qc.json.
- `out/chapters.json` is generated (Podcasting 2.0, with times). The one you
  write is `build/episodes/<slug>/chapters.json` (titles and quoted words).
- The episode's `guid` in `episode.json` is minted once by release.py. Never
  change or remove it: directories key the episode on it.
