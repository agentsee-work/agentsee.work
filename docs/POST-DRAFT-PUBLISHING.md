# Working notes — "The token was single use, and the vault was read-only"

**Published as Issue No. 6**:
[`public/issues/06-publishing.html`](../public/issues/06-publishing.html). That is
the version that ships, cut shorter and with the identifiers stripped.

This file is the longer argument it was cut from, plus the notes for the next
platform. It stays in `docs/` because only `public/` is published. The
technical record is `engine/README.md`, `engine/home/README.md`,
`infra/podcast/workers/README.md` and `tools/README.md`; the design and every
decision, with dates, is AGE-69 on Linear. This is the story of how it got
that way.

If the two ever disagree, the published page wins. This is the workings.

---

## The argument, at length

Issue No. 5 ended with the deliverables in a folder. This is the ten days
that followed, 13 to 23 October 2026, in which everything after the render
became a script: a feed and a page per episode, a media host that counts
listens to the industry's rules without a cookie, and an engine on the mail
box that posts to the platforms at nine in the morning and reads the numbers
back at half past five. Then the three mornings on which it posted everywhere
but X, and the reason, which was a permission nobody could change.

The version to avoid writing is "we automated our marketing". Most of what
was built is plumbing, and the interesting parts are where the plumbing met a
platform's actual behaviour rather than its documentation.

---

## 1. The brief, and whether to build at all

James, 13 October: every deliverable (the episode as video and audio, and
the clips) published to every relevant platform from our own scripts. The
loop closed on every post, so we can see the traction of each clip and
episode. A dashboard of reach and whatever statistics each platform offers.
Build it ourselves unless building is impractical. The podcast is to become
the main vehicle for selling the services and the approach.

The third-party question was answered first, from the platforms' own
documentation. A podcast host adds nothing on Apple Podcasts or Spotify that
we cannot get ourselves: Transistor says in print that neither shares
consumption data with hosts, and Captivate tells its customers to log into
Spotify themselves. What a host adds is an IAB certification badge and native
video on Apple (HLS, partner hosts only) and Spotify (their partner-only
Distribution API). Neither matters at our scale. Everything else with an API
gives it to us on the terms it gives a host. Running cost of the self-built
version: about a pound an episode, nearly all of it X.

The weak spots are the two big directories themselves, and they are weak for
everyone. Apple has no analytics API; the scriptable route is a Java
command-line tool that downloads daily reports under a 180-day token.
Spotify has a dashboard and a CSV export, and video reaches it only by hand
in their web interface.

## 2. The shape

The same principles as the production pipeline: data in, deliverables out,
every step re-runnable from the data, no step that needs a human except the
editorial ones.

**Release.** The last stage of the production run writes `release.json`:
the show, the episode, its people, the chapters in three shapes, the
transcript as VTT and JSON, every file with its size, type, duration and
hash, the clips placed in the episode's time, a post text per clip, a
day-by-day plan, and the warnings. The show's own words are typed once in
`tools/show.json`.

**Approve and stage.** `episode.py approve` records who approved what and
when, with the hash of every deliverable, and sets the release date the plan
counts from; it refuses while the release carries a warning. `episode.py
stage` then puts everything in the right spot in one go: the Riverside
package, the edit as data and every deliverable to a private archive bucket;
the public files (mp3, clips, posters, art, transcripts, chapters) to the
media bucket behind `media.agentsee.work`. Every object carries its hash as
metadata so a re-run puts only what changed. From there the workstation can
be off.

**The site.** `tools/site.py` writes the feed (RSS 2.0 with the Apple tags
and the Podcasting 2.0 ones the apps honour: a stable guid, chapters, both
transcripts, the people, a soundbite per clip), the show page and a page per
episode: a player on the media host, chapters that seek it, the notes, the
moments that went out as clips, the people, the transcript folded. It takes
an episode only when its staging record matches the release and its release
date has arrived, London time, so merging early publishes nothing. Media
cannot live on Pages: the file limit is 25 MB and an episode's mp3 is 55.

**The edge.** Two Workers of about a hundred lines each, declared beside the
buckets. `media.js` in front of the media host serves the public bucket
(ranges, conditionals, the upload's own headers) and writes one row per
request to Analytics Engine: the file, the status, the bytes offered, the
range, the user agent, the country, the referer's origin, and a hash of the
address under a key that lives only in the Worker's bindings and the vault,
over the UTC date and the address. One listener hashes the same all day and
differently tomorrow, which is what counting a listener once a day needs and
nothing more. `links.js` on `agentsee.work/p/*` looks a short id up, logs the
click with the platform it came from, and answers a 302 to the episode page,
at the clip's moment for a clip.

**The engine.** Python and SQLite, no dependencies, server-rendered screens in
the site's own stylesheet. It runs on the mail box in two containers: the
engine, with the repository mounted read-only so a deploy is a pull and a
restart, and `cloudflared` holding a Tunnel that is the only way in.
Cloudflare Access is the lock: a one-time code by email to one of two
addresses, then a passkey. Nothing listens on the box; the security groups
did not change; there is no password of ours anywhere.

Ingest reads the release and the staging record from the archive. Plan walks
the day plan (the episode on day 0, a clip a day after) and, for each account
in `accounts.json` that takes that kind of asset, makes one post: the text
drafted from the release and cut to the platform's budget a sentence at a
time with the link and the hashtags kept, the media named, the due time set,
and a short-link id minted. 115 posts for two episodes. A plan run never
touches a post that exists, so an edit survives a re-plan.

The publisher takes what is due: marked so by a person, or on a platform
that schedules for us, or past its time. One adapter per platform. YouTube
takes everything the moment it exists, private, with a publish time; the
upload is resumable in 8 MiB chunks with resume on a dropped connection,
then captions, thumbnail, playlist. X, Bluesky and Mastodon are posted to at
the account's hour. Everything else is a card: the text to copy, a link that
opens the platform's composer with the text in it, the file on the media
host, the short link, and a paste-back of the permalink that marks it
published. LinkedIn is by hand for good, by decision on 20 October: far
fewer posts, each written by the person, one card per episode per person
for the short link and the record.

A late guard: a platform the engine sends to is not given a post more than
six hours past its time. If nothing was running that morning, the morning's
posts wait on the By hand screen for a person, rather than going out in a
bunch at the next start.

The collector runs once a day at 05:30: the edge's two datasets through
Cloudflare's SQL endpoint into listens and clicks, the user agent classified
with the OPAWG lists (bots first, then apps, libraries, browsers), a listen
counted to the IAB's rule; then each platform into dated snapshots per post
and metric, because a platform only ever says a running total and X forgets
after thirty days. Reach shows each episode and post as last read with the
last week's share; Audience shows thirty days by app, country, referer and
day.

**Secrets.** Every token is an `op://` reference in a committed file,
resolved when the container starts by the 1Password CLI under a service
account made for the box, scoped to the one vault. The engine reads eight
items. It writes one.

## 3. The platforms, as they turned out to be

**Spotify and the 206.** Spotify accepted the feed on 19 October and then
held the show in review with "Episode is not associated with an audio or a
video". The media Worker handed R2 the request's headers as the range, and
R2 reports a range on the object whether or not the request carried one, so
a plain GET came back 206 Partial Content with a Content-Range of the whole
file. Apple's fetcher tolerated it. Spotify's force-download read it as not
a file. A 206 is now the answer to a Range request and nothing else (PR #39,
22 October). The test's fake bucket behaves as the platform does, so the old
Worker fails the first assertion.

**YouTube's cap, and the id that was nearly lost.** Ten uploads in on the
first day, the channel's daily cap closed and the last three clips were
marked failed for a person to find. Now an adapter can raise a hold with a
time: the post keeps its state, the account rests two hours, the worker
tries again by itself, and Health lists what is resting. Separately, the
first real episode upload succeeded and then the playlist step failed with a
409, which failed the post and dropped the video id: a retry would have
uploaded 2 GB again and made a duplicate. Now everything after the upload is
a note, the playlist item is tried three times, the id is logged the moment
it exists, and a restart marks a mid-attempt post failed with a warning to
look on the platform first.

**X.** No free tier. $0.015 a post, $0.20 if it carries a link, $0.001 per
read of your own post; metrics for the last thirty days only. Chunked media
upload in segments under 5 MB with a poll until the video is processed. And
the refresh token is single use.

**The rest.** TikTok posts only privately until an app audit, so it waits.
Instagram and Threads are cards for now; both have APIs that need the
account switched to a business type and a public URL to fetch video from,
which the media host is. Bluesky posts through the account's own data
server on an app password, the link and the hashtags as facets, 300
graphemes. Mastodon on an application token, the file polled until
processed, an idempotency key so a lost reply cannot post twice.

## 4. The token

X's OAuth 2.0 gives a two-hour access token in exchange for a refresh token,
and in the same reply retires the refresh token presented and returns its
replacement. Whatever refreshes must store the replacement before it uses
the access token it came with, or the next refresh fails and a person has to
authorise the account in a browser again. The design, decided on 15 October:
the vault item is the live copy. The engine reads it with `op read` when it
needs an access token, refreshes, writes X's replacement back with `op item
edit` (the item's JSON on stdin, never a value on the command line), reads it
back to confirm, and only then uploads and posts. If the write fails nothing
is posted, the new token is held in the process, and the next attempt tries
the write again; the error says not to restart before then. One refresher
at a time: two engines on one vault retire each other's tokens, so once the
box is live nobody mints from a workstation.

Written on 15 October, tested against a stub X and an in-memory vault,
switched on for the morning of 21 October.

**21 October, 08:00:** Bluesky and Mastodon posted. **09:00:** post 10, the
X clip, failed: `X issued a new refresh token but the vault write failed
([ERROR] ... Couldn't update the item.)`. **22 October:** post 19, the same.
**23 October:** post 28, the same. Each morning the engine kept the only
valid token in memory and tried the write again. Nobody looked at X for two
days because the two accounts that had posted were the ones we looked at.

**The cause.** The service account the box runs under had been created with
read access to Engineering and not write. `op item create` inside the
container: `(101) You do not have permission to perform this action`. The
runbook said read and write; the account had been made with one of them.
1Password cannot change a service account's vault permissions after
creation, so the fix was a new account (`Engine Write`, read and write on
the one vault), its token filed over the old one in Security, the
environment file on the box edited, and the container recreated, not
restarted, because a restart does not reread its environment. That cost a
few more minutes. A retry of post 28 went through: refresh, write-back,
upload, post, in that order, proven once.

**Then two things we did not expect.** Post 19, retried a minute later with
the access token still cached from the success, failed with a 401 on the
upload. Nothing had been re-minted; we do not know why. After a restart, post
19 failed differently: `X podcast token has 2 fields called credential, not
one`. The item had gained a second field of the same name, and the engine
refused to guess which was live. One was deleted by hand; posts 19 and 10
then published. Whether the engine's own `op item edit` with piped JSON is
what doubled the field is the open question as this is written; the next
automatic refresh, post 37 on 24 October, will answer it. If it is, the fix
is in `engine/vault.py`.

**Two smaller wrongs on the way.** The OAuth 2.0 token the Developer Console
generates lacks the `media.write` scope, and the console offers no scope
choice, so a token minted there can post but not upload; our own consent
tool asks for the right scopes and is the route. That tool failed with X's
"Something went wrong" until the app's callback list held
`http://127.0.0.1:8794/callback`; it had held the website's address, and X
matches callbacks character for character. And the tool, run under `op run`
with its output in a pipe, printed nothing: Python block-buffers stdout
that is not a terminal, and `op run` masks any resolved secret in the
output, including the client id the tool had been asked to show.

## 5. Working with a model

It wrote the engine, the Workers, the site generator, the stage step, and
the tests that run the lot against stubs of every platform and the edge.
Given the log line it named the permission; given the permission it found
that it could not be changed, and read 1Password's documentation for the
command that creates a service account with write. The division of labour
from Issue No. 5 held: James at the box, running the commands that touch
production and pasting the output; the model reading it and saying what it
meant.

It was also wrong in a way worth recording. With the cause found and James
fixing it, it proposed an alternative: OAuth 1.0a, X's older signed-request
scheme, whose keys never rotate and so never need writing back. It offered to
build that "on the word", took a reply about not wanting a step on the box
as the word, and rewrote the adapter, the collector, the tests and the
documents across eleven files while James was fixing the problem the other
way. He said: "sorry, you've made a bunch of edits to Python that I'm not
sure if I've asked for." The rewrite was stashed, unmerged. A model will take
a lean for a yes if you let it; the remedy is a rule written where the next
session reads it: build on yes, and a reply that sounds like one is not one.

One more that nobody caught for five weeks. Every page's Instagram link went
to `agentseework`, which Instagram answers with "Profile isn't available",
the same page as a name nobody registered. The plan in September had said
one handle everywhere; the signups registered `agentsee.work`, which was
free; the plan was never corrected; the footer was typed from the plan; the
engine's account records copied the footer. Instagram returns 200 for any
username, so the link checker passed. James clicked it.

## 6. What it is for

As this is written: episode 0 is on every directory and platform we chose.
Episode 1 goes out on Tuesday 27 October at 06:00 London with nobody awake; a
clip a day follows on four accounts; the collector reads back every morning;
the screens show listens by app and country, clicks by platform, each
post's numbers with the last week's share. The last link in the loop, from a
post to a client, is not a platform metric. It is whether someone who found
us through the show writes to the address on the episode page, which is on
our own mail server, which was Issue No. 2.

Still to build: Instagram and Threads adapters, Apple's reporter on a timer,
a drop zone for Spotify's CSV, a spend screen. Still to decide: whether the
dashboard ever goes public. The next issue is the one the register has held
open since September, the first conversation with someone who disagrees;
the sceptic said yes on 23 October.

---

## Notes for the next platform

- Prove a service account can write before anything depends on it: one
  `op item edit` on a throwaway field from inside the container, on day one.
  A read-only account looks identical to a read-write one until the first
  write.
- An environment change on the box is `docker compose up -d
  --force-recreate`, not `restart`. A restart keeps the old environment.
- Once the engine is live with X, nobody runs `tools/x.py check --refresh`
  or `auth` from a workstation. Every mint retires the token the engine
  holds.
- Do not restart the engine while it reports a held token. The token is in
  memory and the restart loses it.
- Check a platform's fetcher with a plain GET as well as a range request.
  R2 reports a range whether or not one was asked for.
- A platform that answers 200 for any username (Instagram) cannot be
  link-checked. Open the page.
- `docker compose exec -T` makes `op` read stdin as a JSON template. Run
  `exec` without `-T` for anything that calls `op`.
- The engine's logs belong to root through the docker socket. A grep as the
  unprivileged user is empty, not clean.
- A tool that prints under `op run` into a pipe needs `python3 -u` and
  `--no-masking`, or it prints nothing and the nothing is masked.
