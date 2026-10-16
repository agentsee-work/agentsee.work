# The engine's home

The engine runs on the box `infra/` provisions, beside the mail server, as
two containers: the engine, and `cloudflared` holding the tunnel that is the
only way in. Cloudflare Access is the lock: a one-time code to one of the
two addresses, then a passkey. Nothing listens on the box; nothing is in the
security groups; there is no password of ours anywhere.

```
Dockerfile            Python 3.14 and the 1Password CLI. The code is mounted, not baked
docker-compose.yml    engine + cloudflared on a private network; no published port
engine.env.example    /etc/agentsee/engine.env: the service-account token, 0600, not in git
tunnel.env.example    /etc/agentsee/tunnel.env: the tunnel token, 0600, not in git
```

What is declared where:

| | File | Token |
|---|---|---|
| Tunnel, Access application, policy, one-time-code login | `infra/podcast/engine.tf` | the podcast's |
| The `engine.agentsee.work` CNAME to the tunnel | `infra/dns.tf` | the DNS one |
| The containers | this directory | none |

The box's own rule applies (`infra/cloud-init.yaml`, `/root/README`):
nothing here is hand-fixed. The engine is a permanent service, so it lives
in `/opt/agentsee`, not `/opt/lab`, and it earns that by being rebuildable
from this repo plus the nightly ledger copy in the archive bucket.

## Once, in dashboards

1. **Zero Trust organisation.** Zero Trust > Settings > Custom Pages: the
   team name. It is the login page (`<team>.cloudflareaccess.com`) and what
   cloudflared checks tokens against. Put it in
   `infra/podcast/terraform.tfvars` as `zero_trust_team` (an identifier;
   committed, like the other tfvars).
2. **Independent MFA**, on at the organisation: Zero Trust > Access
   controls > Access settings, the MFA section: allow Biometrics and
   Security key, set the authentication duration, Save. The application in
   `engine.tf` requires it; the apply fails until this is on. The dashboard
   asks for the account password before a change like this, and an account
   that signs in with GitHub has none until one is set: Forgot Password on
   the login page with the GitHub primary address sets one (Cloudflare's
   documented route; GitHub sign-in keeps working). File it in IT, and turn
   on 2FA for the account so the new password is not a weaker door than
   GitHub's.
3. **The podcast token** (`opentofu-podcast`) grows three account
   permissions: Cloudflare Tunnel > Edit; Access: Apps and Policies > Edit;
   Access: Organizations, Identity Providers, and Groups > Edit. Save the
   edit (Continue to summary > Update token).
4. **1Password service account** for the box: Developer > Service Accounts,
   read and write on **Engineering** (write because the X adapter puts the
   rotated refresh token back; see `engine/publish/x.py`). File the token as
   `Engine service account` in **Security**: it is a standing credential to
   the whole vault, and losing it means minting another, not recovering it.
5. **Engine people**: an API Credential item in Engineering whose
   `credential` is the list of addresses Access lets in, in HCL syntax:
   `["james@…", "abrar@…"]`. `op.env` hands it to tofu as a variable so the
   addresses never enter this public repository.

## The apply, in two roots

```sh
cd infra/podcast
op run --env-file=op.env -- tofu plan      # read it: an IdP, a policy, two apps, a tunnel, its config
op run --env-file=op.env -- tofu apply
tofu output engine_tunnel_id               # → infra/terraform.tfvars, engine_tunnel_id = "…"
op run --env-file=op.env -- tofu output -raw engine_tunnel_token   # → the vault, "Engine tunnel token"

cd ..
op run --env-file=op.env -- tofu plan      # one CNAME, proxied
op run --env-file=op.env -- tofu apply
```

The tunnel token identifies this box's `cloudflared` to Cloudflare. It is
not in the state's outputs in clear (sensitive), and it goes to the box by
hand, like the relay password did.

## On the box

The repository is already at `/opt/agentsee/repo` (the mail runbook put it
there). Then, as root:

```sh
cd /opt/agentsee/repo && git pull
ln -s /opt/agentsee/repo/engine/home /opt/agentsee/engine

install -d -m 0755 /etc/agentsee
install -m 0600 /dev/null /etc/agentsee/engine.env
install -m 0600 /dev/null /etc/agentsee/tunnel.env
$EDITOR /etc/agentsee/engine.env       # OP_SERVICE_ACCOUNT_TOKEN=…   (engine.env.example)
$EDITOR /etc/agentsee/tunnel.env       # TUNNEL_TOKEN=…               (tunnel.env.example)

install -d -m 0750 -o 2001 -g 2001 /var/lib/agentsee-engine

cd /opt/agentsee/engine
docker compose build
docker compose up -d
docker compose logs -f --since 2m
```

The engine's first lines say which ledger it opened and that the worker is
on; `cloudflared`'s say the tunnel registered. A fresh box has no ledger:
the engine creates an empty one, and the posts come from the archive
(below). A rebuilt box restores it first.

Then the passkey, once per person, in a browser:
`https://<team>.cloudflareaccess.com/` (the App Launcher, which the apply
turned on for the same two addresses). Enter the address, enter the code
it emails, open Account > MFA devices > Add an MFA device, choose
Biometrics (Touch ID, Face ID or Windows Hello: the browser's own prompt)
or Security key, and follow it. A lost one is deleted by an administrator
under Team & Resources > Users; there is no "forgot".

Then `https://engine.agentsee.work/`: the address, the code, the passkey.
The screens show who is logged in in the top right, from the header
Cloudflare attaches, which `cloudflared` has already verified.

## The ledger, and the files

The ledger is `/var/lib/agentsee-engine/ledger.sqlite`. The worker copies it
to the archive bucket every night at the hour in `engine/config.json`
(`snapshot_at`), as `engine/ledger-<day>.sqlite` and
`engine/ledger-latest.sqlite`; Health shows the last one. By hand:

```sh
docker compose exec engine op run --env-file=engine/engine.env -- python3 engine/engine.py snapshot
```

Episodes reach the box through the archive too: `stage` puts
`release.json` and `staged.json` in `episodes/<slug>/out/` beside the
files, and an `ingest` on a machine without `build/` reads them from there.
Deliverables are fetched on demand when an adapter needs one, checked
against the hash the ledger holds, into `/var/lib/agentsee-engine/episodes/`
(a cache; safe to delete).

```sh
docker compose exec engine op run --env-file=engine/engine.env -- python3 engine/engine.py ingest ep2
docker compose exec engine op run --env-file=engine/engine.env -- python3 engine/engine.py plan ep2
```

## Rebuilding

Destroy the instance and apply (`infra/README.md`), redo "On the box",
and before `up`:

```sh
docker compose run --rm engine op run --env-file=engine/engine.env -- python3 engine/engine.py restore
```

which fetches `engine/ledger-latest.sqlite` into place (a dated copy with
`--key engine/ledger-YYYY-MM-DD.sqlite`; `--force` over a ledger that
exists). The tunnel token and the service-account token come from the
vault again. Nothing else is state.

## Deploying a change

```sh
cd /opt/agentsee/repo && git pull
cd /opt/agentsee/engine && docker compose restart engine
```

The code is mounted read-only; the image changes only when the interpreter
or the op CLI should (`docker compose build`).

## Two engines at once

Do not run a second engine against the same vault while this one is up: the
X refresh token is single use, and two refreshers retire each other's
tokens (`engine/publish/x.py`). A workstation engine is for a look at the
screens (`serve --no-worker`), not for publishing, once the box is live.
