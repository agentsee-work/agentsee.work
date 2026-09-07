# The box. Infomaniak Public Cloud, which is OpenStack.
#
# ─── Why ext-net1 and not a floating IP ──────────────────────────────────────
#
# Two ways to get a public address here, and they trade against each other:
#
#   ext-net1          dual stack. A public IPv4 (charged) and a public IPv6
#                     (free). PTR is assigned by the platform; changing it is a
#                     support request.
#
#   floating IP       self-service PTR via `openstack ptr record set`, but the
#                     address is NAT'd and IPv4-only, and it needs a private
#                     network, a subnet, a router and an interface to exist.
#
# ext-net1 wins because PTR barely matters in this design. A PTR is checked on
# connections a mail server MAKES, and this one never delivers direct-to-MX —
# everything outbound goes to the relay. What we would be buying with the
# floating IP is a nicer name on connections we never open, at the cost of
# IPv6 and four extra resources.
#
# If that changes — if we ever send direct — switch to a floating IP and set
# the PTR. It is a contained change and this comment is the reason to make it.

data "openstack_images_image_v2" "base" {
  name        = var.image_name
  most_recent = true
}

resource "openstack_compute_keypair_v2" "admin" {
  name       = "agentsee-admin"
  public_key = var.ssh_public_key
}

# ─── Mail's own security group ───────────────────────────────────────────────
# Everything mail needs, and nothing else. Kept separate from the lab group
# below so that opening a port for an experiment can never quietly widen the
# mail server's exposure — the two show up as different resources in the diff.
#
# OpenStack security groups default to "deny all ingress, allow all egress",
# and the provider adds the egress rules for us. So there are no egress rules
# here on purpose: outbound is open, which is what the relay on 8465 needs.
resource "openstack_networking_secgroup_v2" "mail" {
  name        = "agentsee-mail"
  description = "Mail server. Managed by OpenTofu — infra/server.tf"
}

locals {
  # port => description. Every one of these is INBOUND.
  #
  # Host-level SMTP blocks are egress-only, so they do not affect this list.
  # Infomaniak blocks OUTBOUND 25 by default and will open it on request; this
  # design never needs it, because nothing is ever sent direct-to-MX.
  mail_ports = {
    25  = "SMTP inbound — the whole point. An MX is port 25 or nothing"
    80  = "ACME http-01 challenge only. Stalwart renews its own TLS"
    443 = "JMAP and the WebAdmin"
    465 = "Submission, implicit TLS — our own clients sending"
    993 = "IMAPS — Apple Mail"
  }

  # SSH is a separate case: it is scoped to CIDRs rather than the world, and
  # the ethertype has to match the address family or the rule is rejected.
  ssh_rules = {
    for cidr in var.ssh_allowed_ips :
    cidr => strcontains(cidr, ":") ? "IPv6" : "IPv4"
  }
}

resource "openstack_networking_secgroup_rule_v2" "mail_v4" {
  for_each = local.mail_ports

  security_group_id = openstack_networking_secgroup_v2.mail.id
  description       = each.value
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = each.key
  port_range_max    = each.key
  remote_ip_prefix  = "0.0.0.0/0"
}

resource "openstack_networking_secgroup_rule_v2" "mail_v6" {
  for_each = local.mail_ports

  security_group_id = openstack_networking_secgroup_v2.mail.id
  description       = each.value
  direction         = "ingress"
  ethertype         = "IPv6"
  protocol          = "tcp"
  port_range_min    = each.key
  port_range_max    = each.key
  remote_ip_prefix  = "::/0"
}

resource "openstack_networking_secgroup_rule_v2" "ssh" {
  for_each = local.ssh_rules

  security_group_id = openstack_networking_secgroup_v2.mail.id
  description       = "SSH. Key-only auth is enforced in cloud-init regardless"
  direction         = "ingress"
  ethertype         = each.value
  protocol          = "tcp"
  port_range_min    = 22
  port_range_max    = 22
  remote_ip_prefix  = each.key
}

# ─── The lab group ───────────────────────────────────────────────────────────
# Deliberately empty. This is where ports for experiments go, so that adding
# one is a visible change to a resource that has nothing to do with mail.
#
# The rule that makes this worth having: if you find yourself adding a port
# here that mail needs, it belongs in mail_ports instead. If you find yourself
# adding one to mail_ports that an experiment needs, it belongs here.
resource "openstack_networking_secgroup_v2" "lab" {
  name        = "agentsee-lab"
  description = "Experiments. Nothing mail depends on. infra/server.tf"
}

resource "openstack_networking_secgroup_rule_v2" "lab" {
  for_each = var.lab_ports

  security_group_id = openstack_networking_secgroup_v2.lab.id
  description       = each.value
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = tonumber(each.key)
  port_range_max    = tonumber(each.key)
  remote_ip_prefix  = "0.0.0.0/0"
}

# ─── Mail's data, on its own volume ──────────────────────────────────────────
# Separate from the root disk so that destroying and recreating the instance
# does not destroy the mail with it. That is what makes "rebuild it" a
# reasonable first response to a broken box rather than a disaster, and it
# matters more here than it did on a single-purpose server: this box is also
# where things get tried, so it will be rebuilt more often.
#
# ⚠ Formatting and mounting is NOT done here — see MAIL-BUILD-RUNBOOK phase 2.
# Doing it in cloud-init means a first-boot script that can reformat a volume
# holding mail, which is a bad thing to get subtly wrong.
resource "openstack_blockstorage_volume_v3" "mail_data" {
  name        = "agentsee-mail-data"
  size        = var.mail_volume_gb
  description = "/var/lib/stalwart. Survives instance rebuild. infra/server.tf"

  lifecycle {
    prevent_destroy = true
  }
}

resource "openstack_compute_volume_attach_v2" "mail_data" {
  instance_id = openstack_compute_instance_v2.mail.id
  volume_id   = openstack_blockstorage_volume_v3.mail_data.id
}

# ─── The instance ────────────────────────────────────────────────────────────
resource "openstack_compute_instance_v2" "mail" {
  name        = "agentsee-mail"
  image_id    = data.openstack_images_image_v2.base.id
  flavor_name = var.flavor
  key_pair    = openstack_compute_keypair_v2.admin.name
  user_data   = file("${path.module}/cloud-init.yaml")

  security_groups = [
    openstack_networking_secgroup_v2.mail.name,
    openstack_networking_secgroup_v2.lab.name,
  ]

  network {
    name = var.public_network
  }

  lifecycle {
    # user_data: cloud-init runs once, at first boot. Editing this file does
    # not re-run it, so a diff here would offer to rebuild the server to apply
    # a change that a rebuild is not the right way to make.
    #
    # image_id: `most_recent` means a new upstream image would otherwise show
    # up as a pending replacement of a running mail server, on a schedule set
    # by Infomaniak rather than by us. Rebuild deliberately, not incidentally.
    ignore_changes = [user_data, image_id]
  }
}

locals {
  # access_ip_v6 comes back bracketed — "[2001:db8::1]" — because it is shaped
  # for a URL. A DNS AAAA record needs it bare, and Cloudflare rejects the
  # bracketed form with an error that does not mention brackets.
  mail_ipv4 = openstack_compute_instance_v2.mail.access_ip_v4
  mail_ipv6 = trim(openstack_compute_instance_v2.mail.access_ip_v6, "[]")
}
