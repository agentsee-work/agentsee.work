"""
Scheduling (AGE-109, AGE-155, AGE-158): the guest picks the time they would
prefer and up to two backups, in their own time zone with London's beside it;
the engine checks the rules; each host ticks every one of the times they can
do, on the engine, in one go; the guest's highest-ranked time both can do is
booked; and the confirmation carries a join link that opens the studio only
around the booked time.

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

The flow: a request holds the choices in the guest's order, gold, silver and
bronze; each host sees all of them as tiles on the guest's page and ticks every
one they can do, once (and can change the ticks until the other host has
answered). Both answered books the guest's highest-ranked choice both ticked,
or declines the request when there is none; a host who can do none declines it
at once, since the other's answer cannot change that; so does the office when
other bookings clash with every time a booking could still land on. The guest's
rank decides, never the hosts' preference: they are the guest. The guest hears
the outcome only, on their page and by email, with a calendar file on the
booking; the issue is told by the worker (linear.py): a comment at each turn,
and on the booking the due date and the label "booked". A host can cancel a
booking; the guest can withdraw a request the hosts have not answered.

Two recordings clash when their starts are less than a recording's length
apart, and only a clash is blocked. A guest picking afresh is not offered a
day that has a recording. A request that was already waiting when another
guest was booked on one of its days keeps that time: a second recording on a
day is the hosts' to accept or not, as a second in a week is. Its tile names
the other recording; a time that clashes is struck through and cannot be
ticked.

Until 18 November 2026 the hosts answered one choice at a time: `offered` was
the choice on the table and a no moved it on. A request from then still reads:
a host's old yes is a tick on the choice that was on offer, and the choices
before `offered` stay declined. On a booking `offered` is the choice booked.
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
RANKS = ("Preferred", "Backup", "Backup")  # the guest's three, as the tiles name them
MEDALS = ("🥇", "🥈", "🥉")  # gold, silver, bronze: the guest's order, which decides
WORDS_OF_RANK = ("gold", "silver", "bronze")  # the same, in a sentence
CHOICE_OF_RANK = ("first choice", "first backup", "second backup")  # and in the guest's own terms
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

Tick every one of them you can do, on the engine:

    {page}

Once you have both answered, {name}'s highest choice that suits you both
is booked. Nothing is booked until then."""

DECLINED_MAIL = """Hello {name},

None of the times you gave worked, sorry: {why}. Pick again, with a
week's notice or more, at

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
TILE_WORDS = {  # the same, in the two or three words a tile has room for (offer_tiles)
    "week": "one that week",
    "ask": "hard for Abrar",
    "notice": "with notice",
    "works": "works",
    "perfect": "perfect",
}


def london_date(t):
    t = t if isinstance(t, dt.datetime) else dt.datetime.fromisoformat(t)
    return t.astimezone(ledger.ZONE).date()


def taken_days(c, but=None):
    """The London dates with an accepted recording, each with its bookings in the day's order and their
    guests' names. `but` leaves one booking out of the count."""
    out = {}
    rows = c.execute(
        "SELECT b.*, g.name FROM booking b JOIN guest g ON g.id = b.guest WHERE b.state='booked' ORDER BY b.start_at"
    ).fetchall()
    for b in rows:
        if b["id"] != but:
            out.setdefault(london_date(b["start_at"]), []).append(b)
    return out


def busy_weeks(c, but=None):
    """The ISO weeks with a recording already: the rules prefer one a week, so the rest of such a week says ask us."""
    return {d.isocalendar()[:2] for d in taken_days(c, but)}


def clash(a, b, s=None):
    """Two starts clash when they are less than a recording's length apart: the one test, between a guest's
    own choices and between a choice and another recording."""
    return abs((a - b).total_seconds()) < 3600 * (s or settings())["length_hours"]


def clashing(t, taken, s=None):
    """The accepted recordings a start clashes with, in time order. A day can hold a second recording; the
    same hours cannot."""
    return [b for day in taken.values() for b in day if clash(t, dt.datetime.fromisoformat(b["start_at"]), s)]


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
    """The hosts' calendar on the engine: every accepted recording on its day, a day's two in the order they
    happen, and every open request on each of the days it asks for, with the guest's medal for that day, each
    to the guest's page."""
    today = dt.datetime.now(ledger.ZONE).date()
    booked, asked = {}, {}
    rows = c.execute(
        "SELECT b.*, g.name FROM booking b JOIN guest g ON g.id = b.guest WHERE b.state IN ('booked', 'asked') "
        "ORDER BY b.start_at, b.created_at"
    ).fetchall()
    for b in rows:
        if b["state"] == "booked":
            booked.setdefault(london_date(b["start_at"]), []).append(b)
        else:
            for i in open_choices(b):
                asked.setdefault(london_date(choices_of(b)[i]), []).append((b, i))

    def cell(d):
        parts = [f'<span class="num">{d.day}</span>']
        parts += [f'<a class="booked" href="/guests/{esc(b["guest"])}">{esc(b["name"])}</a>' for b in booked.get(d, [])]
        parts += [
            f'<a class="asked" href="/guests/{esc(b["guest"])}">{MEDALS[i]} {esc(b["name"])}?</a>'
            for b, i in asked.get(d, [])
        ]
        return "".join(parts)

    grids = "".join(month_grid(first, cell) for first in months_ahead(today))
    n = sum(len(v) for v in booked.values())
    body = (
        '<article class="lead"><h1>Calendar</h1><p class="deck">Every accepted recording on its day, and every '
        "request on each day it asks for, with the guest&#8217;s medal for that day and a question mark. The "
        "rules prefer one a week; a second in a week, or on a day when the two do not clash, is yours to accept "
        "or not.</p></article>" + section("Ahead", f"{n} booked", f'<div class="cal-hosts">{grids}</div>')
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


def open_choices(b):
    """The indices of the choices still in play: all of them, but for a request from before 18 November
    2026, where the choices before `offered` were declined one at a time."""
    return list(range(b["offered"] if b["state"] == "asked" else 0, len(json.loads(b["choices"]))))


def picks_of(b, host):
    """The choices a host ticked, as indices, or None while they have not answered. A host's answer from
    before 18 November 2026 was a yes or no to the one choice on offer: a yes reads as a tick on it."""
    a = answers_of(b).get(host)
    if a is None:
        return None
    if "picks" in a:
        return [int(i) for i in a["picks"]]
    return [b["offered"]] if a.get("yes") else []


def waiting_for(b):
    return [h for h in HOSTS if picks_of(b, h) is None]


def can_do(b, i):
    """The hosts who ticked choice `i`, in the hosts' order."""
    return [h for h in HOSTS if i in (picks_of(b, h) or [])]


def ranks(picks):
    """Ticks in a sentence: "gold and bronze"."""
    words = [WORDS_OF_RANK[i] for i in sorted(picks)]
    return " and ".join(words) if len(words) < 3 else f"{words[0]}, {words[1]} and {words[2]}"


def hopeless(c, b, taken=None):
    """True when no booking can come of the request as it stands: every open choice clashes with another
    recording by the clock, or a host who has answered has no tick left on one that does not. A choice on a
    day with another recording, clear of its hours, is still the hosts' to accept."""
    taken = taken_days(c) if taken is None else taken
    choices, s = choices_of(b), settings()
    free = [i for i in open_choices(b) if not clashing(choices[i], taken, s)]
    if not free:
        return True
    return any(p is not None and not any(i in free for i in p) for p in (picks_of(b, h) for h in HOSTS))


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
    lines = [f"{MEDALS[i]} {describe(choices[i], b['tz'])}" for i in open_choices(b)]
    text = ASK_MAIL.format(
        name=g["name"], first=lines[0], backups="".join(f"\n    {x}" for x in lines[1:]), page=_page(g)
    )
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
        if any(clash(t, o, s) for o in chosen):
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
def answer(c, b, host, picks, who, note=None):
    """One host's answer: the choices they can do, by index; none means none of them. Both answered books the
    guest's highest-ranked choice both ticked, or declines the request when there is none; a host who can do
    none declines it at once, since the other's answer cannot change that. A host can answer again until the
    other has. Returns the sentence for the screen."""
    if host not in HOSTS:
        raise ScheduleError(f"This engine's hosts are {' and '.join(HOSTS)}; you are {who}")
    if b["state"] != "asked":
        raise ScheduleError(f"Nothing to answer: the request is {b['state']}")
    g, choices, taken, s = _guest(c, b["guest"]), choices_of(b), taken_days(c), settings()
    picks, opened = sorted({int(i) for i in picks}), open_choices(b)
    for i in picks:
        if i not in opened:
            raise ScheduleError(f"That is not one of {g['name'].split()[0]}'s times")
        if clashing(choices[i], taken, s):
            raise ScheduleError(f"{describe(choices[i], b['tz'])} clashes with another recording since; untick it")
    note = " ".join((note or "").split())[:200]
    answers = answers_of(b)
    answers[host] = {"picks": picks, "at": ledger.now(), "note": note}
    b = _update(c, b["id"], answers=json.dumps(answers))
    ledger.event(
        c,
        "booking:answered",
        who,
        detail={"booking": b["id"], "host": host, "picks": picks, "note": note},
        guest=g["id"],
    )
    first = g["name"].split()[0]
    if not picks:
        why = f"{host} could not make any of them" + (f" ({note})" if note else "")
        return _decline(c, b, g, who, why, by=host, note=note)
    left = waiting_for(b)
    if left:
        _tell(c, g, "answered", host=host, picks=ranks(picks), waiting=left, page=_page(g))
        return f"You can do {ranks(picks)}; waiting for {', '.join(left)}"
    common = [i for i in opened if len(can_do(b, i)) == len(HOSTS) and not clashing(choices[i], taken, s)]
    if common:
        book(c, b, g, who, common[0])
        return (
            f"Booked: {describe(choices[common[0]], b['tz'])}, {first}'s {CHOICE_OF_RANK[common[0]]}; "
            f"{first} has the confirmation"
        )
    could = "; ".join(f"{h} {ranks(picks_of(b, h))}" for h in HOSTS)
    return _decline(c, b, g, who, f"none of them suited both of us ({could})", note=note)


def _decline(c, b, g, who, why, by=None, note=None):
    """No booking can come of the request: it is declined, the guest is told why and asked to pick again, and
    so is the issue. `by` is the host who could do none, or "The office" when other bookings took the times."""
    first = g["name"].split()[0]
    b = _update(c, b["id"], state="declined", note=note or None)
    ledger.event(c, "booking:declined", who, detail={"booking": b["id"], "by": by, "why": why}, guest=g["id"])
    _tell(c, g, "declined", why=why, page=_page(g))
    text = DECLINED_MAIL.format(name=first, why=why, host_name=HOST)
    _mail(c, who, g["address"], "Your recording: another time, please", text, g["id"])
    return f"Declined: {why}; {first} is asked to pick again"


def _clear_clashes(c, b, who):
    """Other requests the booking leaves hopeless (every open choice clashing with a recording, or a host's
    every tick on one that does) are declined by the office, so no guest waits on a request that cannot be
    booked. A request with a choice clear of the clash stays, the same day included; the hosts see the
    clashing one struck through."""
    taken = taken_days(c)
    rows = c.execute("SELECT * FROM booking WHERE state='asked' AND id!=? ORDER BY created_at", (b["id"],)).fetchall()
    for o in rows:
        if hopeless(c, o, taken):
            why = "the times that could have worked were booked by another guest since"
            _decline(c, o, _guest(c, o["guest"]), who, why, by="The office", note=why)


def book(c, b, g, who, i=None):
    """Choice `i` is booked: `offered` names it from here on, the guest gets the confirmation with the calendar
    file and the join link, the issue its due date and label, and the requests the booking leaves hopeless go."""
    i = b["offered"] if i is None else i
    start = choices_of(b)[i]
    b = _update(c, b["id"], state="booked", start_at=start.isoformat(), offered=i)
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
        hosts=can_do(b, i),
        rank=CHOICE_OF_RANK[i],
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
    text += f". Tick the times you can do on the engine: {a['page']}"
    cm = linear.comment(env, a["issue"], text)
    return {"issue": a["issue"], "comment": cm["url"]}


def job_answered(env, a, c=None):
    text = f"{a['host']} can do {a['picks']}; waiting for {' and '.join(a['waiting'])}: {a['page']}"
    cm = linear.comment(env, a["issue"], text)
    return {"issue": a["issue"], "comment": cm["url"]}


def job_declined(env, a, c=None):
    text = f"Declined: {a['why']}; {linear.plain(a['name'])} is asked to pick again."
    cm = linear.comment(env, a["issue"], text)
    return {"issue": a["issue"], "comment": cm["url"]}


def job_moved(env, a, c=None):  # a turn of the flow before 18 November 2026, kept for a job still queued then
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
    rank = f", {linear.plain(a['name'])}'s {a['rank']}" if a.get("rank") else ""
    text = (
        f"Booked: {a['time']}{rank}, accepted by {' and '.join(a['hosts'])}. The join link {a['join']} opens the "
        "studio from an hour before the start to three hours after it, and shows the booking outside that."
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


def _day_word(t, taken, weeks, s, names=True):
    """The rules' word for a choice as the hosts read it: clashing with another recording, or on a day with
    one it is clear of (each named with its London time, but not on the guest's own copy), or the day's word
    with "one that week already" when the week has one."""
    hit, same = clashing(t, taken, s), taken.get(london_date(t))
    if hit or same:
        word = "clashes with another recording" if hit else "another recording that day"
        if names:
            word += ", " + " and ".join(
                f"at {dt.datetime.fromisoformat(o['start_at']).astimezone(ledger.ZONE):%H:%M} with {o['name']}"
                for o in hit or same
            )
        return ("taken" if hit else "day"), word
    kind = day_word(t, s)
    word = WORDS.get(kind, "")
    if london_date(t).isocalendar()[:2] in weeks:
        return "week", word + "; one that week already"
    return kind, word


def choices_table(c, b, hosts=False):
    """The request's times as a table: the guest's medal and rank, London, the guest's zone, the rules' word
    and, for the hosts, who can do each; the guest's own copy says only that the hosts have them."""
    choices, i, s = choices_of(b), b["offered"], settings()
    taken, weeks = taken_days(c, but=b["id"]), busy_weeks(c, but=b["id"])
    rows = []
    for n, t in enumerate(choices):
        kind, word = _day_word(t, taken, weeks, s, names=hosts)
        if b["state"] == "booked" and n == i:
            state = "<b>booked</b>"
        elif b["state"] == "cancelled" and b["start_at"] and n == i:
            state = "cancelled"
        elif b["state"] == "asked" and n < i:
            state = "declined"  # one at a time, before 18 November 2026
        elif not hosts:
            state = '<span class="muted">with James and Abrar</span>'
        else:
            who = can_do(b, n)
            yet = " yet" if b["state"] == "asked" and waiting_for(b) else ""
            state = esc(" and ".join(who) + " can") if who else f'<span class="muted">no one{yet}</span>'
        rows.append(
            [
                f"{MEDALS[n]} {esc(RANKS[n])}",
                esc(t.astimezone(ledger.ZONE).strftime("%a %d %b %Y, %H:%M")),
                esc(t.astimezone(zoneinfo.ZoneInfo(b["tz"])).strftime("%a %d %b, %H:%M") + " " + city(b["tz"])),
                esc(word),
                state,
            ]
        )
    return table(
        ["", "London", esc(city(b["tz"])) if b["tz"] != ledger.CONFIG.get("timezone") else "Yours", "Day", ""], rows
    )


def offer_tiles(c, b, host):
    """The request's choices as tiles for a host to tick (AGE-155), in the guest's order: the medal, the day in
    London with the time, the guest's own time and city when theirs is another zone, the rules' word, and who
    can do it so far. A choice that clashes with a recording booked since is struck through and cannot be
    ticked; one on the same day, clear of its hours, can, and says which recording that is. The tick is a
    checkbox the label carries, so the form needs no script."""
    choices, s = choices_of(b), settings()
    taken, weeks = taken_days(c, but=b["id"]), busy_weeks(c, but=b["id"])
    mine, theirs_too = picks_of(b, host) or [], b["tz"] != ledger.CONFIG.get("timezone")
    out = []
    for i in open_choices(b):
        t = choices[i]
        kind, word = _day_word(t, taken, weeks, s)
        london, theirs = t.astimezone(ledger.ZONE), t.astimezone(zoneinfo.ZoneInfo(b["tz"]))
        others = [h for h in can_do(b, i) if h != host]
        box = (
            f'<input type="checkbox" name="pick{i}" value="1"'
            + (" checked" if i in mine else "")
            + (" disabled" if kind == "taken" else "")
            + ">"
        )
        out.append(
            f'<label class="offer {kind}" title="{esc(word)}">{box}<span class="medal">{MEDALS[i]}</span>'
            '<span class="tick">&#10003;</span>'
            f'<span class="rank">{esc(RANKS[i])}</span>'
            f'<span class="wday">{london.strftime("%A")}</span>'
            f'<span class="dom">{london.day}</span>'
            f'<span class="month">{london.strftime("%B")}</span>'
            f'<span class="time">{london.strftime("%H:%M")} London</span>'
            + (
                f'<span class="theirs">{theirs.strftime("%a %-d %b, %H:%M")} {esc(city(b["tz"]))}</span>'
                if theirs_too
                else ""
            )
            + f'<span class="word">{esc(TILE_WORDS.get(kind, word))}</span>'
            + (f'<span class="who">{esc(" and ".join(others))} can</span>' if others else "")
            + "</label>"
        )
    return f'<div class="offers">{"".join(out)}</div>'


def time_body(c, g, msg=None, nxt=""):
    """Where the request or booking stands, and the form: the part of the page that changes, as one block the
    script puts back in place after a save. `nxt` is the hand-off to what comes next once a time is asked for
    or booked (progress.py, by way of guests.py), so it changes with the block."""
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
            f"<p>{go('/time/recording.ics', 'Add it to your calendar', 'signal')}</p>"
            '<p class="muted">If the time needs to move, reply to the confirmation, or write to hello@agentsee.work.</p>',
        )
        return f'<div id="time">{body}{nxt}<p>{go("/office", "Back to the office")}</p></div>'
    if b:  # asked
        body += section(
            "Asked",
            "With James and Abrar now",
            choices_table(c, b) + "<p>You will hear on this page and by email as soon as both have answered.</p>"
            '<form method="post" action="/time/withdraw" class="inline" data-live>'
            '<button type="submit">Withdraw and pick again</button></form>',
        )
        return f'<div id="time">{body}{nxt}<p>{go("/office", "Back to the office")}</p></div>'
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
        '<ol class="picks" data-chosen></ol><p class="muted" data-hint>Tap a day to begin.</p>'
        f"<noscript>{plain}</noscript>"
        '<input type="hidden" name="when0"><input type="hidden" name="when1"><input type="hidden" name="when2">'
        '<p><button type="submit" class="signal">Ask for these times</button></p></form>'
    )
    body += section("How we book", "The rules", rules_html(s)) + section("Pick", "Tap a day, then a time", form)
    body += f"<p>{go('/office', 'Back to the office')}</p>"
    return f'<div id="time">{body}</div>'


def time_page(c, g, msg=None, status=200, nxt=""):
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
        "prefer and up to two backups, in your own time zone; London&#8217;s is shown beside each. Both hosts say which "
        "they can do, your highest that suits both is booked, and you get a link that opens the studio when it is time.</p></article>"
    )
    return "The time", body + time_body(c, g, msg, nxt), status


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
    """The Booking section of a guest's page on the engine: the request as it stands and, while it is asked,
    the tiles the host ticks; the table once it is settled."""
    b = current(c, g["id"]) or latest(c, g["id"])
    if not b:
        return section(
            "Booking", "No request yet", "<p>The guest picks a time in the office once the release is signed.</p>"
        )
    gid, acts, inner = esc(g["id"]), "", ""
    first = g["name"].split()[0]
    if b["state"] == "asked" and host in HOSTS:
        mine, left = picks_of(b, host), ", ".join(waiting_for(b))
        if mine is None:
            lead = (
                f"Tick every time you can do, then accept. {esc(first)}&#8217;s highest that suits you both is booked."
            )
            verb = "Accept the ticked times"
        else:
            lead = f"You said {esc(ranks(mine))}; change it until {esc(left)} has answered."
            verb = "Change to the ticked times"
        inner = (
            f'<form method="post" action="/guests/{gid}/accept" class="answer"><input type="hidden" name="_t" value="{esc(csrf)}">'
            f"<p>{lead}</p>{offer_tiles(c, b, host)}"
            f'<p><button type="submit" class="signal">{verb}</button></p></form>'
        )
        acts = (
            f'<form method="post" action="/guests/{gid}/decline" class="inline"><input type="hidden" name="_t" value="{esc(csrf)}">'
            '<input type="text" name="note" placeholder="why not, in a few words (optional)" maxlength="200" class="why">'
            '<button type="submit">None of these</button></form>'
        )
    else:
        inner = choices_table(c, b, hosts=True)
    if b["state"] in ("asked", "booked") and host in HOSTS:
        acts += (
            f'<form method="post" action="/guests/{gid}/cancel" class="inline confirm"><input type="hidden" name="_t" value="{esc(csrf)}">'
            '<input type="text" name="note" placeholder="why, in a few words (optional)" maxlength="200" class="why">'
            f'<button type="submit">Cancel the {"booking" if b["state"] == "booked" else "request"}</button></form>'
        )
    head = {
        "asked": "Asked; " + ("waiting for " + ", ".join(waiting_for(b))),
        "booked": "Booked for " + describe(b["start_at"], b["tz"]) if b["start_at"] else "Booked",
    }.get(b["state"], b["state"].capitalize())
    inner = (
        f'<p class="muted">{esc(STATES.get(b["state"], ""))}. Asked {esc(when(b["created_at"]))}, in {esc(b["tz"])}.</p>'
        + inner
    )
    if b["state"] == "booked":
        inner += f'<p>Join link <span class="mono">{esc(join_url(b))}</span>: the studio from an hour before to three hours after.</p>'
    if b["note"]:
        inner += f"<p>Note: {esc(b['note'])}</p>"
    return section("Booking", head, inner + f'<div class="actions">{acts}</div>')
