"""A durable memory of sentences already read.

An in-memory cache forgets on every deploy, which is exactly when a launch is
busiest. Interpretations are deterministic given the sentence and never go
stale, so they are worth keeping on disk.

The table earns its place twice. It stops the same sentence being paid for
more than once — and every row where the rules gave up is a phrasing they
should have handled, which is the list that drives the model calls towards
zero.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS interpretations (
    key        TEXT PRIMARY KEY,
    sentence   TEXT NOT NULL,
    intent     TEXT NOT NULL,
    -- The rules could not read this one. True whether or not the model then
    -- managed to: a sentence the parser missed is worth recording even when
    -- there is no credential to fall back on.
    rules_failed INTEGER NOT NULL DEFAULT 0,
    -- Whether `intent` is a final answer worth serving. A gap recorded while
    -- the model was unavailable is not.
    answered   INTEGER NOT NULL DEFAULT 1,
    hits       INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS interpretations_gaps
    ON interpretations(rules_failed, hits DESC);
"""


class IntentStore:
    """SQLite-backed. Unavailable is not fatal: the caller keeps its own
    in-memory cache and simply loses persistence."""

    def __init__(self, path: Optional[str]) -> None:
        self.path = path
        self.available = False
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with self._connect() as connection:
                connection.executescript(SCHEMA)
            self.available = True
        except (sqlite3.Error, OSError):
            self.available = False

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def get(self, key: str) -> Optional[Dict[str, object]]:
        if not self.available:
            return None
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT intent FROM interpretations "
                    "WHERE key = ? AND answered = 1", (key,)
                ).fetchone()
                if row is None:
                    return None
                # Popularity is worth knowing: it ranks which gaps to close.
                connection.execute(
                    "UPDATE interpretations SET hits = hits + 1, "
                    "last_seen = datetime('now') WHERE key = ?", (key,)
                )
            return json.loads(row[0])
        except (sqlite3.Error, ValueError):
            return None

    def put(self, key: str, sentence: str, intent: Dict[str, object],
            rules_failed: bool, answered: bool = True) -> None:
        if not self.available:
            return
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO interpretations
                        (key, sentence, intent, rules_failed, answered,
                         hits, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, 0, datetime('now'), datetime('now'))
                    ON CONFLICT(key) DO UPDATE SET
                        intent = excluded.intent,
                        rules_failed = excluded.rules_failed,
                        -- never downgrade a real answer back to a gap record
                        answered = MAX(interpretations.answered, excluded.answered),
                        last_seen = excluded.last_seen
                    """,
                    (key, sentence, json.dumps(intent),
                     1 if rules_failed else 0, 1 if answered else 0),
                )
        except (sqlite3.Error, ValueError):
            return

    def gaps(self, limit: int = 50) -> List[Dict[str, object]]:
        """Sentences the rules could not read, most-asked first.

        This is the work list: each one is a phrasing worth teaching the
        parser, after which it never costs anything again.
        """
        if not self.available:
            return []
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT sentence, hits, last_seen, answered FROM interpretations "
                    "WHERE rules_failed = 1 ORDER BY hits DESC, last_seen DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        except sqlite3.Error:
            return []
        return [
            {"sentence": r[0], "hits": r[1], "last_seen": r[2],
             "model_answered": bool(r[3])}
            for r in rows
        ]

    def stats(self) -> Dict[str, object]:
        if not self.available:
            return {"available": False}
        try:
            with self._connect() as connection:
                total, missed, hits = connection.execute(
                    "SELECT COUNT(*), COALESCE(SUM(rules_failed), 0), "
                    "COALESCE(SUM(hits), 0) FROM interpretations"
                ).fetchone()
        except sqlite3.Error:
            return {"available": False}
        return {
            "available": True,
            "sentences": total,
            "rules_missed": missed,
            "served_from_store": hits,
        }
