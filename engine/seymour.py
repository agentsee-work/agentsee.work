"""
A Seymour of their own (AGE-159): a guest of the show claims one of the
eye's poses or one of his chains, and it is theirs for good.

The rule, in the ledger's `claim` table: one owner to a pose or chain (its
primary key), one pose or chain to a person (`owner` is unique), so two
people who pick the same one in the same second cannot both have it, and
nobody holds two. First come, first served. A claim follows the name, not
the numbers: the hosts may retune a pose under its owner. Nobody can take
one that is held; its owner gives it up by swapping it for one that is
free, at any time, and the one they leave is free again. Three poses and
six chains are the show's and are never offered (tools/eye.py RESERVED,
tools/eyeanim.py RESERVED_CHAINS, which the export carries).

Who claims, and where:

  a guest   on the office: in the review, under the episode and the clips
            and above the approval ("Pick your Seymour"), offered and never
            required; and at /seymour on that host, where they see theirs
            and swap it. A guest picks once their episode is there to
            watch, or once a host has given them one.
  a host    on the engine at /seymour, one each, by the same rule and with
            no head start; and for a guest from the guest's page, an
            assignment, which is a claim like any other with the host's
            name on it, and the guest's to swap. A host can release any
            claim, for an episode that never goes out.

Every claim, swap, assignment and release is an event (`seymour:claimed`,
`:swapped`, `:assigned`, `:released`): who, when, which, and what was left.

The board of faces is drawn here, as markup, from the export that the site's
own Seymour page plays (public/assets/seymour-data.js, read and drawn by
tools/site.py's seymour_* functions); public/assets/seymour-face.js, which
both hosts serve themselves, moves them. Without script each is a picture
of the pose and the form still works: an option, then the button.

What this module writes for the pipeline, in the archive: a guest's
`guests/<id>/claim.json`, beside their approval, which `episode.py notes`
and `approve` read into episode.json so the guest's Seymour is the eye of
their episode (tools/office.py, tools/show.py); and the whole board at
`seymour/claims.json`, which `episode.py claims` brings to the workstation
for the site. The guest's approval carries what they held when they signed
(review.py). A swap rewrites claim.json and never the approval, so an
episode that is out keeps the eye it was approved with.
"""

import json
import sqlite3

import archive
import ledger
import linear
import pages
from pages import button, esc, go, link, section, table, when

KINDS = ("pose", "chain")
BOARD_KEY = "seymour/claims.json"
WORD = {"pose": "pose", "chain": "chain"}


class SeymourError(Exception):
    pass


# ─── the poses and chains ────────────────────────────────────────────────────
def data():
    """The export: every pose and chain, and what is reserved (tools/eyeanim.py --export)."""
    return pages._site.seymour_data()


def reserved(kind, name):
    return name in (data()["reserved"].get(kind) or [])


def claimable():
    """(kind, item) for everything a person may claim, poses then chains, in the tables' order."""
    d = data()
    return [(k, item) for k in KINDS for item in d[k + "s"] if not reserved(k, item["name"])]


def parse(pick):
    """A form's pick, "pose:wry", as (kind, name); refused when it is not one on the board."""
    kind, _, name = str(pick or "").partition(":")
    if kind not in KINDS or name not in data()[kind]:
        raise SeymourError("Pick a pose or a chain from the board")
    return kind, name


# ─── who holds what ──────────────────────────────────────────────────────────
def claims(c):
    return c.execute("SELECT * FROM claim ORDER BY at, kind, name").fetchall()


def holder(c, kind, name):
    return c.execute("SELECT * FROM claim WHERE kind=? AND name=?", (kind, name)).fetchone()


def of_guest(c, gid):
    return c.execute("SELECT * FROM claim WHERE owner=?", (f"guest:{gid}",)).fetchone()


def of_host(c, host):
    return c.execute("SELECT * FROM claim WHERE owner=?", (f"host:{host}",)).fetchone()


def hosts():
    return [str(h) for h in ledger.CONFIG.get("hosts") or []]


def shown(c, row):
    """The owner's name as others see it: the guest's as they want it shown, today; a host's first name."""
    if row["guest"]:
        g = c.execute("SELECT name, shown_name FROM guest WHERE id=?", (row["guest"],)).fetchone()
        if g:
            return g["shown_name"] or g["name"]
    return row["shown"]


def may_pick(c, g):
    """Whether a guest may pick for themselves yet: their episode is there to watch (a review round), or a
    host has already given them one, which is theirs to swap."""
    if of_guest(c, g["id"]):
        return True
    return bool(c.execute("SELECT 1 FROM review WHERE guest=?", (g["id"],)).fetchone())


def take(c, kind, name, by, guest=None, host=None, how="claimed", client=None):
    """A claim, or a swap when the person holds one already: the pose or chain is theirs if it is free, and the
    one they held is free again. `guest` is a guest's row, `host` a host's first name; `by` is who did it (the
    guest's address, or the engine identity of a host), and `how` is "assigned" when a host does it for a
    guest. The table's two constraints decide a race: the loser is told whose it is.

    Returns what the guest's issue is told (guests.tell), or None for a host's own."""
    if kind not in KINDS or name not in data()[kind]:
        raise SeymourError("Pick a pose or a chain from the board")
    if reserved(kind, name):
        raise SeymourError(f"The {WORD[kind]} {name} is the show's own and is not for claiming")
    if bool(guest) == bool(host):
        raise SeymourError("A claim is a guest's or a host's")
    if host and host not in hosts():
        raise SeymourError(f"This engine's hosts are {' and '.join(hosts())}")
    owner = f"guest:{guest['id']}" if guest else f"host:{host}"
    name_shown = (guest["shown_name"] or guest["name"]) if guest else host
    rnd = c.execute("SELECT slug, round FROM review WHERE guest=?", (guest["id"],)).fetchone() if guest else None
    at = ledger.now()
    c.execute("BEGIN IMMEDIATE")
    try:
        was = c.execute("SELECT * FROM claim WHERE owner=?", (owner,)).fetchone()
        if was and (was["kind"], was["name"]) == (kind, name):
            raise SeymourError(f"The {WORD[kind]} {name} is yours already")
        other = holder(c, kind, name)
        if other:
            raise SeymourError(f"The {WORD[kind]} {name} is {shown(c, other)}'s; pick one that is free")
        if was:
            c.execute("DELETE FROM claim WHERE owner=?", (owner,))
        try:
            c.execute(
                "INSERT INTO claim (kind, name, owner, guest, host, shown, how, by, at, slug, round) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    kind,
                    name,
                    owner,
                    guest["id"] if guest else None,
                    host,
                    name_shown,
                    how,
                    by,
                    at,
                    rnd["slug"] if rnd else None,
                    rnd["round"] if rnd else None,
                ),
            )
        except sqlite3.IntegrityError:  # another connection got there between the look and the write
            raise SeymourError(f"The {WORD[kind]} {name} was taken a moment ago; pick one that is free") from None
    except BaseException:
        c.execute("ROLLBACK")
        raise
    c.execute("COMMIT")
    what = "seymour:assigned" if how == "assigned" else "seymour:swapped" if was else "seymour:claimed"
    detail = {"kind": kind, "name": name, "owner": name_shown}
    if was:
        detail["was"] = {"kind": was["kind"], "name": was["name"]}
    ledger.event(c, what, by, detail=detail, guest=guest["id"] if guest else None)
    mirror(c, guest["id"] if guest else None, client)
    if not guest:
        return None
    return {
        "what": kind,
        "seymour": name,
        "how": "assigned" if how == "assigned" else "swapped" if was else "claimed",
        "was": f"the {WORD[was['kind']]} {was['name']}" if was else None,
        "gid": guest["id"],
    }


def release(c, kind, name, by, client=None):
    """A host lets a claim go without a swap (an episode that never goes out): the pose or chain is free
    again. Returns the row that was, for the screen and the guest's issue."""
    row = holder(c, kind, name)
    if not row:
        raise SeymourError(f"Nobody holds the {WORD.get(kind, kind)} {name}")
    c.execute("DELETE FROM claim WHERE kind=? AND name=?", (kind, name))
    ledger.event(
        c,
        "seymour:released",
        by,
        detail={"kind": kind, "name": name, "owner": shown(c, row), "since": row["at"]},
        guest=row["guest"],
    )
    mirror(c, row["guest"], client)
    return row


# ─── the archive ─────────────────────────────────────────────────────────────
def history(c, gid):
    """Every claim, swap, assignment and release of one guest's, oldest first."""
    rows = c.execute(
        "SELECT at, who, what, detail FROM event WHERE guest=? AND what LIKE 'seymour:%' ORDER BY id", (gid,)
    ).fetchall()
    out = []
    for e in rows:
        d = json.loads(e["detail"] or "{}")
        out.append({"at": e["at"], "by": e["who"], "what": e["what"].split(":", 1)[1], **d})
    return out


def record(c, gid):
    """`guests/<id>/claim.json`: what the guest holds today, with who made the claim and when, and the round
    of the review it was made on; and everything that led to it. `kind` and `name` are null once it is given
    up without another, which is how the pipeline knows there is none."""
    row = of_guest(c, gid)
    g = c.execute("SELECT id, name, shown_name FROM guest WHERE id=?", (gid,)).fetchone()
    doc = {
        "guest": {"id": gid, "name": (g["shown_name"] or g["name"]) if g else None},
        "kind": row["kind"] if row else None,
        "name": row["name"] if row else None,
        "history": history(c, gid),
    }
    if row:
        doc.update(at=row["at"], by=row["by"], how=row["how"], review={"slug": row["slug"], "round": row["round"]})
    return doc


def board_record(c):
    """`seymour/claims.json`: every claim, for the site (tools/site.py) and for the export's check that no
    claimed name is dropped (tools/eyeanim.py). `owner` is the name to show."""
    return {
        "at": ledger.now(),
        "claims": [
            {
                "kind": r["kind"],
                "name": r["name"],
                "owner": shown(c, r),
                "guest": r["guest"],
                "host": r["host"],
                "at": r["at"],
                "how": r["how"],
            }
            for r in claims(c)
        ],
    }


def mirror(c, gid=None, client=None):
    """The board, and the guest's own record when the change was a guest's, to the archive, now. A failure is
    an event and the ledger stands; the next change, or `engine.py seymour`, writes them again."""
    docs = [(BOARD_KEY, board_record(c))]
    if gid:
        docs.append((f"guests/{gid}/claim.json", record(c, gid)))
    done = []
    for key, doc in docs:
        try:
            client = client or archive.Client()
            client.put_bytes(key, json.dumps(doc, indent=1).encode("utf-8"), "application/json")
            done.append(key)
        except archive.ArchiveError as e:
            ledger.event(c, "seymour:mirror:failed", "engine", detail={"key": key, "error": str(e)[:300]}, guest=gid)
    return done


def mirror_all(c, client=None):
    """Everything again: the board and every guest's record who holds or has held one (`engine.py seymour`)."""
    gids = {r["guest"] for r in claims(c) if r["guest"]}
    gids |= {
        r[0] for r in c.execute("SELECT DISTINCT guest FROM event WHERE what LIKE 'seymour:%' AND guest IS NOT NULL")
    }
    done = mirror(c, None, client)
    for gid in sorted(gids):
        done += [k for k in mirror(c, gid, client) if k != BOARD_KEY]
    return done


def of_approval(c, gid):
    """What a guest holds, as their approval records it (review.approval_record): the claim, and when."""
    row = of_guest(c, gid)
    return {"kind": row["kind"], "name": row["name"], "at": row["at"], "how": row["how"]} if row else None


# ─── the issue ───────────────────────────────────────────────────────────────
def job_seymour(env, a, c=None):
    """A guest's claim on their issue: which, how, and what the pipeline does with it."""
    what = f"the {WORD[a['what']]} `{a['seymour']}`" if a.get("what") else None
    if a["how"] == "released":
        text = f"{a['who']} released {a['name']}'s Seymour ({a['was']}) on the engine on {a['when']}; it is free again."
    elif a["how"] == "assigned":
        text = f"{a['who']} gave {a['name']} {what} as their Seymour on the engine on {a['when']}"
        text += f", in place of {a['was']}." if a.get("was") else "."
    elif a["how"] == "swapped":
        text = f"{a['name']} swapped their Seymour for {what} on {a['when']}; {a['was']} is free again."
    else:
        text = f"{a['name']} claimed {what} as their Seymour on {a['when']}."
    if a["how"] != "released":
        text += (
            f" The record: guests/{a['gid']}/claim.json in the archive; `episode.py notes` and `approve` put it on "
            "their episode's cards, art, thumbnail and clips."
        )
    cm = linear.comment(env, a["issue"], text)
    return {"issue": a["issue"], "comment": cm["url"]}


# ─── the board, as markup ────────────────────────────────────────────────────
def face(kind, name, uid, tag="span"):
    return pages._site.seymour_face(kind, name, uid, tag)


def options(c, mine=None, uid="s"):
    """Every claimable pose and chain as an option: the face, its name, its note; one that is taken carries
    its owner's name and cannot be chosen; the reader's own is marked."""
    held = {(r["kind"], r["name"]): r for r in claims(c)}
    out = {}
    for kind, item in claimable():
        name = item["name"]
        row = held.get((kind, name))
        is_mine = bool(mine and (mine["kind"], mine["name"]) == (kind, name))
        cls, state, lock = "seyopt", "", ""
        if is_mine:
            cls, state, lock = "seyopt mine", '<span class="seywhose">Yours</span>', " disabled"
        elif row:
            cls, state, lock = (
                "seyopt taken",
                f'<span class="seywhose">{esc(shown(c, row))}&#8217;s</span>',
                " disabled",
            )
        credit = f' <span class="seydrawn">Drawn by {esc(item["by"])}.</span>' if item.get("by") else ""
        out.setdefault(kind, []).append(
            f'<label class="{cls}"><input type="radio" name="pick" value="{kind}:{esc(name)}"{lock}>'
            f"{face(kind, name, f'{uid}-{kind[0]}-{esc(name)}')}"
            f'<span class="seyname">{esc(name)}</span>{state}'
            f'<span class="seynote">{esc(item["note"])}{credit}</span></label>'
        )
    return out


def board(c, action, label, mine=None, csrf=None, hidden="", word="", uid="s", live=True):
    """The form: the poses, the chains, and the button that makes the chosen one theirs."""
    opts = options(c, mine, uid)
    free = sum(1 for kind, item in claimable() if not holder(c, kind, item["name"]))
    token = f'<input type="hidden" name="_t" value="{esc(csrf)}">' if csrf else ""
    return (
        f'<form method="post" action="{esc(action)}" class="block seyboard"{" data-live" if live else ""}>{token}{hidden}'
        f'<h3>Poses</h3><div class="seypick">{"".join(opts.get("pose", []))}</div>'
        f'<h3>Chains <span class="muted">a few poses in a row, with their own pace</span></h3>'
        f'<div class="seypick">{"".join(opts.get("chain", []))}</div>'
        f'<p><button type="submit" class="signal">{esc(label)}</button> '
        f'<span class="muted">{free} of {len(claimable())} are free.</span></p>{word}</form>'
    )


def mine_block(c, row, whose="Yours"):
    """The pose or chain somebody holds, shown large with its name and note. A name the tables no longer have
    (a claim outlives a rename until it is swapped or released) is shown without a face."""
    item = data()[row["kind"]].get(row["name"])
    if not item:
        return f'<p><b class="seyname">{esc(row["name"])}</b> {esc(whose)}, and no longer one of his: swap it.</p>'
    giver = esc(row["by"].split("@")[0].capitalize())
    how = (
        (f"given to you by {giver}" if whose == "Yours" else f"assigned by {giver}")
        if row["how"] == "assigned"
        else "claimed"
    )
    return (
        f'<div class="seymine">{face(row["kind"], row["name"], "mine")}'
        f'<p><b class="seyname">{esc(row["name"])}</b> {esc(whose)}: the {WORD[row["kind"]]} {how} on '
        f"{esc(when(row['at'], '%-d %B %Y'))}. {esc(item['note'])}</p></div>"
    )


ABOUT = (
    "Seymour is the eye in the fedora on every AgentSee page and card. A guest of the show keeps one of his "
    "faces: a pose, or a chain of poses, that is yours for good and nobody else's. It becomes his face all "
    "over your episode: the title card, the chapter cards, the end card, the art, the thumbnail and the clips; "
    "your name goes on it in his room on the site."
)


def guest_section(c, g, word="", back="review"):
    """The office's section for a guest: theirs, if they hold one, and the board to pick or swap from. On the
    review it sits under the clips and above the approval; `back` says which page the form returns to."""
    mine = of_guest(c, g["id"])
    hidden = f'<input type="hidden" name="back" value="{esc(back)}">'
    if not may_pick(c, g):
        body = (
            f"<p>{esc(ABOUT)}</p>"
            '<p class="muted">You pick yours once your episode is here to watch; it is offered again with the approval.</p>'
        )
        return section("Seymour", "A Seymour of your own", body, anchor="seymour")
    if mine:
        head = (
            mine_block(c, mine)
            + "<p>It is yours for as long as you want it. You can swap it for any that is free, at any time; the one "
            "you leave goes back on the board for someone else. An episode that is already out keeps the one it "
            "was approved with.</p>"
        )
        form = board(c, "/seymour/claim", "Swap to the one I picked", mine, None, hidden, word, back)
        body = head + f'<details class="before"><summary>Swap it for another</summary>{form}</details>'
        return section("Seymour", "Your Seymour", body, anchor="seymour")
    head = (
        f"<p>{esc(ABOUT)}</p>"
        "<p>Tap one to pick it, then the button to make it yours. You do not have to: you can approve without, and "
        "pick later from the office. One each, first come, first served; you can swap it for a free one later.</p>"
    )
    form = board(c, "/seymour/claim", "Make it mine", None, None, hidden, word, back)
    return section("Seymour", "Pick your Seymour", head + form, anchor="seymour")


def office_card(c, g):
    """The line on the office's front page: theirs, or the offer once there is an episode to watch."""
    mine = of_guest(c, g["id"])
    if mine:
        return (
            f'<div class="seycard">{face(mine["kind"], mine["name"], "card")}'
            f'<p><b class="seyname">{esc(mine["name"])}</b> Your Seymour. {link("/seymour", "See it, or swap it")}</p></div>'
        )
    if may_pick(c, g):
        return (
            '<div class="seycard"><p><b class="seyname">Seymour</b> One of his poses or chains is yours to keep, and it '
            f"becomes his face on your episode. {link('/seymour', 'Pick your Seymour')}</p></div>"
        )
    return ""


def office_page(c, g, msg=None, status=200):
    """The guest's own page for it, on the office."""
    note = f'<p class="honesty"><b>{esc(msg)}</b></p>' if msg else ""
    body = (
        '<article class="lead"><p class="kicker">The office</p><h1>A Seymour of your own</h1>'
        f'<p class="deck">One of the eye&#8217;s poses or chains, yours for good.</p></article>{note}'
        + guest_section(c, g, back="seymour")
        + f"<p>{go('/office', 'Back to the office')}</p>"
    )
    return "Your Seymour", body, status


# ─── the engine's side ───────────────────────────────────────────────────────
def host_fact(c, g):
    """The line on a guest's page on the engine."""
    row = of_guest(c, g["id"])
    if not row:
        return '<span class="muted">none yet</span>; ' + link(f"/guests/{g['id']}#seymour", "give them one")
    how = f"assigned by {esc(row['by'])}" if row["how"] == "assigned" else "their own pick"
    return (
        f"the {WORD[row['kind']]} <b>{esc(row['name'])}</b>, {how}, since {esc(when(row['at']))} "
        f'<span class="mono">guests/{esc(g["id"])}/claim.json</span>'
    )


def assign_section(c, g, csrf):
    """On a guest's page on the engine: give them one that is free (for a guest who has not picked, or who said
    which on the recording), or release theirs."""
    row = of_guest(c, g["id"])
    free = [(k, item) for k, item in claimable() if not holder(c, k, item["name"])]
    choices = "".join(
        f'<option value="{k}:{esc(item["name"])}">{esc(item["name"])} ({k}): {esc(item["note"])}</option>'
        for k, item in free
    )
    first = g["name"].split()[0]
    form = (
        f'<form method="post" action="/guests/{g["id"]}/seymour" class="block">'
        f'<input type="hidden" name="_t" value="{esc(csrf)}">'
        f'<label for="seymour-pick">{"Swap it for" if row else "Give " + esc(first)}</label>'
        f'<div class="row"><select id="seymour-pick" name="pick" required><option value="">a free pose or chain</option>'
        f"{choices}</select>"
        f'<button type="submit" class="signal">{"Swap" if row else "Assign"}</button></div>'
        f'<p class="muted">An assignment is a claim like any other, recorded with your name. {esc(first)} sees it as '
        f"theirs in the office and can swap it. {link('/seymour', 'The whole board')}</p></form>"
    )
    head = mine_block(c, row, f"{first}'s") if row else f"<p>{esc(first)} holds none.</p>"
    let_go = button(f"/guests/{g['id']}/seymour/release", "Release it, with no swap", csrf, confirm=True) if row else ""
    return section("Seymour", "The pose or chain that is theirs", head + form + let_go, anchor="seymour")


def host_page(c, host, who, csrf, msg=None):
    """The engine's page: the whole board with who holds what, this host's own claim, and what has happened."""
    note = f'<p class="honesty"><b>{esc(msg)}</b></p>' if msg else ""
    rows = []
    for r in claims(c):
        whose = link(f"/guests/{r['guest']}", shown(c, r)) if r["guest"] else esc(shown(c, r))
        rows.append(
            [
                f"<b>{esc(r['name'])}</b>",
                esc(r["kind"]),
                whose + (' <span class="muted">host</span>' if r["host"] else ""),
                esc(r["how"]) + (f" by {esc(r['by'])}" if r["how"] == "assigned" else ""),
                esc(when(r["at"])),
                (f"{esc(r['slug'])} round {r['round']}" if r["slug"] else ""),
                '<span class="late">not in the tables any more</span>' if r["name"] not in data()[r["kind"]] else "",
                f'<form method="post" action="/seymour/release" class="inline confirm">'
                f'<input type="hidden" name="_t" value="{esc(csrf)}">'
                f'<input type="hidden" name="pick" value="{esc(r["kind"])}:{esc(r["name"])}">'
                '<button type="submit">Release</button></form>',
            ]
        )
    held = (
        table(["Name", "Kind", "Whose", "How", "Since", "Made on", "", ""], rows)
        if rows
        else "<p>Nobody holds one yet.</p>"
    )
    if host in hosts():
        mine = of_host(c, host)
        head = mine_block(c, mine) if mine else "<p>You hold none. One each, by the same rule as a guest.</p>"
        own = head + board(
            c, "/seymour/claim", "Swap to the one I picked" if mine else "Make it mine", mine, csrf, "", "", "h", False
        )
    else:
        own = f"<p>This engine's hosts are {esc(' and '.join(hosts()))}; you are {esc(who)}, so there is none to claim.</p>"
    events = c.execute("SELECT * FROM event WHERE what LIKE 'seymour:%' ORDER BY id DESC LIMIT 60").fetchall()
    log = (
        table(
            ["When", "Who", "What", "Detail"],
            [
                [
                    esc(when(e["at"], "%d %b %H:%M")),
                    esc(e["who"]),
                    esc(e["what"].split(":", 1)[1]),
                    f'<span class="mono">{esc(e["detail"] or "")}</span>',
                ]
                for e in events
            ],
        )
        if events
        else "<p>Nothing yet.</p>"
    )
    free = sum(1 for kind, item in claimable() if not holder(c, kind, item["name"]))
    body = (
        '<article class="lead"><h1>Seymour</h1><p class="deck">Who holds which of his poses and chains. A guest claims '
        "theirs in the office, with their approval or later; you claim yours here, and give a guest one from their "
        f"page. {free} of {len(claimable())} are free; "
        f"{esc(', '.join(data()['reserved']['pose'] + data()['reserved']['chain']))} stay the show's.</p></article>"
        + note
        + section("Yours", f"{host}'s Seymour" if host in hosts() else "Yours", own, anchor="yours")
        + section("Held", f"{len(rows)} claimed", held, anchor="held")
        + section("Record", "Every claim, swap, assignment and release", log)
        + f'<p class="muted">Mirrored to the archive at <span class="mono">{BOARD_KEY}</span> and each guest&#8217;s '
        '<span class="mono">guests/&lt;id&gt;/claim.json</span>; `episode.py claims` brings the board to the '
        "workstation, and `tools/site.py` puts the names on the site.</p>"
    )
    return "Seymour", body
