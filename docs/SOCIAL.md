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

### Bluesky gets the name for free

Bluesky verifies handles by DNS, so owning `agentsee.work` means we can be
`@agentsee.work` outright — the exact brand, no suffix, no compromise, and it
reads as its own proof of ownership. This is the one platform where we don't
have to settle. Still unclaimed as of 17 September 2026.

Create the account on `agentseework.bsky.social` first, then:

```sh
# Get the DID from the account, then publish it as a TXT record.
curl -s "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle=agentseework.bsky.social"
# TXT  _atproto.agentsee.work  ->  did=did:plc:xxxxxxxxxxxxxxxxxxxxxxxx
```

Then change the handle to `agentsee.work` in Bluesky's settings, which releases
`agentseework.bsky.social` back to the pool. The existing Cloudflare token
already has **DNS — Edit** on this zone, so this needs no new permission.

### Three defensive registrations of bare `agentsee`

Not vanity — we are not using these, and should never post from them.

| Where | Why it is worth holding |
|---|---|
| PyPI | A guessable package name published by someone who isn't us is a supply-chain trap for anyone who reaches for the obvious thing. |
| Docker Hub | Same argument, same blast radius. |
| mastodon.social | No identity verification anywhere in the network, so impersonation costs an attacker nothing and costs us the benefit of the doubt. |

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
Threads, TikTok, Reddit, Facebook. They bot-wall or rate-limit datacentre IPs
and return an identical response for a real account and a nonsense one. Treat
these as *unknown*, not as free. Grab them by hand, TikTok first.

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
6. **The three defensive reservations** — PyPI, Docker Hub, mastodon.social.
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

**Phone verification ties an account to a person.** X, TikTok and Instagram will
ask, and Twitch requires 2FA before it will let you stream at all — not at
signup, so it is easy to hit the first time you actually try to go live. A
personal mobile number means one of us is a single point of failure for the
account. Put TOTP in the shared vault — see [CREDENTIALS.md](CREDENTIALS.md) —
and write down which number was used where.

**YouTube: the handle and the channel name are separate things**, and a channel
created under a personal Google account is owned by that person. Create it as a
**Brand Account** so both of us can be managers without sharing a login.
Retrofitting this later means migrating the channel.

**Threads is Instagram's namespace.** Securing `agentseework` on Instagram
secures it on Threads. Don't count it as a second win, and don't let the
Instagram handle go on the assumption Threads is separate.

**Hugging Face and Docker Hub are case-insensitive.** `AgentSEE` and `agentsee`
are the same account. Don't read a case variant as an available handle.

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
