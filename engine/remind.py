"""
The office's reminders (AGE-154): who has stopped, for how long, and the
letter that reaches them. Once a day, on a weekday, after `remind_at`
London (config.json), the worker reads every guest the way the office does
(progress.py) and writes to the ones whose step has been theirs for a
while and who have heard nothing from us in that while. The sending is in
guests.py (`remind_all`), which owns the mail and the issue; this module
decides and composes, and nothing in it touches the network.

The rule, with its numbers in config.json under "remind":

  after_days [3, 7]   the quiet days before each note: the first after three,
                      the second after seven more, and then no more from the
                      engine; the Guests screen marks a week or more as late
                      and the hosts decide. The list's length is the cap.
  door_days  [7, 14]  the same for the door, slower: an invitation unopened
                      for three days is not yet a stop.
  weekdays   true     the office keeps office hours; nobody is nudged on a
                      Sunday.

Quiet means no mail of any kind from us to that guest in the window, so a
hand nudge, a draft mail or an invitation sent again all count as having
spoken. A step re-entered (a time that fell through) starts the count
again, since the count is of notes about that step since it became theirs.
The hand button on the Guests screen sends the same letter, under the same
kind, so a host's note is one of the two; the button itself is never
capped. Nothing goes to a guest whose next step is with the hosts, who is
booked and waiting for the recording, who has approved, or who was
withdrawn; nor to one with no address, nor to one who was never emailed
the invitation (the hosts handed the link some other way, and mail would be
a surprise).

The stages are the four steps and one before them, the door: a guest who
was invited by mail and has not come through. Each letter is about where
the guest stands, in the office's voice, and asks whether anything is
unclear or in the way; a reply reaches James and Abrar.
"""

import datetime as dt
import textwrap

import idea
import ledger
import progress
import review
import schedule
from pages import when

SETTINGS = {"after_days": [3, 7], "door_days": [7, 14], "weekdays": True}
STAGES = {"door": "the door", **{k: v.lower() for k, v in progress.NAMES.items()}}
STAGES["time"] = "a time to record"
DAY = progress.DAY

WIDTH = 72  # the letters are plain text; a paragraph is wrapped here, a URL stands on its own indented line
NBSP = "\xa0"

HELP = (
    "If anything is unclear or in the way, reply to this message; it reaches James and Abrar, who will sort it "
    "out or do the step with you. If the timing is wrong and you would rather leave it for now, a word to that "
    "effect is just as welcome."
)
FOOT = "The office at https://{host} shows where everything stands; it asks for this address and sends you a code."
FIRST = "A note from the office, in case it helps."
SECOND = "This is the office's second note, and its last; anything after it comes from James or Abrar in person."
SUBJECTS = {
    "door": "The invitation to the AgentSee podcast, again",
    "release": "The release for your AgentSee episode",
    "idea": "The idea for your AgentSee episode",
    "time": "A time to record your AgentSee episode",
    "review": "Your AgentSee episode, ready to watch",
}


def settings():
    return {**SETTINGS, **(ledger.CONFIG.get("remind") or {})}


def kind(stage):
    """The mail's kind in the ledger's record, `reminder:<stage>`, so the notes about one step can be counted."""
    return f"reminder:{stage}"


# ─── who ─────────────────────────────────────────────────────────────────────
def stage(c, g):
    """Where the guest stands for a reminder's purposes: the stage, when it became theirs, and the office's
    reading (None before the door); None when nothing is theirs to do or nothing should be sent."""
    if g["state"] == "revoked" or not g["address"]:
        return None
    if not g["claimed_at"]:
        if not mailed(c, g["id"], "invitation"):
            return None
        return "door", g["created_at"], None
    w = progress.where(c, g)
    nxt = w["next"]
    if not nxt or not nxt["since"]:
        return None
    return nxt["key"], nxt["since"], w


def mailed(c, gid, kind_, since=None):
    """Mail of one kind that went to the guest, newest first; from `since` when given."""
    q = "SELECT at FROM event WHERE guest=? AND what='mail:sent' AND detail LIKE ?"
    args = [gid, f'%"subject": "{kind_}"%']
    if since:
        q += " AND at>=?"
        args.append(since)
    return c.execute(q + " ORDER BY at DESC", args).fetchall()


def last_mail(c, gid):
    """When we last wrote to the guest, whatever it was; None if never."""
    r = c.execute("SELECT at FROM event WHERE guest=? AND what='mail:sent' ORDER BY at DESC LIMIT 1", (gid,)).fetchone()
    return r["at"] if r else None


def count(c, gid, stage_, since):
    """How many notes about this stage have gone since it became the guest's, a host's included."""
    return len(mailed(c, gid, kind(stage_), since))


def due(c, now=None):
    """Who gets a note now and about what: (guest, stage, since, n, where) with n the number this note would be.
    The weekday rule is the caller's (run), since a hand look at the list should not depend on the day."""
    cfg = settings()
    now = now or dt.datetime.now(dt.timezone.utc)
    out = []
    for g in c.execute("SELECT * FROM guest WHERE state!='revoked' AND address IS NOT NULL ORDER BY created_at"):
        st = stage(c, g)
        if not st:
            continue
        key, since, w = st
        after = cfg["door_days"] if key == "door" else cfg["after_days"]
        n = count(c, g["id"], key, since)
        if n >= len(after):
            continue
        start = max(since, last_mail(c, g["id"]) or since)
        if now - dt.datetime.fromisoformat(start) < dt.timedelta(days=after[n]):
            continue
        out.append((g, key, since, n + 1, w))
    return out


def office_hours(now=None):
    """Whether the office writes today: any day, or Monday to Friday when `weekdays` is set."""
    now = now or dt.datetime.now(ledger.ZONE)
    return not settings()["weekdays"] or now.astimezone(ledger.ZONE).weekday() < 5


def mark(c, g):
    """For the Guests screen: how many notes have gone about the guest's present stage, in a word; nothing if none."""
    st = stage(c, g)
    if not st:
        return ""
    n = count(c, g["id"], st[0], st[1])
    return "" if not n else f"reminded {('once', 'twice')[n - 1] if n <= 2 else f'{n} times'}"


# ─── the letter ──────────────────────────────────────────────────────────────
def compose(paragraphs):
    """Paragraphs into the letter's text: each wrapped at WIDTH, a line that starts indented (a URL) left alone.
    A date is held together with no-break spaces until it is wrapped, then reads as plain spaces."""
    return "\n\n".join(
        p if p.startswith("    ") else textwrap.fill(p, WIDTH).replace(NBSP, " ") for p in paragraphs if p
    )


def letter(c, g, stage_, since, n, w, host, inviter=None, by=None):
    """The subject and text of the n-th note to a guest at a stage; `by` names the host behind a hand note, whose
    opener is theirs, where the office's own second note says it is the last."""
    name = g["name"].split()[0]
    date = when(since, DAY).replace(" ", NBSP)
    url = f"    https://{host}/{stage_}"
    if stage_ == "door":
        return SUBJECTS["door"], compose(
            [
                f"Hello {name},",
                f"{inviter or 'Someone'} invited you to record an episode of the AgentSee podcast on {date}, and the "
                "office has not seen you yet. The first message may well have found your junk folder, so here it is "
                "again. The invitation is still open:",
                f"    https://{host}/i/{g['invite']}",
                "It asks for this email address and sends you a code; then the release, the idea and a time, each a "
                "few minutes, on any device.",
                "If the timing is wrong, or it is not for you, a one-line reply saying so is welcome. If this was "
                "never expected, ignore it; the link does nothing on its own.",
            ]
        )
    if stage_ == "release":
        body = [
            f"You came into the office on {date}, and the release has been waiting for you since: one page in plain "
            "words, already signed by James and Abrar, for you to read and sign. It is the one thing before the idea "
            "and the time.",
            url,
        ]
    elif stage_ == "idea":
        begun = idea.exchanges(c, g["id"]) > 0
        b = schedule.current(c, g["id"])
        if b and b["state"] == "booked":
            body = [
                f"Your recording is booked for {schedule.describe(b['start_at'], b['tz'])}. Before it, the office "
                "needs the idea fleshed out into the run-sheet James and Abrar record from: a short conversation, "
                f"about ten minutes, {'open where you left it' if begun else 'that starts when you do'}, at",
                url,
            ]
        elif begun:
            body = [
                "You began the conversation with the office and it is still open where you left it; the run-sheet "
                "James and Abrar record from is written when it finishes, and the rest takes about ten minutes.",
                url,
            ]
        else:
            body = [
                f"You signed the release on {date}; thank you. The next thing is the idea: a short conversation with "
                "the office, about ten minutes, that fleshes the episode out and writes the run-sheet James and Abrar "
                "record from. Nothing has moved since, which is fine, and this is a note in case it is not.",
                url,
            ]
    elif stage_ == "time":
        last = schedule.latest(c, g["id"])
        if last and last["state"] in ("declined", "cancelled"):
            lead = "The time you asked for did not work out, and nothing has been picked since; the calendar is open again."
        else:
            lead = (
                "Your run-sheet draft is written and with James and Abrar, and the one thing left before the "
                "recording is the time, which only you can pick."
            )
        body = [
            f"{lead} Pick the time you would prefer and up to two backups at",
            url,
            "Both hosts say which they can do, your highest that suits both is booked, and you get a link that opens the studio when it is time.",
        ]
    else:
        row = review.current(c, g["id"])
        body = [
            f"Round {row['round'] if row else 1} of your episode has been ready since {date}: the episode and the "
            "clips, to watch, note and approve. Nothing goes out until you have approved it, so it waits on you.",
            url,
        ]
    opener = f"A note from {by}, in case it helps." if by else SECOND if n >= 2 else FIRST
    return SUBJECTS[stage_], compose([f"Hello {name},", opener, *body, HELP, FOOT.format(host=host)])
