# COMMITTED, deliberately. Read infra/.gitignore for why.
#
# Nothing here is secret: an SSH *public* key, DKIM *public* keys, DNS records
# that are world-readable anyway, and two flags. Every credential comes from
# op.env via `op run`.
#
# ⚠ If you are tempted to put a secret here, put it in 1Password and reference
# it from op.env instead. The moment this file holds a secret it has to be
# ignored again, and then plans differ by machine — which is how a second laptop
# ends up offering to destroy the apex MX.

ssh_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILnznjfRNwGZEae/CicL166E5Ilcs/SObqOKNrs7R/+8 agentsee-mail"

# Matches the DMARC record already live in the zone, so importing it below
# produces a zero diff. Drops to "none" at phase 3, before sending starts.
dmarc_policy = "reject"

# From SMTP2GO's Sender Domains screen. Labels are relative to the domain.
#   em989721            return-path (VERP) — this is what makes SPF pass
#   s989721._domainkey  their DKIM key, delegated to their DNS
#   link                tracking. Published for verification; tracking is OFF
smtp2go_cname_records = {
  "em989721"           = "return.smtp2go.net"
  "s989721._domainkey" = "dkim.smtp2go.net"
  "link"               = "track.smtp2go.net"
}

# Stalwart's own DKIM public keys, read from the WebAdmin 11 September 2026.
# Public halves only — the private keys live on the box and in the backups.
dkim_records = {
  "v1-rsa-20260911"     = "v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA1J2+1+nwOpZiDgdA+LbrhR58tFljrB2cYFrtyQiOfYFQlTAFMn2/bjO2IKDW5HY2C8tbthi4UVtQckktu61/kxtcojYMAIkLfM0K9z+nlCw8EenQIfcoG7k5B1qTK2sG50aRZq242I3Hy6F7cU4TWZ0JDuEjvDGU+qK+EvpmIaJNNmZXMJNsIzWHhjgfd84hDcHWA3j6a+wPRoqocl9QRvaN9dpA/iJ+pUm1Sy7VP9rMJnNiOwDbiwpwXqVMQBBCyupMNArq/XFDiJ8BNtUbgovB6FJesey7rtFaKq8YsiDRW4154gRE8tZamiR1p8l1qAdx0xp751SpeZdLTrwIBwIDAQAB"
  "v1-ed25519-20260911" = "v=DKIM1; k=ed25519; p=WbUQ92pA20J/BnUytSg7C0dQev02H1dEQPPUl/jiGSk="
}

# ⚠ THE CUTOVER — 14 September 2026. Apex MX moves to our own server.
enable_apex_mx = true
