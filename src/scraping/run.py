"""CIAN data collection CLI.

Commands:
    python -m src.scraping.run probe                 # 1 request: is the API alive, how many listings
    python -m src.scraping.run collect               # full collection per configs/scraping.yaml

collect logic:
- a queue of segments (region × price band), room count inside the request;
- if a segment holds more listings than the search limit, the price band is split
  in half and both halves go back into the queue (adaptive segmentation);
- each segment is paged until an empty page or the limit;
- checkpoints in SQLite: a rerun on the same day skips finished segments.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import deque
from pathlib import Path

import yaml

from .client import AntibotSuspected, CianClient, build_json_query
from .parse import extract_offers, parse_offer, total_count
from .storage import OFFER_COLUMNS, Storage

log = logging.getLogger("cian")

PAGE_SIZE = 28  # listings per search page


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def cmd_probe(cfg: dict) -> None:
    client = CianClient(min_delay=0, max_delay=0, proxy=cfg.get("proxy"))
    query = build_json_query(
        region=cfg["regions"]["spb"], rooms=cfg["rooms"],
        price_gte=1, price_lte=1_000_000, page=1,
    )
    payload = client.search(query)
    offers = extract_offers(payload)
    print(f"OK: the API responds. Total listings in SPb: {total_count(payload)}")
    if offers:
        o = offers[0]
        print(
            f"Example: {o['rooms']} rooms, {o['total_area']} m2, "
            f"{o['price']} RUB/mo, metro {o['metro_name']} ({o['metro_time_min']} min), "
            f"{o['district']}"
        )


def collect_segment(
    client: CianClient, store: Storage, cfg: dict,
    region: int, lo: int, hi: int, queue: deque,
) -> None:
    key = f"r{region}_p{lo}-{hi}"
    if store.is_segment_done(key):
        log.info("[%s] already collected today, skipping", key)
        return

    max_pages = cfg["max_pages_per_segment"]
    max_offers = cfg["max_offers_per_segment"]
    n_offers = 0
    page = 1

    while page <= max_pages:
        query = build_json_query(region, cfg["rooms"], lo, hi, page)
        try:
            payload = client.search(query)
        except AntibotSuspected:
            log.error("[%s] looks like antibot, stopping segment, will resume on rerun", key)
            return

        if page == 1:
            total = total_count(payload)
            log.info("[%s] ~%d listings in the segment", key, total)
            if total > max_offers and hi - lo > 1000:
                mid = (lo + hi) // 2
                log.info("[%s] > %d, splitting the band: [%d, %d] + [%d, %d]",
                         key, max_offers, lo, mid, mid + 1, hi)
                queue.append((region, lo, mid))
                queue.append((region, mid + 1, hi))
                return

        offers = extract_offers(payload)
        if not offers:
            break
        store.dump_raw(key, page, payload)
        raw = (payload.get("data") or {}).get("offersSerialized") or []
        n_offers += store.upsert_offers(offers, raw, region)
        log.info("[%s] page %d: +%d (total %d)", key, page, len(offers), n_offers)
        if len(offers) < PAGE_SIZE:
            break
        page += 1

    store.mark_segment_done(key, pages=page, offers=n_offers)


def cmd_collect(cfg: dict) -> None:
    store = Storage(cfg["db_path"], cfg["raw_dir"])
    client = CianClient(
        min_delay=cfg["min_delay_sec"], max_delay=cfg["max_delay_sec"],
        proxy=cfg.get("proxy"),
    )
    queue: deque = deque(
        (region, lo, hi)
        for region in cfg["regions"].values()
        for lo, hi in cfg["price_bands"]
    )
    while queue:
        region, lo, hi = queue.popleft()
        collect_segment(client, store, cfg, region, lo, hi, queue)

    stats = store.stats()
    log.info("Done. Today: %(offers_today)d records, "
             "unique listings all-time: %(offers_unique_total)d", stats)


def cmd_reparse(cfg: dict) -> None:
    """Refills the parsed columns from the stored raw_json.

    Useful after fixes in parse.py, without re-downloading the data.
    """
    store = Storage(cfg["db_path"], cfg["raw_dir"])
    conn = store.conn
    cols = [c for c in OFFER_COLUMNS
            if c not in ("offer_id", "snapshot_date", "region", "raw_json")]
    set_clause = ", ".join(f"{c}=?" for c in cols)
    rows = conn.execute(
        "SELECT offer_id, snapshot_date, raw_json FROM offers"
    ).fetchall()
    n = 0
    for offer_id, snap, raw in rows:
        rec = parse_offer(json.loads(raw))
        conn.execute(
            f"UPDATE offers SET {set_clause} WHERE offer_id=? AND snapshot_date=?",
            tuple(rec.get(c) for c in cols) + (offer_id, snap),
        )
        n += 1
        if n % 2000 == 0:
            conn.commit()
            log.info("reparse: %d/%d", n, len(rows))
    conn.commit()
    log.info("reparse done: %d records", n)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("scraping.log", encoding="utf-8"),
        ],
    )
    parser = argparse.ArgumentParser(description="Collect rental listings from CIAN")
    parser.add_argument("command", choices=["probe", "collect", "reparse"])
    parser.add_argument("--config", default="configs/scraping.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config)
    Path(cfg["raw_dir"]).mkdir(parents=True, exist_ok=True)

    if args.command == "probe":
        cmd_probe(cfg)
    elif args.command == "reparse":
        cmd_reparse(cfg)
    else:
        cmd_collect(cfg)


if __name__ == "__main__":
    main()
