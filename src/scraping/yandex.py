"""Yandex.Realty scraper via a headless browser (runs on the Mac).

Two-phase scheme (there is no clean HTTP access, offers are rendered by JS):
  Phase 1 (SERP): paginate the search, collect offer ids (46/page);
  Phase 2 (card): open /offer/<id>/, extract fields from <title>
     and coordinates from the static map URL (latitude=..&longitude=..).

Normalized into the project's SHARED schema (same as CIAN) with source='yandex'.
Written to the same SQLite as CIAN (multi-source via the source column).

Running on the Mac:
    .venv_mac/bin/python -m src.scraping.yandex serp     --pages 3     # collect ids
    .venv_mac/bin/python -m src.scraping.yandex offers   --limit 30    # fill in fields
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "https://realty.yandex.ru"
SERP = BASE + "/sankt-peterburg/snyat/kvartira/"
DB_PATH = "data/yandex.sqlite"

# Yandex search, like CIAN, caps depth at ~600 unique per query and reshuffles
# the pool. So the market is split into segments (rooms × price), each below the
# threshold, and each is crawled separately.
SERP_CAP = 500                     # threshold: a larger segment is split by price
MAX_PAGES = 30                     # guard against infinite pagination
ROOM_PATHS = [
    "studiya", "odnokomnatnaya", "dvuhkomnatnaya",
    "tryohkomnatnaya", "4-i-bolee-komnatnie",
]
PRICE_BANDS = [
    (1, 25000), (25000, 35000), (35000, 50000),
    (50000, 80000), (80000, 150000), (150000, 500000),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS yandex_ids (
    offer_id   TEXT PRIMARY KEY,
    seen_date  TEXT,
    fetched    INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS yandex_offers (
    offer_id   TEXT PRIMARY KEY,
    snapshot_date TEXT,
    price      INTEGER,
    rooms      INTEGER,       -- 0 = studio
    flat_type  TEXT,
    total_area REAL,
    floor      INTEGER,
    floors_total INTEGER,
    lat        REAL,
    lon        REAL,
    title      TEXT,
    url        TEXT,
    source     TEXT DEFAULT 'yandex'
);
"""

# --- parsing the card title ---
# example titles the regexes below match:
# "Снять 2-комнатную квартиру 54 м², 5 этаж из 9, за 45 000 ₽ в месяц ..."
# "Снять квартиру-студию 24 м², 4 этаж из 18, за 64 000 ₽ ..."
RE_ROOMS = re.compile(r"(\d+)-комнат")
RE_STUDIO = re.compile(r"студи", re.I)
RE_AREA = re.compile(r"([\d.,]+)\s*м²")
RE_FLOOR = re.compile(r"(\d+)\s*этаж(?:\s*из\s*(\d+))?")
RE_PRICE = re.compile(r"за\s*([\d\s ]+)\s*₽")
RE_COORD = re.compile(r"latitude=([\d.]+)&(?:amp;)?longitude=([\d.]+)")


def parse_title(title: str) -> dict:
    rooms = 0 if RE_STUDIO.search(title) else None
    m = RE_ROOMS.search(title)
    if m:
        rooms = int(m.group(1))
    area = None
    if (m := RE_AREA.search(title)):
        area = float(m.group(1).replace(",", "."))
    floor = floors_total = None
    if (m := RE_FLOOR.search(title)):
        floor = int(m.group(1))
        floors_total = int(m.group(2)) if m.group(2) else None
    price = None
    if (m := RE_PRICE.search(title)):
        price = int(re.sub(r"[^\d]", "", m.group(1)))
    flat_type = "studio" if rooms == 0 else "rooms"
    return dict(price=price, rooms=rooms, flat_type=flat_type,
               total_area=area, floor=floor, floors_total=floors_total)


def parse_coords(html: str) -> tuple[float | None, float | None]:
    m = RE_COORD.search(html)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def db() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout=30000")  # do not crash during a parallel phase 2
    conn.executescript(SCHEMA)
    return conn


def _title_count(page) -> int:
    """Number of listings from the search title (handles non-breaking spaces)."""
    m = re.search(r"([\d\s  ]+)\s*объявлени", page.title())
    digits = re.sub(r"\D", "", m.group(1)) if m else ""
    return int(digits) if digits else -1


def _collect_segment_ids(page, url_base: str) -> set[str]:
    """Pagination + scroll within one segment, collecting offer ids."""
    ids: set[str] = set()
    for pg in range(1, MAX_PAGES + 1):
        sep = "&" if "?" in url_base else "?"
        page.goto(f"{url_base}{sep}page={pg}", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2800)
        before_page = len(ids)
        stale = 0
        for _ in range(15):
            hrefs = page.eval_on_selector_all(
                'a[href*="/offer/"]',
                "els => els.map(e => e.getAttribute('href'))")
            before = len(ids)
            ids |= {m.group(1) for h in hrefs
                    if (m := re.search(r"/offer/(\d+)/", h or ""))}
            stale = stale + 1 if len(ids) == before else 0
            if stale >= 3:
                break
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(1000)
        if len(ids) == before_page:      # the page gave nothing new, stop
            break
    return ids


def _segment_url(room: str, lo: int, hi: int) -> str:
    return f"{SERP}{room}/?priceMin={lo}&priceMax={hi}"


def cmd_serp(pages: int = 0) -> None:
    """Segmented id collection: rooms × price bands with adaptive splitting.

    If a segment holds more than SERP_CAP listings, the band is split in half
    (otherwise the search will not return everything because of the depth cap).
    The pages argument is unused (kept for CLI compatibility).
    """
    from collections import deque

    conn = db()
    today = time.strftime("%Y-%m-%d")
    total_new = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        queue = deque((room, lo, hi) for room in ROOM_PATHS for lo, hi in PRICE_BANDS)
        while queue:
            room, lo, hi = queue.popleft()
            url = _segment_url(room, lo, hi)
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
            cnt = _title_count(page)
            if cnt > SERP_CAP and hi - lo > 2000:
                mid = (lo + hi) // 2
                queue.append((room, lo, mid))
                queue.append((room, mid + 1, hi))
                print(f"[{room} {lo}-{hi}] ~{cnt} > {SERP_CAP}, splitting in half")
                continue
            ids = _collect_segment_ids(page, url)
            seg_new = 0
            for oid in ids:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO yandex_ids (offer_id, seen_date) VALUES (?, ?)",
                    (oid, today))
                seg_new += cur.rowcount
            conn.commit()
            total_new += seg_new
            print(f"[{room} {lo}-{hi}] ~{cnt} in the segment, collected {len(ids)} ids, "
                  f"+{seg_new} new, total {total_new}")
            time.sleep(1.0)
        browser.close()
    print(f"total ids in the database: {conn.execute('SELECT COUNT(*) FROM yandex_ids').fetchone()[0]}")


def cmd_offers(limit: int) -> None:
    conn = db()
    today = time.strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT offer_id FROM yandex_ids WHERE fetched=0 LIMIT ?", (limit,)
    ).fetchall()
    if not rows:
        print("no uncollected ids, run serp first")
        return
    ok = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for (oid,) in rows:
            url = f"{BASE}/offer/{oid}/"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2500)
                title = page.title()
                html = page.content()
            except Exception as e:
                print(f"  {oid}: ERR {type(e).__name__}")
                continue
            fields = parse_title(title)
            lat, lon = parse_coords(html)
            conn.execute(
                """INSERT OR REPLACE INTO yandex_offers
                   (offer_id, snapshot_date, price, rooms, flat_type, total_area,
                    floor, floors_total, lat, lon, title, url, source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'yandex')""",
                (oid, today, fields["price"], fields["rooms"], fields["flat_type"],
                 fields["total_area"], fields["floor"], fields["floors_total"],
                 lat, lon, title[:200], url))
            conn.execute("UPDATE yandex_ids SET fetched=1 WHERE offer_id=?", (oid,))
            conn.commit()
            if fields["price"] and lat:
                ok += 1
            print(f"  {oid}: {fields['rooms']}r {fields['total_area']}m2 "
                  f"{fields['price']}RUB @ ({lat},{lon})")
            time.sleep(1.0)
        browser.close()
    print(f"fields collected: {ok}/{len(rows)} with price and coordinates")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["serp", "offers"])
    ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--limit", type=int, default=30)
    args = ap.parse_args()
    if args.command == "serp":
        cmd_serp(args.pages)
    else:
        cmd_offers(args.limit)


if __name__ == "__main__":
    main()
