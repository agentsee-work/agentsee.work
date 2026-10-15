# ─── R2 for the podcast ──────────────────────────────────────────────────────
# Two buckets, two purposes. The state bucket and the backup bucket are already
# kept apart (../versions.tf says why); these two are a third and a fourth.
#
#   agentsee-podcast-archive  private. The Riverside package, the edit as data
#                             and every deliverable of an approved episode: the
#                             masters, off the workstation. Nothing serves it.
#   agentsee-media            public, at media.agentsee.work. The mp3, the
#                             clips, the art, the transcripts and the chapters:
#                             what the feed, the episode page and the platforms
#                             fetch. Nothing goes here that anyone may not
#                             download.
#
# The custom domain gives the public bucket a hostname in our zone, and
# Cloudflare writes the DNS record for it itself: the token this root runs on
# has no DNS permission and needs none. Counting downloads is the Worker in
# edge.tf, on a route in front of this hostname; with the route gone the
# bucket serves the same files directly and nothing is counted.
#
# Two tokens, minted once, by hand. Neither is the DNS token (op.env says why).
#
#   1. The one tofu runs on, before the first apply. My Profile > API Tokens >
#      Create Custom Token: Account > Workers R2 Storage > Edit, this account,
#      and since the edges (edge.tf, 15 October 2026) also Account > Workers >
#      Admin (the per-product role that can create a Worker; the token editor
#      still lists the old "Workers Scripts > Edit" as Legacy, and it maps to
#      the same), Account > Workers KV Storage > Edit, and Zone > Workers
#      Routes > Edit on this zone. Nothing else, and no DNS. Named
#      opentofu-podcast in Cloudflare, filed as "Cloudflare podcast token"
#      (API Credential) in Engineering; op.env resolves it. Editing a token's
#      permissions keeps its value, so widening it changed nothing in the
#      vault. ⚠ The R2 permission is account-wide. The token can read the
#      objects of every bucket, the mail backups included, so it lives in the
#      vault and nowhere else.
#   2. The one the tools run on, after the first apply, because it is scoped
#      to buckets that have to exist first. R2 > Manage API tokens >
#      Object Read & Write, these two buckets and no other. Named
#      podcast-stage in Cloudflare, filed as "R2 podcast token" in
#      Engineering; ../../tools/stage.env resolves it. It never enters state.

resource "cloudflare_r2_bucket" "podcast_archive" {
  account_id = var.cloudflare_account_id
  name       = "agentsee-podcast-archive"
  location   = "weur"
}

resource "cloudflare_r2_bucket" "media" {
  account_id = var.cloudflare_account_id
  name       = "agentsee-media"
  location   = "weur"
}

resource "cloudflare_r2_custom_domain" "media" {
  account_id  = var.cloudflare_account_id
  bucket_name = cloudflare_r2_bucket.media.name
  domain      = "${var.media_subdomain}.${var.domain}"
  zone_id     = var.cloudflare_zone_id
  enabled     = true
  min_tls     = "1.2"
}
