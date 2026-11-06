# engine/

The private side of the podcast: from an approved, staged episode to every
post on every platform, and back from every post to the numbers. The design
and the decisions are on Linear (AGE-69); this is what exists.

```
engine.py        the command line: ingest, plan, links, posts, publish, serve, mail, linear
ledger.py        the SQLite ledger: episode, deliverable, post, snapshot, spend,
                 click, download, lead, event; guest, code, session, linear_job
ingest.py        release.json + staged.json → episode and deliverables
plan.py          the day plan × accounts.json → posts, with short-link ids and
                 drafted text; the push of id → URL into KV
publish/         the publisher: __init__ runs what is due through an adapter
  youtube.py     upload, publishAt, captions, thumbnail, playlist
  x.py           X: chunked media upload, the post, the rotating refresh token
  bluesky.py     Bluesky: the art as a blob, a clip through the video service,
                 links and tags as facets, through the account's own PDS
  mastodon.py    Mastodon: the file, polled until processed, then the status
  hand.py        a card for a person to post, and a permalink to paste back
vault.py         the credentials the engine writes, X's and Linear's rotating
                 refresh tokens, via op
archive.py       the archive bucket: files fetched on demand, release records for an
                 ingest without build/, the nightly ledger copy and its restore
collect.py       the collector: the edge's log into listens and clicks, each
                 platform's counts into dated snapshots; what the screens ask
useragents.json  OPAWG's patterns for podcast apps, bots, libraries, browsers (MIT)
home/            the box: Dockerfile, compose, the runbook; deploy.sh, which a green
                 tick on main runs there over SSH through deploy-gate.sh
deploy.py        the commit this process runs and the last deploy's outcome, for Health
web.py           the screens and the worker: a threaded HTTP server on loopback,
                 routing the guest host to guests.py by Host
guests.py        the guest's office (see.agentsee.work): the door, a code by
                 email bound to the browser, sessions, invitations and their
                 archive copy; the Guests screen on the engine
mail.py          mail from the office account on Stalwart: codes, invitations,
                 later confirmations and signed copies
linear.py        Linear as "AgentSee Engine": comments, labels, due dates, issues,
                 a guest's words always quoted; the outbox the worker drains
release.py       the release: versions with their hash, both hosts' signatures
                 from their profile, the guest's signing with its record, the
                 PDF, HTML and JSON copies in the archive and by email
idea.py          the idea conversation: the guest-episode skill run with the
                 Claude API, streamed, capped; the run-sheet draft; what the
                 pipeline wants of the guest; the issue told
schedule.py      the time: the guest's choices in their zone against the rules,
                 both hosts' answers, the booking with its mail and calendar
                 file, the join link in its window; the issue told
review.py        the review: the round's render from the archive through signed
                 links, the guest's notes with the moment each is about, their
                 approval with its signature and the clips vetoed, the copies
                 and the mail; the records the pipeline reads; the issue told
pages.py         the page shell in the site's typographic system, and the
                 guest host's
static/engine.css  tables, forms, a status word; the rest is the site's CSS
static/see.css   the door and the office, on top of engine.css; the arrival's stage and lids
static/door.js   the arrival: Seymour, the masthead's eye, big, for a browser's first visit;
                 the site's moods in a sequence, the close, the black, the door behind it
static/sign.js   the signature pad: a canvas, a finger or a mouse, or a photo
static/office.js the office's one script: a save puts its section back in place, the
                 conversation's reply streamed to the page as it is written
static/time.js   the time's calendar: a tap on a day, a sheet with its half-hours in the
                 guest's zone, the choices as chips
static/review.js the review: "at this moment" from the player into a note, a note's
                 moment back into the player
accounts.json    which accounts get which assets, at what hour, by which adapter
config.json      the engine's settings, none secret
engine.env       op:// references for `op run`; no values
test_engine.py   the whole thing against a made-up episode and stubs for every
                 platform and the edge
```

## Running it

```sh
./engine/engine.py ingest ep1                 # needs build/episodes/ep1/out, staged
./engine/engine.py plan ep1 --no-links        # posts drafted, nothing leaves the machine
./engine/engine.py posts --episode ep1
./engine/engine.py publish --due --dry-run    # what the next run would do

op run --env-file=engine/engine.env -- ./engine/engine.py links ep1          # short links into KV
op run --env-file=engine/engine.env -- ./engine/engine.py publish --due      # for real
op run --env-file=engine/engine.env -- ./engine/engine.py serve              # http://127.0.0.1:8877/
op run --env-file=engine/engine.env -- ./engine/engine.py mail you@example.com  # proves the mail credential
op run --env-file=engine/engine.env -- ./engine/engine.py linear AGE-106       # a comment as AgentSee Engine
op run --env-file=engine/engine.env -- ./engine/engine.py linear --retry       # outbox jobs that gave up, back in the queue and drained
op run --env-file=engine/engine.env -- ./engine/engine.py release sign Abrar --png sig.png  # a host's signature by command
op run --env-file=engine/engine.env -- ./engine/engine.py release normalise    # every kept signature as one ink on transparent, once
op run --env-file=engine/engine.env -- ./engine/engine.py threads              # the Threads token's upkeep now: Meta's dates, a refresh when due
op run --env-file=engine/engine.env -- ./engine/engine.py instagram            # which account the Instagram token serves, and whether Meta honours it

ENGINE_GUEST_HOST=localhost ./engine/engine.py serve --no-worker   # the guest's door at http://localhost:8877/

python3 engine/test_engine.py                 # the suite, against stubs for every platform
ruff check . && ruff format --check .         # the lint and the formatting (ruff.toml)
```

The ledger lives at `$ENGINE_DATA/ledger.sqlite`, or `build/engine/` when
that is unset (gitignored). `--ledger` points a run elsewhere. Credentials
come from the vault through `op run`, as everywhere in this repo; a run
without them does what it can and says what it skipped, so the screens can
be browsed bare.

The suite and Ruff both run on every pull request and push to `main`
(`.github/workflows/check.yml`), on the engine's own Python. A failing test
or a lint error is a red tick; `ruff check --fix . && ruff format .` settles
most of what the linter and the formatter ask for.

## How a post comes to be

`ingest` reads the release record and the staging record of an episode and
writes the episode and its files to the ledger, with the media-host URL of
each public file. `plan` walks the release's day plan (the episode on day 0,
a clip a day after) and, for each asset, each account in `accounts.json`
that takes that kind of asset, makes one post: the text drafted from the
release (the clip's hook and attribution, or the episode's title and
summary), cut to the platform's budget a sentence at a time with the link
and the hashtags kept; the media named; the due time set to the plan's day
at the account's hour; and a short-link id minted, `e1xc02` for episode 1,
X, clip 2. The short link stands in for the episode URL in every text, so
the click is counted at the edge, and points at the episode page, at the
clip's moment for a clip. A plan run never touches a post that exists, so an
edit survives a re-plan. `plan --prune` is the one exception: a planned post
the plan would no longer make, because its account was dropped from
`accounts.json` or no longer takes that kind of asset, is marked skipped;
anything a person or a platform has touched is left alone.

Then the states, in `ledger.STATUSES`: `planned` until its day; `due` when
a person says now; `publishing` while the worker has it; `scheduled` when
the platform holds it for its time (YouTube); `published`; `hand` when a
card is ready for a person; `failed` with the error, for a retry; `skipped`.

## The publisher

`publish --due` takes every post that is due: marked so by a person, or on a
platform that schedules for us (YouTube takes everything the moment it
exists, private, with `publishAt`), or past its time. One adapter per
platform, in `publish/`; the docstring of `publish/__init__.py` is the
contract. The YouTube adapter is real: resumable upload in 8 MiB chunks with
resume on a dropped connection, captions, thumbnail (refused rather than
failed while the channel is unverified), the show's playlist; once the
video is up, nothing after it can fail the post, only leave a note, since
losing the id would mean uploading again. YouTube's daily upload limit is a
hold, not a failure: the post keeps its state, the account rests two hours
and the worker tries again by itself (Health shows what is resting). The X adapter
is real: the art or the clip through X's chunked upload (segments under
5 MB, a poll until the video is processed), then the post, at the post's
hour, since X does not schedule. The Bluesky adapter is real: a session on
an app password, whose DID document names the account's own data server;
the art as a blob there (a million bytes at most) in an images embed, a
clip through Bluesky's video service under a service token the PDS issues
for that one upload, polled until the processed blob comes back, in a
video embed with its aspect ratio; the link and the hashtags as facets
over the UTF-8 bytes, or they would be plain text; 300 graphemes. The
Mastodon adapter is real: the file to the instance in the account's
handle, described for screen readers, polled while a video is processed,
then the status, public, in the configured language, with an idempotency
key from the post and its attempt so a lost reply cannot post twice; 500
characters with every link counted as 23. Their credentials are two vault
items (`engine.env`, `docs/CREDENTIALS.md`), resolved when the container
starts: file them before the box pulls a version that references them, or
`op run` refuses to start the engine. Everything else is `hand`: the card.
For Instagram that is for now. For LinkedIn it is the decision
(20 October 2026): one card per episode per person, the post written by the
person, the card there for the short link and the record, and no adapter to
come.

The Threads adapter is real (`publish/threads.py`; the account joins it
with `"adapter": "threads"` in `accounts.json`, the switch-on): the art as
an image post, a clip as a video post, each with alt text, by three
requests, since Meta fetches the file itself from the media host: a
container with the text and the file's public address, a poll until Meta
has fetched and processed it, then the publish; and one read for the
permalink. Five hundred characters and five links at most. Meta's "not
now" (its rate-limit codes, or the day's 250 posts spent) is a hold with
Meta's own estimate of the wait. The container's id goes into an event the
moment Meta issues it, and a retry reads that first: a container Meta says
is published is found among the account's recent posts by its text, never
posted again. The token is a sixty-day user token, minted by a consent
through the engine itself: `/meta/threads/consent` on the engine host
(behind Access, in a browser signed in to Threads as the show's account)
sends the browser to Meta with a state in a signed cookie, and
`/meta/threads/callback` checks the state, exchanges the code twice (an
hour's token, then the sixty-day one), files the token in the vault item
`config.json` names (`"threads"`), asks Meta for its dates (`debug_token`)
and keeps them in the ledger's `credential` row, which Health shows as
"Threads token: valid to …, grant to …". The upkeep runs beside the daily
collection, or by hand as `engine.py threads`: it asks Meta for the dates
again, refreshes under thirty days (allowed once the token is a day old),
writing the new token to the vault before anything uses it, with X's guard
when the write fails; and it mails hello@ when the consent's ninety-day
grant has under fourteen days left (a refresh renews it only while the
profile is public) or a refresh fails. `engine.env` carries the Threads
app's id and secret, the pair on the use case's Settings page; the token
itself is never in the environment.

The Instagram adapter is real too (`publish/instagram.py`; the switch-on
gives the account `"adapter": "instagram"` and, by decision, the episode
asset beside the clips): a clip as a Reel shared to the feed, with the
clip's JPEG poster as its cover (`tools/clips.py` writes one beside the
PNG; an episode staged before that gets the first frame), the art as an
image on the grid with alt text; 2,200 characters and thirty hashtags; the
same container, poll, publish and read as Threads, on graph.facebook.com,
and the same never-twice through the container event, matched by caption.
The token is the system-user token the dashboard generated (AGE-130, C7),
read from the environment, with no consent and no refresh: it stands until
the account leaves the portfolio or the app is removed from it. The
account id is `config.json`'s `"instagram": {"account": …}`, the one
Business settings shows under the app's connected assets (not a secret),
and the adapter proves it by reading the account with the token before
keeping it in the `credential` row; without it the adapter asks Meta to
name the account, under the assets shared at the token's login and under
the token's granular scopes, which for a system-user token has answered
empty even with the asset shared (6 November 2026). The upkeep
(`engine.py instagram`, and daily beside the collection) asks `debug_token`
with the app's own token whether Meta still honours it; Health shows
"Instagram token: @agentsee.work, account …, token honoured by Meta", and
hello@ hears when it is refused, since a new one must be generated in the
dashboard and filed, and the engine recreated. `/meta/instagram/callback`
is a plain page, there because the Unified Login setup asked for a redirect
address; no other business ever logs in through it.

X's refresh token is single use, so the vault item is the live copy: the
adapter reads it with `op read` when it needs an access token and writes
X's replacement back with `op item edit` (the item's JSON on stdin, never a
value on the command line) before it uses the access token; `vault.py` is
those two calls. If the write fails nothing is posted, the new token is
held in the process and written on the next attempt, and the error says not
to restart before then. The engine therefore runs under the op session
`op run` already has. One refresher at a time: do not run two engines
against the same item. The X account is wired to its adapter by three
settings: `"adapter": "x"` on the account in `accounts.json`, an `"x"`
entry in `config.json` naming the vault, item and field that hold the
refresh token, and the four `X_` references in `engine.env` (the ones
`tools/x.env` has).

Late posts. A platform the engine sends to is not given a post whose time
passed more than `late_hours` ago (`config.json`, six): if nothing was
running that morning, the morning's posts wait on the By hand screen for a
person to publish now, move or skip, rather than going out in a bunch at
the next start. `publish --due --late` sends them anyway; a post a person
marked due goes regardless; cards are never late.

`serve` runs the same publisher in a worker thread, every minute and at once
when a screen asks for something now. The command line and the worker can
run at the same time: a post is marked `publishing` before anything is sent,
and `due` ignores that state.

## The collector

`collect` reads back what everything did, once a day at `collect_at`
(`config.json`) by the worker, or now by hand, one source at a time with
`--only`. The edge first: the two Analytics Engine datasets the Workers
write (`infra/podcast/workers/README.md`) through Cloudflare's SQL endpoint,
one row per request to the media host into `download` and one per
short-link click into `click`, the last day read again each time so a row
that arrived late is not missed and nothing counts twice; the user agent is
classified with OPAWG's lists (`useragents.json`), bots first, then apps,
libraries, browsers. Then each platform, into `snapshot` as dated rows per
post and metric, so there is a time series where a platform only ever says
a running total: YouTube by `videos.list` (views, likes, comments, and the
video's state: a rejected upload fails the post with the reason, a scheduled
one that went public is marked published) and the Analytics API (watch
minutes, average view length and share, shares, subscribers gained, two or
three days behind); Bluesky and Mastodon through their public endpoints
with no token, from the permalink a person pasted back; X through the
owned-post lookup once its adapter is on, a cent per thousand reads, and
X forgets after thirty days so the snapshots are the record. A source that
is missing its credentials says so and the others carry on. The Threads
token's upkeep (`publish/threads.py`) runs right after the day's
collection, and its line goes to the same log.

A listen is the IAB's: one address hash per file per day, from a 200 or 206
that carried at least a minute's worth of the file, by something that is
not a bot or an HTTP library. Bytes offered, not heard.

The Cloudflare token the engine already holds for KV needs one more
permission for the edge: Account > Account Analytics > Read.

## The screens

Episodes (what is staged, the files, what went where), Posts (the plan as a
calendar, with filters), a post (its text and title, editable until the
platform has it; its day and hour; publish now, retry, skip; the hand card:
the text to copy, a link that opens the platform's composer with the text
in it, the file on the media host, the poster, the short link, and the
paste-back of the permalink that marks it published; the record of what
happened to it), By hand (what a person posts today, and what is late),
Reach (per episode: listens, the last week's, YouTube views, link clicks;
per post: views or impressions, likes, reposts, replies, clicks, each with
the last week's share beside it; YouTube's watch time), Audience (the last
thirty days of listens by app and by country, clicks by where they came
from, listens by day), Guests (who is invited onto the podcast and how far
each has come: an invitation is made here in two steps, the name and an
address, then the guest's Linear issue, found in the podcast's project by
the name and offered closest first, or made there with its angle, or
typed, or left for later; sent by email when an address is known,
withdrawn, reissued; each of which the issue is told), Health (the ledger, the worker, the last
snapshot and collection, counts, which adapters can see their credentials,
the last mail that did not go, whether guest sessions survive a restart).

Server-rendered, no script but the site's eye, the site's stylesheet and
fonts served from `public/`, the same CSP as the site plus the media host
for posters. Every change is a POST carrying the process's own token. Who
did it is the identity Cloudflare Access attaches to the request
(`Cf-Access-Authenticated-User-Email`), or `ENGINE_USER`, or `local`.

## The guest's office

The same process answers a second hostname, `see.agentsee.work`
(`guest.host` in `config.json`; `ENGINE_GUEST_HOST` overrides it for a
look on a workstation), reached through the tunnel without Access. `web.py`
routes by `Host`; `guests.py` is everything on that side, and the design
is on AGE-92.

The door: an email address, a six-digit code by email, a session cookie
for that host only (thirty days, so a device is remembered and a new one
asks for a code), `/leave`. The code is bound to the browser that asked
for it (a cookie set when it is requested, required when it is typed), is
good for ten minutes and once, allows five wrong tries, and then the
address waits a quarter of an hour; an address gets three codes in ten
minutes and a connection twelve. The email carries the code and no link.
An invitation link carries an id, not a secret: one made without an
address is claimed by the first address that passes a code, one made with
an address refuses any other, and a stranger with a right code is told
there is no invitation for that address. Every sign-in records the
address, the time, the IP and the device, and enforces only the first.

Sessions and bindings are signed with `ENGINE_SESSION_KEY` (the vault item
"Engine session key"); without it the key is made up for the process,
`serve` and Health say so, and a restart signs every guest out. Each change
to a guest is copied to the archive as `guests/<id>/guest.json`, with the
sessions and the record, because the ledger sits on the root disk.

The guest host's pages carry the site's masthead and footer with their
links pointed at the site, and a footer note of their own about the two
cookies, since the site's promises none. No form token there, because the
pages are public: a browser's POST from another origin is refused instead.
That check reads the `Origin` header, so the guest host's pages say
`Referrer-Policy: same-origin` where the engine's say `no-referrer`: under
`no-referrer` a browser posts `Origin: null` to the host's own forms, and
the door would turn everyone away.

The review (`review.py`, AGE-111) is the last step of the office. The
pipeline's `episode.py review <slug>` puts the render as it stands under
`episodes/<slug>/review/` in the archive and the round's record at
`guests/<id>/review.json`; the office reads that record whenever the guest
looks (the ledger's `review` table keeps the copy and the round seen), and
the page streams the episode, the clips and the posters from the archive
through links the archive client signs for an hour (`archive.presign`), so
the bucket stays private and nothing passes through the box; the guest
host's content security policy names the archive for that. A note takes the
moment in the player it is about (`static/review.js`) or one typed as
`m:ss`; every note is sent as it is made, to `guests/<id>/notes.json`, to
the hosts by mail and to the issue (the job `noted`); the page runs the
episode, the notes under it, the clips, then the approval. A note that
asks for a removal is read by the model (`idea.py`'s settings and key)
with the round's transcript, the window around the note's moment or the
whole; the words it names are checked to occur in the transcript exactly
once, compared the way `tools/cut.py` compares them, and shown to the
guest to confirm or decline (the job `cut`), so `notes.json` carries the
cut as `remove.phrase` with `confirmed` and `episode.py notes` takes it
into `removals.json` verbatim; words the model named that are not there,
or the model down, leave a plain note and an event. A later round lists
the last round's notes with their cuts, as a record. The hosts see the
same round on the engine, at `/guests/<id>/review`: the player and the
clips through the same signed links, the guest's notes, their own notes
with the same cut-finding (carrying `by` in `notes.json`, on the issue in
plain words rather than quoted), and an approve button each, recorded
like the release's host signatures (who, when, from where, the hash of
the round's record) and mirrored to `episodes/<slug>/review/hosts.json`,
which the pipeline's `approve` and `stage` require of every host for a
guest's episode. The
approval is a second signature made and kept like the release's: the name
typed, the address, the time, the IP and device, and the hash of the
round's record, with the clips vetoed named as the review's `release.json`
names them; it goes to `guests/<id>/approval.json` and `.html` (the signed
copy, with the image), to the guest by mail with a copy to hello@, and to
the issue with the label `episode-approved` (the job `approved`). A newer
round asks to be approved again. The pipeline's `approve` and `stage` read
those records and refuse without them (`docs/PODCAST-PIPELINE.md`).

## Mail

`mail.py` sends plain text from "AgentSee <office@agentsee.work>" with
Reply-To `hello@agentsee.work`, through Stalwart on the box over 465 as
the `office` account (the vault item "Stalwart podcast submission";
host, port and addresses in `config.json`), and Stalwart relays it. What
goes out today: the sign-in code and the invitation; the booking
confirmation and the signed copies come with their screens. Every send
and every failure is an event in the ledger; a failure is a sentence on
the page that asked for it and on Health, never silence. `engine.py mail
<address>` proves the credential.

## Linear

`linear.py` writes to Linear as "AgentSee Engine", the OAuth application
whose consent `tools/linear.py auth` runs once with `actor=app`, so a
guest's words land on their issue as the guest's and the engine's acts as
the engine's. GraphQL over urllib: an issue by its identifier, a search by
title in the podcast's project, a comment, a label (made on the team if
missing: `guest-invited`, `guest-claimed`, `release-signed`, `booked`,
`episode-approved`), a due date, a new issue. Anything a guest wrote goes through `quote()`,
headed "<name> wrote, through see.agentsee.work:" and quoted line by line,
so whoever reads the issue, person or agent, takes it as what was said and
never as what to do.

The token: Linear's access token lasts a day and its refresh token rotates
on every use, so the vault item "Linear podcast token" is the live copy,
read at refresh time and written back before the access token is used,
as for X; `config.json` ("linear") names the item, the team and the
project. A second engine, or the consent run again, against the same item
retires this one's token.

Nothing a guest does waits on Linear inside a request: a change of state
(an invitation made, sent, withdrawn or reissued; a claim at the door) puts
a job in the ledger's `linear_job` outbox and the worker sends it each
minute, one guest's jobs in their order so the issue reads as a story,
backing off on failure (two, four, eight minutes, up to four hours, eight
tries; a job that fails or gave up holds that guest's later ones) and
recording each outcome as an event on the guest's page; Health counts what
waits and shows the last failure. The Guests screen is the exception: its
search of the project and the issue it makes are answered while a host
waits, with Linear's error on the page and the typed identifier always
open. `engine.py linear <issue>` posts a test comment directly, which is
how the bridge is proved; `--drain` sends the outbox by hand.

Who did a thing is named by first name in the mail and on the issue:
`hosts` in `config.json` lists the show's people, and the engine identity's
local part is matched against it (`jameshartt@…` is James), so no address
of ours is written anywhere.

## The release

The one document with a guest's name on it (AGE-108). Its text is a Linear
document on AGE-55, in plain words, read by both hosts; `release.py` keeps
it versioned. `engine.py release add --issue AGE-55` takes the document's
text in as the next version with its sha256, mirrored to the archive as
`release/v<n>.md` and `.json`; `release` lists the versions and who has
signed them. Each host draws or uploads a signature once on their profile
(You, keyed on the Access identity through `hosts` in `config.json`) and
signs each version there. A version is issued, and shown to guests, only
when every host has signed it, so no real copy carries a placeholder.

The guest reads the issued version in the office, types their name, draws
a signature on the pad (`static/sign.js`: a canvas for a finger or a mouse,
or a photo of the signature placed on it), ticks and signs. Recorded with
it: the address the code verified, the time, the IP, the device and the
text's hash, which is what makes a simple electronic signature worth
something. The copy is a PDF (fpdf2, the engine's one dependency, pinned in
the image and in CI, with DejaVu embedded so a name in any alphabet sets)
with the three signatures and the record on its last page, the same as one
HTML file and a JSON record, all three in the archive as
`guests/<id>/release-v<n>.*`. The PDF goes to the guest by email with a
copy to hello@; the office and the engine's guest page re-render it on
demand; the issue is told and labelled `release-signed`. A later version
never voids an earlier signature: the guest's copy is the version they
signed, and asking a guest to sign a newer one is not built yet.

## The idea

Once the release is signed, the guest sits down with the office (AGE-110):
a short conversation that starts open (what is the episode about, and why
you?) and then asks only for what the run-sheet still needs, one thing at
a time, about ten minutes, ending in a run-sheet draft the hosts can record
from. The skill `.claude/skills/guest-episode/SKILL.md` is the one source:
`idea.py` makes the system prompt from it, with the site's ten questions
read from `public/index.html` and the episodes so far read from the ledger
(each one's summary, chapters and the questions it touched), and
`/idea/skill.md` serves the same to a guest who would rather take it to
their own assistant and paste the run-sheet back. The engine's own prompt
goes further: the latest episodes' transcripts, word for word, fetched from
the archive (`episodes/<slug>/out/transcript.txt`) into the data dir once,
so the office can tell a guest when their idea is an episode the show has
already made, and steer them off it unless they can add to it. How many it
reads in full is `transcripts` in the config; the health page says how
many it knows.

The Claude API over urllib, like every other platform here: the key is
`ANTHROPIC_API_KEY` (the vault item "Anthropic podcast key", a service
account key in a Console workspace of its own under a monthly cap, AGE-115
step 4); the model and the caps are `idea` in `config.json`. The reply
streams to the page as server-sent events (`static/office.js`), and a browser
without script posts the same form and gets the page back whole. The
controls are what make hosting it safe: only a signed-in guest who has
signed the release; twenty turns and a token budget per guest (tokens paid
for in full: the skill and the transcripts are one system block cached for
an hour, and a cache read, at a twentieth of the price, is not charged to
the budget); no tools; the model sees this guest's conversation and nothing
else; one reply at a time per guest. The whole conversation is kept on the
guest's issue as one comment the worker rewrites after each exchange, in
the guest's words; the run-sheet draft, which the office writes in a fenced
block the engine recognises, is its own comment with the label
`idea-drafted`, and the guest can revise it. A form beside the conversation
collects what the pipeline wants: the name as shown, a line, a link, a
headshot (a multipart post; the image in the ledger and the archive) and a
time zone, told to the issue too. `engine.py idea` proves the key with one
streamed reply.

## Its home

The engine lives on the box `infra/` provisions, in two containers behind a
Cloudflare Tunnel with Access in front (a one-time code by email, then a
passkey); `home/README.md` is the runbook, `infra/podcast/engine.tf` the
tunnel and the lock, `infra/dns.tf` the two DNS records. On that box there
is no `build/`: `ingest` reads `release.json` and `staged.json` from the
archive bucket, where `stage` put them, and an adapter that needs a file
gets it from the archive on demand, checked against the hash the ledger
holds (`archive.py`). The ledger is copied to the archive nightly
(`snapshot_at` in `config.json`; Health shows the last) and `restore`
brings it back on a rebuilt box. A merge to `main` deploys itself once the
checks are green (`.github/workflows/check.yml`, `home/deploy.sh`); Health
shows the commit running and the last deploy's outcome. A workstation can still run `serve
--no-worker` to look, but not publish: two engines on one vault retire each
other's X tokens.

## Not yet

The Spend screen and the export drop zones for Spotify's CSV and LinkedIn's
XLSX (AGE-69, items 4 and 5); the collector's Threads and Instagram
sources (AGE-133) and the two accounts' switch-on (AGE-134); Apple's
Reporter (item 6).
