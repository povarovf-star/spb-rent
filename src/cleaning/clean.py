"""Data cleaning: junk filters + deduplication of agency clones.

Every filter is logged: how many rows were dropped and why. The result is the
report data/processed/cleaning_report.md (the loss table goes straight into the README).

Run:
    python -m src.cleaning.clean                # data/cian.sqlite by default
    python -m src.cleaning.clean --db path.sqlite --out data/processed
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

# Hard validity bounds (long-term rentals, SPb + near suburbs)
PRICE_MIN, PRICE_MAX = 8_000, 350_000
AREA_MIN, AREA_MAX = 10.0, 200.0
PPM2_MIN, PPM2_MAX = 300.0, 6_000.0  # RUB per m2 per month

COLUMNS = """offer_id snapshot_date price deposit client_fee_pct agent_fee_pct
utilities_included rooms flat_type is_apartments total_area living_area
kitchen_area floor floors_total build_year material_type lat lon district
okrug address metro_name metro_time_min metro_transport is_by_homeowner
published_ts photos_count region url description""".split()


def load(db_path: str | Path, snapshot: str | None = None) -> pd.DataFrame:
    """Reads the latest (or given) snapshot. raw_json is skipped, it is heavy."""
    conn = sqlite3.connect(db_path)
    if snapshot is None:
        snapshot = conn.execute(
            "SELECT MAX(snapshot_date) FROM offers"
        ).fetchone()[0]
    df = pd.read_sql(
        f"SELECT {', '.join(COLUMNS)} FROM offers WHERE snapshot_date = ?",
        conn, params=(snapshot,),
    )
    conn.close()
    return df


class FilterLog:
    def __init__(self, n_start: int) -> None:
        self.rows: list[tuple[str, int, int]] = []
        self.n = n_start

    def apply(self, df: pd.DataFrame, mask_keep: pd.Series, name: str) -> pd.DataFrame:
        dropped = int((~mask_keep).sum())
        df = df[mask_keep].copy()
        self.rows.append((name, dropped, len(df)))
        self.n = len(df)
        return df

    def to_markdown(self, n_start: int) -> str:
        lines = [
            "| Filter | Rows dropped | Remaining |",
            "|---|---:|---:|",
            f"| initial | - | {n_start} |",
        ]
        for name, dropped, left in self.rows:
            lines.append(f"| {name} | {dropped} | {left} |")
        return "\n".join(lines)


def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    """Derived columns needed by the filters and the model."""
    df = df.copy()
    # rooms_n: 0 = studio/open plan, otherwise the number of rooms
    df["rooms_n"] = df["rooms"].where(df["flat_type"] == "rooms", 0)
    df["is_studio"] = (df["flat_type"] == "studio").astype(int)
    df["is_lenobl"] = (df["region"] == 4588).astype(int)
    df["price_per_m2"] = df["price"] / df["total_area"]
    if "source" not in df.columns:       # single source, default to CIAN
        df["source"] = "cian"
    return df


def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    n_start = len(df)
    log = FilterLog(n_start)
    df = add_derived(df)

    df = log.apply(df, df["price"].between(PRICE_MIN, PRICE_MAX),
                   f"price outside [{PRICE_MIN}, {PRICE_MAX}] RUB/mo")
    df = log.apply(df, df["total_area"].between(AREA_MIN, AREA_MAX),
                   f"area outside [{AREA_MIN}, {AREA_MAX}] m2")
    df = log.apply(df, df["lat"].notna() & df["lon"].notna(), "no coordinates")
    df = log.apply(df, df["price_per_m2"].between(PPM2_MIN, PPM2_MAX),
                   f"price/m2 outside [{PPM2_MIN}, {PPM2_MAX}] RUB")
    df = log.apply(df, df["rooms_n"].notna(), "room count undetermined")
    # gross geocode error: far outside the agglomeration
    df = log.apply(df, df["lat"].between(59.5, 60.5) & df["lon"].between(29.5, 31.0),
                   "coordinates outside the SPb agglomeration")

    # --- suspiciously cheap flag (scam): < 50% of the district's median price/m2 ---
    seg = df["district"].fillna("LO")
    seg_median = df.groupby(seg)["price_per_m2"].transform("median")
    df["is_suspicious_cheap"] = (df["price_per_m2"] < 0.5 * seg_median).astype(int)

    # --- deduplication of agency clones ---
    # One flat listed by several agencies: matching coordinates (~10 m),
    # area (±0.25 m2), floor and room count. Keep the owner's listing,
    # and on a tie the minimum price.
    df["dup_key"] = (
        df["lat"].round(4).astype(str) + "_" + df["lon"].round(4).astype(str)
        + "_" + (df["total_area"] * 2).round().astype(int).astype(str)
        + "_" + df["floor"].astype(str) + "_" + df["rooms_n"].astype(int).astype(str)
    )
    df["n_clones"] = df.groupby("dup_key")["offer_id"].transform("count")
    # number of platforms in a duplicate group: 2 = the flat is on both CIAN and Yandex
    df["n_platforms"] = df.groupby("dup_key")["source"].transform("nunique")
    df["cross_platform"] = (df["n_platforms"] >= 2).astype(int)
    # merge priority: CIAN (richer fields) > owner > lower price
    df["_cian_first"] = (df["source"] != "cian").astype(int)
    df = df.sort_values(
        ["dup_key", "_cian_first", "is_by_homeowner", "price"],
        ascending=[True, True, False, True],
    )
    before = len(df)
    df = df.drop_duplicates("dup_key", keep="first").drop(columns="_cian_first")
    log.rows.append(("duplicates (agencies + cross-platform)", before - len(df), len(df)))

    report = log.to_markdown(n_start)
    return df.drop(columns=["dup_key"]), report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/cian.sqlite")
    parser.add_argument("--parquet", default=None,
                        help="read the combined dataset (CIAN+Yandex) instead of sqlite")
    parser.add_argument("--out", default="data/processed")
    parser.add_argument("--snapshot", default=None)
    args = parser.parse_args()

    if args.parquet:
        df = pd.read_parquet(args.parquet)
    else:
        df = load(args.db, args.snapshot)
    snapshot = df["snapshot_date"].iloc[0]
    cleaned, report = clean(df)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cleaned.to_parquet(out / "listings.parquet", index=False)

    header = (
        f"# Data cleaning report\n\nSnapshot: {snapshot}. "
        f"Input: {len(df)} listings, output: {len(cleaned)}.\n\n"
    )
    by_source = dict(cleaned["source"].value_counts()) if "source" in cleaned else {}
    extras = (
        f"\n\nFlagged (not removed):\n"
        f"- suspiciously cheap (< 50% of the district median): "
        f"{int(cleaned['is_suspicious_cheap'].sum())}\n"
        f"- apart-hotels: {int(cleaned['is_apartments'].fillna(0).sum())}\n"
        f"- Leningrad Oblast: {int(cleaned['is_lenobl'].sum())}\n"
        f"- by source: {by_source}\n"
        f"- cross-platform (flat on both CIAN and Yandex): "
        f"{int(cleaned['cross_platform'].sum()) if 'cross_platform' in cleaned else 0}\n"
    )
    (out / "cleaning_report.md").write_text(header + report + extras, encoding="utf-8")
    print(header + report + extras)


if __name__ == "__main__":
    main()
