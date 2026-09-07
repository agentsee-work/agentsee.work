# Renting the reputation. This is the half of self-hosted mail that cannot be
# bootstrapped — a new IP has no sending history, and "no history" is
# indistinguishable from "a spammer rented this an hour ago".
#
# ─── Why there is so little here ─────────────────────────────────────────────
#
# SMTP2GO has no OpenTofu provider, so the sender domain is registered by hand
# in their dashboard and the three records it issues are pasted back in as a
# variable. That is a genuine step backwards from the SES version of this file,
# which could create the identity and read its own DKIM tokens. It is the
# honest trade for a relay that needs no credit card and no human review.
#
# The dashboard step is therefore load-bearing and undocumented by the code.
# It lives in docs/MAIL-BUILD-RUNBOOK.md phase 0.

# ─── The three records SMTP2GO issues ────────────────────────────────────────
# Sending > Verified Senders > Sender Domains > Add. It returns three CNAMEs:
#
#   1. return-path   the VERP bounce subdomain. THIS is what makes SPF pass.
#   2. DKIM          delegated to their DNS, so they hold that private key.
#   3. link tracking serves https on a subdomain of ours, with their cert.
#
# The hostnames are partly ours to choose and the targets are generated per
# account, so neither can be hardcoded. A map rather than three resources
# because their shape is theirs to change, not ours to predict.
#
# ⚠ proxied = false on all three. The tracking record terminates TLS at
# SMTP2GO using a certificate they issue for our subdomain; orange-clouding it
# puts Cloudflare in that path and breaks the cert. The other two are not HTTP
# at all. Same trap as mail_a in dns.tf, three more times.
resource "cloudflare_dns_record" "smtp2go" {
  for_each = var.smtp2go_cname_records

  zone_id = var.cloudflare_zone_id
  name    = "${each.key}.${var.domain}"
  type    = "CNAME"
  content = each.value
  ttl     = 300
  proxied = false
  comment = "SMTP2GO sender domain verification. DNS-only, deliberately."
}
