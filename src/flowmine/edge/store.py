"""Local SQLite store on the station: the decentralised part of the data storage.

Every validated record is kept with its raw value, quality code and model version for
``retention_s`` seconds. Frames waiting for the uplink sit in an outbox table and are
replayed in order. The cloud asks a station for raw history through ``history``
(backfill on demand).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    sensor TEXT NOT NULL, t INTEGER NOT NULL, raw REAL, value REAL,
    quality INTEGER NOT NULL, model_version INTEGER NOT NULL, score REAL,
    PRIMARY KEY (sensor, t)
);
CREATE TABLE IF NOT EXISTS outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, created INTEGER NOT NULL, frame BLOB NOT NULL
);
"""

Record = tuple[str, int, float | None, float | None, int, float]


class LocalStore:
    def __init__(self, path: str | Path = ":memory:", retention_s: int = 14 * 86400) -> None:
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.retention_s = retention_s

    def put(self, records: Iterable[Record]) -> None:
        """Store records ``(sensor, t, raw, value, quality, score)``; the model version is
        read from the quality code."""
        self.db.executemany(
            "INSERT OR REPLACE INTO samples VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(r[0], r[1], r[2], r[3], r[4], (r[4] >> 8) & 0xFF, r[5]) for r in records],
        )

    def purge(self, now: int) -> int:
        cur = self.db.execute("DELETE FROM samples WHERE t < ?", (now - self.retention_s,))
        return cur.rowcount

    def history(
        self, sensor: str, t0: int, t1: int
    ) -> list[tuple[int, float | None, float | None, int]]:
        """Rows ``(t, raw, value, quality)`` for ``t0 <= t < t1``, oldest first."""
        cur = self.db.execute(
            "SELECT t, raw, value, quality FROM samples WHERE sensor = ? AND t >= ? AND t < ? "
            "ORDER BY t",
            (sensor, t0, t1),
        )
        return list(cur.fetchall())

    def count(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM samples").fetchone()[0])

    def enqueue(self, created: int, frame: bytes) -> None:
        self.db.execute("INSERT INTO outbox (created, frame) VALUES (?, ?)", (created, frame))

    def outbox(self) -> list[tuple[int, int, bytes]]:
        return list(self.db.execute("SELECT seq, created, frame FROM outbox ORDER BY seq"))

    def ack(self, seq: int) -> None:
        self.db.execute("DELETE FROM outbox WHERE seq = ?", (seq,))

    def commit(self) -> None:
        self.db.commit()
