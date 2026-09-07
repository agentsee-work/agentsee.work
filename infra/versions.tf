terraform {
  required_version = ">= 1.8"

  # State records every value this configuration manages. This repo is public,
  # so state must never live in it. R2 is S3-compatible and the Cloudflare
  # account already exists.
  #
  # Bootstrap once, by hand, before the first `tofu init`:
  #
  #   npx wrangler r2 bucket create agentsee-tfstate
  #
  # then mint an R2 API token and put it in ~/.aws/credentials under this
  # profile name. The backup repository gets a SECOND bucket and a SECOND
  # token — see stalwart/backup/README.md. One credential that can both delete
  # the backups and rewrite the infrastructure is a poor blast radius for
  # something sitting on an internet-facing box.
  #
  #   [r2-tfstate]
  #   aws_access_key_id     = ...
  #   aws_secret_access_key = ...
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
    # No relay provider. SMTP2GO does not publish one, so the sender domain is
    # registered by hand and its records arrive as a variable. See relay.tf.
  }
}

# Credentials come from the environment, never from this repo:
#   CLOUDFLARE_API_TOKEN   zone DNS edit on agentsee.work
#   HCLOUD_TOKEN           Hetzner project token
provider "cloudflare" {}

provider "hcloud" {}
