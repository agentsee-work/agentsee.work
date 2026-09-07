output "mail_server_ipv4" {
  description = "Public IP. Check the PTR resolves before cutover."
  value       = hcloud_server.mail.ipv4_address
}

output "mail_server_ipv6" {
  value = hcloud_server.mail.ipv6_address
}

output "mail_hostname" {
  description = "MX target. Must remain DNS-only in Cloudflare."
  value       = local.mail_fqdn
}

output "test_address_hint" {
  description = "Send real mail here to prove the box before the apex moves."
  value       = "anything@${local.test_fqdn}"
}

output "ses_smtp_endpoint" {
  description = "Relay host for Stalwart: Settings > SMTP > Outbound > Relay Hosts."
  value       = "email-smtp.${var.aws_region}.amazonaws.com"
}

output "ses_smtp_username" {
  value = aws_iam_access_key.smtp.id
}

# The SMTP password is derived from the IAM secret key — it is NOT the secret
# key itself, and pasting the wrong one produces an authentication failure that
# looks like a wrong password because it is one.
#
#   tofu output -raw ses_smtp_password
#
# Put it straight in the vault (docs/CREDENTIALS.md, `infra` collection). It is
# already in state, which is why state lives in R2 and never in this repo.
output "ses_smtp_password" {
  description = "SES SMTP password. Sensitive — goes to the vault, not to a file."
  value       = aws_iam_access_key.smtp.ses_smtp_password_v4
  sensitive   = true
}

output "next_steps" {
  value = <<-EOT

    ── after this apply ──────────────────────────────────────────────

    1. Request SES production access if you have not. Sandbox will only
       deliver to verified addresses, and the review is done by a human.

    2. Install Stalwart, then read its generated DKIM public key and set
       dkim_public_key in terraform.tfvars. Re-apply. Until you do, the
       record is absent and nothing warns you.

    3. Configure the relay — Settings > SMTP > Outbound > Relay Hosts:
         host  ${"email-smtp.${var.aws_region}.amazonaws.com"}
         port  465, implicit TLS
         user  see ses_smtp_username / ses_smtp_password
       Then DISABLE DANE and MTA-STS on that route. Both assert things
       about direct-to-MX delivery that are false with a smarthost in the
       path, and the resulting failures present as TLS bugs.

    4. Send real mail to ${"anything@${local.test_fqdn}"} and confirm it
       lands. Reply, then check in Gmail via "Show original":
         spf=pass   dkim=pass   header.d=${var.domain}   dmarc=pass
       header.d must be OUR domain. Anything else means p=reject would
       reject our own mail.

    5. Backups running AND a restore actually restored. Before cutover,
       not after — a first restore test after real mail exists is not a
       test.

    6. Only then: enable_apex_mx = true. Disable Cloudflare Email Routing
       first or the API will refuse to touch the apex MX.

    ──────────────────────────────────────────────────────────────────
  EOT
}
