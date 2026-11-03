#!/bin/bash
# Deploy the engine to one commit of main, as root, and say what happened.
#
# Run by deploy-gate.sh when GitHub's green tick on main asks (the deploy
# job in .github/workflows/check.yml), or by hand on the box:
#
#     /opt/agentsee/repo/engine/home/deploy.sh <commit> [who] [run url]
#
# It fast-forwards /opt/agentsee/repo to the commit, which must be on
# origin/main (it never checks out anything else), looks at what changed
# since the commit that is running, and does the least that makes it live:
# nothing when the engine reads none of it, a restart for code, a recreate
# when engine.env or the compose file changed (op run reads the env file at
# container start), a build and up when the Dockerfile did. Then it waits
# for the container to say healthy (the healthcheck in docker-compose.yml).
# If that does not come within $WAIT seconds, the checkout goes back to the
# commit that was running, the container is brought back on it, and the
# exit status is 1, so the run goes red. Either way the outcome is written
# to $RECORD, which the Health screen shows (engine/deploy.py).
#
# Everything is in functions and main is called on the last line, so bash
# has parsed the whole file before anything runs: the fast-forward replaces
# this very file, and bash reads a script as it goes.
set -euo pipefail

REPO=/opt/agentsee/repo
ENGINE=/opt/agentsee/engine
RECORD=/var/lib/agentsee-engine/deploy.json
WAIT=${WAIT:-60}
# What the engine loads. A change that touches none of it needs no restart:
# a site page, an issue, the media tools, infra.
WATCH=(engine tools/site.py)

SHA="" OLD="" WHO="" RUN="" SUBJECT="" ACTION=none RECORDED=""

say() { printf 'deploy: %s\n' "$*"; }

record() { # outcome detail
  python3 - "$RECORD" "$SHA" "$SUBJECT" "$WHO" "$RUN" "$ACTION" "$1" "$2" <<'EOF'
import datetime, json, os, sys
path, sha, subject, who, run, action, outcome, detail = sys.argv[1:]
at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
rec = dict(at=at, sha=sha, subject=subject, by=who, run=run, action=action, outcome=outcome, detail=detail)
with open(path + ".tmp", "w", encoding="utf-8") as f:
    json.dump(rec, f)
os.chmod(path + ".tmp", 0o644)
os.replace(path + ".tmp", path)
EOF
  RECORDED=1
  logger -t agentsee-deploy "$1 ${SHA:0:7} ($ACTION): $2" 2>/dev/null || true
}

finish() { # a stop that nothing recorded still leaves a record
  local rc=$?
  if [ "$rc" -ne 0 ] && [ -z "$RECORDED" ] && [ -n "$SHA" ]; then
    record failed "stopped with status $rc; the run's log says where"
  fi
}

changed() { # did the commit change anything under these paths?
  [ -n "$(git diff --name-only "$OLD" "$SHA" -- "$@")" ]
}

healthy() { # the container says healthy within $WAIT seconds
  local i status
  for ((i = 0; i < WAIT; i += 3)); do
    status=$(docker inspect -f '{{.State.Health.Status}}' agentsee-engine 2>/dev/null || echo missing)
    case $status in
      healthy) return 0 ;;
      unhealthy) return 1 ;;
    esac
    sleep 3
  done
  return 1
}

apply() { # bring the container to the checkout, by $ACTION
  cd "$ENGINE"
  case $ACTION in
    build)
      # The image that works, kept under a tag, so a build that does not
      # come up can be put back without building again.
      docker image tag agentsee-engine:latest agentsee-engine:previous 2>/dev/null || true
      docker compose build --quiet engine
      docker compose up -d engine
      ;;
    recreate) docker compose up -d --force-recreate engine ;;
    restart) docker compose restart engine ;;
  esac
  cd "$REPO"
}

back() { # the commit that was running, and its container, after one that did not come up
  say "the engine did not come up on ${SHA:0:7} within ${WAIT}s; back to ${OLD:0:7}"
  docker logs --since 3m agentsee-engine 2>&1 | tail -n 40 || true
  # The checkout is a deploy target and holds no work of its own.
  git reset -q --hard "$OLD"
  cd "$ENGINE"
  if [ "$ACTION" = build ]; then
    docker image tag agentsee-engine:previous agentsee-engine:latest
  fi
  docker compose up -d --force-recreate engine
  cd "$REPO"
  if healthy; then
    record "rolled back" "the engine did not come up on ${SHA:0:7}; back on ${OLD:0:7}"
  else
    record down "the engine did not come up on ${SHA:0:7} nor on ${OLD:0:7}: docker compose logs engine"
  fi
  exit 1
}

main() {
  SHA=${1:?a commit of main}
  WHO=${2:-hand}
  RUN=${3:-}
  [[ $SHA =~ ^[0-9a-f]{40}$ ]] || {
    say "not a commit: $SHA"
    exit 2
  }
  trap finish EXIT
  # One at a time; a second request waits its turn rather than racing.
  exec 9> /run/lock/agentsee-deploy
  flock 9
  cd "$REPO"
  OLD=$(git rev-parse HEAD)
  say "running ${OLD:0:7}; asked for ${SHA:0:7} by $WHO"
  git fetch -q origin main
  if ! git merge-base --is-ancestor "$SHA" origin/main; then
    record refused "${SHA:0:7} is not on origin/main"
    say "refused: ${SHA:0:7} is not on origin/main"
    exit 2
  fi
  SUBJECT=$(git log -1 --format=%s "$SHA")
  if git merge-base --is-ancestor "$SHA" HEAD; then
    if [ "$SHA" = "$OLD" ]; then
      record ok "already running"
      say "already running"
    else
      record ok "superseded: ${OLD:0:7} is running and includes it"
      say "superseded by ${OLD:0:7}"
    fi
    exit 0
  fi
  git merge -q --ff-only "$SHA"
  if changed engine/home/Dockerfile; then
    ACTION=build
  elif changed engine/engine.env engine/home/docker-compose.yml; then
    ACTION=recreate
  elif changed "${WATCH[@]}"; then
    ACTION=restart
  fi
  say "${OLD:0:7} -> ${SHA:0:7} \"$SUBJECT\": $ACTION"
  if [ "$ACTION" = none ]; then
    record ok "nothing the engine runs changed"
    exit 0
  fi
  apply
  healthy || back
  record ok "up on ${SHA:0:7} within ${WAIT}s"
  say "ok: the engine is on ${SHA:0:7}"
}

main "$@"
