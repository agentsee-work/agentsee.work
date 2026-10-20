# Podcast pipeline — from a Riverside recording to the deliverables, by script

One recording in, everything out, with nobody opening an editor. Written for
the next episode, like the music and animation docs, because the traps are
the useful part.

**Status: built on episode 0, 7 October 2026; the release stage added after
episode 1, 13 October 2026.** Every stage ran end to end on the real
recordings. Nothing published yet.

## The shape

```
  Riverside  ──(one download: Export all → Timeline, Premiere Pro)──►  timeline.zip
      │
      ▼
  tools/ingest.py      unpack, read the XML, write manifest.json
      │                   who is on which track, their offsets, Riverside's layout and chapters
      ▼
  tools/transcribe.py  faster-whisper on the GPU, one pass per track
      │                   words.json — every word, timed, with its speaker known for free
      ▼
  tools/cut.py         the edit as data: edl.json
      │                   head and tail, pauses closed, chapter cards placed in silence
      ▼
  tools/cards.py       title and end cards animated through eyeanim's chains, chapter cards
      ▼
  tools/assemble.py    gate, match and mix the voices; lay the cues; cut and compose the video
      │                   episode.mp3 / .wav / .mp4, captions.srt, chapters.txt, transcript.txt, qc.json
      ▼
  tools/clips.py       vertical clips from clips.json, captions lit word by word
      ▼
  tools/release.py     notes, chapters in three shapes, transcripts, art, every file hashed: release.json

  tools/episode.py     runs them in order, from episode.json
```

Rendered output lives in `build/episodes/<slug>/`, which is gitignored. The
recipe is the repo; the episode is not.

## What Riverside is for

Recording, and nothing after it. It records each person locally, so the
tracks are clean and start together, and its timeline export packages them
with an XML that states the offsets and the chapters it guessed. That package
is the only thing the pipeline takes from Riverside.

What it does not give, and why the pipeline does these itself:

- **No word timing.** Riverside's transcript is TXT or SRT, per sentence. The
  cut, the captions and the speaking-panel bar all need words, so Whisper
  runs locally.
- **No cut list on any plan we would pay for.** Timeline export is XML or
  AAF, carries trims and chapters only, and is a Business-plan feature. We do
  not need it to be more: the edit is decided here, from the transcript.
- **Its own edit comes along uninvited.** If the studio's editor has been
  used on the recording, the XML is that edit: on episode 1, 580 clips per
  audio track (its filler and pause removal, 346 s of 46 minutes) and a
  video track cut to whoever was speaking. Ingest records the edit in the
  manifest as the stretches it kept, and does not apply it; the cut here
  starts from the raw track. Riverside's chapter marks are in the edited
  sequence's time and are mapped back through the clip list.
- **An edited timeline hides the alignment.** On a raw timeline each track
  is one clip and its start is its offset. On an edited one the leading
  pause has been trimmed from every track, by a different length each (on
  episode 1, 35 frames from one and 36 from the other, with a two-frame stub
  of the second left at the head), so the first clip of each track says
  nothing about how the tracks line up. Reading it that way put one voice
  1.5 s late for a whole episode. Ingest now reads every track's alignment
  (clip start less in-point) at one sequence time they all cover, and warns
  if that reading differs between the cuts they share.
- **No programmatic download.** The API is Business-only and on request, the
  MCP returns no file links, and the terms forbid automating the button. The
  download is the one manual step and it is one click.

## The opening, stated once

The sting plays from 0 and lands on its tonic at 6.4s with three beats of
silence after it; that gap was written for a title to resolve into. Dialogue
enters at 6.0s, on the landing. The bed loop runs under the first stretch of
talk and fades out by 24s. The title card crossfades into the two-up over the
first word. Chapter cards sit between segments for the length of the button.
The outro starts half a second after the last word, the end card fades in
with it, and the episode ends when the outro does.

Every one of those numbers is an argument to `assemble.py` and a default
here, not a decision made per episode at 11pm.

## The cut

Riverside's editor removes filler words and pauses well, and its timeline
export carries those cuts. But its studio is unreliable enough that the edit
is done here instead, from the same material: every word timed, and the
audio itself.

`cut.py` removes three things. Fillers (um, uh, erm, as Whisper heard them).
Stammers: a word said twice, or a word that is the start of the next one,
with the first going and the speaker landing on the last. Pauses longer than
0.7 seconds, closed to 0.4. The numbers are arguments.

What makes a cut inaudible is not where the transcript says the word ends.
Whisper's word timing is good to about 50 ms and it stretches a word's end
across the pause after it. So every cut is placed on a 2 ms energy envelope
of the summed dialogue: the edges of a removed word are snapped to the
quietest moment nearby, the silent part of its span is given back to the
pause rule, and `assemble.py` crossfades 20 ms across each join with the
video cut hard at the same point. Silence is measured relative to the noise
floor, not to the speech: two mics' room tone and breathing sit at around
-48 dB on episode 0 while the floor is -62 and the speech -37.

A removal longer than a word could be is Whisper's span being wrong, not the
speaker being slow, and it is skipped: a missed stammer is invisible, a
chopped word is not. The tool prints what it left alone.

The clips follow the same edit list, so a clip has the same cuts as the
episode and the same crossfades.

Two guards came from the first review. A stammer must be two tokens from the
same transcript segment: Whisper sometimes emits the last word of one segment
again as the first of the next, and that is one spoken word, which the cut
sliced in half ("coding in C, which was quite chall—"). And in `clean` mode
a filler or stammer is only removed when both edges of the cut are quiet; a
cut that joins on sound is the one you hear. The review's own notes — take
that word out, leave that alone, start the section here — go in
`removals.json`, by phrase near a time or by seconds, with `keep: true` for a
span no rule may touch, and are applied before the rules.

The second review found words being clipped next to cuts. Two causes. The
pause rule was measuring silence on the audio alone, and a soft word — "them",
"the" — sits below the silence threshold, so a pause that ran into one ate
it. And a stammer's cut was snapped to the quietest point near where Whisper
said the word started, which for "focus was" was inside the fricative at the
end of "focus". Both are answered by protection: every word that stays owns
the sound inside its span, found at a lower threshold so soft words count,
widened by 30 ms, and no rule may remove a protected hop. Not the span itself:
Whisper hands the pause on either side of a word to the word, so spans cover
most of the silence, and protecting spans protected 85% of the episode.

The same review heard ums that were not being cut. They are not in the
transcript: Whisper leaves most disfluencies out and stretches the word
before over them. The prompt trick ("Um, uh, like...") made it transcribe
five more and lose sixty-one words including the whole opening. An acoustic
rule — a second burst of sound inside an over-long span — found 196
candidates, nearly all of them the second half of ordinary words after a stop
consonant, and was removed. The fix that worked is erm (MIT), run per track by `fillers.py`, and the
useful half of it turned out to be its transcript rather than its cut list.
erm runs Whisper with a prompt that keeps disfluencies — "Um, uh, er, erm,
ah, hmm. Like, you know, I mean, sort of. Verbatim transcription including
all filler words" — and on these tracks that heard every um as its own
token with the words either side of it timed, and lost no words: 960 on
James's track against our 935, 68 fillers against our 41. Our own attempt
at a prompt had lost 40%; the difference is a prompt that asks for verbatim
rather than one that lists names.

`cut.py` takes each verbatim filler token as a cut between its neighbours,
but not at the token's own span. Whisper hands the um's sound to the word
before it and parks the "um" token on the pause after, so the token span is
mostly silence; a cut there removes the pause and leaves the um, which is
what the second medium render sounded like (54 of 75 fillers survived it,
by the test below). The um is the first stretch of sound after the previous
token ends, looking 150 ms back past that end because the token's end is
late or the pause is short; every stretch up to the token's end, stopping
well before the next word, whose onset often arrives a quarter of a second
before its own token — and only where the other speaker's track has no
sound. That evidence is better
than ours, so it overrides the neighbours' protection inside the cut. erm's
acoustic detectors are taken only for sound outside any transcribed word,
because inside a word a quiet "-ing" tail reads as a gap to them.

Three things that did not work on the way, recorded so nobody repeats them.
Removing a filler by the bursts of sound over it, with each neighbouring
word's own burst found by length: fragile, left half the ums. Assigning
bursts to words by order and counting: ate real words ("We are both").
A held-vowel test on the audio, spectral-centroid stability and voicing,
as the arbiter: a short word like "we" or "yeah" is one steady vowel and
passes it exactly as an um does.

The timestamps themselves were the last fault, and the largest. Whisper's
word times on this recording run up to half a second *early* around
fillers: "developers" was timed 15.06 to 15.58 when its sound runs to 15.86,
and the "uh" after it timed 15.88 when it is at 16.2. Every rule that read
those times — the "first sound after the previous word", the transition
dip, the protection — was anchored to a wrong clock and cut the end of the
word before each um while leaving the um. A forced aligner (`align.py`,
wav2vec2-base-960h, character-level CTC, Viterbi) does not guess times from
a transcript of its own; given the words, it finds where each one is, and
its character spikes land within a frame or two of the audio. Its word
spans are spikes rather than acoustic extents, so a filler token is tiny;
what it fixes is the neighbours. The um is the sound between the previous
word's last spike and the next word's first spike. With that, the excerpt
that had read "Yeah, uh, we, um, both developed, uh, we worked, um, a
couple" reads "Yeah, absolutely. We are both developers. We worked together
a couple of years ago now."

Three rules follow from the aligned words. A filler is the sound between
its aligned neighbours, found ten dB under the speaker's line because a
soft um is still an um and nothing else is there. A stammer's first try is
the sound between the word before it and the repeat, with any filler
between them skipped ("quite, um, quite"). And an isolated burst in the
middle of a gap of at least 0.4 s between two words, clear of both by 120
ms and quiet either side, is something said and not written down, and
goes. The aligner's spans end at the last character spike, 80 ms or so
before the word's sound does, so every search starts 80 ms after the
previous spike and ends 80 ms before the next; without that margin the
"sound in the gap" rule chopped the ends of words, 173 times. The guard
against cutting the other person asks whether they have a *word* there, or
speech-level sound of 0.3 s or more with no word for it: a backchannel hum
under an um is not worth keeping; a "thank you", or a reply Whisper never
wrote down, is. An um that has ended by the time the other person comes in
is still cut, up to there; one they talk over stays, as an editor would
leave it. What counts as sound on a track is ten dB under the speaker's
line but never under their local floor, which on a track gated at the
source rises and falls.

`transcribe.py --from-aligned` then makes the aligned verbatim transcript
*the* transcript, so captions, protection, stammers and clips all use the
same corrected clock. The unprompted transcription is kept only as a
fallback when erm is not installed.

The aligner works on the whole track at once. Its first version aligned
twenty-second chunks cut at Whisper's word gaps, and where Whisper was two
seconds out — "absolutely" written at 129.0 when it was said at 126.8 —
the word fell outside its own chunk and was forced onto the wrong sound.
Now the model's log-probabilities are computed in overlapping windows,
stitched, and one Viterbi pass places every word of the track with no
reference to Whisper's clock at all. Two things the transcript gets wrong
are handled in the same pass. Whisper writes "Thank you." into a gated
speaker's silence, and the aligner, made to put it somewhere, crams it
into a few frames of nothing: a word with no sound under it is marked a
*ghost* and dropped by `--from-aligned`, which stops it opening the gate
and shielding an um. And Whisper drops the odd short utterance altogether —
a reply of a second and a half in the middle of the other person's story —
so `verbatim_words.py` transcribes again, on its own with a little
context, every run of speech-level sound the transcript has no word for,
and adds what comes back as `filled` words; a laugh comes back as nothing.
What it cannot recover, the gate and the guards still treat as speech, by
sound.

Fifty-one of the 66 ums on episode 0 run straight on from the word before
them with no silence between — "are, um", "a, uh", "flavor, um" — and there
Whisper's boundary between the two tokens is late by up to a quarter of a
second, which leaves the front of the um in. For those the cut finds the
dip at the transition, the lowest point of the smoothed envelope within a
quarter second that sits 4 dB below the peaks either side; failing that, if
the previous token is longer than its word could be, the overrun is the um;
failing that it leans 40 ms early. Two detectors were tried for the ums no
transcript wrote down and both were put aside: erm's acoustic passes score
as fillers on almost none of Uhm's frames and sit on ordinary words or
silence, and Uhm (Desert Ant Labs' on-device classifier) runs half a second
late with a diffuse peak, so it can confirm an um but not place one.

Sound on a speaker's own track is judged against that speaker's own line,
halfway between the quiet end of their speech and the loud end of their
silence while the other person talks: -52 for James, -63 for Abrar on
episode 0, against -48 on the summed track. A soft um said over the other
person sits below the summed line and is still a sound on its own track;
and the floor of a locally recorded track is so low (-74, -84) that "floor
plus a margin" counts room tone as sound.

The acceptance test is not a transcription of the render. Both the verbatim
prompt and the plain model report fillers *at the joins* where ums were
removed — 40 of the plain model's 47 sat within a quarter of a second of a
cut — because Whisper hallucinates a filler at an abrupt discontinuity. A
level check just before each join cannot tell a leftover um from the tail of
the word before it. What works is cross-correlating each um's own source
audio against the rendered file around its join: absent at 60 of 65 on
episode 0, with the previous word running cleanly into the next.

Which setting is right is a matter of listening, so `variants.py` renders
every one as an mp3 with a list of what it cut, in that mp3's time, next to
it. Pick one, render it with `assemble.py --edl`.

## Levels

Each speaker's track is gated by *their own words*, plus any speech-level
sound the transcript has no word for, not by a threshold. A threshold gate
breathes and chatters on a quiet speaker; a gate that opens where the
transcript says they spoke does neither — and a gate that opened *only*
there muted a reply Whisper had dropped, so sound of 0.25 s or more at the
speaker's own speech level opens it too. The gated tracks are matched to
each other, summed and given a plain gain to -16 LUFS into a float file,
peaks and all. Only after the cut is the dialogue compressed (2:1 from
-14 dBFS RMS, 20 ms in, 400 ms out), limited and normalised. The
compressor is there because the limiter alone was doing the levelling:
raw speech peaks fifteen to twenty dB above its loudness, so a brickwall
at the ceiling worked five or six dB on every loud syllable with a 60 ms
release, and that was heard as the level ducking and recovering at every
onset — most of all at a cut, where a quiet tail meets a loud onset. Of
four chains measured on the same bus the slow 2:1 moved the gain least
from one 50 ms window to the next; a 2.5:1 from -17 rode it more than the
old limiter, not less. It runs after the cut so its gain state flows
across every join instead of being frozen at whatever it was either side
of the material removed, and the limiter is left with the odd plosive. The
cues arrive already levelled by `cues.py` — foreground at -16, bed at -30 —
so the relationship between voice and music is fixed in two files and
nowhere else.

Each join has its own crossfade. Across a pause, a chapter seam or an
editorial cut both sides are quiet and a 60 ms fade hides the change of
room tone; a filler cut sits right against a word and keeps 20 ms.

The whole mix is then limited, normalised once more, and *measured*. `qc.json`
holds measurements of the written files. See `THEME-MUSIC.md` for why not
the forecast.

## The release

Assemble and clips make the media. `release.py` makes everything a
publisher needs to put it anywhere, from what they made, and writes it to
`out/` with `release.json` as the index. It renders nothing but a picture,
runs in seconds, and is safe to re-run.

- **Chapters in three shapes, from one list.** The render's own chapter
  times (the mp3's ID3 chapters, the video's cards) become `chapters.txt`
  in YouTube's description format, which YouTube turns into the chapter
  bar and the timestamp links, and `chapters.json` in the Podcasting 2.0
  shape that Apple, Pocket Casts and Fountain read. Podlove Simple
  Chapters, the form Spotify reads, are an element of the feed and are
  built from the same list there. YouTube wants the first chapter at 0:00,
  three or more, ten seconds apart; the release warns when they are not.
- **The transcript as WebVTT and as JSON.** The VTT carries a voice tag per
  cue, so Apple shows who is speaking when it changes; the JSON is for the
  episode page. The cues follow the caption rule with a longer line: never
  across a speaker change or a long pause, closed at a sentence end. The
  words are tidied for a reader, as `transcript.txt` now is: fillers out,
  "i" capitalised, a capital at the start and after a stop. The times are
  not touched.
- **A guid, minted once and kept in `episode.json`.** Every directory keys
  an episode on it; a guid that changes is a duplicate episode in every
  listener's app, so it is never derived from the title or the date.
- **The notes, in the shape AGE-61 asked for**: what, who, the chapters with
  times, what was mentioned, which of the ten questions it touched, where
  to find people, the standing footer. Written as `notes.md`,
  `description.html` in the five tags Apple allows (p, ol, ul, li, a),
  `description.txt`, and `youtube.txt` with the title and the description.
  The free text comes from a `notes` block in `episode.json` (a summary,
  links, guests, question numbers, tags), written by a person or by Claude
  from the transcript; the questions' own text is read from the site so it
  is typed once. Without a summary the notes build from the chapter list
  and the warning says so.
- **The clips, placed in the episode.** A clip's first surviving word is
  mapped through the edit list to the output's time, so a post can say "at
  6:52" and link there, and the feed can carry the clip as a
  `podcast:soundbite`. Each clip also gets a VTT of its captions in its own
  time, alt text for the platforms that take it, and a post text.
- **Episode artwork**, 3000 square and a 1400 JPEG, for the directories
  that show one beside the cover.
- **Every file with its size, type, duration and SHA-256**, so a publisher
  can tell a re-rendered clip from the one it posted and a feed can state
  the enclosure's length.
- **A day-by-day plan**: the episode on the release day, one clip a day
  after it, for a publisher to follow or override.

**Approval.** `episode.py approve <slug> --release YYYY-MM-DD` records who
approved the episode and when in `episode.json`, with the hash of every
deliverable, sets the release date the plan counts from, and refuses while
the release carries a warning. `episode.py stage <slug>` then puts the
approved episode in the right spot (`stage.py`): the Riverside package, the
edit as data and every deliverable to the private archive bucket, and the
public files (mp3, clips, art, transcripts, chapters) to the media bucket
behind `media.agentsee.work`, both declared in `infra/podcast/`, an OpenTofu
root of its own with its own token and state. A Worker on a route in front of
the media host serves the bucket and writes one row per request to Analytics
Engine, the address hashed at the edge; `infra/podcast/workers/README.md` maps
the columns the download counting is done from. It refuses an
episode whose files changed since the approval, carries every object's
SHA-256 as metadata so a re-run puts only what changed, and records what
went where in `out/staged.json`. The R2 token comes from the vault through
`op run` and `tools/stage.env`; boto3 lives in `build/venv-stage`. Until an
episode is staged its masters exist on the workstation only, so keep the
Riverside package.

Every time in it is the output's, the one the mp3 and the mp4 share. The
show's own words (name, people, URLs, the footer) are in `tools/show.json`,
typed once; the mp3's album tag reads it too. The name is not settled
(AGE-59), and the description is a placeholder the release warns about
until it is written.

### On the site

`tools/site.py` puts every staged episode on the site: `public/podcast/feed.xml`
(RSS 2.0 with the itunes and podcast namespaces: enclosure, guid, chapters,
both transcripts, the people, a soundbite per clip), `public/podcast/` (the
show, how to listen, the directories that list it (`directories` in
`tools/show.json`), the episodes) and `public/podcast/<slug>/` (the player,
chapters that seek it, the links and questions from the notes, the moments
that went out as clips, the people, the transcript folded). It takes an
episode only when `out/staged.json` matches the release, so nothing on the
site points at a file that is not on the media host, and only once its
release date has arrived (London time): until then the show page names it
with its date and it has no page and no feed item. Going live is a run on
the day, a commit and a push; `--as-of` previews a later day. The masthead, the
classifieds and the footer are lifted from `public/index.html` when it runs,
so the mark has one copy. Output is deterministic; commit what it writes and
the push deploys it.

Apple wants a non-zero integer in `itunes:episode`, so episode 0 carries none
and takes its place from its date. The feed's dates are the release dates
from `episode.py approve --release`, at 06:00 London time. The one script the
pages add, `assets/player.js`, makes a time link seek the player; without
JavaScript the link is a time. The CSP in `public/_headers` allows media from
`media.agentsee.work` and nothing else that is not first-party. Short links,
`agentsee.work/p/<id>`, one per post, are a second Worker on the zone: a KV
lookup, a row in Analytics Engine, a 302. They are how a click back from a
platform is attributed without a tracking parameter on the page.

### Into the engine

What happens after staging is the engine's, in `engine/` (its README is the
map; the design is AGE-69). `engine.py ingest <slug>` reads `release.json`
and `staged.json` into a SQLite ledger; `engine.py plan <slug>` turns the
release's day plan into one post per asset per account, text drafted and cut
to each platform's budget, a short link minted per post (`agentsee.work/p/
e1xc02`: episode 1, X, clip 2) so every click back is counted at the edge;
the publisher sends what is due through one adapter per platform, YouTube
first (upload, `publishAt`, captions, thumbnail, playlist), and makes a card
for a person to post everywhere else until that platform's adapter exists.
`engine.py collect` reads back what everything did, daily: the edge's log
into listens (the IAB's: one listener, one file, one day, a minute of it,
no bots) and clicks, and each platform's counts into dated snapshots.
`engine.py serve` is the screens: Episodes, Posts, By hand, Reach,
Audience, Health.

The engine's home is the mail box, behind a Cloudflare Tunnel with Access
in front (`engine/home/README.md`). It has no `build/`, so `stage` now
archives `staged.json` beside `release.json` and `ingest` reads both from
the archive bucket; an adapter that needs a file fetches it from the
archive on demand, checked against the hash the ledger holds; and the
ledger itself is copied to the archive nightly, which is what a rebuilt box
restores from.

## What bit, in order

**The venv's python is a symlink to the system python.** `transcribe.py`
re-executes itself under `build/venv-transcribe` if it is not already there,
and the first version compared resolved executable paths — which are the same
file. Compare `sys.prefix`, not the binary.

**ctranslate2 loads CUDA through the dynamic loader, not pip.** The cuBLAS
and cuDNN wheels install fine and are then not found. The script puts their
`lib/` directories on `LD_LIBRARY_PATH` and re-executes once; the variable is
read at process start, so setting it in-process is not enough.

**A mono file duplicated into both channels measures 3 dB louder.** BS.1770
sums channel energy, so dialogue normalised to -16 as mono comes out at -13
once it is in a stereo mix next to cues that were levelled as stereo. The
dialogue goes in at -3 dB and the final pass measures the file anyway.

**`loudnorm` leaks its internal sample rate.** Known from the cues, hit again
in the first proof render: 96 kHz audio in an MP4 because `-ar` was not
passed. Every ffmpeg call here states the rate.

**xfade's offset is where the second input starts.** Not where the fade is
centred. The two-up has to start at exactly the second the dialogue audio
does, so the offset is `--dialogue-in` and the fade runs *after* it, over
the first word, rather than before it.

**The aligned MP4s in the timeline package have no audio stream.** So the
WAV cannot be checked against its own video by correlation, and the two
WAVs cannot be checked against each other either: both hosts wear headphones,
so there is no bleed to correlate. The XML's offsets are the alignment, and
ingest records them rather than assuming zero — on episode 0 one track starts
two frames late, on episode 1 three.

**720p.** Both cameras recorded at 1280x720, which is a studio setting as
much as a camera limit. The two-up upscales each panel by half to fill
1080p, as Riverside's own layout does. The clips do not upscale at all: two
1080x675 crops of a 720p frame fill the plate's 4:5 band exactly, which is
why the plate's band is 4:5 and not the 16:9 it started as.

**Do not tell Whisper the names.** The obvious fix for "Agency" and "Abra"
is an `initial_prompt` with the show's vocabulary, or `hotwords`. On episode
0 the prompt cost 40% of the words — whole chunks skipped, and the prompt
itself transcribed as speech on line one — and hotwords alone cost a third,
with a different third each run. The clean decode is stable at 1,431 words.
Spelling is fixed afterwards, deterministically, by `fixes` in episode.json,
which loses nothing and is diffable.

**The pipeline's own `pkill` matched the shell that ran it.** A background
command that kills "transcribe.py ep0" and then starts transcribe.py ep0 kills
itself, with exit 144 and no message. Kill by pid, or not at all.

**A two-way conversation leaves almost no silence.** Nine minutes of
episode 0 had one gap over 1.5 seconds. So pause-closing barely applies,
and a chapter card cannot be placed by a rule that looks for a seam near a
time: the seam-finder preferred any sentence-ending gap within 25 seconds,
and put one card in the middle of a sentence and another 24 seconds late.
Where a section starts is an editorial judgement made by reading the
transcript, so `chapters.json` now quotes it: `at` is the first words of
the section and `after` the last words of the one before, matched on the
speaker's own run of words, and the card goes between them with whatever
was said in between ("nice, cool, yeah", a handover question) dropped. The
person or agent writing the file reads the transcript and chooses the
sentences; the tool only finds the quiet point either side of them. A
phrase that is not there, or is said twice, stops the cut with a message
rather than guessing.

**A clip edge must snap to the word, not the speech stretch.** The first
version moved an end to the end of the stretch it fell in, which in a dense
conversation is the end of the paragraph, a minute later.

**`python3 -I` does not see user-site packages.** numpy, scipy, soundfile
and Pillow are installed for the user, not the system, so an isolated
interpreter reports them missing. The tools run as plain `python3`.

**Measure silence against the floor, not the speech.** Speech minus 28 dB
was below the noise floor of two summed mic tracks, so almost nothing was
silent and one pause in nine minutes was closed. Floor plus 14 dB found the
pauses that are actually there.

**Whisper's span for a stammered word can be a second long.** Removing it
takes the word before. Cap a removal at a plausible length for the word and
skip it otherwise.

**Trim video by frame count, not by seconds.** `trim=start:end` keeps every
frame whose time falls inside the range, which rounds each stretch up to a
frame. Seventy stretches put the picture a quarter of a second behind the
sound by the end of the episode. Each stretch now gets the number of frames
the cumulative output time says it should, so the drift can never exceed
half a frame.

**loudnorm goes dynamic when linear would clip, and says nothing.** The
linear mode is the one that leaves dynamics alone, and it silently switches
to dynamic — landing a dB or two short of the target — whenever the gain it
needs would push the true peak past the ceiling. Raw dialogue needs twenty
dB. The normaliser now applies the gain and limits the peaks in a pass of
its own, then measures, then lets the linear pass do the last fraction of a
dB with headroom to spare.

**`alimiter=limit=-2dB` limits at 0 dBFS.** The option is linear and takes
no suffix; the suffix is swallowed and the limit becomes 1.0. It is also a
sample-peak limiter, and true peaks overshoot a sample limit by up to a dB.
The normaliser passes a linear value 2.5 dB under the true-peak ceiling,
turns `level` off so the limiter does not quietly raise the level to its
limit, and repeats until the residual gain will keep the true peak under
the ceiling. Measure the true peak after limiting; do not assume it.

**libass wants a static font.** The site ships Newsreader as a variable
woff2. `clips.py` instances it once, at a reading weight, into `build/fonts/`.

- **Two files called `chapters.json`.** The one at the episode root is the
  input: titles with the quoted words they start at. The one in `out/` is
  the release's output: Podcasting 2.0, with times. Edit the first.

## Operating it

```sh
python3 -m venv build/venv-erm            # Whisper with fillers kept, and erm's acoustic passes
build/venv-erm/bin/pip install erm "av<16" nvidia-cublas-cu12 nvidia-cudnn-cu12
python3 -m venv build/venv-align          # the forced aligner, GPU build of torch
build/venv-align/bin/pip install torch transformers
python3 -m venv build/venv-transcribe     # only needed without erm
build/venv-transcribe/bin/pip install faster-whisper nvidia-cublas-cu12 nvidia-cudnn-cu12

./tools/episode.py new ep1 ~/Downloads/timeline.zip --number 1 \
    --title "Running our own mail server, on purpose" \
    --names 6ac4c0cd="James Hartt",6ac4c0ce="Abrar Mahmood"
./tools/episode.py run ep1
./tools/cut.py ep1 --show                  # read the edit before trusting it
./tools/clips.py ep1 --propose 8           # then write clips.json, and
./tools/clips.py ep1
./tools/release.py ep1                     # after a notes block in episode.json; see The release
```

`tools/README.md` has every option. On a 32-thread machine with the 2080 Ti
a nine-minute episode runs from the transcripts to the deliverables in
about nine minutes, two of them the animated cards: both tracks go through
erm at once (90 s), the aligner runs on the GPU (20 s), the picture is one
encode (under 3 min), and the clips render in a pool alongside it (60 s).
The picture is encoded by the card's NVENC when ffmpeg can reach it
(`--cpu-encode` forces x264): episode 1, forty-six minutes recorded and
thirty-seven cut, took 44 minutes to assemble on x264's slow preset, 35 of
them the two 1080p encodes. NVENC's quantiser is calibrated against x264
by PSNR (cq 24 for the deliverable, 20 for the two-up). The episode's
graph opens the two-up once per chapter, seeked to it and read only across
it: one trim branch per kept stretch made every frame visit all 680 of
them, and one select per chapter over a single decode let the decoder run
the whole file ahead of the encoder — fifty gigabytes of frames waiting on
the concat, and the machine out of memory. A clip's renderer seeks the
source the same way; it used to decode forty minutes of 1080p to reach a
clip near the end, and six clips now take a minute instead of four. The
audio stage of a long episode is the next thing to look at: twelve
minutes, most of it loudness passes over the full length.
Re-running the transcription is not bit-reproducible — Whisper on the GPU
returns a word or two more or fewer each time, and the cut moves by a
second — so a chosen edit is re-rendered with `episode.py run <slug>
--from cut`, not from the start. The two conventions worth adopting on
record: say a slate ("rolling", "that's a wrap") so `cut.py --slate-in
rolling --slate-out "that's a wrap"` finds the edges, and set the studio to
1080p before pressing the button.
