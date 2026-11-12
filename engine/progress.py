"""
Where a guest is, and what comes next: one reading of the four steps from
the ledger that every page of the office draws on (AGE-150). The strip
under the masthead on each step's page, the office page itself, the
hand-off at the foot of a finished step, the Next column on the engine's
Guests screen and the mail that names the next step all come from here,
so they cannot disagree.

Built because guests fleshed their episode out and stopped. The draft said
it had been sent to James and Abrar, which read as handed over, and the
one sign that a time was still to be picked was a line on a page they had
left. The order stays release, idea, time, review: the time is what the
idea hands on to, and a guest who books first is handed back to the idea.

A step is done (with its date), current (the guest's to do), waiting (on
the hosts: the release they are signing, the times they are answering) or
locked (with why). The next step is the first current one; when there is
none, the sentence says who is being waited on, or that it is all done.
"""

import datetime as dt

import idea
import release
import review
import schedule
from pages import esc, go, link, section, when

KEYS = ("release", "idea", "time", "review")
NAMES = {"release": "The release", "idea": "The idea", "time": "The time", "review": "The review"}
SHORT = {"release": "Release", "idea": "Idea", "time": "Time", "review": "Review"}
ABOUT = {  # what each step is, in one line, for the office page and the hand-off
    "release": "One page in plain words, already signed by James and Abrar; you read it and sign it.",
    "idea": "A short conversation with the office, about ten minutes, that fleshes the episode out and writes the "
    "run-sheet James and Abrar record from.",
    "time": "Pick the time you would prefer and up to two backups; both hosts accept, one click each, and you get a "
    "link that opens the studio when it is time.",
    "review": "The episode and the clips, to watch, note and approve before anything goes out.",
}
DAY = "%-d %B"
COUNT = ("None", "One", "Two", "Three", "All four")


def _step(key, state, line, act=None, todo=None, since=None, short=None):
    """One step as the guest stands: `line` is what stands (the date, the reason, the request), `act` the words
    on the way in, `todo` the same as a verb phrase for a sentence, `since` when it became what it is, and
    `short` the line cut for the strip where it would run long."""
    return {
        "key": key,
        "name": NAMES[key],
        "href": f"/{key}",
        "state": state,
        "line": line,
        "act": act,
        "todo": todo,
        "since": since,
        "short": short or line,
    }


def steps(c, g):
    """The four, in order. Nothing here asks the network: the review's row is what the last fetch left."""
    gid, signed = g["id"], g["signed_version"]
    out = []
    if signed:
        out.append(_step("release", "done", f"Signed {when(g['signed_at'], DAY)}", "Your copy", since=g["signed_at"]))
    elif release.issued(c):
        out.append(
            _step(
                "release", "current", ABOUT["release"], "Read and sign it", "read and sign the release", g["claimed_at"]
            )
        )
    else:
        out.append(_step("release", "waiting", "James and Abrar are signing it; it is here in a day or two."))
    if g["brief"]:
        out.append(
            _step(
                "idea",
                "done",
                f"Drafted {when(g['brief_at'], DAY)}",
                "The conversation and the draft",
                since=g["brief_at"],
            )
        )
    elif signed:
        begun = idea.exchanges(c, gid) > 0
        out.append(
            _step(
                "idea",
                "current",
                ABOUT["idea"],
                "Carry on with the office" if begun else "Sit down with the office",
                "finish the idea with the office" if begun else "flesh the idea out with the office",
                g["signed_at"],
            )
        )
    else:
        out.append(_step("idea", "locked", "After the release."))
    b = schedule.current(c, gid)
    row = review.current(c, gid) if signed else None  # a round to review means the recording happened
    if b and b["state"] == "booked":
        out.append(
            _step(
                "time",
                "done",
                f"Booked for {schedule.describe(b['start_at'], b['tz'])}",
                "The details and the join link",
                since=b["updated_at"],
                short=f"Booked {when(b['start_at'], '%a %-d %b, %H:%M')}",
            )
        )
    elif b:
        t = schedule.choices_of(b)[b["offered"]]
        hosts = " and ".join(schedule.waiting_for(b))
        out.append(
            _step(
                "time",
                "waiting",
                f"Asked for {schedule.describe(t, b['tz'])}; waiting for {hosts}",
                "Your request",
                since=b["created_at"],
            )
        )
    elif row:
        out.append(_step("time", "done", "Recorded", since=row["seen_at"]))
    elif signed:
        last = schedule.latest(c, gid)
        again = bool(last and last["state"] in ("declined", "cancelled"))
        out.append(
            _step(
                "time",
                "current",
                ABOUT["time"],
                "Pick again" if again else "Pick a time",
                "pick another time to record" if again else "pick a time to record",
                last["updated_at"] if again else (g["brief_at"] or g["signed_at"]),
            )
        )
    else:
        out.append(_step("time", "locked", "After the release."))
    if row:
        a = review.approval(c, gid, row)
        if a:
            out.append(
                _step(
                    "review",
                    "done",
                    f"Round {row['round']} approved {when(a['at'], DAY)}",
                    "The episode, and your approval",
                    since=a["at"],
                )
            )
        else:
            out.append(
                _step(
                    "review",
                    "current",
                    f"Round {row['round']} is ready: {ABOUT['review'][0].lower()}{ABOUT['review'][1:]}",
                    "Watch, note, approve",
                    "watch the episode and approve it",
                    row["seen_at"],
                )
            )
    else:
        out.append(_step("review", "locked", "After the recording."))
    return out


def where(c, g):
    """The steps, the next one for the guest to do, how many are done, and each by its key."""
    ss = steps(c, g)
    return {
        "steps": ss,
        "by": {s["key"]: s for s in ss},
        "next": next((s for s in ss if s["state"] == "current"), None),
        "done": sum(s["state"] == "done" for s in ss),
    }


def summary(w):
    return f"{COUNT[w['done']]} of four done." if w["done"] < 4 else "All four done."


def rest(w):
    """When nothing is the guest's to do: a heading and a sentence on who is doing what."""
    by = w["by"]
    if by["review"]["state"] == "done":
        return "All done", "James and Abrar take it from here; the published episode carries the number they give it."
    if by["time"]["state"] == "waiting":
        return "Nothing for you just now", "James and Abrar are answering the times you gave; you will hear by email."
    if by["time"]["state"] == "done" and by["time"]["line"].startswith("Booked for "):
        return "The recording", f"{by['time']['line'][len('Booked for ') :]}. The review follows, once the edit exists."
    if by["release"]["state"] == "waiting":
        return "Nothing for you yet", "James and Abrar are signing the release; it is here in a day or two."
    return "Nothing for you just now", "The office will say when there is."


def sentence(w, html=True):
    """What comes next, as a sentence: "Next: pick a time to record." with the verb a link, or who is being waited on."""
    nxt = w["next"]
    if nxt:
        verb = link(nxt["href"], nxt["todo"]) if html else nxt["todo"]
        return f"Next: {verb}."
    head, text = rest(w)
    return esc(f"{head}: {text}") if html else f"{head}: {text}"


# ─── the pages ───────────────────────────────────────────────────────────────
def _mark(n, state):
    return f'<span class="dot" aria-hidden="true">{"&#10003;" if state == "done" else n}</span>'


def strip(c, g, here=None):
    """The line under the masthead on each step's page: the four steps, which this page is, and what is next.
    A row of four with a line under each on a desk; the four marks and names across on a phone, and the sentence
    either way, since the sentence is the point of it."""
    w = where(c, g)
    items = []
    for n, s in enumerate(w["steps"], 1):
        st = s["state"]
        label = esc(SHORT[s["key"]])
        if st != "locked":
            label = f'<a href="{s["href"]}">{label}</a>'
        if st == "done":
            small = s["short"]
        elif st == "current":
            small = "Next" if s is w["next"] else "To do"
        elif st == "waiting":
            small = "With James and Abrar"
        else:
            small = s["line"].rstrip(".")
        cur = ' aria-current="page"' if s["key"] == here else ""
        items.append(f'<li data-state="{st}"{cur}>{_mark(n, st)}<b>{label}</b><small>{esc(small)}</small></li>')
    return (
        '<nav class="progress" aria-label="Where you are"><a class="home" href="/office">The office</a>'
        f'<ol>{"".join(items)}</ol><p class="next">{sentence(w)}</p></nav>'
    )


def hub(w):
    """The office page's list: the next step as the one card with weight and its button; a done step folded to
    its date and a link; a step with the hosts, its line; a locked step, its reason."""
    items = []
    for n, s in enumerate(w["steps"], 1):
        st = s["state"]
        head = f'<p class="head">{_mark(n, st)}<b>{esc(s["name"])}</b></p>'
        if st == "current":
            first = s is w["next"]
            body = f"<p>{esc(s['line'])}</p><p>{go(s['href'], s['act'], 'signal' if first else '')}</p>"
        elif st == "locked":
            body = f'<p class="muted">{esc(s["line"])}</p>'
        elif s["act"]:
            body = f"<p>{esc(s['line'])}. {link(s['href'], s['act'])}</p>"
        else:
            body = f"<p>{esc(s['line'])}</p>"
        nxt = " data-next" if s is w["next"] else ""
        items.append(f'<li data-state="{st}"{nxt}>{head}{body}</li>')
    return f'<ol class="journey">{"".join(items)}</ol>'


def handoff(c, g, after):
    """The card at the foot of a step's page once that step is not the guest's to do: what comes next, with the
    way there. Nothing while the step is still theirs, since the page is the step."""
    w = where(c, g)
    if w["by"][after]["state"] == "current":
        return ""
    nxt = w["next"]
    if nxt:
        inner = f"<p>{esc(nxt['line'])}</p><p>{go(nxt['href'], nxt['act'], 'signal')}</p>"
        return section("Next", nxt["name"], inner)
    head, text = rest(w)
    return section("Next", head, f"<p>{esc(text)}</p>")


# ─── the mail ────────────────────────────────────────────────────────────────
def next_mail(w, host):
    """The paragraph that names the next step, for a mail to the guest; none when nothing is theirs to do."""
    nxt = w["next"]
    if not nxt:
        head, text = rest(w)
        return f"{head}: {text}"
    url = f"https://{host}{nxt['href']}"
    if nxt["key"] == "time":
        return (
            "The next step is a time to record. Pick the time you would prefer and up to two backups at\n\n"
            f"    {url}\n\nBoth hosts accept, one click each, and you get a link that opens the studio when it is time."
        )
    if nxt["key"] == "idea":
        return (
            "The next step is the idea: a short conversation with the office, about ten minutes, that fleshes the "
            f"episode out and writes the run-sheet, at\n\n    {url}"
        )
    if nxt["key"] == "release":
        return f"The next step is the release: one page in plain words, to read and sign at\n\n    {url}"
    return f"The next step is the review: the episode and the clips, to watch, note and approve at\n\n    {url}"


# ─── the engine's side ───────────────────────────────────────────────────────
def days(iso):
    """How long since, in a word: today, yesterday, 6 days; nothing without a time."""
    if not iso:
        return ""
    n = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(iso)).days
    return "today" if n < 1 else "yesterday" if n < 2 else f"{n} days"


def cell(c, g):
    """One cell for the Guests table: the step the guest is on and for how long, so the one to nudge stands out."""
    if g["state"] == "revoked":
        return '<span class="muted">withdrawn</span>'
    if not g["claimed_at"]:
        return f'<span class="muted">not in yet</span> <span class="muted">{esc(days(g["created_at"]))}</span>'
    w = where(c, g)
    nxt = w["next"]
    if not nxt:
        head, text = rest(w)
        return f'<span class="muted">{esc(head.lower())}</span>'
    age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(nxt["since"])).days if nxt["since"] else 0
    cls = "late" if age >= 7 else "muted"
    return f'{esc(nxt["todo"])} <span class="{cls}">{esc(days(nxt["since"]))}</span>'


def host_line(c, g):
    """The guest's standing on their page on the engine: done, next, and for how long."""
    w = where(c, g)
    nxt = w["next"]
    if nxt and nxt["since"]:
        return esc(f"{summary(w)} Next: {nxt['todo']}, since {when(nxt['since'], DAY)} ({days(nxt['since'])}).")
    return esc(f"{summary(w)} {sentence(w, html=False)}")
