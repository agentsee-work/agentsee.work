# engine/

The private side of the podcast: from an approved, staged episode to every
post on every platform, and back from every post to the numbers. The design
and the decisions are on Linear (AGE-69); this is what exists.

```
engine.py        the command line: ingest, plan, links, posts, publish, serve
ledger.py        the SQLite ledger: episode, deliverable, post, snapshot, spend,
                 click, download, lead, event
ingest.py        release.json + staged.json → episode and deliverables
plan.py          the day plan × accounts.json → posts, with short-link ids and
                 drafted text; the push of id → URL into KV
publish/         the publisher: __init__ runs what is due through an adapter
  youtube.py     upload, publishAt, captions, thumbnail, playlist
  x.py           X: chunked media upload, the post, the rotating refresh token
  hand.py        a card for a person to post, and a permalink to paste back
vault.py         the one credential the engine writes: X's refresh token, via op
archive.py       the archive bucket: files fetched on demand, release records for an
                 ingest without build/, the nightly ledger copy and its restore
collect.py       the collector: the edge's log into listens and clicks, each
                 platform's counts into dated snapshots; what the screens ask
useragents.json  OPAWG's patterns for podcast apps, bots, libraries, browsers (MIT)
home/            the box: Dockerfile, compose, the runbook
web.py           the screens and the worker: a threaded HTTP server on loopback
pages.py         the page shell in the site's typographic system
static/engine.css  tables, forms, a status word; the rest is the site's CSS
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

python3 engine/test_engine.py
```

The ledger lives at `$ENGINE_DATA/ledger.sqlite`, or `build/engine/` when
that is unset (gitignored). `--ledger` points a run elsewhere. Credentials
come from the vault through `op run`, as everywhere in this repo; a run
without them does what it can and says what it skipped, so the screens can
be browsed bare.

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
edit survives a re-plan.

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
hour, since X does not schedule. Everything else is `hand` for now: the
card.

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
is missing its credentials says so and the others carry on.

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
from, listens by day), Health (the ledger, the worker, the last snapshot
and collection, counts, which adapters can see their credentials).

Server-rendered, no script but the site's eye, the site's stylesheet and
fonts served from `public/`, the same CSP as the site plus the media host
for posters. Every change is a POST carrying the process's own token. Who
did it is the identity Cloudflare Access attaches to the request
(`Cf-Access-Authenticated-User-Email`), or `ENGINE_USER`, or `local`.

## Its home

The engine lives on the box `infra/` provisions, in two containers behind a
Cloudflare Tunnel with Access in front (a one-time code by email, then a
passkey); `home/README.md` is the runbook, `infra/podcast/engine.tf` the
tunnel and the lock, `infra/dns.tf` the one DNS record. On that box there
is no `build/`: `ingest` reads `release.json` and `staged.json` from the
archive bucket, where `stage` put them, and an adapter that needs a file
gets it from the archive on demand, checked against the hash the ledger
holds (`archive.py`). The ledger is copied to the archive nightly
(`snapshot_at` in `config.json`; Health shows the last) and `restore`
brings it back on a rebuilt box. A workstation can still run `serve
--no-worker` to look, but not publish: two engines on one vault retire each
other's X tokens.

## Not yet

The Spend screen and the export drop zones for Spotify's CSV and LinkedIn's
XLSX (AGE-69, items 4 and 5); the Bluesky, Mastodon, Threads, Instagram and
LinkedIn adapters, each a module in `publish/` with the names the contract
asks for; Apple's Reporter (item 6).
