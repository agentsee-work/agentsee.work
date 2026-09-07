output "mail_server_ipv4" {
  description = "Public IPv4. This is what the A record points at."
  value       = local.mail_ipv4
}

output "mail_server_ipv6" {
  description = "Public IPv6, free on ext-net1."
  value       = local.mail_ipv6
}

output "mail_hostname" {
  description = "MX target. Must remain DNS-only in Cloudflare."
  value       = local.mail_fqdn
}

output "test_address_hint" {
  description = "Send real mail here to prove the box before the apex moves."
  value       = "anything@${local.test_fqdn}"
}

output "relay_smtp_endpoint" {
  description = "Relay host for Stalwart: Settings > SMTP > Outbound > Relay Hosts."
  value       = "mail.smtp2go.com"
}

# There is no relay credential output. SMTP2GO's SMTP users are created in
# their dashboard (Sending > SMTP Users), not by this configuration, so the
# username and password never enter state at all.
#
# That is an accident of there being no provider, and it is a good accident:
# the SES version of this file put a sending credential in state permanently.

output "smtp2go_records_present" {
  description = "False means outbound will be signed as smtp2go.com, not as us."
  value       = length(var.smtp2go_cname_records) > 0
}

output "next_steps" {
  value = <<-EOT

    ── after this apply ──────────────────────────────────────────────

    1. Verify the sender domain at SMTP2GO if you have not: Sending >
       Verified Senders > Sender Domains > Add ${var.domain}. Put the
       three CNAMEs it returns into smtp2go_cname_records and re-apply.

       Skipping this does not break sending. It makes SMTP2GO sign as
       itself, so mail arrives "via smtp2go.com" and unaligned — fine
       today at p=none, discarded silently at p=reject.

    2. Create an SMTP user (Sending > SMTP Users). Those credentials go
       straight to the Engineering vault; they are not managed here and
       never enter state.

    2b. Format and mount the data volume before installing anything —
       MAIL-BUILD-RUNBOOK phase 2. It is deliberately not done by
       cloud-init: a first-boot script that can reformat the volume
       holding the mail is a bad thing to get subtly wrong.

    3. Install Stalwart, then read its generated DKIM public key and set
       dkim_public_key in terraform.tfvars. Re-apply. Until you do, the
       record is absent and nothing warns you.

    4. Configure the relay — Settings > SMTP > Outbound > Relay Hosts:
         host  mail.smtp2go.com
         port  8465, implicit TLS — NOT 465, which hosts commonly
               block outbound
         user  from step 2
       Then DISABLE DANE and MTA-STS on that route. Both assert things
       about direct-to-MX delivery that are false with a smarthost in the
       path, and the resulting failures present as TLS bugs.

       Turn link tracking OFF. It rewrites URLs in the body, and the body
       was signed by Stalwart before handoff — see MAIL-BUILD-RUNBOOK
       phase 4.

    5. Send real mail to ${"anything@${local.test_fqdn}"} and confirm it
       lands. Reply, then check in Gmail via "Show original":
         spf=pass   dkim=pass   header.d=${var.domain}   dmarc=pass
       header.d must be OUR domain. Anything else means p=reject would
       reject our own mail.

    6. Backups running AND a restore actually restored. Before cutover,
       not after — a first restore test after real mail exists is not a
       test.

    7. Only then: enable_apex_mx = true. Disable Cloudflare Email Routing
       first or the API will refuse to touch the apex MX.

    ──────────────────────────────────────────────────────────────────
  EOT
}
