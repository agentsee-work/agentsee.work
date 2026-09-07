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
| **Engineering** | Cloudflare API token, R2 tokens, Infomaniak Public Cloud, SMTP2GO SMTP user | Read by a machine |
| **IT** | Cloudflare and Infomaniak account logins, registrar, GitHub org, mailboxes, healthchecks.io | Typed by a person |
| **Security** | The restic passphrase. Where the Emergency Kits physically are — not what is in them | Losing it loses something unrecoverable |
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
`accounts@agentsee.work`. Recorded in [MAIL-BUILD-RUNBOOK.md](MAIL-BUILD-RUNBOOK.md).

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
- [ ] **Abrar accepts, then make him an Owner.** Verify it took — a team with
      one Owner looks exactly like a team with two until you need the second.
- [ ] Both enable 2FA on their own login; write recovery codes on paper.
- [ ] **Both print the Emergency Kit** and store it apart from the passwords.
- [x] James: personal account linked, free Families membership claimed.
- [ ] Abrar does the same — it is per-member, not something you can grant him.
- [x] Create the vaults: Engineering, Finance, IT, Security, Social, plus the
      Shared one that came with the account.
- [ ] Empty `Shared` and keep it that way.
- [ ] File the existing secrets by the machine-read/human-typed rule above.
      [`infra/op.env`](../infra/op.env) resolves `op://Engineering/...`, so
      those three items must be in **Engineering** under exactly those names.
- [ ] Install the CLI and turn on the desktop-app integration, so `op run` can
      unlock without a session token in the shell.
- [ ] *Then* the social signups in [SOCIAL.md](SOCIAL.md) — so each TOTP seed
      lands in the vault as the account is made.

The last one is why this comes first. Sorting credentials after the accounts
exist means transcribing seeds off a phone, which nobody does, which is how one
person ends up holding everything.

## Later, not now

Business unlocks two things worth knowing about before they are needed:

**Service accounts.** A scoped, non-human credential that `op` can authenticate
as. The obvious target is the Pages token in GitHub Actions secrets — but that
swap trades one stored secret for another, since the service-account token has
to live in GitHub too. It pays off with several secrets, or when a secret needs
rotating without touching CI. Not yet.

**The SSH agent.** 1Password can hold the key for `mail.agentsee.work` and
require biometric confirmation per use, which is a real improvement over a key
sitting in `~/.ssh` — the box is about to become somewhere both of us log into.
Worth doing once the server exists.
