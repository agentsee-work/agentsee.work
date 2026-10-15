# ─── The edges: two Workers, their store, their log ───────────────────────────
# What the podcast's publishing loop sees of the outside world passes through
# two small Workers on our zone, declared here beside the bucket they serve.
#
#   podcast-media   on media.agentsee.work/*. Serves the public bucket (byte
#                   ranges, conditionals, the upload's own headers) and
#                   writes one data point per request: the file, the status,
#                   the bytes, the range, the user agent, the country, the
#                   referer's origin, and a keyed daily hash of the network
#                   address, never the address. Downloads are counted from
#                   this to the IAB rules by the collector, later.
#   podcast-links   on agentsee.work/p/*. One short id per post, looked up
#                   in the KV namespace below, logged, answered with a 302.
#                   Every click back from every platform is attributable
#                   without a tracking parameter on the page.
#
# Both write to Workers Analytics Engine, which is ours, first-party, holds
# three months, and is read with SQL; workers/README.md maps the columns.
# Analytics Engine has to be switched on once, by hand, in the dashboard
# (Workers & Pages > Analytics Engine > Enable; free) before a script can
# bind a dataset: the upload is refused with code 10089 until then, which is
# how the first apply found out (15 October 2026).
# The media route sits on top of the bucket's custom domain (r2.tf), which
# keeps the DNS record and the certificate: remove the route and the bucket
# serves the same files uncounted. That is the fallback, and also the test
# of whether the route took.
#
# The script is uploaded from its file; the sha256 is what makes an edit to
# the file a change in the plan. `main_module` is what tells Cloudflare the
# file is an ES module (it exports a fetch handler) rather than a service
# worker. workers.dev is switched off for both: they answer on the routes
# and nowhere else.
#
# The token this root runs on grew for these (r2.tf lists the whole of it):
# Workers > Admin and Workers KV Storage on the account, Workers Routes on
# the zone. Still no DNS.

locals {
  datasets = {
    media = "podcast_media"
    links = "podcast_links"
  }
  workers = "${path.module}/workers"
}

resource "cloudflare_workers_kv_namespace" "links" {
  account_id = var.cloudflare_account_id
  title      = "podcast-links"
}

resource "cloudflare_workers_script" "media" {
  account_id         = var.cloudflare_account_id
  script_name        = "podcast-media"
  content_file       = "${local.workers}/media.js"
  content_sha256     = filesha256("${local.workers}/media.js")
  main_module        = "media.js"
  compatibility_date = "2026-10-01"

  bindings = [
    { type = "r2_bucket", name = "MEDIA", bucket_name = cloudflare_r2_bucket.media.name },
    { type = "analytics_engine", name = "LOG", dataset = local.datasets.media },
    { type = "secret_text", name = "HASH_KEY", text = var.edge_hash_key },
  ]
}

resource "cloudflare_workers_script" "links" {
  account_id         = var.cloudflare_account_id
  script_name        = "podcast-links"
  content_file       = "${local.workers}/links.js"
  content_sha256     = filesha256("${local.workers}/links.js")
  main_module        = "links.js"
  compatibility_date = "2026-10-01"

  bindings = [
    { type = "kv_namespace", name = "LINKS", namespace_id = cloudflare_workers_kv_namespace.links.id },
    { type = "analytics_engine", name = "LOG", dataset = local.datasets.links },
    { type = "secret_text", name = "HASH_KEY", text = var.edge_hash_key },
  ]
}

resource "cloudflare_workers_script_subdomain" "media" {
  account_id       = var.cloudflare_account_id
  script_name      = cloudflare_workers_script.media.script_name
  enabled          = false
  previews_enabled = false
}

resource "cloudflare_workers_script_subdomain" "links" {
  account_id       = var.cloudflare_account_id
  script_name      = cloudflare_workers_script.links.script_name
  enabled          = false
  previews_enabled = false
}

resource "cloudflare_workers_route" "media" {
  zone_id = var.cloudflare_zone_id
  pattern = "${var.media_subdomain}.${var.domain}/*"
  script  = cloudflare_workers_script.media.script_name
}

resource "cloudflare_workers_route" "links" {
  zone_id = var.cloudflare_zone_id
  pattern = "${var.domain}/p/*"
  script  = cloudflare_workers_script.links.script_name
}
