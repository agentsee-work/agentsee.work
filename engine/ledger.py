"""
The ledger: what closes the loop. Not a tool; the engine's modules import it.

One SQLite file, one table per noun in the design (AGE-69): an episode has
deliverables; a post is a deliverable on a platform from an account, with
its short-link id, its text, when it is due, what the platform called it and
where it lives; snapshots and spend hang off posts; clicks and downloads
arrive from the edges; a lead is where an enquiry says it came from. Events
are the audit trail: who did what, when, in the engine.

Where the file lives: $ENGINE_DATA/ledger.sqlite, or build/engine/ beside the
episodes when that is unset (gitignored, like everything under build/). Every
caller opens its own connection; the file is the shared state, and SQLite's
write lock is the lock.
"""

import datetime as dt
import json
import os
import pathlib
import sqlite3
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "engine" / "config.json").read_text(encoding="utf-8"))
ZONE = ZoneInfo(CONFIG["timezone"])

SCHEMA = """
CREATE TABLE IF NOT EXISTS episode (
  slug TEXT PRIMARY KEY, number TEXT, title TEXT, release TEXT, release_at TEXT,
  guid TEXT, url TEXT, media_url TEXT, approved_by TEXT, approved_at TEXT, staged_at TEXT,
  release_json TEXT NOT NULL, source_dir TEXT, ingested_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS deliverable (
  id INTEGER PRIMARY KEY, episode TEXT NOT NULL REFERENCES episode(slug) ON DELETE CASCADE,
  name TEXT NOT NULL, kind TEXT NOT NULL, sha256 TEXT, bytes INTEGER, type TEXT,
  duration REAL, width INTEGER, height INTEGER, url TEXT, archived INTEGER NOT NULL DEFAULT 0,
  UNIQUE(episode, name));
CREATE TABLE IF NOT EXISTS post (
  id INTEGER PRIMARY KEY, episode TEXT NOT NULL REFERENCES episode(slug) ON DELETE CASCADE,
  asset TEXT NOT NULL, platform TEXT NOT NULL, account TEXT NOT NULL,
  link_id TEXT UNIQUE, link_url TEXT, media TEXT, title TEXT, text TEXT NOT NULL DEFAULT '',
  scheduled_at TEXT, published_at TEXT, remote_id TEXT, permalink TEXT,
  status TEXT NOT NULL DEFAULT 'planned', last_error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(episode, asset, platform, account));
CREATE INDEX IF NOT EXISTS post_due ON post(status, scheduled_at);
CREATE TABLE IF NOT EXISTS snapshot (
  id INTEGER PRIMARY KEY, post INTEGER REFERENCES post(id) ON DELETE CASCADE, account TEXT,
  platform TEXT NOT NULL, metric TEXT NOT NULL, observed_at TEXT NOT NULL, value REAL, raw TEXT);
CREATE TABLE IF NOT EXISTS spend (
  id INTEGER PRIMARY KEY, post INTEGER REFERENCES post(id), campaign TEXT, platform TEXT NOT NULL,
  date TEXT NOT NULL, amount REAL NOT NULL, currency TEXT NOT NULL, objective TEXT, metric TEXT,
  receipt TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS click (
  id INTEGER PRIMARY KEY, link_id TEXT NOT NULL, at TEXT NOT NULL, country TEXT, ua_class TEXT,
  hash TEXT, referer TEXT);
CREATE TABLE IF NOT EXISTS download (
  id INTEGER PRIMARY KEY, episode TEXT, name TEXT NOT NULL, at TEXT NOT NULL, ua_class TEXT,
  hash TEXT, bytes INTEGER, first INTEGER, last INTEGER, status INTEGER);
CREATE TABLE IF NOT EXISTS lead (id INTEGER PRIMARY KEY, at TEXT NOT NULL, source TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS event (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, who TEXT NOT NULL, what TEXT NOT NULL,
  post INTEGER, episode TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS event_post ON event(post, at);
"""

STATUSES = {
    "planned": "waiting for its day",
    "due": "its day has come; the next publisher run takes it",
    "publishing": "the publisher has it now",
    "scheduled": "accepted by the platform, which will publish it at the time set",
    "published": "up",
    "hand": "a card is ready; someone posts it and pastes the permalink back",
    "failed": "the platform said no; see the error, then retry",
    "skipped": "a person said not this one",
}


def data_dir():
    return pathlib.Path(os.environ.get("ENGINE_DATA") or ROOT / "build" / "engine")


def connect(path=None):
    p = pathlib.Path(path) if path else data_dir() / "ledger.sqlite"
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(SCHEMA)
    return c


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def local(iso):
    """An ISO instant as the configured zone's datetime, for people to read."""
    if not iso:
        return None
    return dt.datetime.fromisoformat(iso).astimezone(ZONE)


def at(date, hhmm):
    """A local date and time of day, as an ISO instant with its offset."""
    h, m = (int(x) for x in hhmm.split(":"))
    return dt.datetime.combine(dt.date.fromisoformat(date), dt.time(h, m), tzinfo=ZONE).isoformat()


def event(c, what, who="engine", post=None, episode=None, detail=None):
    c.execute("INSERT INTO event (at, who, what, post, episode, detail) VALUES (?,?,?,?,?,?)",
              (now(), who, what, post, episode,
               detail if isinstance(detail, str) or detail is None else json.dumps(detail)))


def update_post(c, post_id, who="engine", **fields):
    fields["updated_at"] = now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    c.execute(f"UPDATE post SET {sets} WHERE id = ?", (*fields.values(), post_id))


def post(c, post_id):
    return c.execute("SELECT * FROM post WHERE id = ?", (post_id,)).fetchone()


def episode(c, slug):
    return c.execute("SELECT * FROM episode WHERE slug = ?", (slug,)).fetchone()


def release(row):
    return json.loads(row["release_json"])
