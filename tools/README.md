# tools

Everything that draws the brand, three scripts for the theme music, and one
that checks handles.

| | |
|---|---|
| `eye.py` | The mark and its expressions. Everything else imports it. |
| `banner.py` | Social banners, per platform, with a safe-area check. |
| `show.py` | The show's furniture — cover art, cards, thumbnail, 9:16 plate. |
| `eyeanim.py` | The mark moving: expression chains as frames and video. |
| `portraits.py` | Contributor portraits, toned to one ink/paper ramp. |
| `score.py` | Bar ranges out of the theme, as standalone MusicXML. |
| `cues.py` | Rendered cues normalised to consistent levels. |
| `loop.py` | A rendered loop's true length, measured and cut. |
| `handle-probe.sh` | Handle availability. Nothing to do with the rest. |

Everything is rendered through headless Chrome against the real stylesheet
values, for the reason `banner.py` says: art drawn by hand in an editor drifts
the first time a colour changes, and nobody diffs a PNG.

**Output goes two places.** `public/assets/brand/` is committed — banners,
cover art, cards, the expression sheets. `build/` is gitignored and holds video
and frame sequences, which do not belong in a git repository.

## The model

An **expression** is a pose: eight numbers on the mark.

```
lt, lb   lid travel, 0 open to 1 shut
tilt     skews the lid curve — carries wry, suspicious, angry, sad
hat      degrees of hat rotation. The fedora is the only brow this mark has
ix, iy   iris offset, viewBox units
ps       pupil scale. Under 1 is hard and contracted, over 1 is warmth
gl       catchlight scale. 0 is cold or dead, over 1 is wet
```

`./tools/eye.py --list` prints all 22 and what each one moves.

A **chain** is a sequence of expressions plus its own pace. Timing is part of
an emotion, not a global setting: a double take that eases like a sign-off is
not a double take. `./tools/eyeanim.py --list-chains` prints all 18.

A chain step is usually an expression name, but can be a dict of axis
overrides for a waypoint that does not deserve a name. `jitter` adds a fast
iris tremor; `roll` replaces a transition with a continuous arc.

## Recipes

```sh
./tools/eye.py --sheet                    # every expression, for review
./tools/show.py --all --sheet --proof     # the show furniture, + cover at 55px
./tools/banner.py bluesky                 # one banner
./tools/eyeanim.py --list-chains
./tools/eyeanim.py signoff --blink        # one chain -> build/eye/signoff/
./tools/eyeanim.py --reel --blink         # all 18 end to end, for watching
./tools/eyeanim.py --all --blink          # all 18 as alpha video
```

The theme is one 26-bar score; the show needs six sounds out of it.

```sh
./tools/score.py theme.mxl sting.musicxml --bars 5-7 --truncate 1
./tools/score.py theme.mxl bed.musicxml   --bars 1-2 --parts 'Acoustic Bass'
./tools/score.py theme.mxl outro.musicxml --bars 19-26 --title 'AgentSee — outro'
```

`--truncate 1` keeps the first beat of the last bar and fills the rest with
rests, which is how a sting lands: on a downbeat, then out of the way. Like
`portraits.py`, the source is not in the repo — pass your own `.mxl`.

Once the cues are recorded and exported, `cues.py` sets their levels:

```sh
./tools/cues.py masters/ --dry-run        # measure everything, write nothing
./tools/cues.py masters/ -o delivery/     # 48 kHz 24-bit WAV, levelled
./tools/cues.py masters/ -o delivery/ --bed-lufs -28
```

And `loop.py` turns the ×8 bed into a seamless loop:

```sh
./tools/loop.py bed-x8.wav --cycles 8                    # measure only
./tools/loop.py bed-x8.wav --cycles 8 --cut bed-loop.wav
```

It prints the period in samples, the tempo that implies, and a seam score per
candidate cycle, then cuts from the cleanest one.

`cues.py` targets −16 LUFS for cues that play alone and −30 for anything with
`bed` in its name, because a bed plays under speech and has to sit below it. The gap
is the point: setting it by ear per episode is how a show ends up burying the
dialogue one week and losing the music the next.

Defaults are final quality: 1080px, `--ss 2`, `--blur 3`, ProRes 4444 with
alpha. For iterating, `--size 540 --blur 1 --ss 1 --format none` is roughly
twenty times faster and fine for judging a pose or a path. Do not judge edge
quality or motion on a draft render; that is what most of the traps below are.

For an episode: chains come out as a PNG sequence *and* a `.mov`. Every editor
takes the sequence, so it works even if the video path disappoints. ProRes is
the default because its alpha survives a round-trip and can be proved to;
WebM's does not survive ffmpeg's own decoder, so that path is offered
unverified.

## Traps

Each of these cost an evening. None are guessable.

**Render one frame per Chrome launch.** This tool began by laying every frame
out as a grid and taking one screenshot, which is obviously faster. Identical
SVG in two cells of the *same* screenshot does not rasterise identically — 948
of 360×360 pixels differ, at full contrast — while the same content in two
*separate* launches is bit-identical. So every static edge shimmered: the hat,
which does not move at all, was 14% of the flicker in a clip. Arrangement makes
no difference; a single column still diverges.

**Deduplicate instead.** The cost comes back by rendering each distinct pose
once. A hold is one frame repeated; three quarters of a full run was redrawing
pictures it had already made.

**No glow on anything with alpha.** The drop-shadow is right against noir and
becomes a translucent amber cloud over 39% of a transparent frame, reaching
48px past the artwork. It is invisible while you develop against dark
backgrounds, which is exactly how it shipped.

**Render at the size it will be used.** 360px in a 1080 timeline is a 3×
upscale and a player using nearest-neighbour turns it into moving blocks.

**`--ss 2` for anything that moves.** On a shallow curve a one-pixel
antialiasing ramp still stair-steps, because the edge travels far sideways per
pixel down. 2 doubles the ramp; 3 adds nothing over 2.

**Lids pivot, they do not translate.** A translated lid drags its corners with
it, so at a modest squint the upper lid's corners sit below the lower lid's and
the two cross. Both lids are pinned at the canthi and only their middles move.

**The lid takes the light end of the palette in both themes**, like the pupil
and the glint, because a lid is a lit surface and its meaning is its lightness
rather than its role. Filled with `paper` it vanishes in noir; filled with
`ink` it becomes a black bar on newsprint.

**Do not calculate a loop's length from the score's tempo.** MuseScore's
audio export does not reliably come out at the written tempo — a bed written
at 132 rendered at 126, and every figure derived from the score was then 4%
wrong. `loop.py` measures the audio, which is the only thing that is true.

**Length / cycles is not the period.** It is an upper bound. The render ends
with the last note still decaying and that tail belongs to no cycle, so on the
×8 bed the naive figure came out 6% high — far enough that a narrow refinement
search could not get back and the tool confidently reported the wrong tempo.
Autocorrelate the envelope and use length/cycles only to bound the search.

**A loop cut from the first or last cycle will always have a seam.** The first
has nothing ringing into it and the last has no following cycle to ring into,
so both ends are discontinuous by construction. On synthetic bass those two
measured 20 dB worse than any middle cycle. Cut from the middle, and let the
seam score pick which middle.

**Audacity reads a sample selection in the *project* rate, not the clip's.**
A 44.1 kHz clip in a 48 kHz project will silently disagree with the figures
you type, which is reason enough to cut with `ffmpeg atrim` instead.

**Audacity has no CLI, and Audacity 4 has less than 3 did.** Macros and the
scripting pipe are both absent from 4.0, listed as not yet implemented, and
`mod-script-pipe` was 3-only. A converted `.aup4` cannot be saved back to
`.aup3` either, so there is no downgrade path once a project has been opened.
Treat the export as a manual step and automate everything after it.

**Integrated loudness needs about three seconds to mean anything.** EBU R128
gates on 3-second blocks, so a one-bar button measures as near-silence and
normalises to a scream. `cues.py` peak-normalises anything shorter instead,
which is the right treatment for a button regardless.

**MusicXML states the important things exactly once.** `divisions`, `key`,
`time`, `clef` and `transpose` appear in bar 1 and are assumed ever after. Cut
bars 19-26 out on their own and the result is a well-formed file that opens
without complaint and plays at the wrong speed, in the wrong key, an octave
out — because the bass's `transpose` went with bar 1. The last written dynamic
has the same shape of problem. `score.py` walks the bars ahead of the range to
collect all of it.

**`divisions` is not 4.** It is whatever the score says — 6 in this one. Any
arithmetic that assumes ticks-per-quarter passes its tests on a score that
happens to agree and is wrong on the next one.

**Find the title credit by its `credit-type`.** MusicXML holds the title
twice, as metadata and as engraved text, and the engraved one is a
`credit-words` among others. Every heuristic for picking it out — longest
string, first on the page, above some font size — also matches the composer
credit, so retitling an excerpt quietly took the composer's name off the page.

**Export MusicXML, not MIDI.** MIDI drops enharmonic spelling, so D♯ comes
back as E♭ and the key stops making sense on the page. It also drops
articulations, the distinction between a slur and a tie, dynamics as marks
rather than velocities, and written-versus-sounding transposition.

**Pace is not part of the path.** Build lingering as literal holds and the
movement stops dead; no amount of smoothing bridges a pause thirty frames wide.
Position should be one continuous curve, with a speed profile that dips where
the beats are and never reaches zero.

## Measuring

**PNG bytes are not stable across renders.** A checksum will report eight
banners changed when seven are pixel-identical. Compare pixels.

**Do not track the iris by its amber centroid.** The lid clips it, so the
centroid follows the lid and reports tens of pixels of travel during a blink.
Twice this produced confident nonsense. A filmstrip answered it in one look
both times.

**Motion blur damps the tremor well below its nominal amplitude**, averaging
sub-frames across roughly half a cycle. The number in the table is not the
swing you see, and the swing you see is not measurable from the frames. Judge
it by eye.

**Alpha coverage is not antialiasing quality.** The alpha ramp at the outer
silhouette can be perfect while every internal edge is a hard one-pixel
transition. They are separate questions and the second one is the one that
looks wrong.
