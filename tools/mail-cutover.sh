#!/usr/bin/env bash
#
# Move agentsee.work mail from Cloudflare Email Routing to Infomaniak.
#
# Run the steps in order, with a pause between each. Nothing is destructive
# until `cutover`, and no step writes anything without --confirm.
#
#   ./mail-cutover.sh status                  what the zone says right now
#   ./mail-cutover.sh dmarc-relax --confirm   p=reject -> p=none
#   ./mail-cutover.sh dkim <sel> <value>      DKIM from the Infomaniak panel
#   ./mail-cutover.sh cutover --confirm       MX + SPF. The one-way door
#   ./mail-cutover.sh verify                  did it land
#   ./mail-cutover.sh dmarc-restore --confirm p=none -> p=reject
#
# Needs a token with Zone:DNS:Edit on this zone. NOT the CI token, which is
# Pages-only by design — see docs/RUNBOOK.md.
#
#   export CLOUDFLARE_API_TOKEN=...
#
set -euo pipefail

ZONE_ID="d2ab9ee564f8c164c1c32f57414ce749"
DOMAIN="agentsee.work"
API="https://api.cloudflare.com/client/v4"

# Infomaniak's published mail endpoints, confirmed against their own zones.
IK_MX="mta-gw.infomaniak.ch"
IK_MX_PRIO=5
IK_SPF="v=spf1 include:spf.infomaniak.ch ~all"

DMARC_NONE="v=DMARC1; p=none; sp=none; rua=mailto:dmarc@${DOMAIN}; fo=1"
DMARC_REJECT="v=DMARC1; p=reject; sp=reject; rua=mailto:dmarc@${DOMAIN}; fo=1"

CONFIRM=0
for a in "$@"; do [ "$a" = "--confirm" ] && CONFIRM=1; done

c_red()  { printf '\033[31m%s\033[0m\n' "$*"; }
c_grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
c_yel()  { printf '\033[33m%s\033[0m\n' "$*"; }
c_dim()  { printf '\033[2m%s\033[0m\n' "$*"; }

die() { c_red "error: $*" >&2; exit 1; }

command -v jq  >/dev/null || die "jq is required"
command -v dig >/dev/null || die "dig is required (dnsutils)"
[ "${CLOUDFLARE_API_TOKEN:-}" ] || die "CLOUDFLARE_API_TOKEN is not set"

api() {
  local method="$1" path="$2" body="${3:-}"
  local args=(-s -X "$method"
    -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}"
    -H "Content-Type: application/json")
  [ -n "$body" ] && args+=(--data "$body")
  local out; out=$(curl "${args[@]}" "${API}${path}")
  if [ "$(jq -r '.success' <<<"$out")" != "true" ]; then
    c_red "Cloudflare API rejected the call:" >&2
    jq -r '.errors[]? | "  [\(.code)] \(.message)"' <<<"$out" >&2
    # The trap that costs an hour if you hit it cold.
    if grep -q '"code":81044\|email routing\|Email Routing' <<<"$out"; then
      c_yel "  ^ Email Routing locks its own MX/SPF records." >&2
      c_yel "    Disable Email Routing for the apex in the dashboard first:" >&2
      c_yel "    Email > Email Routing > Settings > Disable. Subdomains are unaffected." >&2
    fi
    exit 1
  fi
  printf '%s' "$out"
}

records_of_type() { api GET "/zones/${ZONE_ID}/dns_records?type=$1&per_page=100"; }

# Records live at the apex except DMARC/DKIM, which are subnames.
upsert_txt() {
  local name="$1" value="$2" existing id
  existing=$(api GET "/zones/${ZONE_ID}/dns_records?type=TXT&name=${name}")
  id=$(jq -r '.result[0].id // empty' <<<"$existing")
  local payload; payload=$(jq -nc --arg n "$name" --arg c "$value" \
    '{type:"TXT",name:$n,content:$c,ttl:1}')
  if [ -n "$id" ]; then
    c_dim "  updating existing TXT $name"
    api PUT "/zones/${ZONE_ID}/dns_records/${id}" "$payload" >/dev/null
  else
    c_dim "  creating TXT $name"
    api POST "/zones/${ZONE_ID}/dns_records" "$payload" >/dev/null
  fi
}

guard() {
  if [ "$CONFIRM" -ne 1 ]; then
    c_yel "DRY RUN — nothing written. Re-run with --confirm to apply."
    exit 0
  fi
}

cmd_status() {
  echo "Zone ${DOMAIN} — live DNS (authoritative, not cache):"
  echo
  printf '  MX     '; dig +short "$DOMAIN" MX | paste -sd' ' - ; :
  printf '  SPF    '; dig +short "$DOMAIN" TXT | grep -i 'v=spf1' || echo '(none)'
  printf '  DMARC  '; dig +short "_dmarc.${DOMAIN}" TXT || echo '(none)'
  echo
  local mx; mx=$(dig +short "$DOMAIN" MX)
  if grep -q 'mx.cloudflare.net' <<<"$mx"; then
    c_dim "  → mail is on Cloudflare Email Routing (pre-cutover)"
  elif grep -q 'infomaniak' <<<"$mx"; then
    c_grn "  → mail is on Infomaniak (post-cutover)"
  fi
}

cmd_dmarc_relax() {
  echo "Step 1 — relax DMARC so our own mail isn't rejected while we set up."
  c_dim "  $DMARC_NONE"
  echo
  c_yel "Do this BEFORE sending anything as @${DOMAIN}. Under p=reject a"
  c_yel "misaligned message is thrown away silently by the recipient."
  guard
  upsert_txt "_dmarc.${DOMAIN}" "$DMARC_NONE"
  c_grn "done. Let the TTL pass before the next step."
}

cmd_dkim() {
  local sel="${1:-}" val="${2:-}"
  [ -n "$sel" ] && [ -n "$val" ] || die "usage: $0 dkim <selector> <p=... value>"
  echo "Step 2 — DKIM, before the MX move so signing is live from the first message."
  c_dim "  ${sel}._domainkey.${DOMAIN}"
  guard
  upsert_txt "${sel}._domainkey.${DOMAIN}" "$val"
  c_grn "done."
}

cmd_cutover() {
  echo "Step 3 — MX and SPF. THIS IS THE ONE-WAY DOOR."
  echo
  c_yel "The moment MX changes:"
  c_yel "  • Cloudflare Email Routing stops receiving for the apex"
  c_yel "  • the email-fanout Worker stops running"
  c_yel "  • mail in flight is NOT lost — senders retry for days"
  echo
  echo "Current MX records that will be removed:"
  records_of_type MX | jq -r '.result[] | "  \(.priority)\t\(.content)"'
  echo
  echo "Replaced with:"
  echo "  ${IK_MX_PRIO}	${IK_MX}"
  echo "SPF becomes:"
  echo "  ${IK_SPF}"
  guard

  local ids; ids=$(records_of_type MX | jq -r '.result[].id')
  for id in $ids; do
    c_dim "  deleting MX $id"
    api DELETE "/zones/${ZONE_ID}/dns_records/${id}" >/dev/null
  done

  c_dim "  creating MX ${IK_MX}"
  api POST "/zones/${ZONE_ID}/dns_records" \
    "$(jq -nc --arg n "$DOMAIN" --arg c "$IK_MX" --argjson p "$IK_MX_PRIO" \
       '{type:"MX",name:$n,content:$c,priority:$p,ttl:1}')" >/dev/null

  upsert_txt "$DOMAIN" "$IK_SPF"
  c_grn "done. Send yourself a test message before touching DMARC again."
}

cmd_verify() {
  local fail=0
  echo "Checking live DNS…"
  echo

  local mx; mx=$(dig +short "$DOMAIN" MX)
  if grep -q "$IK_MX" <<<"$mx"; then c_grn "  MX    → $IK_MX"
  else c_red "  MX    → not Infomaniak: ${mx:-none}"; fail=1; fi

  local spf; spf=$(dig +short "$DOMAIN" TXT | grep -i 'v=spf1' || true)
  if grep -q 'spf.infomaniak.ch' <<<"$spf"; then c_grn "  SPF   → includes Infomaniak"
  else c_red "  SPF   → ${spf:-none}"; fail=1; fi

  if grep -q 'cloudflare' <<<"$spf"; then
    c_yel "  SPF   → still lists Cloudflare; drop it once forwarding is retired"
  fi

  local dmarc; dmarc=$(dig +short "_dmarc.${DOMAIN}" TXT || true)
  echo "  DMARC → ${dmarc:-none}"
  grep -q 'p=none' <<<"$dmarc" && \
    c_yel "          still relaxed — restore p=reject once alignment is proven"

  echo
  c_yel "DNS is necessary and not sufficient. The real test:"
  cat <<'EOF'
  send a message to a Gmail address, open "Show original", require all three —
      spf=pass    header.from=agentsee.work
      dkim=pass   header.d=agentsee.work     <- must be OUR domain
      dmarc=pass
  header.d saying anything else means DKIM is signing as the provider, not as
  us, and p=reject would then reject our own mail.
EOF
  return $fail
}

cmd_dmarc_restore() {
  echo "Step 5 — re-arm DMARC."
  c_dim "  $DMARC_REJECT"
  echo
  c_yel "Only after 'Show original' shows dkim=pass with header.d=${DOMAIN}."
  guard
  upsert_txt "_dmarc.${DOMAIN}" "$DMARC_REJECT"
  c_grn "done. agentsee.work is again asserting that forged mail is forged."
}

case "${1:-}" in
  status)        cmd_status ;;
  dmarc-relax)   cmd_dmarc_relax ;;
  dkim)          shift; cmd_dkim "${1:-}" "${2:-}" ;;
  cutover)       cmd_cutover ;;
  verify)        cmd_verify ;;
  dmarc-restore) cmd_dmarc_restore ;;
  *) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
