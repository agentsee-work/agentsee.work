#!/bin/bash
# The door GitHub's deploy key opens, and the only one. sshd runs this for
# the `deploy` user whatever the client asked for (its authorized_keys line:
# restrict,command=…), with the client's words in SSH_ORIGINAL_COMMAND.
# Those words are untrusted. This checks that they are exactly a commit, a
# GitHub login and a run's URL on our repository, then hands the three to
# deploy.sh as root (sudoers-deploy). Anything else is refused here, as
# this user, with nothing done. README.md, "Deploying on green".
set -euo pipefail

refuse() {
  echo "deploy: refused: $*" >&2
  exit 2
}

read -r -a words <<< "${SSH_ORIGINAL_COMMAND:-}"
[ "${#words[@]}" -eq 3 ] || refuse "expected <commit> <who> <run url>, got ${#words[@]} words"

commit='^[0-9a-f]{40}$'
login='^[A-Za-z0-9._/-]{1,64}(\[bot\])?$'
run='^https://github\.com/agentsee-work/agentsee\.work/actions/runs/[0-9]+$'
[[ ${words[0]} =~ $commit ]] || refuse "not a commit: ${words[0]}"
[[ ${words[1]} =~ $login ]] || refuse "not a GitHub login: ${words[1]}"
[[ ${words[2]} =~ $run ]] || refuse "not a run of ours: ${words[2]}"

exec sudo -n /opt/agentsee/repo/engine/home/deploy.sh "${words[0]}" "${words[1]}" "${words[2]}"
