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
  hand.py        a card for a person to post, and a permalink to paste back
web.py           the screens and the worker: a threaded HTTP server on loopback
pages.py         the page shell in the site's typographic system
static/engine.css  tables, forms, a status word; the rest is the site's CSS
accounts.json    which accounts get which assets, at what hour, by which adapter
config.json      the engine's settings, none secret
engine.env       op:// references for `op run`; no values
test_engine.py   the whole thing against a made-up episode and a stub YouTube
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
failed while the channel is unverified), the show's playlist. Everything
else is `hand` for now: the card. The X adapter is next; the credentials for
it are already in the vault.

`serve` runs the same publisher in a worker thread, every minute and at once
when a screen asks for something now. The command line and the worker can
run at the same time: a post is marked `publishing` before anything is sent,
and `due` ignores that state.

## The screens

Episodes (what is staged, the files, what went where), Posts (the plan as a
calendar, with filters), a post (its text and title, editable until the
platform has it; its day and hour; publish now, retry, skip; the hand card:
the text to copy, a link that opens the platform's composer with the text
in it, the file on the media host, the poster, the short link, and the
paste-back of the permalink that marks it published; the record of what
happened to it), By hand (what a person posts today), Health (the ledger,
the worker, counts, which adapters can see their credentials).

Server-rendered, no script but the site's eye, the site's stylesheet and
fonts served from `public/`, the same CSP as the site plus the media host
for posters. Every change is a POST carrying the process's own token. Who
did it is the identity Cloudflare Access attaches to the request
(`Cf-Access-Authenticated-User-Email`), or `ENGINE_USER`, or `local`.

## Not yet

The VPS home behind Tunnel + Access (the engine runs on a workstation until
then, which is enough to publish from); the collector and the Reach,
Audience and Spend screens (AGE-69, items 4 and 5); the X, Bluesky,
Mastodon, Threads, Instagram and LinkedIn adapters, each a module in
`publish/` with the five names the contract asks for; fetching an episode's
files from the archive bucket when they are not on the machine.
