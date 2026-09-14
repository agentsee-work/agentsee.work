#!/usr/bin/env bash
#
# Prove the backups can actually be restored.
#
# `restic check` verifies the repository is internally coherent. It does NOT
# verify that what is inside restores into a working mail server. That is a
# different claim and this script is the only thing that tests it.
#
# It restores the latest snapshot to a scratch directory, then boots a
# throwaway Stalwart against the restored data on a private port and asks it
# whether it is alive. The live server is never touched.
#
# Run it BEFORE cutover and quarterly after. The failure this guards against is
# a backup that has silently been covering the wrong directory for months —
# which looks exactly like a working backup until the day it matters.
#
#   ./restore-test.sh              # primary   (Infomaniak Swiss Backup)
#   ./restore-test.sh secondary    # secondary (Cloudflare R2)
#
# TEST BOTH. The secondary exists for the day the primary's entire account is
# gone, which makes it the copy most likely to be reached for in an emergency
# and the least likely to have ever been exercised. An untested second copy is
# not redundancy, it is the belief in redundancy — worse than knowing you have
# only one.
#
set -euo pipefail

RESTORE_ROOT="${RESTORE_ROOT:-/var/tmp/stalwart-restore-test}"
DATA_DIR="${DATA_DIR:-/var/lib/stalwart}"
# Must match docker-compose.yml. A restore tested against a different version
# than the one running is not a test of this server.
IMAGE="${IMAGE:-stalwartlabs/stalwart:v0.16.21}"

WHICH="${1:-primary}"
case "$WHICH" in
  primary) ;;
  secondary)
    : "${SECONDARY_REPOSITORY:?no secondary configured}"
    export RESTIC_PASSWORD_FILE="${SECONDARY_PASSWORD_FILE:-${RESTIC_PASSWORD_FILE:-}}"
    export RESTIC_REPOSITORY="$SECONDARY_REPOSITORY"
    export AWS_ACCESS_KEY_ID="${SECONDARY_AWS_ACCESS_KEY_ID:?not set}"
    export AWS_SECRET_ACCESS_KEY="${SECONDARY_AWS_SECRET_ACCESS_KEY:?not set}"
    ;;
  *) echo "usage: $0 [primary|secondary]" >&2; exit 2 ;;
esac

: "${RESTIC_REPOSITORY:?not set}"
: "${RESTIC_PASSWORD_FILE:?not set}"

# Distinct scratch path per repository, so testing one can never disturb the
# other and both can be run back to back.
RESTORE_ROOT="${RESTORE_ROOT}-${WHICH}"

log()  { printf '%s  %s\n' "$(date -Is)" "$*"; }
pass() { printf '\033[32m  PASS\033[0m  %s\n' "$*"; }
fail() { printf '\033[31m  FAIL\033[0m  %s\n' "$*"; FAILED=1; }
FAILED=0

CONTAINER="stalwart-restore-test-${WHICH}-$$"
cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  rm -rf "$RESTORE_ROOT"
}
trap cleanup EXIT

# ── 1. Restore ───────────────────────────────────────────────────────────────
log "testing the ${WHICH} repository: ${RESTIC_REPOSITORY}"
log "restoring latest snapshot to ${RESTORE_ROOT}"
rm -rf "$RESTORE_ROOT"; mkdir -p "$RESTORE_ROOT"
restic restore latest --target "$RESTORE_ROOT" --tag stalwart

RESTORED="${RESTORE_ROOT}${DATA_DIR}"
[ -d "$RESTORED" ] || { fail "restored tree has no ${DATA_DIR}"; exit 1; }
pass "snapshot restored"

# ── 2. Is it plausibly a datastore? ──────────────────────────────────────────
# RocksDB always writes CURRENT and at least one MANIFEST. Their absence means
# we backed up the wrong path — the exact silent failure this exists to catch.
if find "$RESTORED" -maxdepth 3 -name 'CURRENT' | grep -q .; then
  pass "RocksDB CURRENT present"
else
  fail "no RocksDB CURRENT — is DATA_DIR right?"
fi

if find "$RESTORED" -maxdepth 3 -name 'MANIFEST-*' | grep -q .; then
  pass "RocksDB MANIFEST present"
else
  fail "no RocksDB MANIFEST"
fi

size=$(du -sm "$RESTORED" | cut -f1)
if [ "$size" -gt 0 ]; then
  pass "restored ${size} MB"
else
  fail "restored tree is empty"
fi

# ── 3. Does a server actually come up on it? ─────────────────────────────────
# The part that makes this a test rather than a file check. A restore that
# produces bytes but not a bootable database has told you nothing.
#
# ⚠ --network none is not caution, it is required. The restored datastore
# contains the MTA QUEUE. A server booted on it with working network would
# happily deliver those messages again — a restore test that re-sends last
# night's mail is worse than no restore test.
#
# ⚠ --config must match docker-compose.yml. The image defaults to
# /etc/stalwart/config.json, which is not in the restored tree, so the server
# would start in bootstrap mode and report itself healthy while having opened
# nothing. That is exactly the false pass this script exists to prevent.
log "booting a throwaway server against the restored data (no network)"
if docker run -d \
      --name "$CONTAINER" \
      --network none \
      -v "${RESTORED}:/var/lib/stalwart" \
      "$IMAGE" --config /var/lib/stalwart/etc/config.json >/dev/null 2>&1; then

  # With no network there is nothing to curl, so the evidence is the log. These
  # lines only appear after RocksDB has opened and the configuration has been
  # read out of it — which is the claim under test.
  booted=0
  for i in $(seq 1 30); do
    sleep 2
    if docker logs "$CONTAINER" 2>&1 | grep -qE 'MTA queue started|Network listener started'; then
      pass "server started on restored data (after ${i} tries)"
      booted=1
      break
    fi
  done
  [ "$booted" = 1 ] || {
    fail "server never reached startup — restored data may not be bootable"
    docker logs "$CONTAINER" 2>&1 | tail -15
  }

  # A bootstrap banner means it found no configuration and started an empty
  # datastore. Everything else would look like success.
  if docker logs "$CONTAINER" 2>&1 | grep -qiE 'bootstrap'; then
    fail "server entered BOOTSTRAP mode — it did not read the restored config"
  else
    pass "no bootstrap mode — restored configuration was read"
  fi

  if docker logs "$CONTAINER" 2>&1 | grep -qiE 'corrupt|panic|fatal'; then
    fail "server logged corruption/panic:"
    docker logs "$CONTAINER" 2>&1 | grep -iE 'corrupt|panic|fatal' | head -5
  else
    pass "no corruption in server logs"
  fi
else
  fail "throwaway container would not start"
fi

# ── Verdict ──────────────────────────────────────────────────────────────────
echo
if [ "$FAILED" = 0 ]; then
  printf '\033[32mRESTORE TEST PASSED\033[0m (%s) — %s\n' "$WHICH" "$(date -Is)"
  echo "Record the date. Next test due in three months."
  # Written as `if`, not `a && b && echo`. That chain is the last statement in
  # the passing branch, so its status becomes the script's exit status — and
  # when the condition is false (which is exactly the secondary run) it is
  # non-zero. The script would print PASSED and exit 1. Verified, not guessed.
  if [ "$WHICH" = primary ] && [ -n "${SECONDARY_REPOSITORY:-}" ]; then
    echo "Not done yet — now run: $0 secondary"
  fi
else
  printf '\033[31mRESTORE TEST FAILED\033[0m (%s)\n' "$WHICH"
  echo "Per docs/MAIL-SELFHOST.md: not fixed this week means move to a hosted"
  echo "provider. That criterion exists so the decision isn't made mid-incident."
  exit 1
fi
