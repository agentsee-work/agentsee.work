# Social accounts

Which handle we use, why, and the things that will bite during signup.

Nothing here is a credential. Handles are public by definition; the passwords
and recovery codes belong in the shared vault, never in this repo.

## The decision

**`agentseework` everywhere. `@agentsee.work` on Bluesky.**

Bare `agentsee` was gone on all five platforms that matter before we started —
checked 22 August 2026, all five dormant squatters rather than a rival product
(see the audit below). Chasing it means a reclaim request nobody owes us, so we
don't chase it.

`agentseework` is the domain with the dot removed. It was free on every
platform we could verify, it survives every platform's charset rules — no
underscore, no hyphen, 12 characters — and it degrades gracefully in speech:
"agentsee dot work". The alternatives (`agentseehq`, `agentseeshow`,
`agentseetv`, `weareagentsee`) were equally free and all say less.

Two deliberate exceptions:

| Where | Handle | Why |
|---|---|---|
| GitHub org | `agentsee-work` | Already exists, already in the runbook. Leave it. |
| Bluesky | `agentsee.work` | Bluesky lets a domain *be* the handle. See below. |

### Bluesky gets the name for free

Bluesky verifies handles by DNS, so owning `agentsee.work` means we can be
`@agentsee.work` outright — the exact brand, no suffix, no compromise, and it
reads as its own proof of ownership. This is the one platform where we don't
have to settle.

Create the account on `agentseework.bsky.social` first, then:

```sh
# Get the DID from the account, then publish it as a TXT record.
curl -s "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle=agentseework.bsky.social"
# TXT  _atproto.agentsee.work  ->  did=did:plc:xxxxxxxxxxxxxxxxxxxxxxxx
```

Then change the handle to `agentsee.work` in Bluesky's settings, which releases
`agentseework.bsky.social` back to the pool. The existing Cloudflare token
already has **DNS — Edit** on this zone, so this needs no new permission.

## What was actually free

Checked 22 August 2026. Every probe was calibrated against a known-taken and a
known-free control first — several platforms return `200 OK` for accounts that
don't exist, and a naive check reads that as "taken" (or worse, "free").

**Verified free for `agentseework`:**

| Platform | Method |
|---|---|
| GitHub | `api.github.com/users/…` → 404 |
| YouTube | `youtube.com/@…` → 404 |
| Instagram + Threads | `web_profile_info` API → 404 (one namespace, both platforms) |
| X | `x.com/…` → 404 |
| Twitch | GraphQL `user(login:)` → null |
| Kick | `kick.com/api/v2/channels/…` → 404 |
| Mastodon (mastodon.social) | `accounts/lookup` → 404 |
| Bluesky | `resolveHandle` → unresolvable |
| Substack | subdomain → 404 |
| Trovo, Vimeo, Patreon, LinkedIn, Hugging Face | 404 against a taken control |

**Could not be verified, and the signup form is the only authority:** TikTok,
Reddit, Facebook, Odysee, npm, PyPI, Docker Hub. All of them bot-wall or
rate-limit datacenter IPs and return an identical response for a real account
and a nonsense one — including through two different fetchers. Treat these as
*unknown*, not as free. Grab them by hand, TikTok first.

### Who holds `agentsee`

Worth recording so nobody re-runs this. None is a company of that name — the
brand is unclaimed, only the handle is.

| Platform | Holder | State |
|---|---|---|
| GitHub | no name set | Created 19 Feb 2026, 0 repos, 0 followers, never touched |
| Instagram | full name "-" | 0 posts, 47 followers, no bio |
| Twitch | "Agentsee" | Last broadcast 3 Jan 2015, 2 followers |
| X | a personal account | Real person, in use |
| YouTube | unrelated channel | In use |

A web search for "AgentSee" as a company or product returns nothing. No
trademark collision to design around.

## Registering, in order

Do them in this order — the email address has to exist before anything else,
and the platforms that gate on a phone number are the ones worth doing while
you still have patience.

1. **`accounts@agentsee.work`, fanned out to both of us.** Not `hello@`.
   Platform mail is high-volume and mostly noise, and it must not drown the
   public inbox. Fanning it to both means neither of us solely owns the
   accounts. Same Worker pattern as `hello@` and `show@` — the rule action is
   `{"type":"worker","value":["email-fanout"]}`, and the address goes in the
   Worker's `RECIPIENTS` secret, not the repo. See the runbook.
2. **Bluesky** — instant, no phone, and the DNS step above gets us the exact
   name. Do it first for the morale.
3. **GitHub** — the org exists; nothing to do but keep it.
4. **X, Instagram, TikTok** — phone verification, see the trap below.
5. **YouTube and Twitch** — the streaming pair, both with their own traps.
6. **Everything else** — LinkedIn, Mastodon, Kick, Substack, Reddit — as and
   when there is something to put on them.

Reserving a handle costs nothing and forecloses a squatter. Posting to eleven
dead accounts costs credibility. Reserve broadly, publish narrowly.

## Traps

**We can receive mail as `@agentsee.work` but cannot send as it.** DMARC is
`p=reject` and nothing is authorised to send (README, "Mail"). Signup
verification links arrive fine — that is inbound. But any platform
support or appeal flow that says *reply from the address on the account* is
impossible until DMARC is relaxed, in the order the README sets out. Discover
this during an account lockout and it is a very bad day. Consider it before
picking `accounts@agentsee.work` as the recovery address for anything we would
be hurt to lose.

**A forwarding destination must be confirmed by a human before any rule can
reference it.** Already in the runbook; it applies here too. Set the address up
before signup night, not during it.

**Phone verification ties an account to a person.** X, TikTok and Instagram
will ask, and Twitch requires 2FA before it will let you stream at all — not at
signup, so it is easy to hit the first time you actually try to go live. A
personal mobile number means one of us is a single point of failure for the
account. Put TOTP in the shared vault — see [CREDENTIALS.md](CREDENTIALS.md),
**which should be set up before the first signup**, not after — and write down
which number was used where.

**YouTube: the handle and the channel name are separate things**, and a channel
created under a personal Google account is owned by that person. Create it as a
**Brand Account** so both of us can be managers without sharing a login.
Retrofitting this later means migrating the channel.

**Threads is Instagram's namespace.** Securing `agentseework` on Instagram
secures it on Threads. Don't count it as a second win, and don't let the
Instagram handle go on the assumption Threads is separate.

## Assets

Already in the repo, already the right shape:

| File | Size | Use |
|---|---|---|
| `public/assets/brand/avatar-newsprint.png` | 1000×1000 | Avatar, light platforms |
| `public/assets/brand/avatar-noir.png` | 1000×1000 | Avatar, dark platforms |
| `public/assets/og.png` | 1200×630 | Banner / share card |

Bio, everywhere, so it stays the same everywhere:

> Two people building in public. Issue No. 1 at agentsee.work

**Don't add social links to the site until the accounts exist.** Same rule as
the register in section 04 — we don't list an issue that hasn't been published,
and we don't link a profile that isn't there.
