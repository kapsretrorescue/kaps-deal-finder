"""SQLite storage: which listings we've already seen, which deals are
waiting for the next digest, and when the last digest went out.

The database file lives at data/dealfinder.db. Deleting it just makes the
bot treat everything as new again — it's safe to nuke if it ever misbehaves.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .analysis import Analysis

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    source      TEXT NOT NULL,
    listing_id  TEXT NOT NULL,
    title       TEXT,
    url         TEXT,
    price       REAL,
    per_unit    REAL,
    quantity    INTEGER,
    consoles    TEXT,      -- comma-separated console keys
    condition   TEXT,
    is_deal     INTEGER,   -- 1 = beat the max-buy threshold
    price_note  TEXT,
    est_profit  REAL,
    max_buy     REAL,
    first_seen  TEXT,
    notified    INTEGER DEFAULT 0,
    PRIMARY KEY (source, listing_id)
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def is_seen(self, source: str, listing_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM listings WHERE source=? AND listing_id=?",
            (source, listing_id),
        ).fetchone()
        return row is not None

    def add(self, a: Analysis) -> None:
        l = a.listing
        self.conn.execute(
            """INSERT OR IGNORE INTO listings
               (source, listing_id, title, url, price, per_unit, quantity,
                consoles, condition, is_deal, price_note, est_profit, max_buy,
                first_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                l.source, l.listing_id, l.title, l.url, l.price,
                a.per_unit, a.quantity, ",".join(a.consoles), a.condition,
                1 if a.is_deal else 0, l.price_note, a.est_profit, a.max_buy,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()

    def pending_deal_rows(self) -> list[sqlite3.Row]:
        """Un-notified listings that beat the max-buy threshold (for instant alerts)."""
        return self.conn.execute(
            """SELECT * FROM listings
               WHERE notified = 0 AND is_deal = 1
               ORDER BY est_profit DESC"""
        ).fetchall()

    def pending_digest_rows(self) -> list[sqlite3.Row]:
        """Deals (and priced-less matches worth eyeballing) not yet sent."""
        return self.conn.execute(
            """SELECT * FROM listings
               WHERE notified = 0 AND (is_deal = 1 OR price IS NULL)
               ORDER BY is_deal DESC, est_profit DESC"""
        ).fetchall()

    def mark_notified(self, rows) -> None:
        self.conn.executemany(
            "UPDATE listings SET notified=1 WHERE source=? AND listing_id=?",
            [(r["source"], r["listing_id"]) for r in rows],
        )
        self.conn.commit()

    # --- digest timing -----------------------------------------------------
    def last_digest(self) -> datetime | None:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key='last_digest'"
        ).fetchone()
        return datetime.fromisoformat(row["value"]) if row else None

    def set_last_digest(self) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_digest', ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        self.conn.commit()
