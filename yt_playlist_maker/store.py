"""The database, data/app.sqlite3: his lists and what was found for them, the kept answers of outside services,
and the YouTube budgets used each day. Times are RFC 3339 in UTC."""
from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from .config import DATA

SCHEMA = """
CREATE TABLE IF NOT EXISTS lists (
    id INTEGER PRIMARY KEY,
    created TEXT NOT NULL,
    text TEXT NOT NULL,
    songs_per_line INTEGER NOT NULL,
    step TEXT NOT NULL,              -- reading, choosing, videos, ready, failed
    progress TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    wait_until TEXT,                 -- what it waits for (a budget, the GPU, a service) is tried again then
    wait_reason TEXT NOT NULL DEFAULT '',
    tries INTEGER NOT NULL DEFAULT 0 -- waits in a row for a service that failed
);
CREATE TABLE IF NOT EXISTS lines (
    id INTEGER PRIMARY KEY,
    list_id INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    text TEXT NOT NULL,
    reading TEXT,                    -- JSON, reading.py
    chosen INTEGER NOT NULL DEFAULT 0,
    pool TEXT NOT NULL DEFAULT '[]', -- JSON, the next candidates, choosing.py
    error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS picks (
    id INTEGER PRIMARY KEY,
    line_id INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    recording_mbid TEXT NOT NULL,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    artist_mbids TEXT NOT NULL,      -- JSON list
    artist_youtube TEXT NOT NULL,    -- JSON list: the artists' YouTube addresses in MusicBrainz
    year INTEGER,
    length_ms INTEGER,
    why TEXT NOT NULL,
    removed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS videos (
    recording_mbid TEXT PRIMARY KEY,
    state TEXT NOT NULL,             -- found, none, gone
    video_id TEXT,
    source TEXT NOT NULL DEFAULT '', -- musicbrainz, wikidata, channel, search
    looked TEXT NOT NULL,
    steps TEXT NOT NULL DEFAULT '',  -- the steps tried, comma-separated
    yt_title TEXT,                   -- YouTube's: checked again or deleted within 30 days
    yt_channel TEXT,
    yt_duration_s INTEGER,
    yt_checked TEXT
);
CREATE TABLE IF NOT EXISTS playlists (
    list_id INTEGER PRIMARY KEY REFERENCES lists(id) ON DELETE CASCADE,
    step TEXT NOT NULL,              -- saving, saved, failed
    visibility TEXT NOT NULL,
    title TEXT NOT NULL,
    youtube_id TEXT,                 -- YouTube's: checked again or deleted within 30 days
    yt_checked TEXT,
    added INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    wait_until TEXT,
    wait_reason TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS answers (
    service TEXT NOT NULL,
    key TEXT NOT NULL,
    fetched TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (service, key)
);
CREATE TABLE IF NOT EXISTS usage (
    budget TEXT NOT NULL,
    day TEXT NOT NULL,               -- the Pacific date: YouTube's day
    used INTEGER NOT NULL,
    PRIMARY KEY (budget, day)
);
"""

_path: Path = DATA / 'app.sqlite3'
_write = threading.Lock()


def use(path: Path) -> None:
    """Point the store at another database (tests)."""
    global _path
    _path = path


def iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat(timespec='seconds')


def stamp() -> str:
    return iso(datetime.now(UTC))


def at(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


@contextlib.contextmanager
def db():
    _path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    try:
        with con:
            yield con
    finally:
        con.close()


def setup() -> None:
    with db() as con:
        con.execute('PRAGMA journal_mode = WAL')
        con.executescript(SCHEMA)


def answer(service: str, key: str, fresh_after: datetime):
    with db() as con:
        row = con.execute('SELECT fetched, body FROM answers WHERE service = ? AND key = ?', (service, key)).fetchone()
    if row is None or at(row['fetched']) < fresh_after:
        return None
    return json.loads(row['body'])


def keep_answer(service: str, key: str, data) -> None:
    with db() as con:
        con.execute('INSERT OR REPLACE INTO answers (service, key, fetched, body) VALUES (?, ?, ?, ?)',
                    (service, key, stamp(), json.dumps(data, ensure_ascii=False)))


def forget_answers(service: str, older_than: datetime | None = None) -> int:
    with db() as con:
        if older_than is None:
            return con.execute('DELETE FROM answers WHERE service = ?', (service,)).rowcount
        return con.execute('DELETE FROM answers WHERE service = ? AND fetched < ?',
                           (service, iso(older_than))).rowcount


def usage(budget: str, day: str) -> int:
    with db() as con:
        row = con.execute('SELECT used FROM usage WHERE budget = ? AND day = ?', (budget, day)).fetchone()
    return row['used'] if row else 0


def spend(budget: str, day: str, amount: int, limit: int) -> bool:
    """Add amount to the day's use of the budget, unless that would pass limit."""
    with _write, db() as con:
        row = con.execute('SELECT used FROM usage WHERE budget = ? AND day = ?', (budget, day)).fetchone()
        used = row['used'] if row else 0
        if amount > 0 and used + amount > limit:
            return False
        con.execute('INSERT INTO usage (budget, day, used) VALUES (?, ?, ?) '
                    'ON CONFLICT (budget, day) DO UPDATE SET used = excluded.used', (budget, day, used + amount))
        return True
