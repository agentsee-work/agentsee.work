terraform {
  required_version = ">= 1.8"

  # State records every managed value in clear, and gains secrets the moment any
  # resource has one. This repo is public, so state must never live in it. R2 is
  # S3-compatible and the Cloudflare account already exists.
  #
  # Bootstrap once, by hand, before the first `tofu init`:
  #
  #   npx wrangler r2 bucket create agentsee-tfstate
  #
  # then mint an R2 API token and put it in the vault. The backend speaks S3,
  # so it reads AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY — supplied by
  # `op run` from op.env, so there is no ~/.aws/credentials file holding it.
  #
  # The backup repository gets a SECOND bucket and a SECOND token, which lives
  # on the box — see stalwart/backup/README.md. One credential that can both
  # delete the backups and rewrite the infrastructure is a poor blast radius.
  #
  # This is the manual root of trust. Something has to hold the credential that
  # lets the automation run, and it cannot be automated away — here that is a
  # 1Password account. See docs/CREDENTIALS.md.
  backend "s3" {
    bucket = "agentsee-tfstate"
    key    = "mail/terraform.tfstate"
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
    # NOTE: provider v5 renamed `cloudflare_record` to `cloudflare_dns_record`
    # and made `name` a FQDN. Most examples online are still v4 and will not
    # work here. Pinned deliberately.
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.0"
    }

    # Infomaniak Public Cloud is OpenStack, so the host is declared with the
    # generic OpenStack provider rather than a vendor one. Their own docs
    # specify this source and constraint.
    #
    # That is a nicer position than it looks: the same configuration would
    # largely apply to any OpenStack cloud, so the host is the least
    # vendor-locked part of this repo. Which is the opposite of how it read on
    # Hetzner, where every resource was hcloud_*.
    openstack = {
      source  = "terraform-provider-openstack/openstack"
      version = "~> 2.0.0"
    }
  }
}

# Both provider blocks are empty on purpose. Every credential arrives in the
# environment, injected by `op run` from op.env at the moment tofu starts:
#
#   op run --env-file=op.env -- tofu plan
#
# Cloudflare reads CLOUDFLARE_API_TOKEN. OpenStack reads the standard OS_*
# variables, which is why there is no `cloud` argument and no clouds.yaml on
# disk — Infomaniak documents clouds.yaml, and it works, but it is a password
# sitting in a file at mode 0600 and hoping. Setting OS_CLOUD instead of the
# individual OS_* variables restores that path if you ever want it.
#
# Putting credentials in a provider block is the thing this repo does not do.
provider "cloudflare" {}

provider "openstack" {}
