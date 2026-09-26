# tools

Everything that draws the brand, plus one script that checks handles.

| | |
|---|---|
| `eye.py` | The mark and its expressions. Everything else imports it. |
| `banner.py` | Social banners, per platform, with a safe-area check. |
| `show.py` | The show's furniture — cover art, cards, thumbnail, 9:16 plate. |
| `eyeanim.py` | The mark moving: expression chains as frames and video. |
| `portraits.py` | Contributor portraits, toned to one ink/paper ramp. |
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
