#!/usr/bin/env bash
#
# Nightly backup of the Stalwart datastore to Cloudflare R2.
#
# The datastore is RocksDB. Copying its files while the server is writing can
# capture an inconsistent set — a backup that restores into a corrupt database
# is worse than no backup, because you find out during the incident. So this
# stops the container, backs up cold, and starts it again.
#
# The downtime is seconds. Sending servers retry for days; nobody notices.
#
# Environment (systemd EnvironmentFile, mode 0600, NOT in git):
#   RESTIC_REPOSITORY       s3:https://<account>.r2.cloudflarestorage.com/<bucket>
#   RESTIC_PASSWORD_FILE    path to the repo passphrase
#   AWS_ACCESS_KEY_ID       R2 token
#   AWS_SECRET_ACCESS_KEY   R2 token
#   HEALTHCHECK_URL         optional; pinged on success, /fail on failure
#
set -euo pipefail

DATA_DIR="${DATA_DIR:-/var/lib/stalwart}"
COMPOSE_DIR="${COMPOSE_DIR:-/opt/agentsee/stalwart}"
SERVICE="${SERVICE:-stalwart}"

: "${RESTIC_REPOSITORY:?not set}"
: "${RESTIC_PASSWORD_FILE:?not set}"
: "${AWS_ACCESS_KEY_ID:?not set}"
: "${AWS_SECRET_ACCESS_KEY:?not set}"

log() { printf '%s  %s\n' "$(date -Is)" "$*"; }

ping_health() {
  [ -n "${HEALTHCHECK_URL:-}" ] || return 0
  curl -fsS -m 10 --retry 3 "${HEALTHCHECK_URL}${1:-}" >/dev/null || true
}

# A failed backup must never leave mail down. This runs on every exit path,
# including the set -e ones, and starting an already-started container is a
# no-op — so it is safe to be unconditional.
started=0
restart_mail() {
  if [ "$started" = 1 ]; then
    log "restarting ${SERVICE}"
    docker compose -f "${COMPOSE_DIR}/docker-compose.yml" start "$SERVICE" \
      || log "WARNING: could not restart ${SERVICE} — CHECK THIS NOW"
  fi
}
trap restart_mail EXIT

fail() {
  log "FAILED: $*"
  ping_health "/fail"
  exit 1
}

log "stopping ${SERVICE} for a consistent copy"
docker compose -f "${COMPOSE_DIR}/docker-compose.yml" stop "$SERVICE" \
  || fail "could not stop ${SERVICE}; refusing to take a hot backup"
started=1

log "backing up ${DATA_DIR}"
restic backup "$DATA_DIR" \
  --tag stalwart \
  --host agentsee-mail \
  --verbose \
  || fail "restic backup"

# Start the server before pruning: pruning is slow and there is no reason for
# mail to be down during it.
restart_mail
started=0

log "applying retention"
restic forget \
  --tag stalwart \
  --keep-daily 7 \
  --keep-weekly 4 \
  --keep-monthly 12 \
  --prune \
  || fail "restic forget/prune"

# Cheap structural check every night. This is NOT a restore test — it verifies
# the repository is coherent, not that the data inside it is usable. Only
# restore-test.sh answers that question.
log "checking repository"
restic check || fail "restic check"

log "done"
ping_health
