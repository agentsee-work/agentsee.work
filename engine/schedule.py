"""
Scheduling (AGE-109): the guest picks the time they would prefer and up to two
backups, in their own time zone with London's beside it; the engine checks the
rules; both hosts accept on the engine, one click each; and the confirmation
carries a join link that opens the studio only around the booked time.

The engine is the scheduler. Riverside cannot create a session or a link from
outside (no API on our plan, none of the MCP's tools; checked 29 October
2026), and the session links it emails die a day after the start, so the
studio's standing guest link is a vault item (RIVERSIDE_GUEST_LINK, the
1Password item "Riverside podcast guest link", AGE-115 step 5) that the engine
reads at start and reveals through /join/<id> from an hour before the booked
start to three hours after it. Outside the window the link shows the booking.
The host admits the guest from the studio, as now.

The rules, as shown to the guest and kept in config.json ("schedule"): Monday
10:00 London is perfect; Monday and Tuesday work; Wednesday to Friday are hard
for Abrar and are shown as "ask us"; weekends work with reasonable notice. Two
hours, the latest start 16:00 so nothing runs past 18:00, and at least seven
days' notice. A day marked "ask us" is allowed and flagged; the hosts'
acceptance is the check, by decision (no calendar read, 29 October).

The flow: a request holds the choices in order and the one the hosts are
looking at; each host answers that one; both yes books it; one no moves to the
next choice and asks both again; no choice left declines it and the guest is
asked to pick again. The guest is told on their page and by email at every
turn, with a calendar file on the booking; the issue is told by the worker
(linear.py): a comment at each turn, and on the booking the due date and the
label "booked". A host can cancel a booking; the guest can withdraw a request
the hosts have not answered.
"""

import calendar
import datetime as dt
import json
import os
import secrets
import zoneinfo

import idea
import ledger
import linear
import mail
from pages import esc, go, link, section, table, when

HOSTS = ledger.CONFIG.get("hosts") or []
HOST = os.environ.get("ENGINE_GUEST_HOST") or (ledger.CONFIG.get("guest") or {}).get("host", "see.agentsee.work")
ENGINE = (ledger.CONFIG.get("engine") or {}).get("host", "engine.agentsee.work")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WORDS = {  # what a day's setting says to the guest
    "perfect": "perfect",
    "works": "works for us",
    "ask": "ask us; hard for Abrar",
    "notice": "works with reasonable notice",
}
SLOTS = ("The time you would prefer", "A backup", "Another backup")
STATES = {
    "asked": "the hosts are looking at it",
    "booked": "both hosts accepted; the join link is out",
    "declined": "no time worked; the guest is asked to pick again",
    "withdrawn": "the guest took it back before an answer",
    "cancelled": "a host cancelled it",
}

ASK_MAIL = """Hello,

{name} asked to record on

    {first}{backups}

Accept or decline, one click each, on the engine:

    {page}

The first choice goes to you both; if either of you declines it, the next
is offered. Nothing is booked until both of you have accepted one."""

MOVED_MAIL = """Hello {name},

{host} cannot make {time}{note}. The next time you gave is with
James and Abrar now:

    {next}

You will hear as soon as both have answered. Your page shows where it
stands: https://{host_name}/time"""

DECLINED_MAIL = """Hello {name},

None of the times you gave worked, sorry; {host} could not make
{time}{note}. Pick again, with a week's notice or more, at

    https://{host_name}/time

Monday 10:00 London is the easiest for us; Monday and Tuesday work; Wednesday
to Friday are harder. Weekends work with notice."""

BOOKED_MAIL = """Hello {name},

Booked: you record with James and Abrar on

    {time}

Two hours, over a video link. Your link to the studio:

    {join}

It opens the studio from an hour before the start to three hours after it;
before then it shows this booking. A calendar file is attached. Have a quiet
room, headphones and, if you can, a wired connection; a laptop with a camera
is better than a phone.

If the time needs to move, reply to this message."""

CANCELLED_MAIL = """Hello {name},

{host} had to cancel the recording on {time}{note}, sorry. Pick another
time, with a week's notice or more, at

    https://{host_name}/time"""


class ScheduleError(Exception):
    pass


# ─── the rules ───────────────────────────────────────────────────────────────
def settings():
    s = {
        "notice_days": 7,
        "length_hours": 2,
        "earliest_start": "08:00",
        "latest_start": "16:00",
        "perfect": {"day": "mon", "time": "10:00"},
        "days": {
            "mon": "works",
            "tue": "works",
            "wed": "ask",
            "thu": "ask",
            "fri": "ask",
            "sat": "notice",
            "sun": "notice",
        },
        "join_before_minutes": 60,
        "join_after_hours": 3,
    }
    s.update(ledger.CONFIG.get("schedule") or {})
    return s


def city(tz):
    return tz.rsplit("/", 1)[-1].replace("_", " ")


def parse(value, tz):
    """A datetime-local value in the guest's zone, as an aware UTC datetime."""
    try:
        t = dt.datetime.fromisoformat((value or "").strip())
    except ValueError:
        raise ScheduleError("A day and a time, as the field asks") from None
    zone = zoneinfo.ZoneInfo(tz)
    t = t.astimezone(zone) if t.tzinfo else t.replace(tzinfo=zone)
    return t.replace(second=0, microsecond=0).astimezone(dt.timezone.utc)


def refused(t, now=None, s=None):
    """Why a start cannot be asked for, or None."""
    s = s or settings()
    now = now or dt.datetime.now(dt.timezone.utc)
    if t < now + dt.timedelta(days=s["notice_days"]):
        return f"at least {s['notice_days']} days from today, please"
    hhmm = t.astimezone(ledger.ZONE).strftime("%H:%M")
    if not (s["earliest_start"] <= hhmm <= s["latest_start"]):
        end = f"{int(s['latest_start'][:2]) + s['length_hours']:02d}:{s['latest_start'][3:]}"
        return f"a start between {s['earliest_start']} and {s['latest_start']} London, so nothing runs past {end}"
    return None


def day_word(t, s=None):
    """What the rules say about this start: perfect, works, ask or notice."""
    s = s or settings()
    local = t.astimezone(ledger.ZONE)
    day = DAYS[local.weekday()]
    perfect = s.get("perfect") or {}
    if day == perfect.get("day") and local.strftime("%H:%M") == perfect.get("time"):
        return "perfect"
    return (s.get("days") or {}).get(day, "works")


def describe(t, tz, date=True):
    """A start in London's time and the guest's: "Mon 16 Nov 2026, 10:00 London (Mon 16 Nov, 21:00 Sydney)"."""
    t = t if isinstance(t, dt.datetime) else dt.datetime.fromisoformat(t)
    london = t.astimezone(ledger.ZONE).strftime("%a %d %b %Y, %H:%M" if date else "%H:%M") + " London"
    if tz == ledger.CONFIG.get("timezone"):
        return london
    theirs = t.astimezone(zoneinfo.ZoneInfo(tz)).strftime("%a %d %b, %H:%M" if date else "%H:%M")
    return f"{london} ({theirs} {city(tz)})"


def join_url(b):
    return f"https://{HOST}/join/{b['id']}"


def window(b, s=None):
    """When the join link opens the studio: (from, until), aware UTC."""
    s = s or settings()
    start = dt.datetime.fromisoformat(b["start_at"])
    return start - dt.timedelta(minutes=s["join_before_minutes"]), start + dt.timedelta(hours=s["join_after_hours"])


# ─── the calendar ────────────────────────────────────────────────────────────
MONTHS = 3  # the calendar's reach: this month and the two after it
TITLES = {  # a day's word on the calendar, as its title
    "gone": "past",
    "soon": "too soon: we ask for a week's notice",
    "taken": "taken: a recording that day",
    "week": "ask us: we have one that week already",
    "ask": "ask us: hard for Abrar",
    "notice": "works, with reasonable notice",
    "works": "works for us",
}


def london_date(t):
    t = t if isinstance(t, dt.datetime) else dt.datetime.fromisoformat(t)
    return t.astimezone(ledger.ZONE).date()


def taken_days(c, but=None):
    """The London dates with an accepted recording, each with its booking: one recording a day. `but` leaves
    one booking out of the count."""
    out = {}
    for b in c.execute("SELECT * FROM booking WHERE state='booked' ORDER BY start_at").fetchall():
        if b["id"] != but:
            out[london_date(b["start_at"])] = b
    return out


def busy_weeks(c, but=None):
    """The ISO weeks with a recording already: the rules prefer one a week, so the rest of such a week says ask us."""
    return {d.isocalendar()[:2] for d in taken_days(c, but)}


def day_state(d, today, soonest, taken, weeks, s=None):
    """A day on the calendar: gone, soon (inside the notice), taken, week (one that week already), or the
    rules' word for the weekday: works, ask, notice."""
    s = s or settings()
    if d < today:
        return "gone"
    if d < soonest:
        return "soon"
    if d in taken:
        return "taken"
    if d.isocalendar()[:2] in weeks:
        return "week"
    return (s.get("days") or {}).get(DAYS[d.weekday()], "works")


def months_ahead(today, n=MONTHS):
    firsts, y, m = [], today.year, today.month
    for _ in range(n):
        firsts.append(dt.date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return firsts


def month_grid(first, cell):
    """One month as a table, Monday first; `cell(d)` renders a day's inside; the padding days are empty."""
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(first.year, first.month)
    rows = "".join(
        "<tr>" + "".join(f"<td>{cell(d) if d.month == first.month else ''}</td>" for d in week) + "</tr>"
        for week in weeks
    )
    head = "".join(f"<th>{w}</th>" for w in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))
    return (
        f'<table class="cal"><caption>{first.strftime("%B %Y")}</caption><thead><tr>{head}</tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )


def calendar_html(c, s=None):
    """The guest's calendar: every day with its word, and the days that can be asked for as buttons the script
    opens a sheet on, each carrying its date and London's offset from UTC that day, in minutes."""
    s = s or settings()
    today = dt.datetime.now(ledger.ZONE).date()
    soonest = london_date(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=s["notice_days"]))
    taken, weeks = taken_days(c), busy_weeks(c)

    def cell(d):
        st = day_state(d, today, soonest, taken, weeks, s)
        if st in ("gone", "soon", "taken"):
            return f'<span class="day {st}" title="{esc(TITLES[st])}">{d.day}</span>'
        off = int(dt.datetime(d.year, d.month, d.day, 12, tzinfo=ledger.ZONE).utcoffset().total_seconds() // 60)
        return (
            f'<button type="button" class="day {st}" data-day="{d.isoformat()}" data-offset="{off}" '
            f'title="{esc(TITLES[st])}">{d.day}</button>'
        )

    return "".join(month_grid(first, cell) for first in months_ahead(today))


def calendar_page(c):
    """The hosts' calendar on the engine: every accepted recording on its day, and every open request on the
    day it is waiting for, each to the guest's page."""
    today = dt.datetime.now(ledger.ZONE).date()
    booked, asked = {}, {}
    rows = c.execute(
        "SELECT b.*, g.name FROM booking b JOIN guest g ON g.id = b.guest WHERE b.state IN ('booked', 'asked') "
        "ORDER BY b.created_at"
    ).fetchall()
    for b in rows:
        if b["state"] == "booked":
            booked.setdefault(london_date(b["start_at"]), []).append(b)
        else:
            asked.setdefault(london_date(choices_of(b)[b["offered"]]), []).append(b)

    def cell(d):
        parts = [f'<span class="num">{d.day}</span>']
        parts += [f'<a class="booked" href="/guests/{esc(b["guest"])}">{esc(b["name"])}</a>' for b in booked.get(d, [])]
        parts += [f'<a class="asked" href="/guests/{esc(b["guest"])}">{esc(b["name"])}?</a>' for b in asked.get(d, [])]
        return "".join(parts)

    grids = "".join(month_grid(first, cell) for first in months_ahead(today))
    n = sum(len(v) for v in booked.values())
    body = (
        '<article class="lead"><h1>Calendar</h1><p class="deck">Every accepted recording on its day, and every '
        "request on the day it is waiting for, with a question mark. One recording a day; the rules prefer one a "
        "week, and a second in a week is yours to accept or not.</p></article>"
        + section("Ahead", f"{n} booked", f'<div class="cal-hosts">{grids}</div>')
    )
    return "Calendar", body


# ─── the ledger ──────────────────────────────────────────────────────────────
def booking(c, bid):
    return c.execute("SELECT * FROM booking WHERE id=?", (bid,)).fetchone()


def current(c, gid):
    """The guest's live request or booking, or None."""
    return c.execute(
        "SELECT * FROM booking WHERE guest=? AND state IN ('asked', 'booked') ORDER BY created_at DESC LIMIT 1", (gid,)
    ).fetchone()


def latest(c, gid):
    return c.execute("SELECT * FROM booking WHERE guest=? ORDER BY created_at DESC LIMIT 1", (gid,)).fetchone()


def bookings(c, gid):
    return c.execute("SELECT * FROM booking WHERE guest=? ORDER BY created_at", (gid,)).fetchall()


def _guest(c, gid):
    return c.execute("SELECT * FROM guest WHERE id=?", (gid,)).fetchone()


def _update(c, bid, **fields):
    fields["updated_at"] = ledger.now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    c.execute(f"UPDATE booking SET {sets} WHERE id = ?", (*fields.values(), bid))
    return booking(c, bid)


def choices_of(b):
    return [dt.datetime.fromisoformat(x) for x in json.loads(b["choices"])]


def answers_of(b):
    return json.loads(b["answers"] or "{}")


def waiting_for(b):
    a = answers_of(b)
    return [h for h in HOSTS if not (a.get(h) or {}).get("yes")]


def _mail(c, who, to, subject, text, gid, attachments=(), copy=False):
    """A message the flow goes on without: a failure is recorded against the guest, not raised."""
    try:
        msgid = mail.send(to, subject, text, attachments=attachments, copy=copy)
    except mail.MailError as e:
        mail.record(c, who, to, subject, error=e, guest=gid)
        return None
    mail.record(c, who, to, subject, msgid, guest=gid)
    return msgid


def _tell(c, g, kind, **args):
    if not g["issue"]:
        return None
    return linear.enqueue(
        c, kind, {"issue": g["issue"], "name": g["name"], "when": when(ledger.now()), **args}, g["id"]
    )


def _page(g):
    return f"https://{ENGINE}/guests/{g['id']}"


def _ask_hosts(c, g, b, who):
    choices = choices_of(b)
    i = b["offered"]
    backups = "".join(f"\n    {describe(t, b['tz'])}" for t in choices[i + 1 :])
    text = ASK_MAIL.format(name=g["name"], first=describe(choices[i], b["tz"]), backups=backups, page=_page(g))
    _mail(c, who, mail.settings()["copy_to"], f"A time to accept: {g['name']}", text, g["id"])


# ─── the guest asks ──────────────────────────────────────────────────────────
def ask(c, g, tz, values, who):
    """The guest's request: up to three starts, the first required, each checked against the rules,
    in their zone. A request the hosts have not answered is replaced; a booking is not."""
    if not g["signed_version"]:
        raise ScheduleError("Sign the release first; then a time")
    live = current(c, g["id"])
    if live and live["state"] == "booked":
        raise ScheduleError(
            "You have a booking; reply to its confirmation, or write to hello@agentsee.work, to move it"
        )
    tz = (tz or "").strip()
    if tz not in zoneinfo.available_timezones():
        raise ScheduleError("That is not a time zone the office knows")
    s, now, chosen, taken = settings(), dt.datetime.now(dt.timezone.utc), [], taken_days(c)
    for n, v in enumerate(values[:3]):
        if not (v or "").strip():
            if n == 0:
                raise ScheduleError("A day and a time for the slot you would prefer")
            continue
        t = parse(v, tz)
        why = refused(t, now, s)
        if why:
            raise ScheduleError(f"{SLOTS[n]}: {why}")
        if london_date(t) in taken:
            raise ScheduleError(f"{SLOTS[n]}: that day is taken by a recording already; another day, please")
        if any(abs((t - o).total_seconds()) < 3600 * s["length_hours"] for o in chosen):
            raise ScheduleError(f"{SLOTS[n]} overlaps another of your times; a backup is a different time")
        chosen.append(t)
    if live:
        withdraw(c, live, g, who, replaced=True)
    bid, at = secrets.token_urlsafe(12), ledger.now()
    c.execute(
        "INSERT INTO booking (id, guest, tz, choices, offered, answers, state, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (bid, g["id"], tz, json.dumps([t.isoformat() for t in chosen]), 0, "{}", "asked", at, at),
    )
    if tz != g["tz"]:
        c.execute("UPDATE guest SET tz=?, updated_at=? WHERE id=?", (tz, at, g["id"]))
    ledger.event(
        c,
        "booking:asked",
        who,
        detail={"booking": bid, "tz": tz, "choices": [describe(t, tz) for t in chosen]},
        guest=g["id"],
    )
    b = booking(c, bid)
    _tell(
        c,
        g,
        "asked",
        first=describe(chosen[0], tz),
        backups=[describe(t, tz) for t in chosen[1:]],
        words=[day_word(t, s) for t in chosen],
        page=_page(g),
    )
    _ask_hosts(c, g, b, who)
    return b


def withdraw(c, b, g, who, replaced=False):
    if b["state"] != "asked":
        raise ScheduleError("Only a request the hosts have not answered is withdrawn")
    b = _update(c, b["id"], state="withdrawn")
    ledger.event(c, "booking:withdrawn", who, detail={"booking": b["id"], "replaced": replaced}, guest=g["id"])
    if not replaced:
        _tell(c, g, "withdrawn", time=describe(choices_of(b)[b["offered"]], b["tz"]))
    return b


# ─── the hosts answer ────────────────────────────────────────────────────────
def answer(c, b, host, yes, who, note=None):
    """One host's answer to the choice on offer. Both yes books it; a no moves to the next choice,
    or declines the request when none is left. Returns the sentence for the screen."""
    if host not in HOSTS:
        raise ScheduleError(f"This engine's hosts are {' and '.join(HOSTS)}; you are {who}")
    if b["state"] != "asked":
        raise ScheduleError(f"Nothing to answer: the request is {b['state']}")
    answers = answers_of(b)
    if host in answers:
        raise ScheduleError(f"You answered this time already ({'yes' if answers[host]['yes'] else 'no'})")
    g, choices, i = _guest(c, b["guest"]), choices_of(b), b["offered"]
    note = " ".join((note or "").split())[:200]
    if yes and london_date(choices[i]) in taken_days(c):
        raise ScheduleError(
            f"{describe(choices[i], b['tz'])} is taken by another recording since; decline it, and the next time is offered"
        )
    answers[host] = {"yes": bool(yes), "at": ledger.now(), "note": note}
    b = _update(c, b["id"], answers=json.dumps(answers))
    ledger.event(
        c,
        "booking:answered",
        who,
        detail={"booking": b["id"], "host": host, "yes": bool(yes), "choice": i, "note": note},
        guest=g["id"],
    )
    if yes:
        left = waiting_for(b)
        if left:
            return f"Accepted; waiting for {', '.join(left)}"
        book(c, b, g, who)
        return f"Booked: {describe(choices[i], b['tz'])}; {g['name'].split()[0]} has the confirmation"
    return _move_on(c, b, g, host, who, note)


def _move_on(c, b, g, host, who, note=None):
    """The time on offer is declined, by a host or by the office when another booking took its day: the next
    choice goes to both hosts, or the request is declined when none is left; the guest is told either way."""
    choices, i, first = choices_of(b), b["offered"], g["name"].split()[0]
    note = " ".join((note or "").split())[:200]
    said, time = f" ({note})" if note else "", describe(choices[i], b["tz"])
    if i + 1 < len(choices):
        b = _update(c, b["id"], offered=i + 1, answers="{}", note=note or None)
        ledger.event(c, "booking:moved", who, detail={"booking": b["id"], "to": i + 1, "by": host}, guest=g["id"])
        nxt = describe(choices[i + 1], b["tz"])
        _tell(c, g, "moved", host=host, time=time, note=note, next=nxt, page=_page(g))
        text = MOVED_MAIL.format(name=first, host=host, time=time, note=said, next=nxt, host_name=HOST)
        _mail(c, who, g["address"], "Your recording: the next time", text, g["id"])
        _ask_hosts(c, g, b, who)
        return f"Declined; the next time, {nxt}, is offered to you both"
    b = _update(c, b["id"], state="declined", note=note or None)
    ledger.event(c, "booking:declined", who, detail={"booking": b["id"], "by": host}, guest=g["id"])
    _tell(c, g, "moved", host=host, time=time, note=note, next=None, page=_page(g))
    text = DECLINED_MAIL.format(name=first, host=host, time=time, note=said, host_name=HOST)
    _mail(c, who, g["address"], "Your recording: another time, please", text, g["id"])
    return f"Declined; no backup left, so {first} is asked to pick again"


def _clear_clashes(c, b, who):
    """Other requests whose time on offer falls on the day just booked are moved on, as if the office had
    declined them, so no two guests wait on one day: one recording a day."""
    day = london_date(b["start_at"])
    rows = c.execute("SELECT * FROM booking WHERE state='asked' AND id!=? ORDER BY created_at", (b["id"],)).fetchall()
    for o in rows:
        if london_date(choices_of(o)[o["offered"]]) == day:
            _move_on(c, o, _guest(c, o["guest"]), "The office", who, "that day was booked by another guest")


def book(c, b, g, who):
    start = choices_of(b)[b["offered"]]
    b = _update(c, b["id"], state="booked", start_at=start.isoformat())
    ledger.event(c, "booking:booked", who, detail={"booking": b["id"], "start": start.isoformat()}, guest=g["id"])
    text = BOOKED_MAIL.format(name=g["name"].split()[0], time=describe(start, b["tz"]), join=join_url(b))
    _mail(
        c,
        who,
        g["address"],
        "Booked: your recording with AgentSee",
        text,
        g["id"],
        attachments=[("agentsee-recording.ics", ics(b, g).encode("utf-8"), "text/calendar")],
        copy=True,
    )
    _tell(
        c,
        g,
        "booked",
        time=describe(start, b["tz"]),
        date=start.astimezone(ledger.ZONE).strftime("%Y-%m-%d"),
        hosts=[h for h in HOSTS if (answers_of(b).get(h) or {}).get("yes")],
        join=join_url(b),
    )
    _clear_clashes(c, b, who)
    return b


def cancel(c, b, host, who, note=None):
    """A host cancels a booking, or a request still on offer; the guest is told and asked to pick again."""
    if host not in HOSTS:
        raise ScheduleError(f"This engine's hosts are {' and '.join(HOSTS)}; you are {who}")
    if b["state"] not in ("asked", "booked"):
        raise ScheduleError(f"Nothing to cancel: it is {b['state']}")
    g = _guest(c, b["guest"])
    note = " ".join((note or "").split())[:200]
    was, start = b["state"], b["start_at"] or choices_of(b)[b["offered"]].isoformat()
    b = _update(c, b["id"], state="cancelled", note=note or None)
    ledger.event(c, "booking:cancelled", who, detail={"booking": b["id"], "was": was, "note": note}, guest=g["id"])
    time = describe(start, b["tz"])
    text = CANCELLED_MAIL.format(
        name=g["name"].split()[0], host=host, time=time, note=f" ({note})" if note else "", host_name=HOST
    )
    _mail(c, who, g["address"], "Your recording: cancelled, another time please", text, g["id"])
    _tell(c, g, "unbooked", host=host, time=time, note=note, booked=was == "booked")
    return f"Cancelled; {g['name'].split()[0]} is asked to pick again"


# ─── the join link ───────────────────────────────────────────────────────────
def join(c, b, ip, ua, env=None):
    """("studio", url) inside the window, ("before"|"after"|"off", None) otherwise; every click an event."""
    env = os.environ if env is None else env
    g = _guest(c, b["guest"])
    if b["state"] != "booked":
        state = "off"
    else:
        opens, closes = window(b)
        now = dt.datetime.now(dt.timezone.utc)
        state = "before" if now < opens else "after" if now > closes else "studio"
    url = env.get("RIVERSIDE_GUEST_LINK") if state == "studio" else None
    ledger.event(
        c,
        "booking:join",
        g["address"] or "guest",
        detail={"booking": b["id"], "state": state, "studio": bool(url), "ip": ip, "ua": (ua or "")[:300]},
        guest=g["id"],
    )
    return state, url


def ics(b, g):
    """The booking as a calendar file, in UTC."""
    s = settings()
    start = dt.datetime.fromisoformat(b["start_at"]).astimezone(dt.timezone.utc)
    end = start + dt.timedelta(hours=s["length_hours"])
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AgentSee//Engine//EN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{b['id']}@agentsee.work",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",
        f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}",
        "SUMMARY:AgentSee podcast recording",
        f"DESCRIPTION:With {' and '.join(HOSTS)}. The studio opens from an hour before: {join_url(b)}",
        f"URL:{join_url(b)}",
        f"LOCATION:{join_url(b)}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"


# ─── Linear, through the worker ──────────────────────────────────────────────
def job_asked(env, a, c=None):
    text = f"{linear.plain(a['name'])} asked, through {linear.office()}, to record on {a['first']}"
    words = a.get("words") or []
    if words:
        text += f" ({WORDS.get(words[0], words[0])})"
    if a.get("backups"):
        text += "; backups " + "; ".join(
            f"{t} ({WORDS.get(w, w)})" for t, w in zip(a["backups"], words[1:] + [None] * 2) if t
        )
    text += f". Accept or decline on the engine: {a['page']}"
    cm = linear.comment(env, a["issue"], text)
    return {"issue": a["issue"], "comment": cm["url"]}


def job_moved(env, a, c=None):
    text = f"{a['host']} declined {a['time']}" + (f" ({a['note']})" if a.get("note") else "")
    text += (
        f"; {a['next']} is offered to both hosts now: {a['page']}"
        if a.get("next")
        else f"; no backup left, so {linear.plain(a['name'])} is asked to pick again."
    )
    cm = linear.comment(env, a["issue"], text)
    return {"issue": a["issue"], "comment": cm["url"]}


def job_booked(env, a, c=None):
    key = a["issue"]
    linear.due(env, key, a["date"])
    text = (
        f"Booked: {a['time']}, accepted by {' and '.join(a['hosts'])}. The join link {a['join']} opens the studio "
        "from an hour before the start to three hours after it, and shows the booking outside that."
    )
    cm = linear.comment(env, key, text)
    return {"issue": key, "comment": cm["url"], "due": a["date"], "label": linear.label_softly(env, key, "booked")}


def job_unbooked(env, a, c=None):
    key = a["issue"]
    what = "the booking for" if a.get("booked") else "the request for"
    text = f"{a['host']} cancelled {what} {a['time']}" + (f" ({a['note']})" if a.get("note") else "")
    text += f"; {linear.plain(a['name'])} is asked to pick again."
    cm = linear.comment(env, key, text)
    out = {"issue": key, "comment": cm["url"]}
    if a.get("booked"):
        linear.due(env, key, None)
        out["due"] = None
    return out


def job_withdrawn(env, a, c=None):
    cm = linear.comment(env, a["issue"], f"{linear.plain(a['name'])} withdrew their request for {a['time']}.")
    return {"issue": a["issue"], "comment": cm["url"]}


# ─── the guest's page ────────────────────────────────────────────────────────
def rules_html(s=None):
    s = s or settings()
    end = f"{int(s['latest_start'][:2]) + s['length_hours']:02d}:{s['latest_start'][3:]}"
    return (
        "<ul>"
        "<li>Monday 10:00 London is perfect. Monday and Tuesday work. Wednesday to Friday are hard for Abrar, "
        "so they are marked &#8220;ask us&#8221;. Weekends work with reasonable notice.</li>"
        f"<li>{s['length_hours']} hours. The latest start is {s['latest_start']} London, so nothing runs past {end}.</li>"
        f"<li>At least {s['notice_days']} days from today.</li>"
        "<li>Times are in your time zone; London&#8217;s is shown beside each once you have asked.</li></ul>"
    )


def choices_table(c, b):
    """The request's times as a table: London, the guest's, the rules' word, and where each stands."""
    choices, answers, i, s = choices_of(b), answers_of(b), b["offered"], settings()
    taken, weeks = taken_days(c, but=b["id"]), busy_weeks(c, but=b["id"])
    rows = []
    for n, t in enumerate(choices):
        if london_date(t) in taken:
            word = "taken by another recording"
        else:
            word = WORDS.get(day_word(t, s), "")
            if london_date(t).isocalendar()[:2] in weeks:
                word += "; one that week already"
        if b["state"] == "booked" and n == i:
            state = "<b>booked</b>"
        elif b["state"] in ("declined", "cancelled", "withdrawn") and n == i:
            state = b["state"]
        elif n < i:
            state = "declined"
        elif n == i:
            yes = [h for h, a in answers.items() if a.get("yes")]
            state = ("accepted by " + ", ".join(yes) + "; " if yes else "") + "waiting for " + ", ".join(waiting_for(b))
        else:
            state = '<span class="muted">if needed</span>'
        rows.append(
            [
                esc(("Preferred", "Backup", "Backup")[n]),
                esc(t.astimezone(ledger.ZONE).strftime("%a %d %b %Y, %H:%M")),
                esc(t.astimezone(zoneinfo.ZoneInfo(b["tz"])).strftime("%a %d %b, %H:%M") + " " + city(b["tz"])),
                esc(word),
                state,
            ]
        )
    return table(
        ["", "London", esc(city(b["tz"])) if b["tz"] != ledger.CONFIG.get("timezone") else "Yours", "Day", ""], rows
    )


def time_body(c, g, msg=None):
    """Where the request or booking stands, and the form: the part of the page that changes, as one block the
    script puts back in place after a save."""
    s, b, last = settings(), current(c, g["id"]), latest(c, g["id"])
    tz = (b["tz"] if b else None) or g["tz"] or ledger.CONFIG.get("timezone") or "Europe/London"
    body = f'<p class="said honesty" data-said><b>{esc(msg)}</b></p>' if msg else ""
    if b and b["state"] == "booked":
        start = dt.datetime.fromisoformat(b["start_at"])
        body += section(
            "Booked",
            describe(start, b["tz"]),
            f'<p>Your link to the studio: <span class="mono">{esc(join_url(b))}</span></p>'
            f"<p>It opens the studio from an hour before the start to {s['join_after_hours']} hours after it; before "
            "then it shows this booking. Have a quiet room, headphones and, if you can, a wired connection.</p>"
            f"<p>{go('/time/recording.ics', 'Add it to your calendar', 'signal')} {go('/office', 'Back to the office')}</p>"
            '<p class="muted">If the time needs to move, reply to the confirmation, or write to hello@agentsee.work.</p>',
        )
        return f'<div id="time">{body}</div>'
    if b:  # asked
        body += section(
            "Asked",
            "With James and Abrar now",
            choices_table(c, b) + "<p>You will hear on this page and by email as soon as both have answered.</p>"
            '<form method="post" action="/time/withdraw" class="inline" data-live>'
            '<button type="submit">Withdraw and pick again</button></form> ' + go("/office", "Back to the office"),
        )
        return f'<div id="time">{body}</div>'
    if last and last["state"] in ("declined", "cancelled"):
        why = "None of those times worked" if last["state"] == "declined" else "That recording was cancelled"
        body += (
            f'<p class="honesty"><b>{why}</b>'
            + (f": {esc(last['note'])}" if last["note"] else "")
            + ". Pick again below.</p>"
        )
    options = "".join(
        f'<option value="{esc(z)}"{" selected" if z == tz else ""}>{esc(z)}</option>' for z in idea.zones()
    )
    soonest = london_date(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=s["notice_days"])).isoformat()
    plain = "".join(  # without script: three plain fields, a day at the earliest and no grid of minutes
        f'<label for="when{n}">{esc(label)}</label>'
        f'<input type="datetime-local" id="when{n}" name="when{n}" min="{soonest}T00:00"{" required" if n == 0 else ""}>'
        for n, label in enumerate(SLOTS)
    )
    form = (
        '<form method="post" action="/time/ask" class="block times" data-live data-calendar '
        f'data-from="{esc(s["earliest_start"])}" data-to="{esc(s["latest_start"])}">'
        '<label for="tz">Your time zone</label>'
        f'<select id="tz" name="tz">{options}</select>'
        + calendar_html(c, s)
        + '<p class="muted legend">Plain days work '
        "for us; hatched, ask us; dashed, with notice; struck through, taken; faded, too soon.</p>"
        '<ol class="chosen" data-chosen></ol><p class="muted" data-hint>Tap a day to begin.</p>'
        f"<noscript>{plain}</noscript>"
        '<input type="hidden" name="when0"><input type="hidden" name="when1"><input type="hidden" name="when2">'
        '<p><button type="submit" class="signal">Ask for these times</button></p></form>'
    )
    body += section("How we book", "The rules", rules_html(s)) + section("Pick", "Tap a day, then a time", form)
    body += f"<p>{go('/office', 'Back to the office')}</p>"
    return f'<div id="time">{body}</div>'


def time_page(c, g, msg=None, status=200):
    """The guest's page: the rules, where their request or booking stands, and the form."""
    if not g["signed_version"]:
        note = f'<p class="honesty"><b>{esc(msg)}</b></p>' if msg else ""
        body = (
            '<article class="lead"><p class="kicker">The time</p><h1>After the release</h1>'
            f'<p class="deck">Sign the release first; then pick a time here.</p></article>{note}'
            f"<p>{go('/release', 'The release', 'signal')} {go('/office', 'Back to the office')}</p>"
        )
        return "The time", body, status
    s = settings()
    body = (
        '<article class="lead"><p class="kicker">The time</p><h1>A time to record</h1>'
        f'<p class="deck">{s["length_hours"]} hours over a video link, with James and Abrar. Pick the time you would '
        "prefer and up to two backups, in your own time zone; London&#8217;s is shown beside each. Both hosts accept, "
        "one click each, and you get a link that opens the studio when it is time.</p></article>"
    )
    return "The time", body + time_body(c, g, msg), status


def join_page(c, b, state):
    """What the join link shows when it does not open the studio."""
    g = _guest(c, b["guest"])
    first = g["name"].split()[0]
    if state == "off":
        head, text = "No booking here", "This link belonged to a booking that was cancelled or withdrawn. "
        text += f"Pick a time at {link(f'https://{HOST}/time', 'the office')}."
    elif state == "before":
        head = "Not yet"
        text = (
            f"Hello {esc(first)}. Your recording is {esc(describe(b['start_at'], b['tz']))}. This link opens the studio "
            "from an hour before the start; come back then, on this device or any other."
        )
    else:
        head = "That recording has been"
        text = f"It was {esc(describe(b['start_at'], b['tz']))}. If something went wrong, write to hello@agentsee.work."
    body = f'<article class="lead"><p class="kicker">The studio</p><h1>{esc(head)}</h1><p class="deck">{text}</p></article>'
    return "The studio", body


def step_line(c, g):
    """The office's line for step 3."""
    b = current(c, g["id"])
    if b and b["state"] == "booked":
        return f"Booked for {esc(describe(b['start_at'], b['tz']))}. {link('/time', 'The details and the join link')}"
    if b:
        t = choices_of(b)[b["offered"]]
        return f"Asked for {esc(describe(t, b['tz']))}; waiting for {esc(', '.join(waiting_for(b)))}. {link('/time', 'Your request')}"
    if g["signed_version"]:
        return link("/time", "Pick a time")
    return '<span class="muted">After the release.</span>'


# ─── the engine's side ───────────────────────────────────────────────────────
def summary(c, gid):
    """One cell for the Guests table."""
    b = current(c, gid)
    if not b:
        return ""
    if b["state"] == "booked":
        return esc(dt.datetime.fromisoformat(b["start_at"]).astimezone(ledger.ZONE).strftime("%a %d %b, %H:%M"))
    return f'<span class="muted">asked; waiting for {esc(", ".join(waiting_for(b)))}</span>'


def host_section(c, g, csrf, host):
    """The Booking section of a guest's page on the engine: the request as it stands, and the host's buttons."""
    b = current(c, g["id"]) or latest(c, g["id"])
    if not b:
        return section(
            "Booking", "No request yet", "<p>The guest picks a time in the office once the release is signed.</p>"
        )
    acts = ""
    if b["state"] == "asked" and host in HOSTS and host not in answers_of(b):
        acts = (
            f'<form method="post" action="/guests/{esc(g["id"])}/accept" class="inline"><input type="hidden" name="_t" value="{esc(csrf)}">'
            '<button type="submit" class="signal">Accept this time</button></form>'
            f'<form method="post" action="/guests/{esc(g["id"])}/decline" class="inline"><input type="hidden" name="_t" value="{esc(csrf)}">'
            '<input type="text" name="note" placeholder="why not, in a few words (optional)" maxlength="200" class="why">'
            '<button type="submit">Decline, offer the next</button></form>'
        )
    elif b["state"] == "asked" and host in HOSTS:
        acts = f'<p class="muted">You answered; waiting for {esc(", ".join(waiting_for(b)))}.</p>'
    if b["state"] in ("asked", "booked") and host in HOSTS:
        acts += (
            f'<form method="post" action="/guests/{esc(g["id"])}/cancel" class="inline confirm"><input type="hidden" name="_t" value="{esc(csrf)}">'
            '<input type="text" name="note" placeholder="why, in a few words (optional)" maxlength="200" class="why">'
            f'<button type="submit">Cancel the {"booking" if b["state"] == "booked" else "request"}</button></form>'
        )
    head = {
        "asked": "Asked; " + ("waiting for " + ", ".join(waiting_for(b))),
        "booked": "Booked for " + describe(b["start_at"], b["tz"]) if b["start_at"] else "Booked",
    }.get(b["state"], b["state"].capitalize())
    inner = f'<p class="muted">{esc(STATES.get(b["state"], ""))}. Asked {esc(when(b["created_at"]))}, in {esc(b["tz"])}.</p>'
    inner += choices_table(c, b)
    if b["state"] == "booked":
        inner += f'<p>Join link <span class="mono">{esc(join_url(b))}</span>: the studio from an hour before to three hours after.</p>'
    if b["note"]:
        inner += f"<p>Note: {esc(b['note'])}</p>"
    return section("Booking", head, inner + f'<div class="actions">{acts}</div>')
