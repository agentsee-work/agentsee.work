# Renting the reputation. This is the half of self-hosted mail that cannot be
# bootstrapped — a new IP has no sending history, and "no history" is
# indistinguishable from "a spammer rented this an hour ago".
#
# ⚠ SES starts in a SANDBOX: capped volume, and it will only deliver to
# addresses you have verified. Production access is a support request reviewed
# by a human — no API, no exception. It is the long pole in the whole build.
# Request it on day one; nothing else blocks on it.

resource "aws_ses_domain_identity" "main" {
  domain = var.domain
}

resource "aws_ses_domain_dkim" "main" {
  domain = aws_ses_domain_identity.main.domain
}

# Verifying the apex also authorises sending from its subdomains, so the test
# subdomain can prove end-to-end alignment on one identity.
resource "aws_ses_domain_identity_verification" "main" {
  domain     = aws_ses_domain_identity.main.id
  depends_on = [cloudflare_dns_record.ses_verification]
}

# ─── SMTP credentials for Stalwart's relay route ─────────────────────────────
# An IAM user that can do exactly one thing. If these credentials leak, the
# blast radius is "someone sends mail as us until we rotate" — bad, bounded,
# and detectable in the SES console. Compare with what an over-broad AWS key
# would cost.
resource "aws_iam_user" "smtp" {
  name = "agentsee-mail-smtp"
  path = "/service/"
}

resource "aws_iam_user_policy" "smtp" {
  name = "ses-send-only"
  user = aws_iam_user.smtp.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ses:SendRawEmail"]
      Resource = "*"
      Condition = {
        StringEquals = {
          "ses:FromAddress" = "*@${var.domain}"
        }
      }
    }]
  })
}

resource "aws_iam_access_key" "smtp" {
  user = aws_iam_user.smtp.name
}
