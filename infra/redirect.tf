# ─── www → the bare domain ───────────────────────────────────────────────────
# Pages serves the whole site at www.agentsee.work as well as at agentsee.work
# (both are custom domains of the project, docs/RUNBOOK.md), so every page has
# two addresses. Each page's canonical link names the bare one, which is enough
# for a search engine to choose correctly, but a link someone makes to the www
# address is then credited to a copy. A permanent redirect leaves one address.
#
# The www DNS record stays as it is: the redirect happens at Cloudflare's edge,
# which only sees the request because that record is proxied.
#
# How it came to be (10 November 2026): the rule was first made in the dashboard
# from Cloudflare's "Redirect from WWW to root" template, then imported here:
#   tofu import cloudflare_ruleset.www_to_apex zones/<zone id>/<ruleset id>
# Two things that taught:
#
#   1. A zone has one ruleset per phase, and Cloudflare names it "default". A
#      create beside it is refused ("exceeded maximum number of zone rulesets"),
#      and a different name here forces a destroy and re-create, so the name
#      below stays "default".
#   2. The token. opentofu-dns needed Zone > Single Redirect > Edit added to
#      its DNS > Edit. Editing a token's permissions keeps its value.
#
# The rule matches the www host only, so the bare domain, mail, the engine, the
# office and the media host never reach it.
#
# Check:
#   curl -sI https://www.agentsee.work/podcast/?x=1
# should answer 301 with location: https://agentsee.work/podcast/?x=1
#
# Reversible: remove this file and apply.
resource "cloudflare_ruleset" "www_to_apex" {
  zone_id     = var.cloudflare_zone_id
  name        = "default"
  description = "www to the bare domain. Managed by OpenTofu (infra/redirect.tf)."
  kind        = "zone"
  phase       = "http_request_dynamic_redirect"

  rules = [
    {
      ref         = "www_to_apex"
      description = "www.${var.domain} to ${var.domain}, path and query kept"
      expression  = "(http.host eq \"www.${var.domain}\")"
      action      = "redirect"
      action_parameters = {
        from_value = {
          status_code = 301
          target_url = {
            expression = "concat(\"https://${var.domain}\", http.request.uri.path)"
          }
          preserve_query_string = true
        }
      }
    }
  ]
}
