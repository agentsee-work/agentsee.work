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
