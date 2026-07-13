"""Storage: raw dumps of API responses + SQLite with parsed listings.

Principles:
- the raw response is saved to disk BEFORE parsing (gzip json), because the parser
  changes but the data cannot be re-downloaded;
- (offer_id, snapshot_date) is the primary key: repeated snapshots give a price history;
- segments_done holds checkpoints: a rerun skips already collected segments.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    offer_id        INTEGER NOT NULL,
    snapshot_date   TEXT    NOT NULL,
    price           INTEGER,
    deposit         INTEGER,
    client_fee_pct  REAL,
    agent_fee_pct   REAL,
    utilities_included INTEGER,
    rooms           INTEGER,
    flat_type       TEXT,
    is_apartments   INTEGER,
    total_area      REAL,
    living_area     REAL,
    kitchen_area    REAL,
    floor           INTEGER,
    floors_total    INTEGER,
    build_year      INTEGER,
    material_type   TEXT,
    lat             REAL,
    lon             REAL,
    district        TEXT,
    okrug           TEXT,
    address         TEXT,
    metro_name      TEXT,
    metro_time_min  REAL,
    metro_transport TEXT,
    is_by_homeowner INTEGER,
    published_ts    INTEGER,
    description     TEXT,
    photos_count    INTEGER,
    region          INTEGER,
    url             TEXT,
    raw_json        TEXT,
    PRIMARY KEY (offer_id, snapshot_date)
);

CREATE TABLE IF NOT EXISTS segments_done (
    segment_key     TEXT NOT NULL,
    snapshot_date   TEXT NOT NULL,
    pages           INTEGER,
    offers          INTEGER,
    finished_at     TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (segment_key, snapshot_date)
);
"""

OFFER_COLUMNS = [
    "offer_id", "snapshot_date", "price", "deposit", "client_fee_pct",
    "agent_fee_pct", "utilities_included", "rooms", "flat_type", "is_apartments",
    "total_area", "living_area", "kitchen_area", "floor", "floors_total",
    "build_year", "material_type", "lat", "lon", "district", "okrug", "address",
    "metro_name", "metro_time_min", "metro_transport", "is_by_homeowner",
    "published_ts", "description", "photos_count", "region", "url", "raw_json",
]


class Storage:
    def __init__(self, db_path: str | Path, raw_dir: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.raw_dir = Path(raw_dir)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.snapshot_date = date.today().isoformat()

    def _migrate(self) -> None:
        """Adds missing columns to older databases (a simple migration)."""
        existing = {r[1] for r in self.conn.execute("PRAGMA table_info(offers)")}
        for col in OFFER_COLUMNS:
            if col not in existing:
                self.conn.execute(f"ALTER TABLE offers ADD COLUMN {col} TEXT")
        self.conn.commit()

    # --- raw dumps ---

    def dump_raw(self, segment_key: str, page: int, payload: dict[str, Any]) -> None:
        d = self.raw_dir / self.snapshot_date
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{segment_key}_p{page:02d}.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

    # --- listings ---

    def upsert_offers(
        self, parsed: list[dict[str, Any]], raw_offers: list[dict[str, Any]], region: int
    ) -> int:
        rows = []
        for rec, raw in zip(parsed, raw_offers):
            if rec.get("offer_id") is None:
                continue
            rec = {
                **rec,
                "snapshot_date": self.snapshot_date,
                "region": region,
                "raw_json": json.dumps(raw, ensure_ascii=False),
            }
            rows.append(tuple(rec.get(c) for c in OFFER_COLUMNS))
        placeholders = ",".join("?" * len(OFFER_COLUMNS))
        self.conn.executemany(
            f"INSERT OR REPLACE INTO offers ({','.join(OFFER_COLUMNS)}) "
            f"VALUES ({placeholders})",
            rows,
        )
        self.conn.commit()
        return len(rows)

    # --- segment checkpoints ---

    def is_segment_done(self, segment_key: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM segments_done WHERE segment_key=? AND snapshot_date=?",
            (segment_key, self.snapshot_date),
        )
        return cur.fetchone() is not None

    def mark_segment_done(self, segment_key: str, pages: int, offers: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO segments_done (segment_key, snapshot_date, pages, offers) "
            "VALUES (?, ?, ?, ?)",
            (segment_key, self.snapshot_date, pages, offers),
        )
        self.conn.commit()

    def stats(self) -> dict[str, int]:
        q = self.conn.execute
        return {
            "offers_today": q(
                "SELECT COUNT(*) FROM offers WHERE snapshot_date=?", (self.snapshot_date,)
            ).fetchone()[0],
            "offers_unique_total": q("SELECT COUNT(DISTINCT offer_id) FROM offers").fetchone()[0],
            "segments_done_today": q(
                "SELECT COUNT(*) FROM segments_done WHERE snapshot_date=?",
                (self.snapshot_date,),
            ).fetchone()[0],
        }
