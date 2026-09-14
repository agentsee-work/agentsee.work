#!/usr/bin/env bash
#
# Nightly backup of the Stalwart datastore, to two repositories at two
# different companies.
#
# The datastore is RocksDB. Copying its files while the server is writing can
# capture an inconsistent set — a backup that restores into a corrupt database
# is worse than no backup, because you find out during the incident. So this
# stops the container, backs up cold, and starts it again.
#
# The downtime is seconds. Sending servers retry for days; nobody notices.
#
# ── Why two, and why not `restic copy` ──────────────────────────────────────
#
# The primary is Infomaniak Swiss Backup, which also runs the server. That is
# the problem: one suspended or compromised account takes the mail server and
# its backups in the same instant, which is precisely the event backups exist
# for. The secondary is at Cloudflare so that no single account holds both.
#
# `restic copy` would be the obvious way to mirror one into the other, and it
# does not work here: both repositories speak S3 with DIFFERENT credentials,
# and restic reads AWS_ACCESS_KEY_ID once per process for both source and
# destination. So this runs two independent `restic backup` passes instead,
# each in a subshell with its own environment. They are separate repositories
# with separate snapshot IDs, which is slightly more work and rather more
# independent.
#
# Environment (systemd EnvironmentFile, mode 0600, NOT in git):
#   RESTIC_REPOSITORY        s3:https://s3.swiss-backup0N.infomaniak.com/<bucket>
#   RESTIC_PASSWORD_FILE     path to the repo passphrase
#   AWS_ACCESS_KEY_ID        Swiss Backup key
#   AWS_SECRET_ACCESS_KEY    Swiss Backup key
#   HEALTHCHECK_URL          optional; pinged on success, /fail on failure
#
#   SECONDARY_REPOSITORY     s3:https://<account>.r2.cloudflarestorage.com/<bucket>
#   SECONDARY_AWS_ACCESS_KEY_ID      R2 token — a DIFFERENT credential
#   SECONDARY_AWS_SECRET_ACCESS_KEY  R2 token
#   SECONDARY_HEALTHCHECK_URL        its own dead-man's switch. See below
#   SECONDARY_PASSWORD_FILE  optional; defaults to RESTIC_PASSWORD_FILE
#
# Leave SECONDARY_REPOSITORY unset and the second copy is skipped, which is
# what makes this safe to deploy before the second bucket exists.
#
set -euo pipefail

DATA_DIR="${DATA_DIR:-/var/lib/stalwart}"
COMPOSE_DIR="${COMPOSE_DIR:-/opt/agentsee/stalwart}"
SERVICE="${SERVICE:-stalwart}"

: "${RESTIC_REPOSITORY:?not set}"
: "${RESTIC_PASSWORD_FILE:?not set}"
: "${AWS_ACCESS_KEY_ID:?not set}"
: "${AWS_SECRET_ACCESS_KEY:?not set}"

SECONDARY_PASSWORD_FILE="${SECONDARY_PASSWORD_FILE:-$RESTIC_PASSWORD_FILE}"

log() { printf '%s  %s\n' "$(date -Is)" "$*"; }

ping_health() {
  [ -n "${HEALTHCHECK_URL:-}" ] || return 0
  curl -fsS -m 10 --retry 3 "${HEALTHCHECK_URL}${1:-}" >/dev/null || true
}

# The secondary gets its OWN dead-man's switch rather than sharing the primary's.
#
# Sharing one would mean a secondary that has been failing for months still
# looks healthy, because the primary keeps pinging — and "I have two copies"
# would be false in exactly the way that is never discovered until the day both
# are needed. Two switches, two alerts, and you can tell which one stopped.
ping_secondary() {
  [ -n "${SECONDARY_HEALTHCHECK_URL:-}" ] || return 0
  curl -fsS -m 10 --retry 3 "${SECONDARY_HEALTHCHECK_URL}${1:-}" >/dev/null || true
}

# Runs a restic command against the secondary repository, in a subshell so the
# overridden credentials cannot leak into anything after it.
secondary() {
  (
    export RESTIC_REPOSITORY="$SECONDARY_REPOSITORY"
    export RESTIC_PASSWORD_FILE="$SECONDARY_PASSWORD_FILE"
    export AWS_ACCESS_KEY_ID="$SECONDARY_AWS_ACCESS_KEY_ID"
    export AWS_SECRET_ACCESS_KEY="$SECONDARY_AWS_SECRET_ACCESS_KEY"
    restic "$@"
  )
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

log "backing up ${DATA_DIR} → primary"
restic backup "$DATA_DIR" \
  --tag stalwart \
  --host agentsee-mail \
  --verbose \
  || fail "restic backup (primary)"

# Both passes happen while the container is stopped. Backing the secondary up
# after restarting would make it a hot copy of a live RocksDB — the exact thing
# the stop exists to prevent, and it would be the copy you reach for on the
# worst day.
secondary_failed=0
if [ -n "${SECONDARY_REPOSITORY:-}" ]; then
  : "${SECONDARY_AWS_ACCESS_KEY_ID:?set when SECONDARY_REPOSITORY is set}"
  : "${SECONDARY_AWS_SECRET_ACCESS_KEY:?set when SECONDARY_REPOSITORY is set}"

  log "backing up ${DATA_DIR} → secondary"
  if secondary backup "$DATA_DIR" --tag stalwart --host agentsee-mail; then
    ping_secondary
  else
    # Deliberately not fatal. A secondary that cannot be written is bad; a
    # primary reported as broken because of it is worse, and mail staying down
    # over it would be worse still. Its own healthcheck raises the alarm.
    log "WARNING: secondary backup failed — one copy only tonight"
    ping_secondary "/fail"
    secondary_failed=1
  fi
else
  log "SECONDARY_REPOSITORY unset — single copy"
fi

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

if [ -n "${SECONDARY_REPOSITORY:-}" ] && [ "$secondary_failed" = 0 ]; then
  log "applying retention and checking secondary"
  secondary forget --tag stalwart \
    --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --prune \
    && secondary check \
    || { log "WARNING: secondary retention/check failed"; ping_secondary "/fail"; }
fi

log "done"
ping_health
