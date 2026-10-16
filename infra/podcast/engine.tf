# The engine's door. The engine itself (engine/, a Python process in a
# container on the box infra/server.tf provisions) listens on nothing the
# internet can reach; cloudflared beside it holds an outbound connection to
# Cloudflare, and Cloudflare presents it as ${engine_subdomain}.${domain}
# behind Access. No inbound port, no TLS on the box, no password of ours:
# a login is a one-time code to one of the addresses in engine_people, then
# a passkey (Cloudflare's independent MFA: a platform authenticator or a
# security key). A passkey is enrolled in the App Launcher, the account's
# own page at <team>.cloudflareaccess.com, which is why the launcher is
# declared below too: it is off by default, and it is the one door that
# must open without MFA so that there is somewhere to enrol. An
# administrator removes a lost passkey from Zero Trust > Team & Resources >
# Users; there is no "forgot".
#
# Two things are done once in the dashboard and named here, not declared:
#
#   - The Zero Trust organisation: its team name (zero_trust_team), chosen
#     when Zero Trust is first opened, is the login page's subdomain and the
#     audience cloudflared checks. Account-wide, not the podcast's.
#   - Independent MFA, switched on at the organisation: Zero Trust > Access
#     controls > Access settings, the MFA section, allowing Biometrics and
#     Security key. The engine application below then requires it, and the
#     apply fails until it is on.
#
# The DNS record for the hostname is in ../dns.tf, on the DNS token, with
# the tunnel id from `tofu output engine_tunnel_id` here: this token cannot
# write DNS and must not learn to (the reasoning is in op.env).
#
# The token grew for this file: Account > Cloudflare Tunnel > Edit, Account >
# Access: Apps and Policies > Edit, Account > Access: Organizations,
# Identity Providers, and Groups > Edit.

locals {
  engine_fqdn = "${var.engine_subdomain}.${var.domain}"
}

# ─── Who may come in ─────────────────────────────────────────────────────────
# One-time PIN: Access emails a code to an address a policy allows. No
# third-party identity provider, nothing to federate, nothing to pay.
resource "cloudflare_zero_trust_access_identity_provider" "otp" {
  account_id = var.cloudflare_account_id
  name       = "One-time code by email"
  type       = "onetimepin"
  config     = {}
}

# The addresses come from the vault as TF_VAR_engine_people (op.env), not
# from tfvars: tfvars is committed and this repository is public.
resource "cloudflare_zero_trust_access_policy" "engine_people" {
  account_id = var.cloudflare_account_id
  name       = "Podcast engine: the people who run it"
  decision   = "allow"
  include    = [for e in var.engine_people : { email = { email = e } }]
}

resource "cloudflare_zero_trust_access_application" "engine" {
  account_id                 = var.cloudflare_account_id
  name                       = "Podcast engine"
  type                       = "self_hosted"
  domain                     = local.engine_fqdn
  session_duration           = "24h"
  allowed_idps               = [cloudflare_zero_trust_access_identity_provider.otp.id]
  auto_redirect_to_identity  = true
  app_launcher_visible       = false
  http_only_cookie_attribute = true
  same_site_cookie_attribute = "lax"

  # The passkey, after the code. Every 24 h, like the session.
  mfa_config = {
    allowed_authenticators = ["biometrics", "security_key"]
    session_duration       = "24h"
  }

  policies = [{
    id         = cloudflare_zero_trust_access_policy.engine_people.id
    precedence = 1
  }]
}

# The App Launcher: the same two people, the same login, no passkey asked,
# because this is where the passkey is made. Its hostname is the team
# domain and is not ours to set, so there is no `domain` here. Nothing is
# listed in it (the engine has app_launcher_visible = false); it exists for
# Account > MFA devices.
resource "cloudflare_zero_trust_access_application" "launcher" {
  account_id                = var.cloudflare_account_id
  name                      = "App Launcher"
  type                      = "app_launcher"
  session_duration          = "24h"
  allowed_idps              = [cloudflare_zero_trust_access_identity_provider.otp.id]
  auto_redirect_to_identity = true

  policies = [{
    id         = cloudflare_zero_trust_access_policy.engine_people.id
    precedence = 1
  }]
}

# ─── The tunnel ──────────────────────────────────────────────────────────────
# Remotely configured: the ingress below is the whole of cloudflared's
# configuration, and the box runs `cloudflared tunnel run` with a token
# (engine/home/docker-compose.yml) and no config file.
resource "cloudflare_zero_trust_tunnel_cloudflared" "engine" {
  account_id = var.cloudflare_account_id
  name       = "podcast-engine"
  config_src = "cloudflare"
}

#
# A plan warns that this resource "cannot be destroyed from Terraform". The
# configuration is not a thing apart from the tunnel: it goes when the
# tunnel does, and a destroy here only forgets it. Expected; nothing to do.
resource "cloudflare_zero_trust_tunnel_cloudflared_config" "engine" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.engine.id

  config = {
    ingress = [
      {
        hostname = local.engine_fqdn
        # The engine container, by its compose service name.
        service = "http://engine:8877"
        origin_request = {
          # cloudflared itself checks the Access JWT on every request, so a
          # request that did not pass Access never reaches the engine, and
          # the identity header the engine trusts is Cloudflare's, not the
          # caller's.
          access = {
            required  = true
            team_name = var.zero_trust_team
            aud_tag   = [cloudflare_zero_trust_access_application.engine.aud]
          }
        }
      },
      # Everything else the tunnel is asked for: nothing.
      { service = "http_status:404" },
    ]
  }
}

data "cloudflare_zero_trust_tunnel_cloudflared_token" "engine" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.engine.id
}
