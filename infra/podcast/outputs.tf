output "media_host" {
  description = "Where the public podcast files are served from. tools/show.json's media_url must agree."
  value       = "https://${var.media_subdomain}.${var.domain}"
}

output "buckets" {
  description = "What tools/stage.env names. The two must agree, and nothing but a reader checks that they do."
  value = {
    archive = cloudflare_r2_bucket.podcast_archive.name
    media   = cloudflare_r2_bucket.media.name
  }
}
