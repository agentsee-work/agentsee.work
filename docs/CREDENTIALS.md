# Credentials

Where shared secrets live, and the rules that keep the arrangement honest.

**Nothing in this file is a secret.** It describes the system, not its contents.
No password, seed, recovery code or token belongs in this repo — it is public.

## Status: account exists, not yet loaded

1Password Business is signed up and Abrar is invited. Nothing else below has
happened — no vault, no roles confirmed, no secrets in it.

The gap between "the account exists" and "the arrangement works" is entirely
the [Order](#order) at the end of this file, and every item in it is the kind
of thing that gets skipped because nothing warns you.

## Why

Three things forced it, and they are the same thing wearing different hats:

- The social accounts in [SOCIAL.md](SOCIAL.md) need TOTP that both of us can
  reach, or one person becomes a single point of failure for the brand.
- The Cloudflare API token in the runbook is shared by ad hoc means today.
- Standing up our own mail adds a set of logins worth losing: Infomaniak,
  SMTP2GO, two R2 tokens, and a restic passphrase that is more important than
  any of them.

The rule we settled on for mail applies here: **nothing important should be
recoverable by exactly one person.**

## Choice: 1Password Business — $215.76/year

1Password because one of us already used it, which is a better reason than it
looks: a password manager nobody enjoys is one that gets bypassed, and the
bypass is where the single points of failure come back.

| Option | 2 users/yr | |
|---|---|---|
| **Business** | **$215.76** (≈£171) | $8.99/user/mo, annual only. Chosen |
| Families | $71.88 (≈£57) | 5 seats and shared vaults, but organizer-based recovery |
| Teams Starter Pack | $239.40 (≈£190) | Flat rate for 10 seats we don't have |

This file previously recommended Families now and Business at the first client.
We went to Business directly. Three things make that defensible rather than
just more expensive:

- **The free Families membership per member** means both personal accounts stop
  being separate bills. If both were on Individual at $47.88, that is $95.76
  back, and the real gap against the Families plan is nearer $120/year than the
  $144 the table implies.
- **Real roles now.** Families recovery runs through *family organizers*, a role
  that also carries everything else. Business separates recovery from
  administration, so a third person can be given the ability to recover accounts
  and nothing more. That matters the first time someone is added, and adding
  someone is not a thing you want to re-architect the vault for.
- **Service accounts and the developer tooling.** Business is the tier where
  service accounts, SSH agent and Git commit signing land. Given this repo runs
  `op run` in front of every apply, that is the difference between a password
  manager and part of the toolchain.

There is also now an **activity log**, which quietly retires the reasoning that
"who changed this" is answerable by asking. It is answerable by looking.

### Linking the personal accounts

Not migration — the word is wrong for it. **Nothing of yours moves.** The
personal account stays exactly as it is and is *linked*, which is a billing
relationship and nothing else; 1Password's own wording is that the two accounts
"aren't connected in any technical way". Only items you deliberately drag across
go anywhere.

One surprise worth knowing before clicking: the free membership is a *Families*
membership, so an existing **Individual** account gets prompted to convert to a
family account. Prepaid time is credited. Unlinking later is graceful — the
account drops into a complimentary trial rather than locking.

Both of us should claim it. An unclaimed benefit is a bill someone is still
paying.

With Infomaniak's box at £60–80, the pair comes to **≈£230–250/year** before
the personal savings and **≈£155–175** after. Either way it is over the original
£100, and worth naming rather than hiding in a subtotal.

## Vaults

Six, created rather than planned, and the structure is the filing — there are
no tags. A second scheme layered on top of vaults would be two answers to the
same question, which is how items end up findable by neither.

| Vault | Holds | The test |
|---|---|---|
| **Engineering** | Cloudflare API tokens (DNS, podcast), R2 tokens (state, backup, podcast), Swiss Backup keys, Infomaniak Public Cloud, SMTP2GO SMTP user | Read by a machine |
| **IT** | Cloudflare and Infomaniak account logins, registrar, GitHub org, mailboxes, healthchecks.io | Typed by a person |
| **Security** | The restic passphrase — one, shared by both backup repositories. Where the Emergency Kits physically are, not what is in them | Losing it loses something unrecoverable |
| **Social** | Platform logins and their passkeys | Obvious |
| **Finance** | Bank, invoicing, accounting | Empty until there is money |
| **Shared** | Nothing | See below |

### The rule that makes it decidable

**Engineering is machine-read; IT is human-typed.** That is the whole boundary,
and it is the one that would otherwise cost an argument every time — "Cloudflare"
is both an API token and a dashboard login, and they are not the same secret.
The API token that `op run` resolves goes in Engineering. The login you sign
into with a browser goes in IT. Same company, different vaults, and each is in
the one you would look in.

The same split settles GitHub: the org account is IT, a deploy token is
Engineering. And SMTP2GO: the dashboard login is IT, the SMTP user the mail
server authenticates with is Engineering.

**When a credential is genuinely both, the machine wins.** The Infomaniak `PCU-`
user is read by `op run` on every apply *and* is what signs into the Horizon
dashboard, so the rule above does not settle it on its own. It goes in
Engineering, because a person who looks in the wrong vault finds it in ten
seconds and `op run` does not — it fails with a provider auth error that reads
like a wrong password.

### Item types are part of the contract

The type decides the field names, and `infra/op.env` references fields by name:

| Item | Type | Fields |
|---|---|---|
| Cloudflare API token | API Credential | `username`, `credential` |
| Cloudflare podcast token | API Credential | `username`, `credential` |
| Podcast edge hash key | API Credential | `credential` (64 hex characters from `openssl rand -hex 32`) |
| R2 tfstate token | API Credential | `username`, `credential` |
| R2 podcast token | API Credential | `username`, `credential` |
| YouTube podcast client | API Credential | `username` (the OAuth client ID), `credential` (its secret) |
| YouTube podcast token | API Credential | `username` (the channel id it is for), `credential` (the refresh token) |
| X podcast app | API Credential | `username` (the OAuth 2.0 Client ID), `credential` (its Client Secret) |
| X podcast token | API Credential | `username` (the account id it is for), `credential` (the OAuth 2.0 refresh token, not the two-hour access token beside it; X replaces it on every refresh, and the publisher writes the new one back here) |
| Bluesky podcast token | API Credential | `username` (the handle, `agentsee.work`), `credential` (an app password from Settings > Privacy and security > App passwords, not the account password) |
| Mastodon podcast token | API Credential | `username` (the handle), `credential` (the access token of an application the account made at Preferences > Development, scopes `write:media write:statuses`) |
| Spotify podcast app | API Credential | `username` (the Client ID of an app made at developer.spotify.com, under an account that holds Premium, which Spotify has required of app owners since March 2026), `credential` (its Client Secret). Read only by `tools/directories.py` (`tools/directories.env`), for each episode's own Spotify link; nothing of any user |
| Engine people | API Credential | `credential`: the addresses Cloudflare Access lets into the engine, as an HCL list. Read by `infra/podcast/op.env` as a tofu variable, so the addresses stay out of this public repository |
| Engine tunnel token | API Credential | `credential`: what the box's `cloudflared` runs with (`tofu output -raw engine_tunnel_token` in `infra/podcast`). Goes into `/etc/agentsee/tunnel.env` by hand; `engine/home/README.md` |
| Infomaniak Public Cloud | **Login** | `username`, `password`, + custom `project` |

Login rather than API Credential for the last one precisely because an API
Credential item names that field `credential`, and `OS_PASSWORD` expects
`password`. A Login also carries a website field, so the Horizon URL autofills
on the occasions a person does use it.

⚠ **Add the custom `project` field outside any section.** A field inside a named
section needs that section in the path, and the reference in `op.env` does not
have one. Rather than reasoning about it: right-click the field in the app and
**Copy Secret Reference**, which gives the authoritative form.

Security is not "important things" — everything here is important. It is
**things whose loss cannot be undone.** A leaked R2 token is a bad afternoon;
you mint another. A lost `RESTIC_PASSWORD` turns every backup into ciphertext
nobody can read, permanently. Only the second kind belongs there, or the vault
becomes a synonym for "sensitive" and stops meaning anything.

### Why this beats the single vault this file used to specify

Everything `op run` resolves now lives in **one** vault. So when a service
account is eventually created for CI, it gets read on Engineering and nothing
else — least privilege by construction, rather than something to be discovered
in an audit. That was awkward with one shared vault and is free here.

### Shared stays empty

It came with the account and it is the path of least resistance, which is
exactly what makes it dangerous: things land there by default rather than by
decision.

So it has a job, and the job is to hold nothing. **An item in Shared means an
unfiled item and nothing else** — an inbox to be triaged, not a drawer. That is
checkable at a glance, which "don't let it become a dumping ground" is not.

### What this does not yet buy

Six vaults with identical permissions is filing, not security. The separation
starts paying the day there is a third person or a service account, and until
then it is an investment in not having to reorganise then. Worth being straight
about that rather than describing it as defence in depth.

Private items stay in your own **Private** vault, which nobody else can see —
including Owners, who can recover an account but cannot read what is in it.

Anything belonging to *you* rather than to AgentSee belongs in the linked
Families account instead, so it survives you ever leaving the Business team.

⚠ **And it goes one way only.** The linked Families account has other people in
it — partners, family — with access to its shared vaults. An AgentSee
credential dragged into one of those is a business secret handed to someone
outside the business, and the drag is a two-second gesture between two accounts
sitting in the same sidebar. Business credentials go to a business vault or to
your own Private vault, never to a family shared vault.

## Rules

These are the parts that matter. The product is interchangeable; these aren't.

**Both of us are Owners.** Not one Owner and one member. Two separate reasons,
and only the first is obvious:

- **Recovery.** In Business, account recovery is performed by anyone in a group
  holding *Recover Accounts*, which the Owners and Administrators groups have by
  default. If only one of us can recover, the other has no recovery path and we
  have reproduced precisely the failure this was bought to prevent.
- **Billing and deletion.** Administrator is enough to recover accounts, but
  only an **Owner** can change billing or delete the team. A single Owner means
  a single person who can let the subscription lapse — and a single person who
  cannot be overruled if it comes to that.

Set it as soon as Abrar accepts, before loading any secrets, and **confirm it
took**. Nothing warns you that a team has one Owner.

It is not a feature to switch on; it is a consequence of group membership,
which is exactly what makes it easy to leave undone.

*When a third person arrives:* Business can grant *Recover Accounts* through a
custom group without any of the other administrator permissions. Use that rather
than making a contractor an Administrator.

**The Emergency Kit goes on paper, offline, both of us.** 1Password has an
account-specific **Secret Key** as well as your password, and *you cannot sign
in on a new device without it*. It is not recoverable by support and it is not
in your head. Write it down or print it, store it away from the passwords, and
do it before there is anything in the vault worth losing.

**Then sign out and sign back in using only the paper.** A kit nobody has tested
is a guess about a character that may have been transcribed wrong, and the test
costs two minutes now against everything later. James's is written by hand and
verified this way, which is better than an untested printout.

⚠ The kit has a **blank for the account password**, and a Secret Key alone will
not get you in. Owner recovery covers one of us being locked out — the paper's
real job is the case where we both are, or the account is gone, and that is
precisely the case where a missing password matters.

**The vault's own 2FA is never in the vault.** Storing a vault's second factor
inside itself is a circular dependency that only reveals itself when locked out.
Each of us secures our 1Password login with an authenticator on our own phone —
and the recovery codes for *that* go on paper, one copy each, offline.

**Prefer a passkey in the shared vault; fall back to TOTP.** Both live in the
vault and both are therefore reachable by either of us, which is the property
that matters — 2FA on one person's phone guarantees a lockout the first time
that person is unavailable, which is a certainty rather than a risk.

Where they differ is the attack we will actually see. A TOTP code can be typed
into a convincing fake login page and replayed by whoever is running it; a
passkey is bound to the real origin and simply will not offer itself to the
fake. Nobody is going to break our crypto. Somebody might well send Abrar a
Cloudflare login page.

Both collapse two factors into one, and that is a real trade worth naming: a
compromised vault loses the account outright. We take it for *shared* accounts
because the alternative is worse. Personal logins keep genuine 2FA separation.

### Security keys: not yet, and not one

A hardware key is the one factor that does *not* collapse into the vault, which
is exactly why it is the right protection for the vault itself rather than for
the accounts inside it.

It is not bought yet, for two reasons:

- **It cannot be shared.** Making it work across shared accounts means
  registering both our keys on each one, and a spare each so that losing one is
  an inconvenience rather than a lockout. That is four keys, around £200.
- **Fewer than four is worse than none.** A single key is a new single point of
  failure, which is this file's recurring rule wearing yet another hat.
  1Password's own guidance is to have a recovery code or authenticator
  configured *before* adding a key, not after.

Against a password plus a Secret Key that is itself unphishable, the marginal
gain today is real but small.

**The trigger is the first client** — the same trigger as the Business plan.
The moment this vault holds credentials that are not ours to lose, the
calculation changes, and the first thing to protect is the vault.

**No recovery path may run through the thing it recovers.** Four instances of
one rule, and they keep appearing because it is easy to miss:

| Don't | Because |
|---|---|
| The vault's 2FA inside the vault | Locked out of the vault, locked out of its key |
| The Emergency Kit only in the vault | The Secret Key is what gets you *into* the vault |
| Mail alerts about mail failures | The alert can't arrive if mail is what broke |
| `@agentsee.work` on Infomaniak, SMTP2GO, Cloudflare, 1Password | Mail breaks → recovery link goes to broken mail → can't fix mail |

So the accounts that mail *depends on* use personal addresses. Everything else —
social platforms, booking, anything not load-bearing — uses
`accounts@agentsee.work`.

### When a vendor refuses a free email address

SMTP2GO rejects free providers at signup — "please use an email at your own
domain" — which collides head-on with the rule above: it is *in* mail's recovery
path, so it must not depend on our mail.

**The check is usually only at signup.** Sign up with whatever the form will
accept, then change the account email to a personal address afterwards — this
worked for SMTP2GO on 14 September 2026. Try that first; it costs a minute and
leaves nothing to maintain.

If a vendor enforces it on change too, the fallback is a subdomain kept on
**Cloudflare Email Routing permanently**, forwarding to personal inboxes:

```
smtp2go@ops.agentsee.work  →  personal inbox
```

The vendor sees a domain address; delivery is Cloudflare's, not Stalwart's, so
our mail server can be entirely dead and the reset link still arrives.

⚠ **That fallback is load-bearing in a way that is easy to lose.** It depends on
Cloudflare Email Routing continuing to serve a subdomain of a zone whose apex
has been cut over to our own server — which is not clearly a supported
configuration, and reportedly leaves subdomain MX records locked. If you rely on
it, verify after any change to the apex that mail to it still arrives, by
sending some. And it must never be migrated onto Stalwart "for tidiness": the
loop would close again with no warning.

`ops.agentsee.work` is not currently in any recovery path — SMTP2GO's account
email is personal — and that is the better state to be in. Recorded in [MAIL-BUILD-RUNBOOK.md](MAIL-BUILD-RUNBOOK.md).

**API tokens live in the vault, and nowhere else that persists.** Specifically:
the full Cloudflare token, which per the runbook can repoint the domain and the
mail. The Pages-only CI token stays in GitHub Actions secrets — that is its
proper home — but its *existence, scope and expiry* are recorded under `infra`
so a roll doesn't depend on memory.

**Log the token expiry as a vault item with a reminder.** The runbook says to
record it and put a reminder somewhere you'll see it. This is that somewhere:
the CI token expires **20 November 2026**.

## Secret references, not copy and paste

This is the part that earns 1Password its place in *this* repo rather than any
other password manager.

`op` resolves `op://Vault/Item/field` at the moment a command runs, so
[`infra/op.env`](../infra/op.env) can be committed — it holds references, not
values:

```sh
cd infra
op run --env-file=op.env -- tofu plan
```

Three things that buys, all of which were previously solved by discipline:

- **Nothing is exported into a shell.** `export CLOUDFLARE_API_TOKEN=...` puts a
  token that can repoint our domain into shell history and into the environment
  of every process started afterwards, for as long as that terminal lives.
- **No plaintext credential files.** The R2 keys come from the vault rather than
  `~/.aws/credentials`, and the Infomaniak password rather than a `clouds.yaml`
  sitting at mode 0600 and hoping.
- **Rotation is one edit.** Change the item, and every reference to it is already
  correct. Nobody has to remember which machines held a copy.

`op run` also masks resolved secrets in the output it passes through, which
matters more than it sounds when the thing being run is `tofu plan`.

The item names in `op.env` are the contract. Renaming a vault item breaks the
apply, so rename deliberately and grep `infra/op.env` first.

## Order

- [x] Create the Business account.
- [x] Invite Abrar.
- [x] **Abrar accepts, then make him an Owner.** Note that accepting has its own
      confirmation step after the invite, which is easy to miss and looks
      identical to an invitation nobody opened. Verify with `op user list` and
      read the `TYPE` column — a team with one Owner looks exactly like a team
      with two until you need the second.
- [x] James: 2FA on his own login; recovery codes on paper.
- [ ] Abrar does the same.
- [x] James: Emergency Kit written out by hand, **and verified by signing out
      and back in with it**. That verification is worth more than printing one
      and filing it unread.
- [ ] Abrar does the same.
- [x] James: personal account linked, free Families membership claimed.
- [ ] Abrar does the same — it is per-member, not something you can grant him.
- [x] Create the vaults: Engineering, Finance, IT, Security, Social, plus the
      Shared one that came with the account.
- [x] Empty `Shared` and keep it that way.
- [x] Install the CLI. Every `tofu` command since has gone through `op run`, so
      that much is proven rather than configured.
- [ ] **Turn on the desktop-app integration** — this line used to claim it was
      already done and proven. It isn't: `op run` fails with `You are not
      currently signed in` once the session token expires, which is the CLI
      session model, not app integration. Until it is on, every apply needs an
      interactive `op signin` first and cannot be run by anything that isn't a
      human at a terminal.
- [x] File the infrastructure secrets in **Engineering**: the Cloudflare token,
      the R2 state token, Infomaniak Public Cloud, the SMTP2GO SMTP user, the R2
      backup token and the Swiss Backup keys. `op.env` resolves the first three
      on every apply, which is a stronger check than a tick.
- [x] File the podcast's two tokens in **Engineering**, neither of them the
      DNS token: `Cloudflare podcast token` (Account > Workers R2 Storage >
      Edit, named `opentofu-podcast` in Cloudflare; `infra/podcast/op.env`
      resolves it) before the first podcast apply, and `R2 podcast token`
      (Object Read & Write on the two podcast buckets, named `podcast-stage`;
      `tools/stage.env` resolves it) after it, because it is scoped to buckets
      that have to exist first. The reasoning is in `infra/podcast/op.env`.
- [x] **The edges** (`infra/podcast/edge.tf`), applied 15 October 2026: widen `opentofu-podcast` in
      Cloudflare (My Profile > API Tokens > Edit) with Account > Workers >
      Admin (the per-product role since September 2026, the one that can
      create a Worker; "Workers Scripts > Edit" is listed as Legacy and maps
      to it), Account > Workers KV Storage > Edit and Zone > Workers Routes >
      Edit on agentsee.work; the value stays the same, so nothing is refiled. Then `openssl rand -hex 32` into `Podcast edge hash key` (API
      Credential, the `credential` field) in **Engineering**;
      `infra/podcast/op.env` hands it to tofu as `TF_VAR_edge_hash_key`, and
      it ends up in the two Workers' bindings and the private state, nowhere
      on disk. The address hashes on both datasets depend on it; rotating it
      only breaks counting within the day it happens. One click first:
      Analytics Engine is off until enabled in the dashboard (Workers &
      Pages > Analytics Engine), and the apply refuses the scripts until it is.
- [x] File the YouTube pair in **Engineering**: `YouTube podcast client` (the
      OAuth client `podcast-engine` in the Google Cloud project, type Desktop
      app, from a consent screen set to In production; `tools/youtube-auth.env`
      resolves it) and then `YouTube podcast token` (what
      `op run --env-file=tools/youtube-auth.env -- ./tools/youtube.py auth`
      prints once, after a consent in the browser as the brand channel;
      `tools/youtube.env` resolves both). A refresh token from a consent
      screen in Testing dies after seven days, and the script says so if it
      sees one. Google also drops a refresh token that goes unused for six
      months.
- [x] File the X pair in **Engineering**: `X podcast app` (the OAuth 2.0
      Client ID and Client Secret of the app `podcast-engine` in the X
      Developer Console, a confidential client) and `X podcast token` (the
      account id, and the OAuth 2.0 refresh token the console generates for
      the show's account; the access token beside it dies in two hours and is
      not filed). Then `op run --env-file=tools/x.env -- ./tools/x.py check`,
      which says which kind of token the item holds and whose it is, and
      `check --refresh` to prove a refresh token and print its replacement to
      file. Unlike Google's, X's refresh token is single use: every refresh
      returns a new one and retires the old, so the vault item is the live
      copy and the publisher writes each new token back to it before using the
      access token it came with (`engine/vault.py`: `op read` for the live
      value, `op item edit` with the item's JSON on stdin for the write, so
      the engine runs under the op session `op run` has; two engines on one
      item would retire each other's tokens). If the console's token is ever
      not enough,
      `op run --env-file=tools/x-auth.env -- ./tools/x.py auth` is the consent
      in a browser, with `http://127.0.0.1:8794/callback` on the app's callback
      list. Done 15 October 2026: both items filed, the token proven with
      `check --refresh` and its replacement filed.
- [x] The restic passphrase in **Security**, and on paper with the Emergency
      Kit. The one whose loss cannot be undone.
- [~] **The social signups in [SOCIAL.md](SOCIAL.md)** — under way, and the
      reason this checklist came first. Bluesky, X, Instagram, TikTok, YouTube,
      Twitch, PyPI and Mastodon are registered; Docker Hub is blocked on the
      platform's own address validation rather than on us. Each TOTP seed goes
      in the vault as the account is made, not in a retrofit that never happens.

The last one is why this comes first. Sorting credentials after the accounts
exist means transcribing seeds off a phone, which nobody does, which is how one
person ends up holding everything.

## Later, not now

Business unlocks two things worth knowing about before they are needed:

**Service accounts.** A scoped, non-human credential that `op` can authenticate
as. The obvious target is the Pages token in GitHub Actions secrets — but that
swap trades one stored secret for another, since the service-account token has
to live in GitHub too. It pays off with several secrets, or when a secret needs
rotating without touching CI.

Now, for one thing: the podcast engine on the box runs under `op run` with a
service account (`engine/home/README.md`), because it resolves nine
references at start and writes one item back (X's rotating refresh token).
Read and write on **Engineering**, nothing on the other vaults; the token
itself is filed as `Engine service account` in **Security**, since it is a
standing credential to a whole vault and there is no recovering it, only
minting another. Service accounts exist on every plan now, with lower rate
limits below Business (1,000 reads and 100 writes an hour, 1,000 requests a
day on Families); the engine uses a few dozen a day.

**The SSH agent.** 1Password can hold the key for `mail.agentsee.work` and
require biometric confirmation per use, which is a real improvement over a key
sitting in `~/.ssh` — the box is about to become somewhere both of us log into.
Worth doing once the server exists.
