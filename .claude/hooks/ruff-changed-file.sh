#!/bin/sh
# PostToolUse hook (.claude/settings.json): Ruff on the one Python file the
# Edit or Write tool just changed, so an agent's Python is lint-fixed and
# formatted before it is ever committed and CI's Ruff job has nothing to say.
#
# On that file only:
#   ruff check --fix     the fixable findings (unused imports, import order, ...)
#   ruff format          the formatting (tools/ is excluded from it: ruff.toml)
# It says nothing unless findings remain that Ruff cannot fix; those go back
# to the agent (exit 2 feeds stderr to the model), to be dealt with now rather
# than as a red tick later.
#
# Quiet exits: not a .py file; Ruff not installed (CI still checks); a path
# ruff.toml excludes (--force-exclude applies the exclusions to a file named
# on the command line, which Ruff otherwise does not).

f=$(jq -r '.tool_input.file_path // empty')
case "$f" in *.py) ;; *) exit 0 ;; esac
[ -f "$f" ] || exit 0

ruff=$(command -v ruff 2>/dev/null) || ruff="$HOME/.local/bin/ruff"
[ -x "$ruff" ] || exit 0

left=$("$ruff" check --fix --force-exclude --output-format concise "$f" 2>&1)
status=$?
"$ruff" format --force-exclude --quiet "$f" 2>/dev/null
[ "$status" -eq 0 ] && exit 0
printf 'ruff: still flagged after --fix in %s\n%s\n' "$f" "$left" >&2
exit 2
