# Social accounts

Which handle we use, why, and the things that will bite during signup.

Nothing here is a credential. Handles are public by definition; the passwords
and recovery codes belong in the shared vault, never in this repo.

## The decision

**`agentseework` everywhere. `@agentsee.work` on Bluesky.**

`agentseework` is the domain with the dot removed. It was free on every platform
we could verify, it survives every platform's charset rules — no underscore, no
hyphen, 12 characters — and it degrades gracefully in speech: "agentsee dot
work". The alternatives (`agentseehq`, `agentseeshow`, `agentseetv`,
`weareagentsee`) were equally free and all say less.

One string everywhere is the point. A handle you can say once in a podcast
outro, without qualifying it per platform, is worth more than a better handle on
the platforms nobody finds you through.

Two deliberate exceptions:

| Where | Handle | Why |
|---|---|---|
| GitHub org | `agentsee-work` | Already exists, already in the runbook. Leave it. |
| Bluesky | `agentsee.work` | Bluesky lets a domain *be* the handle. See below. |

### Bluesky gets the name for free — **done, 17 September 2026**

Bluesky verifies handles by DNS, so owning `agentsee.work` means we can be
`@agentsee.work` outright — the exact brand, no suffix, no compromise, and it
reads as its own proof of ownership. This is the one platform where we don't
have to settle.

`did:plc:6247kupcnwvc4lu5vmbf4fni`, declared in `infra/dns.tf` as
`_atproto.agentsee.work`. How it was done, for when we do it again:

```sh
# Get the DID from the account, then publish it as a TXT record.
curl -s "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle=agentseework.bsky.social"
# TXT  _atproto.agentsee.work  ->  did=did:plc:xxxxxxxxxxxxxxxxxxxxxxxx
```

Then change the handle to `agentsee.work` in Bluesky's settings. The existing
Cloudflare token already has **DNS — Edit** on this zone, so this needs no new
permission.

**Verifying the record and changing the handle are two separate steps, and the
app will happily tell you it did the first while you believe it did both.** Ours
sat verified-but-unchanged until we went looking. The app is not the place to
check — ask the network:

```sh
# Authoritative. This is what the PDS believes, not what the UI last rendered.
curl -s "https://bsky.social/xrpc/com.atproto.repo.describeRepo?repo=<did>" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["handle"])'

# A handle change writes a second entry here. One entry means it never happened.
curl -s "https://plc.directory/<did>/log/audit"
```

Note that `resolveHandle(agentsee.work)` starts succeeding the moment the TXT
record is live — **before** the handle has been changed, and regardless of
whether it ever is. It proves DNS answered, nothing more. We briefly read it as
proof of success; it isn't.

**The old `agentseework.bsky.social` is released, but not immediately.** Right
after the change it still resolved to our DID; by the next day it had stopped
resolving at all. So the release is real and lagged by roughly a day — long
enough that checking straight afterwards tells you the opposite of the truth.

### Two defensive registrations of bare `agentsee`

Not vanity — we are not using these, and should never post from them.

| Where | Why it is worth holding |
|---|---|
| Docker Hub | A Docker ID *is* the namespace, so the account holds the name. Someone else publishing `agentsee/…` is a supply-chain trap for anyone who guesses the obvious thing. |
| mastodon.social | No identity verification anywhere in the network, so impersonation costs an attacker nothing and costs us the benefit of the doubt. |

**PyPI was on this list and should not have been.** A PyPI *account* reserves no
names at all: a project name is claimed by uploading a distribution, so holding
`agentsee` would mean publishing an empty placeholder. PEP 541 names that case
exactly — "name squatting (package has no functionality or is empty)" is an
invalid project and removable — so the reservation would be both against policy
and not durable. Have the account, because you need one to publish. Do not
upload anything to hold a name.

`agentsee` is also free on Kick, Bluesky, Substack, Patreon and LinkedIn. We are
deliberately **not** taking those: none of them is a namespace where someone
else holding the name can hurt us, and each one is another account to hold with
2FA in the vault forever.

**LinkedIn was the close call.** `linkedin.com/company/agentsee` reads better
than `/company/agentseework`, and LinkedIn is the most client-facing surface we
have. Consistency won, narrowly. If that ever feels wrong, it is a cheap
decision to revisit — the vanity URL is editable.

## What was actually free

**Re-verified 17 September 2026** with `tools/handle-probe.sh`, which is the
audit rather than the table below — run it, don't trust this.

Free for `agentseework` on all of: GitHub, X, YouTube, Twitch, Kick, Bluesky,
mastodon.social, Substack, Patreon, LinkedIn, Hugging Face, Docker Hub, and as a
package name on npm and PyPI.

**Could not be verified, and the signup form is the only authority:** Instagram,
Threads, Reddit, Facebook. They bot-wall or rate-limit datacentre IPs and return
an identical response for a real account and a nonsense one. Treat these as
*unknown*, not as free.

**TikTok is now probeable after all.** Its profile pages return `200` for real
and invented handles alike, which is what made it look hopeless. Its **oEmbed**
endpoint does not: `tiktok.com/oembed?url=…/@handle` returns `200` for an account
that exists and `400` for one that doesn't, and it answers for an account with no
posts, so it detects existence rather than content. Calibrated against two
known-taken and four invented handles. The lesson is that one endpoint being
useless doesn't make a platform unprobeable — it makes that endpoint useless.

### Why the probe is a script and not a list

Handles go stale — someone can take one tomorrow. **The probe methods go stale
too, and that is the dangerous half.** Between the August and September audits:

- **Instagram closed `web_profile_info` to unauthenticated callers.** It now
  returns `401` for everything, including a known-good account. In August it
  was a clean 404/200 discriminator. A probe carried over unchanged would have
  reported Instagram as taken, or free, with equal confidence and no signal.
- **YouTube began 302ing every request**, real or nonsense, unless redirects
  are followed. Same failure: uniform response, confident wrong answer.
- **Hugging Face and Docker Hub normalise case with a redirect.** Unfollowed,
  `huggingface.co/api/users/agentsee` returns `307` — which matches neither
  control, so an uncalibrated probe has to either guess or give up. Followed, it
  lands on `AgentSEE`, a real account. It is taken, and case-insensitively so.

This is why every platform in the script is measured against a known-taken
**and** a known-free control on the same run, and why a platform whose controls
fail to separate is reported `UNKNOWN` rather than guessed. A probe that cannot
tell a real account from a nonsense one must never be allowed to say "free".

### Who holds `agentsee`

Worth recording so nobody re-runs this. None is a company of that name — the
brand is unclaimed, only the handle is. A web search for "AgentSee" as a company
or product returns nothing, so there is no trademark collision to design around.

| Platform | Holder | State |
|---|---|---|
| GitHub | no name set | Created 19 Feb 2026. Still 0 repos, 0 followers, `updated_at` identical to `created_at` — never touched since the hour it was made |
| Instagram | full name "-" | 0 posts, 47 followers, no bio (Aug 2026; no longer probeable) |
| Twitch | "Agentsee" | Last broadcast 3 Jan 2015, 2 followers |
| X | a personal account | Real person, in use |
| YouTube | unrelated channel | In use |
| Hugging Face | "Saurav P" as `AgentSEE` | 0 models, 0 datasets, 0 spaces |
| npm | a package, not a user | Name is published |

Every one of these is dormant rather than a rival product, which is exactly why
chasing them is a waste: a reclaim request nobody owes us, for a string we have
already decided not to build on.

## Registering, in order

**Registered so far:** Bluesky (`@agentsee.work`), X, Instagram and TikTok, all
17–18 September 2026. Outstanding: YouTube, Twitch, and the three defensive
reservations.

1. **`accounts@agentsee.work` — done.** It is a `MailingList` on our own mail
   server, fanning to both of us, and has been since cutover on 14 September
   2026. Not `hello@`: platform mail is high-volume and mostly noise and must
   not drown the public inbox. Fanning it to both means neither of us solely
   owns the accounts.
2. **Bluesky** — instant, no phone, and the DNS step above gets us the exact
   name. Do it first for the morale.
3. **GitHub** — the org exists; nothing to do but keep it.
4. **X, Instagram, TikTok** — phone verification, see the trap below.
5. **YouTube and Twitch** — the streaming pair, both with their own traps.
6. **The two defensive reservations** — Docker Hub and mastodon.social.
   Reserve, set the avatar, never post.
7. **Everything else** — LinkedIn, Kick, Substack, Reddit — as and when there is
   something to put on them.

Reserving a handle costs nothing and forecloses a squatter. Posting to eleven
dead accounts costs credibility. Reserve broadly, publish narrowly.

## Traps

**We can now send as `@agentsee.work`, and this used to be a trap.** Until
cutover the domain could receive but not send, which made any support or appeal
flow that says *reply from the address on the account* impossible — the kind of
thing you discover during a lockout, on the worst possible day. Since 14
September outbound relays with aligned DKIM and passes DMARC at `p=reject`, so
replying from `accounts@agentsee.work` works. That removes the main argument
against using it as the recovery address for things we would be hurt to lose.

The residual risk moved rather than vanished: recovery for the *mail* itself
must never depend on mail. Infomaniak, SMTP2GO, Cloudflare and 1Password all use
personal addresses on purpose. Platform accounts are a different tier and
`accounts@` is the right home for them.

**TikTok may never have given you a password.** Signing up with a phone number
or an emailed code — or with Continue-with-Google — creates an account that has
no password at all, so "forgot password" has nothing to reset and appears
broken. The way in is the login *code*, not the password flow; a password can
then be set afterwards under Manage account, where the option reads **Set**
password rather than Change. Do set one: an account reachable only through
someone's personal Google login is the single point of failure this section is
about. TikTok's captcha is also unreliable in Firefox on Linux, which makes a
working flow look like a broken one.

**Phone verification ties an account to a person.** X, TikTok and Instagram will
ask, and Twitch requires 2FA before it will let you stream at all — not at
signup, so it is easy to hit the first time you actually try to go live. Do it
on signup night while the vault is already open, not at the point of going
live. A
personal mobile number means one of us is a single point of failure for the
account. Put TOTP in the shared vault — see [CREDENTIALS.md](CREDENTIALS.md) —
and write down which number was used where.

**YouTube: the handle and the channel name are separate things**, and a channel
created under a personal Google account is owned by that person. A Brand Account
is a Google account for the business that several people manage from their own
logins, which is what we want — but there is no "make this a Brand Account"
option anywhere. It is decided entirely by where you start:

    youtube.com/channel_switcher  ->  Create a new channel
                                  ->  name, handle, picture  ->  Create channel

Starting there produces a Brand Account. Starting from "create a channel" on
your own account produces a personal one, silently, and the two look identical
afterwards. Set the **name** (AgentSee) and the **handle** (`agentseework`)
separately — they are different fields and the handle is the addressable one.

Then add the second person. There are two systems and Google is moving everyone
off the old one, so use the new one:

```
studio.youtube.com → Settings → Permissions → (migrate, if prompted) → Invite
```

Migration copies existing Brand Account users across but does **not** set their
level or send their invite — you do both by hand, so a migration that looks
finished may have granted nobody anything.

Pick **Owner**, not Manager. Manager covers everything day to day and stops
short of deleting the channel or holding ownership, which makes it the wrong
role for the person whose entire job here is to be able to act if the other one
can't.

**Owner is not primary owner.** There is exactly one primary owner and it is
whichever personal Google account created the thing. A second Owner is
resilience, not a transfer — and transferring primary ownership needs the
recipient to have been owner or manager for **seven days** first, so the
invitation has to exist before the emergency, not during it.

**Invite the second owner immediately, not when you need one.** You must have
been an owner for **seven days or more** before you can make yourself primary
owner. That clock only starts once, so an invitation sent today is the
difference between a seven-day wait and a seven-day wait *beginning* on the day
something has already gone wrong.

**Deleting the primary owner's Google account deletes the channel.** Not
transfers it, not orphans it — deletes it. Whichever personal Google account
creates this is load-bearing for as long as the channel exists, so use one that
is going to outlive our enthusiasm, and write down which one it is. That account
needs a recovery address and a recovery phone on it, and Google will mention
this only as a tooltip while you are busy reading about something else.

**Don't point that recovery address at `@agentsee.work`.** Same rule as the mail
build: nothing in a recovery path should depend on infrastructure we operate and
might be trying to recover at the same time. Our mail is one VPS. Use a provider
we don't run, and a phone as well.

Converting a personal channel afterwards is possible (Settings → Advanced
settings → Move channel) but it is a migration, and migrations are the thing we
are avoiding by getting this right at creation.

**Threads is Instagram's namespace.** Securing `agentseework` on Instagram
secures it on Threads. Don't count it as a second win, and don't let the
Instagram handle go on the assumption Threads is separate.

**Hugging Face and Docker Hub are case-insensitive.** `AgentSEE` and `agentsee`
are the same account. Don't read a case variant as an available handle.

**A Docker Hub organisation cannot share a name with an existing Docker ID.** So
taking `agentseework` as a personal Docker ID forecloses an organisation of that
name later, and free Team organisations have been phased out — an org is now a
paid plan. For two people publishing occasionally a personal namespace is the
right shape, and Docker does support converting a user account into an
organisation if that changes. Verify that conversion still exists before relying
on it; it is the kind of escape hatch that quietly disappears.

## Assets

Already in the repo, already the right shape:

| File | Size | Use |
|---|---|---|
| `public/assets/brand/avatar-newsprint.png` | 1000×1000 | Avatar, light platforms |
| `public/assets/brand/avatar-noir.png` | 1000×1000 | Avatar, dark platforms |
| `public/assets/brand/banner-bluesky.png` | 1500×500 | Bluesky header |
| `public/assets/brand/banner-x.png` | 1500×500 | X header |
| `public/assets/brand/banner-linkedin.png` | 1128×191 | LinkedIn company page |
| `public/assets/brand/banner-youtube.png` | 2560×1440 | YouTube channel art |
| `public/assets/brand/banner-twitch.png` | 1200×480 | Twitch profile banner |
| `public/assets/brand/banner-twitch-offline.png` | 1920×1080 | Twitch offline screen |
| `public/assets/og.png` | 1200×630 | **Share card only** — not a banner |

Banners come from `tools/banner.py`, which renders them through headless Chrome
against the stylesheet's own palette and the self-hosted typeface. Don't edit the
PNGs; change the tool and re-run, or they drift from the site the first time a
colour changes and nobody notices, because nobody diffs a PNG.

```sh
./tools/banner.py --list
./tools/banner.py bluesky              # noir, the default
./tools/banner.py bluesky --newsprint  # light platforms
```

**`og.png` is not a banner** and was the wrong thing to reach for. It is 1.9:1
and a banner slot is 3:1, so it crops. It is also a different composition — a
three-column dateline and the headline — which is right for a share card and
wrong for a profile header.

Each preset is checked against a centred safe area by measuring the bounding box
of what was actually drawn, not by trusting the layout arithmetic. YouTube is
why: it renders at 2560×1440 and guarantees only the centre 1235×338, under 12%
of the area, so type scaled off the canvas height walks straight out of frame on
a phone. The check is calibrated — it passes the shipped layout and fails a
deliberately oversized one, reporting which edge went over and by how much.

Bio, everywhere, so it stays the same everywhere:

> Two people building in public. Issue No. 1 at agentsee.work

**Don't add social links to the site until the accounts exist.** Same rule as
the register in section 04 — we don't list an issue that hasn't been published,
and we don't link a profile that isn't there.
