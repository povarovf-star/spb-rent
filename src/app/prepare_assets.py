"""Prepares the app's static assets from the processed data.

- app_assets.json: lookups (districts with centroids, metro stations,
  building types) + feature defaults (medians) + form validation ranges;
- map_hex.parquet: H3 hexagon aggregates for the price map.

Run: python -m src.app.prepare_assets  (after explain scan)
"""

from __future__ import annotations

import json
from pathlib import Path

import h3
import pandas as pd

OUT = Path("data/processed")

# H3 resolution for the MAP (not the model). res 8 (~0.7 km2) gives hexagons
# that are too small: 976 of them with ~7 offers each, the city full of holes.
# res 7 (~5 km2) coarsens to ~300 dense cells, and all of SPb gets filled in.
MAP_H3_RES = 7
MAP_MIN_OFFERS = 1


def main() -> None:
    df = pd.read_parquet(OUT / "features.parquet")
    spb = df[df["is_lenobl"] == 0]

    districts = (
        spb.groupby("district")
        .agg(lat=("lat", "median"), lon=("lon", "median"), n=("price", "size"))
        .query("n >= 30").drop(columns="n")
        .round(5).to_dict("index")
    )
    metros = sorted(m for m in df["metro_name"].unique() if m != "unknown")
    materials = sorted(m for m in df["material_type"].unique() if m != "unknown")

    assets = {
        "districts": districts,
        "metro_stations": metros,
        "material_types": materials,
        "defaults": {
            "photos_count": float(df["photos_count"].median()),
            "desc_len": float(df["desc_len"].median()),
            "living_area_ratio": float((df["living_area"] / df["total_area"]).median()),
            "kitchen_area_ratio": float((df["kitchen_area"] / df["total_area"]).median()),
        },
        "form_ranges": {  # input validation by train-data ranges
            "total_area": [float(df["total_area"].quantile(0.005)),
                            float(df["total_area"].quantile(0.995))],
            "price_typical": [float(df["price"].quantile(0.05)),
                               float(df["price"].quantile(0.95))],
        },
    }
    (OUT / "app_assets.json").write_text(
        json.dumps(assets, ensure_ascii=False, indent=1))

    # map: median price/m2 and listing count per hexagon,
    # aggregated over coarser H3 hexagons (res 7) from coordinates
    g = df.dropna(subset=["lat", "lon"]).copy()
    g["price_per_m2"] = g["price"] / g["total_area"]
    g["h3_map"] = [h3.latlng_to_cell(la, lo, MAP_H3_RES)
                   for la, lo in zip(g["lat"], g["lon"])]
    hex_agg = (
        g.groupby("h3_map")
        .agg(ppm2=("price_per_m2", "median"),
             price_median=("price", "median"), n=("price", "size"),
             lat=("lat", "median"), lon=("lon", "median"))
        .query("n >= @MAP_MIN_OFFERS").reset_index()
        .rename(columns={"h3_map": "h3_08"})  # keep the 'h3_08' key for the front
    )
    hex_agg["ppm2"] = hex_agg["ppm2"].round(0)
    hex_agg["price_median"] = hex_agg["price_median"].round(-2)
    hex_agg.to_parquet(OUT / "map_hex.parquet", index=False)
    print(f"districts: {len(districts)}, metros: {len(metros)}, "
          f"map hexagons (res {MAP_H3_RES}): {len(hex_agg)}, "
          f"offers covered: {int(hex_agg['n'].sum())}, max/hex: {int(hex_agg['n'].max())}")


if __name__ == "__main__":
    main()
