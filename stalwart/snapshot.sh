#!/usr/bin/env bash
#
# Snapshot the running server into plan.json, correctly.
#
#   ./snapshot.sh [--url https://mail.agentsee.work] [--user admin@agentsee.work]
#
# Exists because the three steps below were a documented convention and the
# convention was skipped the first time it was used. A snapshot that is one
# command cannot be half done.
#
#   1. snapshot the configuration object types (not state)
#   2. add the matchOn keys the CLI cannot infer
#   3. refuse to write the file if it contains a secret value
#
set -euo pipefail
cd "$(dirname "$0")"

URL="${STALWART_URL:-https://mail.agentsee.work}"
USER="${STALWART_USER:-admin@agentsee.work}"
while [ $# -gt 0 ]; do
  case "$1" in
    --url)  URL="$2";  shift 2 ;;
    --user) USER="$2"; shift 2 ;;
    *) echo "usage: $0 [--url URL] [--user USER]" >&2; exit 2 ;;
  esac
done

# Configuration only. Most of the ~120 object types are state — queued messages,
# logs, metrics, spam samples, received reports — and belong in the backup.
#
# Directory, Tenant, DnsServer, Role and PublicKey are unresolved because we use
# none of them; Role and PublicKey CANNOT be added, as they form reference
# cycles.
TYPES=(Domain Account MailingList AcmeProvider DkimSignature
       MtaRoute MtaOutboundStrategy Tracer NetworkListener)

echo "→ snapshotting $URL"
stalwart-cli --url "$URL" --user "$USER" \
  snapshot --output plan.json.tmp \
  --allow-unresolved Directory,Tenant,DnsServer,Role,PublicKey \
  "${TYPES[@]}"

echo "→ adding matchOn keys"
./add-matchon.py plan.json.tmp

echo "→ checking for secret values"
python3 - plan.json.tmp <<'PY'
import json, sys

# Shapes that are structure rather than secret. The CLI strips values by
# default; this catches --include-secrets being added, or a future field that
# is not stripped.
SAFE = ('"@type": "Text"', '"@type": "EnvironmentVariable"',
        '"@type": "Password"', '"@type": "AppPassword"', '"@type": "ApiKey"')

bad = []
for line in open(sys.argv[1]):
    if not line.strip():
        continue
    obj = json.loads(line)
    for _, body in obj.get("value", {}).items():
        if not isinstance(body, dict):
            continue
        for key in ("credentials", "authSecret", "privateKey", "secret", "password"):
            if key not in body:
                continue
            blob = json.dumps(body[key])
            if blob != "{}" and not any(s in blob for s in SAFE):
                bad.append(f"{obj.get('object')}.{key} = {blob[:120]}")

if bad:
    print("REFUSING TO WRITE — secret values present:", file=sys.stderr)
    for b in bad:
        print("  " + b, file=sys.stderr)
    raise SystemExit(1)
PY

mv plan.json.tmp plan.json
echo "✓ plan.json written — review the diff before committing"
