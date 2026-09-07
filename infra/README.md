# infra/

The mail server, its DNS and its outbound relay, declared. OpenTofu, not
Terraform — the BSL relicensing is exactly the kind of dependency this repo
avoids, and it is a drop-in.

This doubles as the reference implementation we point clients at, which is the
reason for the comment density. The build it produces is specified in
[../docs/MAIL-SELFHOST.md](../docs/MAIL-SELFHOST.md).

```
versions.tf     providers, and R2 as the state backend
variables.tf    inputs, including the cutover flag
dns.tf          Cloudflare: MX, SPF, DKIM, DMARC
server.tf       Infomaniak/OpenStack: instance, security groups, data volume
relay.tf        SMTP2GO's three sender-domain CNAMEs
outputs.tf      the relay endpoint, and what to do next
cloud-init.yaml base image only — deliberately not the Stalwart config

.terraform.lock.hcl   provider checksums. COMMITTED, on purpose
```

The lock file records hashes for **linux_amd64, darwin_arm64 and darwin_amd64**,
because we are two people and will not both be on the same OS. A lock generated
on one platform makes `tofu init` fail on any other with a checksum mismatch
that reads like a supply-chain alarm rather than a missing entry. If a third
platform ever joins:

```sh
tofu providers lock -platform=linux_amd64 -platform=darwin_arm64 -platform=<new>
```

## Running it

```sh
cd infra
cp terraform.tfvars.example terraform.tfvars   # gitignored
$EDITOR terraform.tfvars

export CLOUDFLARE_API_TOKEN=...   # Zone > DNS > Edit
# OpenStack auth comes from ~/.config/openstack/clouds.yaml, not the shell.

tofu init
tofu fmt -check
tofu validate
tofu plan       # read this. every time.
tofu apply
```

`fmt -check` and `validate` pass as committed — they were run against this
configuration, unlike the runbook, which is still written from design rather
than transcribed from a successful run. `validate` checks syntax and internal
references only; it does not talk to Cloudflare or Infomaniak, so it says nothing
about whether the plan is *correct*.

`tofu output next_steps` prints the sequence for what follows.

## What is deliberately not automated

Being straight about this is the point. An agency claiming end-to-end IaC while
quietly clicking through a console is making a claim its own repo disproves —
and the README already commits us to not claiming more than the infrastructure
delivers.

| Not automated | Why |
|---|---|
| **The SMTP2GO sender domain** | No provider exists, so the domain is added in their dashboard and the three CNAMEs it issues are pasted back as a variable |
| **Reverse DNS** | Designate exposes floating-IP PTR at `/reverse/floatingips`, which the OpenStack provider has no resource for. On `ext-net1` it is platform-assigned anyway — and since we never deliver direct-to-MX, it barely matters |
| **Formatting the data volume** | One `mkfs` in phase 2. Doing it in cloud-init means a first-boot script that can reformat the disk holding the mail |
| **The relay's SMTP user** | Same reason. Which means the sending credential never enters state — an accidental improvement on the SES version, which stored one permanently |
| **The R2 state bucket** | Chicken-and-egg: state has to live somewhere before there is state. One `wrangler` command, once |
| **Credentials** | IaC references secrets, never contains them. Doubly so in a public repo |
| **The DKIM private key** | Stalwart generates it on the box. We declare its *publication*; the key material is state |
| **Stalwart's own config** | Versioned separately. Modelling mail-server internals as TF resources gives a bad module and a worse mail server |

There is always a manual root of trust: something holds the credential that
lets the automation run, and it cannot be automated away. That boundary is
documented in [../docs/CREDENTIALS.md](../docs/CREDENTIALS.md) rather than
hidden.

## The host is Infomaniak, and the provider is generic

Infomaniak Public Cloud is OpenStack, so the box is declared with the community
`terraform-provider-openstack` rather than a vendor-specific one. That is a
better position than it sounds: almost none of `server.tf` is Infomaniak-specific,
so the host is the *least* locked-in part of this repo. On Hetzner every
resource was `hcloud_*`.

It was chosen over Hetzner on conduct rather than technology, at roughly
£15–30/year more — the reasoning is in
[../docs/MAIL-SELFHOST.md](../docs/MAIL-SELFHOST.md#why-infomaniak-and-why-that-costs-more).

### The box is also a lab, and that is a risk to this whole design

It is deliberately oversized, and it is where new things get tried. That cuts
directly against the rebuild property below, because experiments mean hand-fixes
and hand-fixes are how IaC starts lying.

Two things hold the line, and neither is enforced by anything but discipline:

- **A separate `lab` security group.** Opening a port for something you are
  testing is a change to a resource mail has nothing to do with, so it can
  never quietly widen the mail server's exposure. If a port seems to belong in
  both, it belongs in neither yet.
- **`/opt/lab`, in containers, not backed up.** Nothing outside it gets touched
  by hand. If something there earns permanence it earns a place in the repo
  first.

The test is simple and worth running for real: can you destroy the instance and
get the mail server back from this repo plus a restore? The day the answer is no,
it stopped being infrastructure and became a pet.

## The relay is SMTP2GO, and it is meant to be swappable

Chosen over SES for one reason: SES starts in a sandbox and leaving it needs a
support request reviewed by a human, with no API and no timebox. That review sat
on the critical path for the whole build. SMTP2GO's free tier is 1,000 messages
a month with no card, which is more than two people's correspondence and enough
to prove the entire pipeline.

The cost is the two dashboard steps above. SES could create its own identity and
read back its own DKIM tokens; this cannot.

Because the relay is expected to change, nothing structural depends on it:

- **Stalwart signs with our own DKIM key** before handoff, published as a TXT
  record we control. SMTP2GO signs too, but through a CNAME delegated to them —
  that key is theirs and leaves when they do. Ours stays.
- **The relay host is one `MtaRoute` object** in the Stalwart plan, not
  something wired through the infrastructure.
- **SPF names nobody**, so there is no include to unpick. See the comment at the
  top of `dns.tf` — this is the part that looks wrong and isn't.

## The cutover is a variable

`enable_apex_mx` is `false` by default. While it is false the apex keeps working
exactly as it does today, and the server is proved against `test.agentsee.work`
— a name nothing depends on, receiving real mail from real senders.

Flipping it to `true` is the one-way door. It is a variable rather than a code
change so that the diff is one unmistakable line, and so the decision is taken
deliberately rather than as a side effect of an unrelated apply.

Before flipping it, all of:

- [ ] Real inbound mail arrives at `test.agentsee.work`
- [ ] Gmail → *Show original* shows `dkim=pass` with **`header.d=agentsee.work`**
- [ ] `dkim_public_key` is set and applied
- [ ] Backups run nightly to R2
- [ ] **A restore has actually restored.** Not "the job exited 0"
- [ ] Cloudflare Email Routing disabled for the apex — it locks its own MX
      records and the API will refuse the write otherwise

## Rebuilding is the normal repair

The server is disposable: config comes from this repo, mail comes from backups.
So `tofu destroy -target=openstack_compute_instance_v2.mail && tofu apply` is a
legitimate first response to a broken box, and it is *expected to work*. If it
doesn't, that's the bug — and better found deliberately than during an outage.

That property is the actual argument for doing this in IaC. Not tidiness:
**it converts the biggest risk in self-hosting — a dead disk holding all your
mail — from an archaeology project into a documented procedure you have already
rehearsed.**

The corollary is a rule, and it is the one that decides whether any of this
holds: **don't hand-fix anything you haven't also changed in the repo.** A 2am
SSH fix is how the IaC starts lying, and IaC that lies is worse than none —
it's confidently wrong.
