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
- Standing up our own mail adds a set of logins worth losing: Infomaniak,
  SMTP2GO, two R2 tokens, and a restic passphrase that is more important than
  any of them.

The rule we settled on for mail applies here: **nothing important should be
recoverable by exactly one person.**

## Choice: 1Password Families — $71.88/year

Chosen because one of us already uses it, which is a better reason than it
looks: a password manager nobody enjoys is a password manager that gets
bypassed, and the bypass is where the single points of failure come back.

| Option | 2 users/yr | Verdict |
|---|---|---|
| **Families** | **$71.88** (≈£57) | 5 seats, shared vaults, TOTP, and both can be organizers |
| Business | $215.76 (≈£171) | Real Owner roles, audit log, and a free Families each |
| Teams Starter Pack | $239.40 (≈£190) | Flat rate for 10 seats we don't have |

**Families now, Business at the first client.** What Business buys over Families
is an audit log, provisioning and proper role separation — worth real money the
moment there are client secrets or a third person, and worth very little at two,
where "who changed this" is answerable by asking. That is the trigger, written
down now so it is a decision rather than a drift.

Starting with Families creates **no migration debt.** Moving later is dragging
items between vaults plus linking the accounts — the same work whenever it
happens.

### The linking thing, because it is not obvious

A Business account includes a **free Families membership** for each member. So
under Business the personal 1Password stops being a separate bill — which
offsets about $96 of the $216, making the real gap nearer $120/year than $144.

Worth being precise about what that does, because "migration" is the wrong word
for it: **nothing of yours moves.** The personal account stays exactly as it is
and is *linked*, which is a billing relationship and nothing else — 1Password's
own wording is that the two accounts "aren't connected in any technical way".
Only the items you deliberately drag across go anywhere.

One surprise worth knowing before you click: the free membership is a *Families*
membership, so an existing **Individual** account gets prompted to convert to a
family account. Prepaid time is credited. Unlinking later is graceful — the
account drops into a complimentary trial rather than locking.

With Infomaniak's box at £60–80 and the relay free, the pair comes to
**≈£120–140/year**, which is over the original £100 and worth naming rather
than hiding in a subtotal.

## One vault, three tags

| Tag | Holds |
|---|---|
| `social` | The platform logins and their TOTP seeds |
| `infra` | Cloudflare, Infomaniak, SMTP2GO, R2 tokens, GitHub org, registrar |
| `mail` | Mailbox passwords, app passwords, the restic passphrase |

**One shared `AgentSee` vault, not three.** 1Password's permissions are per
vault, so three vaults means three permission sets to keep in sync for two
people who both need all of it. Tags give the same organisation with none of
the drift.

**Split into a real vault the moment someone should *not* see something** — a
contractor, or a client's credentials. That is when the boundary is real and a
vault is the right tool for it. Until then it is filing, and tags are filing.

Personal items stay in the Personal vault. The shared vault is for things that
are genuinely shared, and putting a personal login in it is how the boundary
starts to blur.

## Rules

These are the parts that matter. The product is interchangeable; these aren't.

**Both of us are family organizers.** Not one organizer and one member. In
1Password Families, account recovery is performed *by a family organizer* — so
if only one of us holds that role, the other has no recovery path and we have
reproduced precisely the failure this was bought to prevent. Set it on day one,
before loading any secrets, and confirm it took.

This is 1Password's equivalent of the emergency access we would have configured
in Bitwarden. It is not a separate feature to switch on; it is a consequence of
the role, which makes it easy to leave undone.

**The Emergency Kit goes on paper, offline, both of us.** 1Password has an
account-specific **Secret Key** as well as your password, and *you cannot sign
in on a new device without it*. It is not recoverable by support and it is not
in your head. Print the Emergency Kit, store it away from the passwords, and do
it before there is anything in the vault worth losing.

**The vault's own 2FA is never in the vault.** Storing a vault's second factor
inside itself is a circular dependency that only reveals itself when locked out.
Each of us secures our 1Password login with an authenticator on our own phone —
and the recovery codes for *that* go on paper, one copy each, offline.

**TOTP for shared accounts goes in the vault; personal 2FA does not.** Putting
the seed next to the password collapses two factors into one, so this is a real
trade and worth naming: it means a compromised vault loses the account outright.
We take it for *shared* accounts because the alternative — TOTP on one person's
phone — guarantees a lockout the first time that person is unavailable, which is
a certainty rather than a risk. Personal logins keep genuine 2FA separation.

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

1. Create the Families plan and get both accounts in it.
2. Promote the second account to **family organizer**. Verify — recovery
   depends on it and nothing will warn you.
3. Both enable 2FA on their own login; write recovery codes on paper.
4. **Both print the Emergency Kit** and store it apart from the passwords.
5. Create the `AgentSee` vault and the three tags.
6. *Then* start the social signups in [SOCIAL.md](SOCIAL.md) — so each TOTP seed
   lands in the vault as the account is made, not in a retrofit that never
   happens.

Step 6 is why this comes first. Sorting credentials after the accounts exist
means transcribing seeds from a phone, which nobody does, which is how one
person ends up holding everything.
