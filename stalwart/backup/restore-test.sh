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
set -euo pipefail

RESTORE_ROOT="${RESTORE_ROOT:-/var/tmp/stalwart-restore-test}"
DATA_DIR="${DATA_DIR:-/var/lib/stalwart}"
TEST_PORT="${TEST_PORT:-18080}"
IMAGE="${IMAGE:-stalwartlabs/stalwart:latest}"

: "${RESTIC_REPOSITORY:?not set}"
: "${RESTIC_PASSWORD_FILE:?not set}"

log()  { printf '%s  %s\n' "$(date -Is)" "$*"; }
pass() { printf '\033[32m  PASS\033[0m  %s\n' "$*"; }
fail() { printf '\033[31m  FAIL\033[0m  %s\n' "$*"; FAILED=1; }
FAILED=0

CONTAINER="stalwart-restore-test-$$"
cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  rm -rf "$RESTORE_ROOT"
}
trap cleanup EXIT

# ── 1. Restore ───────────────────────────────────────────────────────────────
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
log "booting a throwaway server against the restored data"
if docker run -d --rm \
      --name "$CONTAINER" \
      -v "${RESTORED}:/opt/stalwart" \
      -p "127.0.0.1:${TEST_PORT}:443" \
      "$IMAGE" >/dev/null 2>&1; then

  for i in $(seq 1 30); do
    sleep 2
    if curl -fsS -m 5 -k "https://127.0.0.1:${TEST_PORT}/healthz" >/dev/null 2>&1 \
    || curl -fsS -m 5 -k "https://127.0.0.1:${TEST_PORT}/" >/dev/null 2>&1; then
      pass "server responded on restored data (after ${i} tries)"
      break
    fi
    [ "$i" = 30 ] && fail "server never responded — restored data may not be bootable"
  done

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
  printf '\033[32mRESTORE TEST PASSED\033[0m — %s\n' "$(date -Is)"
  echo "Record the date. Next test due in three months."
else
  printf '\033[31mRESTORE TEST FAILED\033[0m\n'
  echo "Per docs/MAIL-SELFHOST.md: not fixed this week means move to a hosted"
  echo "provider. That criterion exists so the decision isn't made mid-incident."
  exit 1
fi
