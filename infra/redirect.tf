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
# ⚠ Not applied when this was written (10 November 2026), and three things can
# stop the first apply:
#
#   1. The token. opentofu-dns holds Zone > DNS > Edit and nothing else. This
#      resource needs Zone > Single Redirect > Edit (shown as "Dynamic URL
#      Redirects" in some token editors) added to it. Editing a token's
#      permissions keeps its value, so op.env does not change.
#   2. A zone has one ruleset per phase. If a redirect rule was ever made in the
#      dashboard (Rules > Redirect Rules), the phase's ruleset already exists
#      and this create is refused; import it instead:
#        tofu import cloudflare_ruleset.www_to_apex zones/<zone id>/<ruleset id>
#   3. Nothing else: the rule matches the www host only, so the bare domain,
#      mail, the engine, the office and the media host never reach it.
#
# Check after applying:
#   curl -sI https://www.agentsee.work/podcast/?x=1
# should answer 301 with location: https://agentsee.work/podcast/?x=1
#
# Reversible: remove this file and apply.
resource "cloudflare_ruleset" "www_to_apex" {
  zone_id     = var.cloudflare_zone_id
  name        = "Redirects"
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
