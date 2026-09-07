terraform {
  required_version = ">= 1.8"

  # State contains secrets — the SES SMTP password, at minimum. This repo is
  # public, so state must never live in it. R2 is S3-compatible and the
  # Cloudflare account already exists.
  #
  # Bootstrap once, by hand, before the first `tofu init`:
  #
  #   npx wrangler r2 bucket create agentsee-tfstate
  #
  # then mint an R2 API token and put it in ~/.aws/credentials as its own
  # profile. Two profiles, because the backend talks to R2 and the AWS provider
  # talks to real AWS — sharing AWS_ACCESS_KEY_ID between them silently sends
  # one set of credentials to the wrong service:
  #
  #   [r2-tfstate]                     [agentsee-ses]
  #   aws_access_key_id     = ...      aws_access_key_id     = ...
  #   aws_secret_access_key = ...      aws_secret_access_key = ...
  #
  # This is the manual root of trust. Something has to hold the credential that
  # lets the automation run, and it cannot be automated away. See
  # docs/CREDENTIALS.md — the R2 token belongs in the `infra` collection.
  backend "s3" {
    bucket  = "agentsee-tfstate"
    key     = "mail/terraform.tfstate"
    region  = "auto"
    profile = "r2-tfstate"

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
    # NOTE: provider v5 renamed `cloudflare_record` to `cloudflare_dns_record`
    # and made `name` a FQDN. Most examples online are still v4 and will not
    # work here. Pinned deliberately.
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.0"
    }
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.50"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# Credentials come from the environment, never from this repo:
#   CLOUDFLARE_API_TOKEN   zone DNS edit on agentsee.work
#   HCLOUD_TOKEN           Hetzner project token
provider "cloudflare" {}

provider "hcloud" {}

provider "aws" {
  region  = var.aws_region
  profile = "agentsee-ses"
}
