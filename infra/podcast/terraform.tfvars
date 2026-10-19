# COMMITTED, deliberately. Read ../.gitignore for why: identifiers only,
# never secrets. Every credential comes from op.env via `op run`.

# The Zero Trust team name, chosen once in the dashboard. It is the login
# page (agentsee.cloudflareaccess.com) and what cloudflared checks Access
# tokens against (engine.tf, engine/home/README.md).
zero_trust_team = "agentsee"
