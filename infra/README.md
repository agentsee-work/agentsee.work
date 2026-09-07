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
dns.tf          Cloudflare: MX, SPF, DKIM, DMARC, SES verification
server.tf       Hetzner: box, firewall, PTR
ses.tf          AWS: domain identity, DKIM, send-only SMTP user
outputs.tf      credentials to move to the vault, and what to do next
cloud-init.yaml base image only — deliberately not the Stalwart config
```

## Running it

```sh
cd infra
cp terraform.tfvars.example terraform.tfvars   # gitignored
$EDITOR terraform.tfvars

export CLOUDFLARE_API_TOKEN=...   # Zone > DNS > Edit
export HCLOUD_TOKEN=...

tofu init
tofu plan       # read this. every time.
tofu apply
```

`tofu output next_steps` prints the sequence for what follows.

## What is deliberately not automated

Being straight about this is the point. An agency claiming end-to-end IaC while
quietly clicking through a console is making a claim its own repo disproves —
and the README already commits us to not claiming more than the infrastructure
delivers.

| Not automated | Why |
|---|---|
| **SES production access** | A human reviews a support request. No API exists. Start it day one; it blocks nothing else |
| **The R2 state bucket** | Chicken-and-egg: state has to live somewhere before there is state. One `wrangler` command, once |
| **Credentials** | IaC references secrets, never contains them. Doubly so in a public repo |
| **The DKIM private key** | Stalwart generates it on the box. We declare its *publication*; the key material is state |
| **Stalwart's own config** | Versioned separately. Modelling mail-server internals as TF resources gives a bad module and a worse mail server |

There is always a manual root of trust: something holds the credential that
lets the automation run, and it cannot be automated away. That boundary is
documented in [../docs/CREDENTIALS.md](../docs/CREDENTIALS.md) rather than
hidden.

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
So `tofu destroy -target=hcloud_server.mail && tofu apply` plus a restore is a
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
