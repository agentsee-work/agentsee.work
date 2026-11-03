"""
What is running, and what was last deployed, for Health.

The deploy itself is the box's business: home/deploy.sh fast-forwards the
checkout, restarts, recreates or rebuilds as the commit asks, waits for the
healthcheck and writes its outcome to $ENGINE_DATA/deploy.json. GitHub's
green tick on main runs it over SSH (the deploy job in
.github/workflows/check.yml) and the runbook says how that is set up
(home/README.md, "Deploying on green"). The engine only reads: the commit
this process started on, from .git without git, and that record.
"""

import datetime as dt
import json
import pathlib

import ledger
from pages import esc, link, when

RECORD = "deploy.json"
STARTED = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def head(root=ledger.ROOT):
    """The commit a checkout is on, read from .git; None when it cannot be told."""
    try:
        git = pathlib.Path(root) / ".git"
        if git.is_file():  # a worktree: "gitdir: <path>"
            git = pathlib.Path(git.read_text(encoding="utf-8").split(":", 1)[1].strip())
        common = git
        if (git / "commondir").is_file():  # a worktree's refs live in the main .git
            common = (git / (git / "commondir").read_text(encoding="utf-8").strip()).resolve()
        ref = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not ref.startswith("ref: "):
            return ref  # detached
        name = ref[5:]
        for d in (git, common):
            if (d / name).is_file():
                return (d / name).read_text(encoding="utf-8").strip()
        if (common / "packed-refs").is_file():
            for line in (common / "packed-refs").read_text(encoding="utf-8").splitlines():
                if line.endswith(" " + name):
                    return line.split(" ", 1)[0]
    except OSError:
        pass
    return None


RUNNING = head()  # once: the commit this process loaded


def record():
    """The last deploy's outcome as home/deploy.sh wrote it, or None."""
    try:
        return json.loads((ledger.data_dir() / RECORD).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None


def running_html():
    sha = RUNNING or "unknown commit"
    return f'<span class="mono">{esc(sha[:7])}</span> since {esc(when(STARTED))}'


def record_html():
    r = record()
    if not r:
        return "no record: by hand, or deploy.sh has not run on this box yet (home/README.md, Deploying on green)"
    said = (
        f'{esc(when(r.get("at", "")))} <span class="mono">{esc(str(r.get("sha", ""))[:7])}</span> '
        f"{esc(r.get('subject', ''))}; {esc(r.get('action', ''))}, by {esc(r.get('by', ''))}: "
    )
    outcome = f"{esc(r.get('outcome', ''))}: {esc(r.get('detail', ''))}"
    if r.get("outcome") != "ok":
        outcome = f'<span class="error">{outcome}</span>'
    run = f" ({link(r['run'], 'the run', external=True)})" if r.get("run") else ""
    return said + outcome + run
