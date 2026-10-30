"""
Linear, in the engine's own name. The office writes to a guest's issue as
"AgentSee Engine", the OAuth application James made (AGE-115, step 3), so
what a guest says lands on their issue as a quotation in their name and what
the engine does is the engine's. GraphQL over urllib, no SDK, like the rest.

The token. Linear's access token lasts a day and its refresh token rotates:
each refresh returns a new access token and a NEW refresh token. So the
vault item is the live copy, exactly as for X (publish/x.py): the engine
reads the refresh token from the vault at refresh time, not from the
environment (a copy from when the process started), and writes the
replacement back before it uses the access token. Linear answers the same
refresh request again for thirty minutes, so a failed vault write is less
dangerous than X's; it is still held in this process, retried on the next
attempt, and said out loud. One refresher at a time: a second engine, or
the consent script, against the same item retires this one's token.

Nothing here waits on Linear inside a request. The door and the screens put
a job in the outbox (the ledger's `linear_job` table) and the worker sends
it, retrying with a growing pause, and each outcome is an event that the
guest's page and Health show. `engine.py linear <issue>` posts a test
comment directly, which is how the bridge is proved.
"""

import datetime as dt
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import ledger
import vault
from vault import VaultError

API = "https://api.linear.app/graphql"
TOKEN_URL = "https://api.linear.app/oauth/token"
NEEDS = "LINEAR_CLIENT_ID / LINEAR_CLIENT_SECRET / LINEAR_REFRESH_TOKEN"
LABELS = ("guest-claimed", "release-signed", "booked", "episode-approved")
TRIES = 8  # attempts at one job before it waits for a person
TIMEOUT = 30
_token = {"access": None, "until": 0.0, "unsaved": None}

Q_ME = "query Me { viewer { id name } }"
Q_ISSUE = "query Issue($id: String!) { issue(id: $id) { id identifier title url labelIds team { id key } } }"
Q_ISSUES = (
    "query Issues($project: String!, $text: String!) { issues(first: 20, filter: { project: { name: { eq: $project } }, "
    "title: { containsIgnoreCase: $text } }) { nodes { id identifier title url state { name } } } }"
)
Q_TEAM = "query Team($key: String!) { teams(filter: { key: { eq: $key } }) { nodes { id } } }"
Q_PROJECT = "query Project($name: String!) { projects(filter: { name: { eq: $name } }) { nodes { id } } }"
Q_LABELS = (
    "query Labels($name: String!) { issueLabels(filter: { name: { eqIgnoreCase: $name } }) "
    "{ nodes { id name team { id } } } }"
)
M_COMMENT = (
    "mutation Comment($issue: String!, $body: String!) { commentCreate(input: { issueId: $issue, body: $body }) "
    "{ success comment { id url } } }"
)
M_LABEL = (
    "mutation Label($team: String!, $name: String!) { issueLabelCreate(input: { teamId: $team, name: $name }) "
    "{ success issueLabel { id } } }"
)
M_UPDATE = (
    "mutation Update($id: String!, $input: IssueUpdateInput!) { issueUpdate(id: $id, input: $input) "
    "{ success issue { id identifier labelIds dueDate } } }"
)
M_CREATE = (
    "mutation Create($input: IssueCreateInput!) { issueCreate(input: $input) "
    "{ success issue { id identifier url labelIds } } }"
)


class LinearError(Exception):
    pass


def ready(env):
    return all(env.get(k) for k in ("LINEAR_CLIENT_ID", "LINEAR_CLIENT_SECRET", "LINEAR_REFRESH_TOKEN"))


def settings():
    cfg = ledger.CONFIG.get("linear")
    if not cfg:
        raise LinearError('config.json has no "linear" entry naming the vault item, the team and the project')
    return cfg


def item():
    cfg = settings()
    return cfg["vault"], cfg["item"], cfg.get("field", "credential")


def office():
    return (ledger.CONFIG.get("guest") or {}).get("host", "see.agentsee.work")


# ─── the wire ────────────────────────────────────────────────────────────────
def post(url, data, headers, timeout=TIMEOUT):
    """One POST, (status, body); the tests put their own here."""
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LinearError(f"could not reach {url}: {e}") from e


def said(status, body):
    try:
        j = json.loads(body)
        if j.get("errors"):
            return f"{status}: " + "; ".join(str(e.get("message") or e) for e in j["errors"])
        if "error" in j:
            return f"{status}: {j['error']}: {j.get('error_description', '')}".strip(": ")
    except ValueError, TypeError, AttributeError:
        pass
    return f"{status}: {body[:300]!r}"


# ─── the token ───────────────────────────────────────────────────────────────
def keep(value):
    """The new refresh token into the vault, or held for the next attempt."""
    _token["unsaved"] = value
    try:
        vault.write(*item(), value)
    except VaultError as e:
        raise LinearError(
            f"Linear issued a new refresh token but the vault write failed ({e}). It is held in this process and "
            "the write is retried on the next attempt; Linear accepts the old token again for thirty minutes, "
            "so a restart inside that window loses nothing"
        ) from e
    _token["unsaved"] = None


def token(env):
    if _token["unsaved"]:
        keep(_token["unsaved"])
    if _token["access"] and time.time() < _token["until"] - 300:
        return _token["access"]
    try:
        current = vault.read(*item())
    except VaultError as e:
        raise LinearError(
            f"could not read the Linear refresh token from the vault ({e}): the engine needs an op session "
            "(run it under op run) to refresh the token and write the replacement back"
        ) from e
    data = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": current,
            "client_id": env["LINEAR_CLIENT_ID"],
            "client_secret": env["LINEAR_CLIENT_SECRET"],
        }
    ).encode()
    status, body = post(TOKEN_URL, data, {"Content-Type": "application/x-www-form-urlencoded"})
    if status != 200:
        raise LinearError(
            "Linear would not refresh the token: "
            + said(status, body)
            + ". If something else refreshed it first, the vault holds a retired token: consent again with "
            "tools/linear.py auth and file the new one"
        )
    j = json.loads(body)
    new = j.get("refresh_token")
    if not new:
        raise LinearError("Linear refreshed but sent no new refresh token")
    _token["access"], _token["until"] = j["access_token"], time.time() + int(j.get("expires_in", 86400))
    keep(new)
    return _token["access"]


def query(env, name, text, variables=None, again=True):
    """One GraphQL request as the engine: the data, or LinearError in Linear's words.
    A 401 drops the access token and tries once more with a fresh one."""
    tok = token(env)
    payload = json.dumps({"query": text, "variables": variables or {}, "operationName": name}).encode()
    status, body = post(API, payload, {"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
    if status == 401 and again:
        _token["access"] = None
        return query(env, name, text, variables, again=False)
    try:
        j = json.loads(body)
    except ValueError:
        j = {}
    if status != 200 or j.get("errors") or "data" not in j:
        raise LinearError(said(status, body))
    return j["data"]


# ─── what the engine does there ──────────────────────────────────────────────
def me(env):
    """Who the token acts as; "AgentSee Engine" when the consent was given with actor=app."""
    return query(env, "Me", Q_ME)["viewer"]


def issue(env, key):
    """An issue by its identifier (AGE-73) or id."""
    try:
        i = query(env, "Issue", Q_ISSUE, {"id": key})["issue"]
    except LinearError as e:
        raise LinearError(f"issue {key}: {e}") from e
    if not i:
        raise LinearError(f"issue {key}: Linear has no such issue")
    return i


def search(env, text, project=None):
    """Issues in the project whose title contains the text, the podcast's project unless another is named."""
    project = project or settings()["project"]
    return query(env, "Issues", Q_ISSUES, {"project": project, "text": text})["issues"]["nodes"]


def team_id(env, key=None):
    key = key or settings()["team"]
    nodes = query(env, "Team", Q_TEAM, {"key": key})["teams"]["nodes"]
    if not nodes:
        raise LinearError(f"Linear has no team {key}")
    return nodes[0]["id"]


def project_id(env, name=None):
    name = name or settings()["project"]
    nodes = query(env, "Project", Q_PROJECT, {"name": name})["projects"]["nodes"]
    if not nodes:
        raise LinearError(f"Linear has no project {name}")
    return nodes[0]["id"]


def label_id(env, team, name):
    """The label's id; made on the team when there is none. A workspace label of the name counts."""
    nodes = query(env, "Labels", Q_LABELS, {"name": name})["issueLabels"]["nodes"]
    ours = [n for n in nodes if not n["team"] or n["team"]["id"] == team]
    if ours:
        return ours[0]["id"]
    return query(env, "Label", M_LABEL, {"team": team, "name": name})["issueLabelCreate"]["issueLabel"]["id"]


def create_issue(env, title, description, labels=(), project=None):
    team = team_id(env)
    inp = {"teamId": team, "title": title, "description": description, "projectId": project_id(env, project)}
    if labels:
        inp["labelIds"] = [label_id(env, team, n) for n in labels]
    return query(env, "Create", M_CREATE, {"input": inp})["issueCreate"]["issue"]


def comment(env, key, body):
    i = issue(env, key)
    return query(env, "Comment", M_COMMENT, {"issue": i["id"], "body": body})["commentCreate"]["comment"]


def label(env, key, name):
    """The label on the issue, made if missing; nothing changes when it is there already."""
    i = issue(env, key)
    lid = label_id(env, i["team"]["id"], name)
    if lid in i["labelIds"]:
        return i
    inp = {"labelIds": [*i["labelIds"], lid]}
    return query(env, "Update", M_UPDATE, {"id": i["id"], "input": inp})["issueUpdate"]["issue"]


def due(env, key, date):
    """The issue's due date, YYYY-MM-DD."""
    i = issue(env, key)
    return query(env, "Update", M_UPDATE, {"id": i["id"], "input": {"dueDate": date}})["issueUpdate"]["issue"]


def plain(s):
    """A name with markdown's own characters escaped."""
    return re.sub(r"([\\`*_\[\]])", r"\\\1", s or "")


def quote(name, text):
    """A guest's words for an issue, headed and quoted, so that a reader, person
    or agent, takes them as what was said and never as what to do."""
    lines = (text or "").strip().splitlines() or [""]
    return f"**{plain(name)} wrote, through [{office()}](https://{office()}):**\n\n" + "\n".join(
        "> " + ln for ln in lines
    )


# ─── the outbox ──────────────────────────────────────────────────────────────
def _stamp(seconds=0):
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


def enqueue(c, kind, args, guest=None):
    """A job for the worker: what to do on Linear, in the engine's name, and for which guest."""
    if kind not in JOBS:
        raise ValueError(f"no such Linear job: {kind}")
    c.execute(
        "INSERT INTO linear_job (kind, args, guest, created_at) VALUES (?, ?, ?, ?)",
        (kind, json.dumps(args), guest, ledger.now()),
    )
    c.commit()
    return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def waiting(c):
    return c.execute("SELECT COUNT(*) FROM linear_job WHERE done_at IS NULL AND tries < ?", (TRIES,)).fetchone()[0]


def stuck(c):
    return c.execute("SELECT COUNT(*) FROM linear_job WHERE done_at IS NULL AND tries >= ?", (TRIES,)).fetchone()[0]


def drain(c, env, log=print):
    """Send what is waiting, oldest first; each outcome an event. Nothing without the credential."""
    if not ready(env):
        return 0
    rows = c.execute(
        "SELECT * FROM linear_job WHERE done_at IS NULL AND tries < ? AND (next_at IS NULL OR next_at <= ?) ORDER BY id",
        (TRIES, _stamp()),
    ).fetchall()
    sent = 0
    for job in rows:
        try:
            detail = JOBS[job["kind"]](env, json.loads(job["args"]))
        except (LinearError, KeyError, ValueError, TypeError) as e:
            tries = job["tries"] + 1
            pause = min(2**tries, 240) * 60  # two, four, eight minutes ... at most four hours
            c.execute(
                "UPDATE linear_job SET tries=?, last_error=?, next_at=? WHERE id=?",
                (tries, str(e)[:500], _stamp(pause), job["id"]),
            )
            ledger.event(
                c,
                "linear:failed",
                "worker",
                detail={"job": job["id"], "kind": job["kind"], "try": tries, "error": str(e)[:500]},
                guest=job["guest"],
            )
            log(f"  linear: {job['kind']} #{job['id']} failed (try {tries}): {e}")
        else:
            c.execute("UPDATE linear_job SET done_at=?, last_error=NULL WHERE id=?", (ledger.now(), job["id"]))
            ledger.event(
                c, "linear:done", "worker", detail={"job": job["id"], "kind": job["kind"], **detail}, guest=job["guest"]
            )
            sent += 1
        c.commit()
    return sent


def last_failure(c):
    return c.execute("SELECT at, detail FROM event WHERE what='linear:failed' ORDER BY at DESC LIMIT 1").fetchone()


def _claimed(env, a):
    """The claim on the guest's issue: who, when, from which address; and the label."""
    key = a["issue"]
    cm = comment(
        env, key, f"{a['name']} claimed their invitation through {office()} on {a['when']}, from {a['address']}."
    )
    label(env, key, "guest-claimed")
    return {"issue": key, "comment": cm["url"], "label": "guest-claimed"}


JOBS = {"claimed": _claimed}
