# The account, the zone and the domain also appear in ../versions.tf and
# ../variables.tf. Defaults rather than shared state: the two roots have to
# agree, and nothing but a reader checks that they do.

variable "cloudflare_account_id" {
  description = "Account ID. An identifier, not a secret: it is the host of the state backend's endpoint in versions.tf."
  type        = string
  default     = "9468eccb7caed9f96283a9139c37a4df"
}

variable "cloudflare_zone_id" {
  description = "Zone ID for the domain. An identifier, not a secret; it is in docs/RUNBOOK.md."
  type        = string
  default     = "d2ab9ee564f8c164c1c32f57414ce749"
}

variable "domain" {
  description = "Apex domain. The media host is a subdomain of it."
  type        = string
  default     = "agentsee.work"
}

variable "media_subdomain" {
  description = "Hostname of the public media bucket, under the apex. tools/show.json's media_url must agree."
  type        = string
  default     = "media"
}
