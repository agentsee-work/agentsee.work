# Working notes — "Every cut landed, and the um was still there"

**Published as Issue No. 5**:
[`public/issues/05-podcast.html`](../public/issues/05-podcast.html). That is
the version that ships, cut shorter and with the meta stripped.

This file is the longer argument it was cut from, plus the notes for the
next recording. It stays in `docs/` because only `public/` is published.
The technical record — every rule, every trap, every number — is
[`PODCAST-PIPELINE.md`](PODCAST-PIPELINE.md); this is the story of how it
got that way.

If the two ever disagree, the published page wins. This is the workings.

---

## The argument, at length

We recorded the first episode of the podcast on 7 October and did not edit
it. Scripts did: 149 cuts, no timeline, nobody dragging a playhead. The
thing that nearly beat us was not the music, the cards or the video. It was
the ums, which survived six attempts at removing them, and the reason was
half a second wide and sat underneath every tool we used.

The version to avoid writing is "AI edited our podcast", because it did not.
A model wrote every line of the pipeline, measured most of what it claimed,
and was wrong in a way that no amount of its own checking would have found.
What found it was one of us listening and naming a timestamp.

---

## 1. What we set out to do

Two people, building in public, who had just recorded nine minutes and three
seconds of themselves on Riverside and wanted it finished without
spending an evening in an editor. Not once: every time. The point of the
show is that we will be recording again next week, and the week after, and
the production has to be something we run, not something we do.

Riverside's export gives you more than you might expect. Its timeline
package holds one WAV per speaker, recorded locally and therefore clean of
each other, a video per speaker already aligned to it, and a Premiere XML
with the offsets, the layout and the chapter marks its own tools guessed.
What it does not give is a way to fetch that without clicking: the API is
on the plan above ours, and the terms do not allow a script to download it.
So the package is downloaded by hand, once, and from there every step is a
file written by one script and read by the next.

Its studio's editor did a genuinely nice job of removing fillers when it
worked. We could not get it to stay stable long enough to trust it with the
edit, and in any case an edit that lives in someone's browser is not one you
can re-run. We wanted the edit as data.

## 2. The shape

Ingest unpacks the package and writes a manifest: who is on which track,
their offset, the layout. Transcribe writes the words with times. Cut reads
the words and the audio and writes an edit decision list — the stretches of
the source that survive, in order, with the chapter cards placed. Cards
renders the title and end cards from the site's own CSS, through Chrome,
frame by frame, animated with the same model that moves the mark on the
homepage. Assemble gates and matches the voices, lays the sting, the bed,
the buttons and the outro around the cut, writes the mp3 with chapters, the
mp4 with the cards faded in, the captions, the transcript and the thumbnail.
Clips cuts the vertical versions for the socials, with the captions and a
bar that follows who is speaking.

The opening is stated once. The sting is 6.4 seconds long and resolves at
the end; the first word lands as it resolves, and the bed fades under it
over the next six seconds. Those numbers live in one place and the timing of
the whole mix follows from them.

Levels are measured, not predicted. Every file that ships is read back and
its loudness and true peak written to a QC file, because the first time we
predicted a level the limiter was silently doing something other than what
we had told it.

## 3. The ums

Whisper writes readable text, which means it leaves most ums out. Prompting
it to keep them cost it a third of the real words, and the prompt that did
not — a tool called erm, which runs Whisper with a prompt that keeps the
disfluencies and loses nothing — was the first real step. Every um became a
token with a time.

Then the cuts happened and the ums were still there. Six times. The joins
were clean, the words either side were clipped, and the um sat between them
untouched. Counting the ums in a transcript of the output said they were
gone, because Whisper, transcribing a render with cuts in it, hallucinated
fillers at the joins. Cross-correlating each um's audio against the render
said they were gone too, because it was looking for the right sound and the
cut had removed a different one. Two transcripts, one plain and one
prompted, agreed on every word time to within ten milliseconds, and that
agreement was offered as proof.

James gave a timestamp: seventeen to twenty seconds of the tightest cut.
Transcribing just that excerpt of the render and reading it as a sentence
gave: "Yeah, uh, we, um, both developed, uh, we worked, um, a couple." Every
cut had removed the end of the word before the um and left the um.

Whisper's word times on this recording run up to half a second early around
fillers. "Developers" was timed to end at 15.58 seconds when its sound runs
to 15.86; the "uh" after it was timed at 15.88 when it sits at 16.2. The two
transcripts agreed because they were the same model making the same error.
Every rule anchored to those times, and every energy analysis anchored to
them, took the end of the previous word for the um.

The fix is a forced aligner. Given the words, a small speech model
(wav2vec2, character-level CTC) finds where each one actually is, within a
frame or two. The um is then the sound between the previous word's last
character and the next word's first. The same excerpt reads: "Yeah,
absolutely. We are both developers. We worked together a couple of years
ago now."

The test that found it is the one we kept: transcribe a short excerpt of
the finished audio and read it. Not count it. Read it.

## 4. The second listen

With the ums gone, James listened to the whole thing and came back with
four notes. Each was a different kind of wrong.

**The level moved at every cut.** A brickwall limiter was holding the peaks
of raw speech, which sit fifteen to twenty decibels above its loudness, so
it was pulling five or six decibels on every loud syllable and letting go
in sixty milliseconds. That is heard as ducking, loudest at a cut, where a
quiet tail meets a loud onset. We measured four alternatives on the same
material. The compressor the model proposed first rode the gain more than
the limiter it replaced; the slow one (two to one, twenty milliseconds in,
four hundred out) moved it least. It now runs on the dialogue after the
cut, so its state carries across every join instead of being frozen on
either side of the material removed.

**A reply vanished.** Whisper had dropped a sentence and a half of James
entirely — "Manual memory management as well. I mean" — and everything
downstream trusted the transcript. The gate, which opens a speaker's track
where the transcript says they spoke, muted it; the cut over Abrar's um
took it. Now any speech-level sound with no word is treated as speech, is
transcribed again on its own with a little context, and that reply came
back. The same pass found the opposite error: Whisper had written "Thank
you." into Abrar's silence four times, and the aligner, made to put the
words somewhere, had stretched one of them across thirty seconds of nothing.

**The chapter cards were in the wrong places.** A rule looked for a seam
near the time we had given it and preferred any sentence-ending gap within
twenty-five seconds: one card landed mid-sentence, another twenty-four
seconds late. Where a section begins is not a property of the audio. It is
an editorial judgement, made by reading. So the chapters file now quotes
the first words of each section and the last words of the one before,
someone reads the transcript and chooses them, and the tool only finds the
quiet point either side.

**The clip captions were one and a bit lines.** Now two, balanced, centred
in the band under the lower panel, with fillers and the other person's
brief interjections left out.

Then we rendered six versions of the edit, from one that closes only long
pauses to one that takes every um, every stammer and every pause over
0.45 seconds, listened, and chose the tightest. The model recommended the
middle one; the two of us preferred the pace.

## 5. What working with a model was actually like

It wrote all of it: twelve scripts, a shared library, the document that
records every trap, the skill that tells the next run how to proceed. It
measured before it changed things, mostly. When it proposed a compressor it
measured four chains and found its own first choice was worse. When it
rewrote the aligner to work on the whole track it checked the result was
word for word identical to the old one before using it.

It was also wrong in ways worth naming. It built and removed four
heuristics for finding ums before the real cause was found, each one
plausible, each one measured against a test that could not see the fault.
It offered the agreement of two Whisper transcripts as evidence they were
right, when their agreement was the problem. It killed its own shell twice
by pattern-matching on a process name that matched itself. Its first
version of the aligner, cutting the audio into chunks at Whisper's own word
gaps, put a word on the wrong sound when Whisper was two seconds out,
because the word fell outside its own chunk.

What broke the loop every time was a human listening and naming a
timestamp. The model could not hear the episode, and its proxies for
hearing it — counts, correlations, agreement — all said it was fine. Given
"17 to 20 seconds", it found the cause in ten minutes. That is the division
of labour: we listen, it finds out why.

Three days, 7 to 9 October. The cut itself is now four seconds.

## 6. What it is for

Next week we record again, to have one in the can before the show starts
going out. A fresh recording goes from its transcripts to every deliverable
in about nine minutes on the workstation, down from eighteen, after an
afternoon of measuring where the time went: both tracks through the
transcriber at once, the aligner on the GPU rather than the CPU, the picture
in one encode instead of two, the clips rendered in a pool alongside it. The
only decisions a person makes are the ones a rule cannot: where each
chapter begins, which passages become clips, which of the six cuts to
publish.

Everything is in the repository, including the six attempts that failed.
The scripts, the document, the skill. If you record two people on Riverside
and want the edit as data rather than as an afternoon, it is yours.

This is the production side only: a recording in, the deliverables out.
Getting them onto the feeds — the hosting, the RSS, the socials, and
whatever of that can also be a script — is not built yet, and is the next
issue.

---

## Notes for the next recording

- Say a slate on record ("rolling", "that's a wrap") so the head and tail
  are found by the words, not by the first and last thing anyone said.
- Say "new chapter" or pause for a breath when a section genuinely begins;
  the chapters are chosen by reading, and a clean start is easier to quote.
- An um the other person talks over stays in. That is the right call and
  also a reason to let each other finish.
- Set the studio to 1080p before pressing record; the package was 720p.
- Re-render a chosen edit from the cut, not from the transcription:
  Whisper on the GPU returns a word or two differently each run and the
  cut moves by a second.
