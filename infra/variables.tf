variable "domain" {
  description = "Apex domain. Mail is received for this."
  type        = string
  default     = "agentsee.work"
}

variable "cloudflare_zone_id" {
  description = "Zone ID for the domain. An identifier, not a secret — it is in docs/RUNBOOK.md."
  type        = string
  default     = "d2ab9ee564f8c164c1c32f57414ce749"
}

variable "mail_hostname" {
  description = "Hostname of the mail server itself. Becomes the MX target and the PTR."
  type        = string
  default     = "mail"
}

variable "test_subdomain" {
  description = <<-EOT
    Subdomain used to prove the server before the apex moves. Its MX points at
    the box from the first apply, so real mail can be delivered to a name that
    nothing depends on.
  EOT
  type        = string
  default     = "test"
}

# ─── The safety catch ────────────────────────────────────────────────────────
variable "enable_apex_mx" {
  description = <<-EOT
    Move the apex MX to our own server. THIS IS THE CUTOVER.

    Leave false until every item in docs/MAIL-SELFHOST.md step 3-5 passes on the
    test subdomain: real inbound mail, dkim=pass with header.d=<domain>, and a
    restore test that actually restored. Flipping this is the one-way door, and
    it is a variable rather than a code change so the diff is unmistakable.
  EOT
  type        = bool
  default     = false
}

variable "dmarc_policy" {
  description = <<-EOT
    DMARC policy. Runs `none` through the migration — under `reject` our own
    unaligned mail is discarded silently by recipients. Move to `reject` only
    once aggregate reports show alignment.
  EOT
  type        = string
  default     = "none"

  validation {
    condition     = contains(["none", "quarantine", "reject"], var.dmarc_policy)
    error_message = "dmarc_policy must be none, quarantine or reject."
  }
}

variable "dkim_selector" {
  description = "Selector for the DKIM key Stalwart signs with."
  type        = string
  default     = "stalwart"
}

variable "dkim_public_key" {
  description = <<-EOT
    Public half of Stalwart's DKIM key, as the full TXT value
    ("v=DKIM1; k=rsa; p=..."). Generated on the server, so it does not exist on
    the first apply — leave empty, then fill it in and re-apply.

    The PRIVATE half never comes near this repo. It lives on the box, is
    included in the backup, and its existence is recorded in the vault.

    Deliberately ours rather than SES-managed: a key that lives with the relay
    has to be rebuilt if the relay ever changes, and DKIM is what stands between
    p=reject and our own mail vanishing.
  EOT
  type        = string
  default     = ""
}

# ─── Host ────────────────────────────────────────────────────────────────────
variable "server_type" {
  description = "Hetzner type. Stalwart needs 512 MB; cx22 is 2 vCPU / 4 GB."
  type        = string
  default     = "cx22"
}

variable "location" {
  description = "Hetzner location. fsn1/nbg1/hel1 are EU."
  type        = string
  default     = "fsn1"
}

variable "ssh_public_key" {
  description = "Public key for admin access. Public by nature — safe in tfvars."
  type        = string
}

variable "ssh_allowed_ips" {
  description = <<-EOT
    CIDRs permitted to reach SSH. Defaults to the whole internet because home IPs
    are dynamic; narrow it if you have a static address or a bastion. Key-only
    auth is enforced in cloud-init regardless.
  EOT
  type        = list(string)
  default     = ["0.0.0.0/0", "::/0"]
}

variable "aws_region" {
  description = "SES region. eu-west-1 keeps mail in the EU."
  type        = string
  default     = "eu-west-1"
}
