resource "hcloud_ssh_key" "admin" {
  name       = "agentsee-mail-admin"
  public_key = var.ssh_public_key
}

# The server's IP is public by necessity — that is what an MX is. So the
# firewall is the perimeter, and it is deny-by-default: anything not listed
# here is unreachable.
resource "hcloud_firewall" "mail" {
  name = "agentsee-mail"

  rule {
    description = "SSH (key-only, enforced in cloud-init)"
    direction   = "in"
    protocol    = "tcp"
    port        = "22"
    source_ips  = var.ssh_allowed_ips
  }

  rule {
    description = "SMTP inbound — the whole point"
    direction   = "in"
    protocol    = "tcp"
    port        = "25"
    source_ips  = ["0.0.0.0/0", "::/0"]
  }

  rule {
    description = "HTTP — ACME http-01 challenge only"
    direction   = "in"
    protocol    = "tcp"
    port        = "80"
    source_ips  = ["0.0.0.0/0", "::/0"]
  }

  rule {
    description = "HTTPS — JMAP and the Stalwart WebAdmin"
    direction   = "in"
    protocol    = "tcp"
    port        = "443"
    source_ips  = ["0.0.0.0/0", "::/0"]
  }

  rule {
    description = "Submission (implicit TLS) — our own clients sending"
    direction   = "in"
    protocol    = "tcp"
    port        = "465"
    source_ips  = ["0.0.0.0/0", "::/0"]
  }

  rule {
    description = "IMAPS — Apple Mail"
    direction   = "in"
    protocol    = "tcp"
    port        = "993"
    source_ips  = ["0.0.0.0/0", "::/0"]
  }

  # No outbound rules: Hetzner allows all egress by default, and we only need
  # 465 out to the relay. Note we never need outbound 25 — Hetzner blocks it
  # for new accounts, and this design does not care.
}

resource "hcloud_server" "mail" {
  name         = "agentsee-mail"
  image        = "debian-12"
  server_type  = var.server_type
  location     = var.location
  ssh_keys     = [hcloud_ssh_key.admin.id]
  firewall_ids = [hcloud_firewall.mail.id]
  user_data    = file("${path.module}/cloud-init.yaml")

  public_net {
    ipv4_enabled = true
    ipv6_enabled = true
  }

  labels = {
    role    = "mail"
    managed = "opentofu"
  }

  # The box is disposable; the data is not. Replacing it should be routine —
  # that is what makes drift impossible and what makes restore-testing real.
  # Everything that matters is in the repo (config) or in backups (mail).
  lifecycle {
    ignore_changes = [user_data]
  }
}

# PTR matters less when relaying — our IP never sends to the world — but a
# mismatched PTR is free suspicion on inbound connections and costs nothing.
resource "hcloud_rdns" "mail_v4" {
  server_id  = hcloud_server.mail.id
  ip_address = hcloud_server.mail.ipv4_address
  dns_ptr    = local.mail_fqdn
}

resource "hcloud_rdns" "mail_v6" {
  server_id  = hcloud_server.mail.id
  ip_address = hcloud_server.mail.ipv6_address
  dns_ptr    = local.mail_fqdn
}
