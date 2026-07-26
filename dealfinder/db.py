"""SQLite storage: seen listings (dedup), tier scoring results, alert state,
and digest timing. Deleting data/dealfinder.db just makes everything look
new again — safe to nuke if it misbehaves.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .analysis import Analysis

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    source      TEXT NOT NULL,
    listing_id  TEXT NOT NULL,
    title       TEXT,
    url         TEXT,
    price       REAL,
    first_seen  TEXT,
    notified    INTEGER DEFAULT 0,
    PRIMARY KEY (source, listing_id)
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# Columns added over time — applied via ALTER TABLE so existing databases
# (including the one committed in the repo by the cloud runs) migrate in place.
MIGRATIONS = {
    "per_unit": "REAL", "quantity": "INTEGER", "consoles": "TEXT",
    "condition": "TEXT", "is_deal": "INTEGER", "price_note": "TEXT",
    "est_profit": "REAL", "max_buy": "REAL",
    "shipping": "REAL", "total_cost": "REAL", "eff_per_unit": "REAL",
    "yield_rate": "REAL", "tier": "TEXT", "cheap_fixes": "TEXT",
    "screen_issue": "INTEGER", "signals": "TEXT", "qty_uncertain": "INTEGER",
    "mixed_lot": "INTEGER", "listing_type": "TEXT", "end_time": "TEXT",
    "seller_feedback": "TEXT", "parts_credit": "REAL",
    "est_profit_total": "REAL", "good_units": "INTEGER",
    "ending_notified": "INTEGER DEFAULT 0",
}


class Database:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        existing = {r["name"] for r in self.conn.execute("PRAGMA table_info(listings)")}
        for col, coltype in MIGRATIONS.items():
            if col not in existing:
                self.conn.execute(f"ALTER TABLE listings ADD COLUMN {col} {coltype}")
        self.conn.commit()

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
               (source, listing_id, title, url, price, first_seen,
                per_unit, quantity, consoles, condition, price_note,
                shipping, total_cost, eff_per_unit, yield_rate, tier,
                cheap_fixes, screen_issue, signals, qty_uncertain, mixed_lot,
                listing_type, end_time, seller_feedback, parts_credit,
                est_profit, est_profit_total, good_units)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                l.source, l.listing_id, l.title, l.url, l.price,
                datetime.now(timezone.utc).isoformat(),
                a.raw_per_unit, a.quantity, ",".join(a.consoles), a.condition,
                l.price_note, a.shipping, a.total_cost, a.eff_per_unit,
                a.yield_rate, a.tier, ",".join(a.cheap_fixes),
                1 if a.screen_issue else 0, ",".join(a.signals),
                1 if a.qty_uncertain else 0, 1 if a.mixed_lot else 0,
                l.listing_type, l.end_time, l.seller_feedback, a.parts_credit,
                a.est_profit_unit, a.est_profit_total, a.good_units,
            ),
        )
        self.conn.commit()

    def refresh_price(self, a: Analysis) -> None:
        """Auctions change price as bids come in — update the money fields."""
        l = a.listing
        self.conn.execute(
            """UPDATE listings SET price=?, shipping=?, total_cost=?,
               per_unit=?, eff_per_unit=?, tier=?, est_profit=?,
               est_profit_total=?, end_time=?
               WHERE source=? AND listing_id=?""",
            (l.price, a.shipping, a.total_cost, a.raw_per_unit, a.eff_per_unit,
             a.tier, a.est_profit_unit, a.est_profit_total, l.end_time,
             l.source, l.listing_id),
        )
        self.conn.commit()

    def mark(self, rows, column: str) -> None:
        self.conn.executemany(
            f"UPDATE listings SET {column}=1 WHERE source=? AND listing_id=?",
            [(r["source"], r["listing_id"]) for r in rows],
        )
        self.conn.commit()

    def pending_instant_rows(self, min_tier: str) -> list[sqlite3.Row]:
        """Un-notified listings at or above the instant-alert tier."""
        tiers = ["great"] if min_tier == "great" else ["great", "good"]
        marks = ",".join("?" * len(tiers))
        return self.conn.execute(
            f"""SELECT * FROM listings
                WHERE notified = 0 AND tier IN ({marks})
                ORDER BY CASE tier WHEN 'great' THEN 0 ELSE 1 END,
                         est_profit_total DESC""",
            tiers,
        ).fetchall()

    def digest_rows(self, lookback_hours: int) -> list[sqlite3.Row]:
        """Everything worth showing from the last N hours, best first."""
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(hours=lookback_hours)).isoformat()
        return self.conn.execute(
            """SELECT * FROM listings
               WHERE first_seen >= ?
                 AND (tier IN ('great','good','marginal') OR tier = 'no_price'
                      OR qty_uncertain = 1)
               ORDER BY CASE tier WHEN 'great' THEN 0 WHEN 'good' THEN 1
                                  WHEN 'marginal' THEN 2 ELSE 3 END,
                        est_profit_total DESC""",
            (cutoff,),
        ).fetchall()

    # --- generic meta key/value --------------------------------------------
    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
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
