# Theme music — how the cues are made

One score, cut into eight cues, rendered from a sample library and levelled by
script. Written for the next time, because almost none of what bit us was
guessable.

**Status: done, 2 October 2026.** Eight cues delivered and levelled, two
seamless loops. Nothing left to record.

## The shape

```
  AgentSee.mxl  (MuseScore, 26 bars, E Dorian, 132 bpm)
        │
        │  tools/score.py --bars 5-7 --truncate 1
        ▼
  one .musicxml per cue ──► MuseScore + MuseSounds ──► Audacity ──► .wav
                             (render, GUI only)        (master)      │
        ┌───────────────────────────────────────────────────────────┘
        ▼
  tools/cues.py   ──►  48 kHz 24-bit, -16 LUFS / -30 for beds
        │
        ▼
  tools/loop.py   ──►  the two beds, cut to a seamless loop
```

Sources live on the composer's machine, like `portraits.py`'s photos. The repo
holds the tooling, not the score.

## The cues

| cue | length | role |
|---|---|---|
| `01-sting` | 10.3s | opening, lands on the tonic |
| `01b-sting-alt` | 5.0s | shorter opening, starts on the tonic bar |
| `02-outro` / `-dark` | 17.6s / 19.4s | close, diminuendo |
| `04-button` | 5.0s | transition |
| `theme-light` | 50.3s | the whole piece |
| `bed-loop` / `bed-dark-loop` | 3.64s | under speech, loops forever |

Foreground cues sit at **-16 LUFS**, beds at **-30**. The gap is the point: set
by ear per episode it drifts, and a bed that buries the dialogue one week and
vanishes the next is worse than one that is slightly wrong every week.

## What bit, in order

**MusicXML states the important things exactly once.** `divisions`, `key`,
`time`, `clef` and `transpose` appear in bar 1 and are assumed thereafter. Cut
bars 19–26 on their own and you get a well-formed file that plays at the wrong
speed, in the wrong key, an octave out. `score.py` walks the bars ahead of a
range to collect them.

**Tempo is written into the first part only.** So a bass-only excerpt had no
tempo at all, even one cut from bar 1, and rendered at MuseScore's default.
That one would have shipped: a bed looping at a speed nothing else agreed with.

**Export MusicXML, not MIDI.** MIDI drops enharmonic spelling — D♯ comes back
as E♭ and the key stops making sense on the page — along with articulations,
slur-versus-tie, dynamics as marks, and written-versus-sounding transposition.

**Audacity has no CLI, and Audacity 4 has less than 3 did.** Macros and the
scripting pipe are both absent from 4.0, and a converted `.aup4` cannot be
saved back to `.aup3`, so there is no downgrade path. Treat export as manual
and automate everything after it.

**MuseScore's command-line audio export is silent with MuseSounds.** The GUI
export works. Do not build a one-command pipeline through it.

**Integrated loudness needs about three seconds to mean anything.** EBU R128
gates on 3-second blocks, so a one-bar button measures as near-silence and
normalises to a scream. `cues.py` peak-normalises anything shorter.

**`loudnorm` reports a pessimistic forecast.** It predicted -19.0 LUFS for a
file that measured -16.4 once written, which made four of eight cues look off
target when none were. Measure the output, never the prediction.

**`loudnorm` also leaks its internal 192 kHz.** It oversamples to find true
peaks, and with no `-ar` on the output that rate lands in the file. "Keep the
source rate" has to pass the source rate, not nothing.

**A loop's length cannot be calculated.** Not from the score's tempo, and not
from file length ÷ cycles — the render ends with the last note still decaying
and that tail belongs to no cycle, which put the naive figure 6% high. Measure
the audio. `loop.py` autocorrelates the envelope and uses length ÷ cycles only
to bound the search.

**A loop cut from the first or last cycle always seams.** The first has nothing
ringing into it, the last has no following cycle to ring into, so both ends are
discontinuous by construction — they measured 70 dB worse than the middle
cycles. Cut from the middle and let the seam score choose which.

**Audacity reads a sample selection in the *project* rate, not the clip's.** A
44.1 kHz clip in a 48 kHz project silently disagrees with the figures you type,
which is reason enough to cut with `ffmpeg atrim` instead.

**Resample before cutting a loop, never after.** A resampler has no data beyond
a file's edges, and the edges are where a loop joins.

## Decisions worth keeping

**48 kHz delivery.** The masters came out of MuseScore at 44.1; everything video
touches is 48. Better to convert once, knowingly, through soxr than to leave it
to whatever an editor does quietly.

**Bass centred, never panned.** The ostinato sits G1–G2, roughly 49–98 Hz, and
fundamentals that low carry almost no directional information — you would hear
the pluck attack move and the body stay put. Panning it costs headroom and buys
no width. Opposing pans are worse still: a listener with one earbud, which is
most podcast listening, loses an instrument.

**A fade to nothing is an audio operation, not a notation.** A hairpin can only
make each successive pluck quieter; it cannot ride a note that has already been
struck. `morendo` and a niente hairpin state the intent for a reader. The actual
fade happens in the edit.

## Licensing

MuseSounds' standard licence permits use where the sounds are **combined with
other sounds within a musical composition**. Every cue qualifies — bass and
guitar together — except the bed, which is one library's output essentially
raw. MuseSounds Pro covers commercial broadcast and streaming outright and
removes the question. Read the terms before the bed ships under episodes.

## Operating it

`tools/README.md` has the commands and the full trap list. The short version:

```sh
./tools/score.py theme.mxl sting.musicxml --bars 5-7 --truncate 1
./tools/cues.py  build/audio/masters/ -o build/audio/delivery/
./tools/loop.py  build/audio/delivery/03-bed-x8.wav --cycles 8 --cut bed-loop.wav
```

Rendered audio lives in `build/`, which is gitignored. Only the recipe belongs
in a git repository.
