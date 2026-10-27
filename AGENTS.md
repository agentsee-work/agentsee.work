# AgentSee

## Where information lives

This repository is public. Only technical material belongs here: code, infrastructure, and the documentation that explains them.

Any non-technical documentation or information remains in Linear. That includes research findings: record them on the relevant Linear issue or as a Linear document, never as files committed to this repo.

## Commits

Use Conventional Commits.

## Python

Ruff is the linter and the formatter (`ruff.toml`); CI runs `ruff check` and `ruff format --check` on every pull request and push to `main`. Before committing Python, run `ruff check --fix . && ruff format .`. A hook in `.claude/settings.json` does the same to each `.py` file as the Edit and Write tools change it, when `ruff` is on the machine (`pip install ruff==0.17.3` in a venv, the version CI pins; `~/.local/bin/ruff` is also looked for), and hands back what it could not fix.

## Agent skills

### Issue tracker

Issues live in Linear, team AgentSee (prefix AGE), via the Linear MCP. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Working in parallel

The checkout is shared by several sessions at once, so a session that will change tracked files works in its own git worktree: start it with `claude --worktree <name>`, or call `EnterWorktree` in a running session before the first edit. A worktree branches from `origin/main` under `.claude/worktrees/` and shares the one `.git`, so branches cannot collide and a session in a worktree cannot write to the main checkout. `build/` (episodes, venvs, the engine's local ledger) is untracked and far too large to copy; `.claude/settings.json` symlinks it into every worktree, so the tools find it where they expect. Producing an episode writes only under `build/`, and the pipeline's own writes go through the shell, which works from a worktree; the Edit and Write tools refuse a path that resolves into the main checkout, so hand-edits to episode data (`episode.json`, `chapters.json`, `removals.json`) from a worktree session go through the shell too, or the session runs in the main checkout. Name the Linear issue in a PR body ("Part of AGE-28") so GitHub links the two.
