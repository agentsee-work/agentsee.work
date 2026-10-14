# A second root, not a second file in the first one. The podcast is a service
# of its own: it runs on its own Cloudflare token, keeps its own state, and a
# plan here never reads the mail server, let alone touches it. What the two
# roots share is the zone, because the media hostname has to live in it, and
# the state bucket, under a different key. Nothing else.
terraform {
  required_version = ">= 1.8"

  # Same bucket as ../versions.tf, which explains the backend; different key.
  # The tfstate token is the one credential the roots have in common, and it
  # is OpenTofu's rather than either service's: it reaches the state bucket
  # and nothing else.
  backend "s3" {
    bucket = "agentsee-tfstate"
    key    = "podcast/terraform.tfstate"
    region = "auto"

    endpoints = {
      s3 = "https://9468eccb7caed9f96283a9139c37a4df.r2.cloudflarestorage.com"
    }

    # R2 is not S3. Each of these skips a check that assumes it is.
    use_path_style              = true
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
  }

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.0"
    }
  }
}

# Empty on purpose. CLOUDFLARE_API_TOKEN arrives from `op run` (op.env), and
# it is the podcast's token, not the DNS one.
provider "cloudflare" {}
