"""Single entry point for feature building: build_features(df) -> df.

Input is the clean dataset from src.cleaning (data/processed/listings.parquet).
Output is the feature matrix for the model (data/processed/features.parquet).

IMPORTANT: there are no target-dependent features here (no target encoding);
those are computed inside CV during training to avoid leakage.

Run: python -m src.features.build
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .geo import add_geo_features
from .text import add_text_features

CURRENT_YEAR = 2026

# Categorical features for CatBoost (passed to cat_features)
CAT_FEATURES = ["district", "okrug", "metro_name", "material_type", "flat_type", "h3_08"]

# Numeric/binary features
NUM_FEATURES = [
    "total_area", "living_area", "kitchen_area", "rooms_n", "is_studio",
    "floor", "floors_total", "floor_first", "floor_last", "floor_ratio",
    "building_age", "dist_center_km", "dist_moscow_st_km",
    "metro_walk_min", "has_metro_nearby",
    "is_by_homeowner", "is_apartments", "is_lenobl", "photos_count",
    # deposit_ratio EXCLUDED: deposit/price has the target in the denominator, a leak
    "no_deposit", "utilities_included", "no_client_fee",
    # text
    "renov_euro", "renov_cosmetic", "renov_needed", "furnished", "unfurnished",
    "dishwasher", "washer", "aircon", "fridge", "pets_ok", "pets_no", "kids_no",
    "balcony", "nice_view", "parking", "concierge", "new_building",
    "has_description", "desc_len",
]

TARGET = "price"


def add_flat_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["floor_first"] = (df["floor"] == 1).astype(int)
    df["floor_last"] = (df["floor"] == df["floors_total"]).astype(int)
    df["floor_ratio"] = (df["floor"] / df["floors_total"]).clip(0, 1)
    df["building_age"] = (CURRENT_YEAR - df["build_year"]).clip(0, 300)
    # deal terms
    df["deposit_ratio"] = (df["deposit"] / df["price"]).clip(0, 3)
    df["no_deposit"] = (df["deposit"].fillna(0) == 0).astype(int)
    df["no_client_fee"] = (df["client_fee_pct"].fillna(0) == 0).astype(int)
    df["is_by_homeowner"] = df["is_by_homeowner"].fillna(0).astype(int)
    df["is_apartments"] = df["is_apartments"].fillna(0).astype(int)
    df["utilities_included"] = df["utilities_included"].fillna(0).astype(int)
    return df


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Full feature pipeline. Returns a df with columns
    CAT_FEATURES + NUM_FEATURES + TARGET + service columns (offer_id, lat, lon)."""
    df = add_flat_features(df)
    df = add_geo_features(df)
    df = add_text_features(df)

    for c in CAT_FEATURES:
        df[c] = df[c].fillna("unknown").astype(str)

    keep = ["offer_id", "snapshot_date", "lat", "lon", "url",
            "is_suspicious_cheap", TARGET] + CAT_FEATURES + NUM_FEATURES
    return df[keep]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="inp", default="data/processed/listings.parquet")
    parser.add_argument("--out", default="data/processed/features.parquet")
    args = parser.parse_args()

    df = pd.read_parquet(args.inp)
    feats = build_features(df)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    feats.to_parquet(args.out, index=False)

    n_nan = feats[NUM_FEATURES].isna().sum()
    print(f"features: {len(CAT_FEATURES)} categorical + {len(NUM_FEATURES)} numeric")
    print(f"rows: {len(feats)}")
    print("NaN in numeric (top):")
    print(n_nan[n_nan > 0].sort_values(ascending=False).head(10).to_string())


if __name__ == "__main__":
    main()
