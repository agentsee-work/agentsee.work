locals {
  mail_fqdn = "${var.mail_hostname}.${var.domain}"
  test_fqdn = "${var.test_subdomain}.${var.domain}"

  # This record authorises nothing, and that is correct.
  #
  # It looks like a mistake, so: SMTP2GO does not use an SPF include. Since 2019
  # they use VERP, setting the return-path to a subdomain of ours that is CNAMEd
  # to them (see relay.tf). SPF is therefore evaluated against THAT subdomain
  # and their record, not against this one. Their own docs say "you do not need
  # to update your domain's existing SPF record".
  #
  # DMARC still passes: relaxed alignment accepts a return-path on a subdomain
  # of the From domain, which is exactly what the CNAME produces.
  #
  # So nothing sends with an apex envelope-from, and nothing is authorised to.
  # Softfail rather than -all only because we have not proven that yet; tighten
  # it in the same change that sets dmarc_policy = "reject".
  spf = "v=spf1 ~all"
}

# ─── The mail host ───────────────────────────────────────────────────────────
# proxied = false is load-bearing. Cloudflare proxies HTTP; it does not proxy
# SMTP. Orange-clouding this record silently breaks inbound mail, and the
# symptom is "mail just stops" with a green dashboard. Same family of trap as
# the Email Address Obfuscation one in docs/RUNBOOK.md.
resource "cloudflare_dns_record" "mail_a" {
  zone_id = var.cloudflare_zone_id
  name    = local.mail_fqdn
  type    = "A"
  content = local.mail_ipv4
  ttl     = 300
  proxied = false
  comment = "Mail server. MUST stay DNS-only — Cloudflare cannot proxy SMTP."
}

# Free on ext-net1, so there is no reason not to. Some senders are v6-first.
resource "cloudflare_dns_record" "mail_aaaa" {
  zone_id = var.cloudflare_zone_id
  name    = local.mail_fqdn
  type    = "AAAA"
  content = local.mail_ipv6
  ttl     = 300
  proxied = false
  comment = "Mail server. MUST stay DNS-only — Cloudflare cannot proxy SMTP."
}

# ─── Test subdomain: real mail, nothing depending on it ──────────────────────
resource "cloudflare_dns_record" "test_mx" {
  zone_id  = var.cloudflare_zone_id
  name     = local.test_fqdn
  type     = "MX"
  content  = local.mail_fqdn
  priority = 10
  ttl      = 300
  comment  = "Proving ground. Apex keeps working while this is exercised."
}

resource "cloudflare_dns_record" "test_spf" {
  zone_id = var.cloudflare_zone_id
  name    = local.test_fqdn
  type    = "TXT"
  content = local.spf
  ttl     = 300
}

# ─── Apex MX: the cutover ────────────────────────────────────────────────────
# Gated on enable_apex_mx. While false, the apex stays on whatever serves it
# today (Cloudflare Email Routing) and this resource does not exist.
#
# Email Routing locks its own MX records. Disable it in the dashboard —
# Email > Email Routing > Settings > Disable — before the first apply with this
# on, or the API refuses the write.
resource "cloudflare_dns_record" "apex_mx" {
  count = var.enable_apex_mx ? 1 : 0

  zone_id  = var.cloudflare_zone_id
  name     = var.domain
  type     = "MX"
  content  = local.mail_fqdn
  priority = 10
  ttl      = 300
  comment  = "Managed by OpenTofu — infra/dns.tf"
}

resource "cloudflare_dns_record" "apex_spf" {
  count = var.enable_apex_mx ? 1 : 0

  zone_id = var.cloudflare_zone_id
  name    = var.domain
  type    = "TXT"
  content = local.spf
  ttl     = 300
}

# ─── Client auto-configuration ───────────────────────────────────────────────
# Stalwart answers Mozilla autoconfig, Microsoft autodiscover and Apple
# mobileconfig itself; it only needs these names to reach it. Without them a
# client guesses, and Apple Mail's guess is port 25 — which is the MX listener,
# offers no authentication, and fails in a way that looks like a password
# problem. Abrar hit exactly that on 14 September 2026.
#
# CNAMEs rather than A/AAAA so the server's address lives in one place.
#
# ⚠ These must also be added to the mail.agentsee.work domain's Additional
# Hostnames in Stalwart, or the certificate will not cover them and every client
# will refuse the config it fetches. DNS first, then the SANs — ACME validates
# by connecting to the name.
resource "cloudflare_dns_record" "autoconfig" {
  for_each = toset(["autoconfig", "autodiscover"])

  zone_id = var.cloudflare_zone_id
  name    = "${each.key}.${var.domain}"
  type    = "CNAME"
  content = local.mail_fqdn
  ttl     = 300
  proxied = false
  comment = "Mail client auto-configuration. Served by Stalwart."
}

# ─── MTA-STS, TLS-RPT and modern autoconfig ──────────────────────────────────
# All three are served by Stalwart over HTTPS on their own hostnames, so they
# need the same treatment autoconfig did: a CNAME here, and the name added to
# the certificate's Subject Alternative Names in Stalwart. A name that resolves
# but has no certificate is worse than one that does not resolve — senders that
# honour MTA-STS will refuse to deliver rather than fall back.
resource "cloudflare_dns_record" "policy_hosts" {
  for_each = toset(["mta-sts", "ua-auto-config"])

  zone_id = var.cloudflare_zone_id
  name    = "${each.key}.${var.domain}"
  type    = "CNAME"
  content = local.mail_fqdn
  ttl     = 300
  proxied = false
  comment = "Policy host served by Stalwart over HTTPS."
}

# ⚠ The id must change whenever the POLICY changes, or senders keep serving the
# cached one. Stalwart generates it; take the current value from
# `stalwart-cli get Domain <apex-id>` rather than inventing one.
resource "cloudflare_dns_record" "mta_sts" {
  count = var.mta_sts_id == "" ? 0 : 1

  zone_id = var.cloudflare_zone_id
  name    = "_mta-sts.${var.domain}"
  type    = "TXT"
  content = "v=STSv1; id=${var.mta_sts_id}"
  ttl     = 300
  comment = "MTA-STS policy version. Bump when the policy changes."
}

# Reports from other servers about TLS failures reaching us. The only way we
# would hear about a problem that is invisible from this side.
resource "cloudflare_dns_record" "tls_rpt" {
  zone_id = var.cloudflare_zone_id
  name    = "_smtp._tls.${var.domain}"
  type    = "TXT"
  content = "v=TLSRPTv1; rua=mailto:dmarc@${var.domain}"
  ttl     = 300
  comment = "TLS-RPT. Reports land with the DMARC ones."
}

# PACC — the standardised successor to Mozilla autoconfig and Microsoft
# autodiscover, covering mail, calendar and contacts in one document. Clients
# that speak it need no per-vendor hostname; the hash pins the document.
resource "cloudflare_dns_record" "ua_auto_config" {
  count = var.ua_auto_config_hash == "" ? 0 : 1

  zone_id = var.cloudflare_zone_id
  name    = "_ua-auto-config.${var.domain}"
  type    = "TXT"
  content = "v=UAAC1; a=sha256; d=${var.ua_auto_config_hash}"
  ttl     = 300
  comment = "PACC discovery. Hash pins the config document."
}

# ─── CAA: who may issue certificates for this domain ─────────────────────────
# Without this, any CA in the world can be persuaded to issue for agentsee.work.
# With it, only Let's Encrypt can, and only for our ACME account.
#
# ⚠ Adding a second ACME provider or moving CA means changing this FIRST, or
# issuance fails with an error that does not mention CAA.
resource "cloudflare_dns_record" "caa_issue" {
  zone_id = var.cloudflare_zone_id
  name    = var.domain
  type    = "CAA"
  ttl     = 300
  comment = "Only Let's Encrypt, only our account, may issue."

  data = {
    flags = 0
    tag   = "issue"
    value = "letsencrypt.org;accounturi=https://acme-v02.api.letsencrypt.org/acme/acct/${var.acme_account_id}"
  }
}

resource "cloudflare_dns_record" "caa_iodef" {
  zone_id = var.cloudflare_zone_id
  name    = var.domain
  type    = "CAA"
  ttl     = 300
  comment = "Where to report attempted mis-issuance."

  data = {
    flags = 0
    tag   = "iodef"
    value = "mailto:dmarc@${var.domain}"
  }
}

# ─── DMARC ───────────────────────────────────────────────────────────────────
resource "cloudflare_dns_record" "dmarc" {
  zone_id = var.cloudflare_zone_id
  name    = "_dmarc.${var.domain}"
  type    = "TXT"
  content = "v=DMARC1; p=${var.dmarc_policy}; sp=${var.dmarc_policy}; rua=mailto:dmarc@${var.domain}; fo=1"
  ttl     = 300
  comment = "Nothing sends as @agentsee.work yet. Relax to p=none BEFORE any send-as."
}

# ─── DKIM: ours, signed by Stalwart before handoff to the relay ──────────────
# One record per selector. Absent until the keys exist on the server, and their
# absence is invisible — the relay's own aligned signature keeps DMARC passing,
# so nothing breaks until the day the relay changes.
resource "cloudflare_dns_record" "dkim" {
  for_each = var.dkim_records

  zone_id = var.cloudflare_zone_id
  name    = "${each.key}._domainkey.${var.domain}"
  type    = "TXT"
  content = each.value
  ttl     = 300
  comment = "Stalwart DKIM. Private half lives on the box and in backups."
}
