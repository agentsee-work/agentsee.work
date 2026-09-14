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

variable "dkim_records" {
  description = <<-EOT
    Stalwart's DKIM public keys, as { "<selector>" = "<full TXT value>" }.

    A map rather than a single key because Stalwart signs with BOTH Ed25519 and
    RSA by default, and rotates selectors on a schedule — the selector contains
    the date it was generated (v1-rsa-20260911). One variable could never hold
    that, and the rotation would quietly break alignment.

    Until a key is published, its signature arrives as
    `dkim=permerror (no key for signature)` at the receiver. Deliveries still
    pass DMARC while the relay's own aligned signature holds, which is exactly
    why this is easy to leave broken: nothing fails until the relay changes.

    The PRIVATE halves never come near this repo. They live on the box, are
    included in the backup, and their existence is recorded in the vault.

    Ours as well as the relay's: SMTP2GO signs via a CNAME delegated to them, so
    that key is theirs and leaves when they do. Two aligned signatures is legal
    and DMARC passes if either validates.
  EOT
  type        = map(string)
  default     = {}
}

# ─── Host ────────────────────────────────────────────────────────────────────
variable "public_network" {
  description = <<-EOT
    Network the instance attaches to. ext-net1 is Infomaniak's dual-stack
    public network: a charged public IPv4 and a free public IPv6.

    See the comment at the top of server.tf for why this rather than a private
    network and a floating IP.
  EOT
  type        = string
  default     = "ext-net1"
}

variable "flavor" {
  description = <<-EOT
    Instance size. Named a{vcpu}-ram{mb}-disk{gb}-{perf}; -perf1 is 500 IOPS
    and 200 MB/s.

    Stalwart itself needs 512 MB, so this is oversized for mail on purpose —
    the box is also the place new things get tried, and an experiment competing
    with the mail server for memory is a bad way to find out it was too small.
  EOT
  type        = string
  default     = "a2-ram4-disk20-perf1"
}

variable "image_name" {
  description = <<-EOT
    Base image, matched by name. ⚠ VERIFY THIS FIRST — image names here follow
    "Debian NN codename" and the available releases move:

      openstack image list | grep -i debian

    A wrong name fails at plan time rather than apply time, which is the good
    kind of failure, but it fails.
  EOT
  type        = string
  default     = "Debian 12 bookworm"
}

variable "mail_volume_gb" {
  description = <<-EOT
    Block volume mounted at /var/lib/stalwart, separate from the root disk so
    the instance can be destroyed and recreated without taking the mail with
    it. Marked prevent_destroy in server.tf.

    Two people's correspondence grows slowly; 20 GB is years. Growing it later
    is an in-place change, shrinking it is not.
  EOT
  type        = number
  default     = 20
}

variable "lab_ports" {
  description = <<-EOT
    Extra inbound TCP ports for experiments, as { "8080" = "why" }. Lands in a
    security group of its own so that widening the box for something you are
    trying out is never the same change as widening the mail server.

    Every entry wants a reason in its value. "temporary" is a reason; it is
    also a thing to grep for later.
  EOT
  type        = map(string)
  default     = {}
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

# ─── Relay ───────────────────────────────────────────────────────────────────
variable "smtp2go_cname_records" {
  description = <<-EOT
    The three CNAMEs SMTP2GO issues when a sender domain is verified, as
    { "<label>" = "<target>" }. Labels are relative to the domain.

    There is no SMTP2GO provider, so these are copied from their dashboard by
    hand — Sending > Verified Senders > Sender Domains. Empty until that has
    been done, which means the first apply is expected to create none of them.

    Until they exist, SMTP2GO signs outbound with its OWN domain instead of
    ours. Mail still delivers, so nothing appears wrong; it just arrives marked
    "via smtp2go.com" and unaligned, and would be discarded under p=reject.
  EOT
  type        = map(string)
  default     = {}
}
