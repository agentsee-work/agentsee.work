# Credentials

Where shared secrets live, and the rules that keep the arrangement honest.

**Nothing in this file is a secret.** It describes the system, not its contents.
No password, seed, recovery code or token belongs in this repo — it is public.

## Status: proposed, not yet set up

Like [MAIL-MIGRATION.md](MAIL-MIGRATION.md), this is a plan. When the vault
exists, this file stops saying "will" and starts saying "does".

## Why

Three things forced it, and they are the same thing wearing different hats:

- The social accounts in [SOCIAL.md](SOCIAL.md) need TOTP that both of us can
  reach, or one person becomes a single point of failure for the brand.
- The Cloudflare API token in the runbook is shared by ad hoc means today.
- Mail moving to a real provider adds another set of logins worth losing.

The rule we settled on for mail applies here: **nothing important should be
recoverable by exactly one person.**

## Choice: Bitwarden Families — $47.88/year

Six users, of which we need two. Includes the two features that decide it:
**integrated TOTP** and **unlimited shared collections**.

| Option | 2 users/yr | Verdict |
|---|---|---|
| **Bitwarden Families** | **$47.88** (≈£38) | Six seats, TOTP, shared collections |
| Bitwarden Teams | $96 (≈£76) | Adds event logs and SCIM. Twice the price |
| 1Password Teams Starter | $299 | Ten seats we don't need |
| Proton Pass | bundled | Only via the £250 suite we already ruled out |

Bitwarden Teams is the nominally *correct* product for a company, and what it
buys over Families is an audit log — worth real money at ten people and worth
very little at two, where "who changed this" is answerable by asking. Revisit
if we ever hire. Families is a personal-tier plan; if that sits badly, Teams is
the same software at £76 and nothing else changes.

With Infomaniak at ≈£37, the pair comes to **≈£75/year**, inside the £100.

## Rules

These are the parts that matter. The product is interchangeable; these aren't.

**Both of us are Owners.** Not one owner and one member. A vault administered by
a single person reproduces exactly the failure we bought it to prevent. Set the
second Owner on day one, before loading any secrets, and confirm it took.

**The vault's own 2FA is never in the vault.** Storing a vault's second factor
inside itself is a circular dependency that only reveals itself when locked out.
Each of us secures our Bitwarden login with an authenticator on our own phone —
and the recovery codes for *that* go on paper, one copy each, offline.

**TOTP for shared accounts goes in the vault; personal 2FA does not.** Putting
the seed next to the password collapses two factors into one, so this is a real
trade and worth naming: it means a compromised vault loses the account outright.
We take it for *shared* accounts because the alternative — TOTP on one person's
phone — guarantees a lockout the first time that person is unavailable, which is
a certainty rather than a risk. Personal logins keep genuine 2FA separation.

**Emergency access is configured both ways.** Each of us grants the other
emergency access with a waiting period. It is the backstop for the case this
whole exercise is about.

**No recovery path may run through the thing it recovers.** Three instances of
one rule, and they keep appearing because it is easy to miss:

| Don't | Because |
|---|---|
| The vault's 2FA inside the vault | Locked out of the vault, locked out of its key |
| Mail alerts about mail failures | The alert can't arrive if mail is what broke |
| `@agentsee.work` on Infomaniak, the relay, Cloudflare, the vault | Mail breaks → recovery link goes to broken mail → can't fix mail |

So the accounts that mail *depends on* use personal addresses. Everything else —
social platforms, booking, anything not load-bearing — uses
`accounts@agentsee.work`. Recorded in [MAIL-BUILD-RUNBOOK.md](MAIL-BUILD-RUNBOOK.md).

**Collections, not one shared pile:**

| Collection | Holds |
|---|---|
| `social` | The platform logins and their TOTP seeds |
| `infra` | Cloudflare, Infomaniak (Public Cloud **and** clouds.yaml), SMTP2GO, GitHub org, registrar |
| `mail` | Mailbox passwords, app passwords |

**API tokens live in the vault, and nowhere else that persists.** Specifically:
the full Cloudflare token, which per the runbook can repoint the domain and the
mail. The Pages-only CI token stays in GitHub Actions secrets — that is its
proper home — but its *existence, scope and expiry* are recorded in `infra` so a
roll doesn't depend on memory.

**Log the token expiry as a vault item with a reminder.** The runbook says to
record it and put a reminder somewhere you'll see it. This is that somewhere:
the CI token expires **20 November 2026**.

## Order

1. Create the Families plan and get both accounts in it.
2. Promote the second account to Owner. Verify.
3. Both enable 2FA on their own login; write recovery codes on paper.
4. Configure emergency access in both directions.
5. Create the three collections.
6. *Then* start the social signups in [SOCIAL.md](SOCIAL.md) — so each TOTP seed
   lands in the vault as the account is made, not in a retrofit that never
   happens.

Step 6 is why this comes first. Sorting credentials after the accounts exist
means transcribing seeds from a phone, which nobody does, which is how one
person ends up holding everything.
