"""SQLite storage. Every table lives in one local file (config.DB_PATH)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Iterable

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY,
    key     TEXT UNIQUE,          -- set by the sender so repeated updates of one visit merge
    kind    TEXT NOT NULL,        -- page | video | search | pdf | ai | app
    source  TEXT NOT NULL,        -- firefox | windows
    start   REAL NOT NULL,        -- unix seconds
    end     REAL,
    seconds REAL DEFAULT 0,       -- active / watched seconds
    title   TEXT,
    url     TEXT,
    site    TEXT,
    app     TEXT,
    meta    TEXT DEFAULT '{}'     -- json: author, published, channel, duration, query, engine...
);
CREATE INDEX IF NOT EXISTS events_start ON events(start);

CREATE TABLE IF NOT EXISTS notes (
    id        INTEGER PRIMARY KEY,
    ts        REAL NOT NULL,
    type      TEXT NOT NULL,
    text      TEXT NOT NULL,
    tags      TEXT DEFAULT '[]',
    ctx_app   TEXT,
    ctx_title TEXT,
    ctx_url   TEXT
);

CREATE TABLE IF NOT EXISTS screenshots (
    id        INTEGER PRIMARY KEY,
    ts        REAL NOT NULL,
    path      TEXT NOT NULL,
    caption   TEXT,
    tags      TEXT DEFAULT '[]',
    ctx_app   TEXT,
    ctx_title TEXT,
    ctx_url   TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id    INTEGER PRIMARY KEY,
    start REAL NOT NULL,
    end   REAL,
    label TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path=config.DB_PATH):
        config.ensure_dirs()
        self._lock = threading.RLock()
        self._con = sqlite3.connect(str(path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(SCHEMA)
        self._con.commit()

    # -- settings -------------------------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        with self._lock:
            self._con.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
            self._con.commit()

    def excluded_domains(self) -> list[str]:
        return self.get_setting("excluded_domains", config.DEFAULT_EXCLUDED_DOMAINS)

    def excluded_apps(self) -> list[str]:
        return self.get_setting("excluded_apps", config.DEFAULT_EXCLUDED_APPS)

    # -- study sessions -----------------------------------------------------
    def study_mode(self) -> bool:
        return bool(self.get_setting("study_mode", False))

    def set_study_mode(self, on: bool, label: str | None = None) -> None:
        with self._lock:
            if on == self.study_mode():
                return
            now = time.time()
            if on:
                self._con.execute("INSERT INTO sessions(start, label) VALUES(?, ?)", (now, label))
            else:
                self._con.execute("UPDATE sessions SET end=? WHERE end IS NULL", (now,))
            self._con.commit()
            self.set_setting("study_mode", on)

    # -- events -----------------------------------------------------------------
    def upsert_event(self, ev: dict) -> None:
        meta = json.dumps(ev.get("meta") or {}, ensure_ascii=False)
        with self._lock:
            existing = None
            if ev.get("key"):
                existing = self._con.execute("SELECT id, meta, seconds FROM events WHERE key=?", (ev["key"],)).fetchone()
            if existing:
                merged = json.loads(existing["meta"] or "{}")
                merged.update({k: v for k, v in (ev.get("meta") or {}).items() if v not in (None, "")})
                self._con.execute(
                    "UPDATE events SET end=?, seconds=?, title=COALESCE(?, title), url=COALESCE(?, url), "
                    "site=COALESCE(?, site), meta=? WHERE id=?",
                    (
                        ev.get("end"),
                        max(float(ev.get("seconds") or 0), float(existing["seconds"] or 0)),
                        ev.get("title"),
                        ev.get("url"),
                        ev.get("site"),
                        json.dumps(merged, ensure_ascii=False),
                        existing["id"],
                    ),
                )
            else:
                self._con.execute(
                    "INSERT INTO events(key, kind, source, start, end, seconds, title, url, site, app, meta) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        ev.get("key"),
                        ev["kind"],
                        ev.get("source", "firefox"),
                        float(ev["start"]),
                        ev.get("end"),
                        float(ev.get("seconds") or 0),
                        ev.get("title"),
                        ev.get("url"),
                        ev.get("site"),
                        ev.get("app"),
                        meta,
                    ),
                )
            self._con.commit()

    def events_between(self, start: float, end: float) -> list[dict]:
        with self._lock:
            rows = self._con.execute(
                "SELECT * FROM events WHERE start >= ? AND start < ? ORDER BY start", (start, end)
            ).fetchall()
        return [_row(r, json_cols=("meta",)) for r in rows]

    # -- notes and screenshots ----------------------------------------------
    def add_note(self, type_: str, text: str, tags: Iterable[str], ctx: dict) -> int:
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO notes(ts, type, text, tags, ctx_app, ctx_title, ctx_url) VALUES(?,?,?,?,?,?,?)",
                (time.time(), type_, text, json.dumps(list(tags), ensure_ascii=False),
                 ctx.get("app"), ctx.get("title"), ctx.get("url")),
            )
            self._con.commit()
            return cur.lastrowid

    def add_screenshot(self, path: str, caption: str, tags: Iterable[str], ctx: dict) -> int:
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO screenshots(ts, path, caption, tags, ctx_app, ctx_title, ctx_url) VALUES(?,?,?,?,?,?,?)",
                (time.time(), path, caption, json.dumps(list(tags), ensure_ascii=False),
                 ctx.get("app"), ctx.get("title"), ctx.get("url")),
            )
            self._con.commit()
            return cur.lastrowid

    def notes_between(self, start: float, end: float) -> list[dict]:
        with self._lock:
            rows = self._con.execute("SELECT * FROM notes WHERE ts >= ? AND ts < ? ORDER BY ts", (start, end)).fetchall()
        return [_row(r, json_cols=("tags",)) for r in rows]

    def screenshots_between(self, start: float, end: float) -> list[dict]:
        with self._lock:
            rows = self._con.execute(
                "SELECT * FROM screenshots WHERE ts >= ? AND ts < ? ORDER BY ts", (start, end)
            ).fetchall()
        return [_row(r, json_cols=("tags",)) for r in rows]

    def first_and_last_timestamp(self) -> tuple[float | None, float | None]:
        with self._lock:
            row = self._con.execute(
                "SELECT MIN(t), MAX(t) FROM ("
                " SELECT start AS t FROM events UNION ALL SELECT ts FROM notes UNION ALL SELECT ts FROM screenshots)"
            ).fetchone()
        return row[0], row[1]


def _row(r: sqlite3.Row, json_cols: tuple[str, ...] = ()) -> dict:
    d = dict(r)
    for c in json_cols:
        try:
            d[c] = json.loads(d.get(c) or ("[]" if c == "tags" else "{}"))
        except ValueError:
            d[c] = [] if c == "tags" else {}
    return d
