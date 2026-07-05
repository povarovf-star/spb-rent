"""Готовит статические ассеты приложения из обработанных данных.

- app_assets.json: справочники (районы с центроидами, станции метро,
  типы домов) + дефолты признаков (медианы) + диапазоны валидации формы;
- map_hex.parquet: агрегаты по H3-гексагонам для карты цен.

Запуск: python -m src.app.prepare_assets  (после explain scan)
"""

from __future__ import annotations

import json
from pathlib import Path

import h3
import pandas as pd

OUT = Path("data/processed")

# Разрешение H3 для КАРТЫ (не для модели). res 8 (~0.7 км²) даёт слишком
# мелкие гексагоны — 976 штук по ~7 оферов, город в дырах. res 7 (~5 км²)
# укрупняет до ~300 плотных ячеек, весь СПб закрашивается.
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
        "form_ranges": {  # валидация ввода диапазонами train-данных
            "total_area": [float(df["total_area"].quantile(0.005)),
                            float(df["total_area"].quantile(0.995))],
            "price_typical": [float(df["price"].quantile(0.05)),
                               float(df["price"].quantile(0.95))],
        },
    }
    (OUT / "app_assets.json").write_text(
        json.dumps(assets, ensure_ascii=False, indent=1))

    # карта: медианная цена/м² и число объявлений по гексагонам
    # карта: агрегаты по укрупнённым H3-гексагонам (res 7) из координат
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
        .rename(columns={"h3_map": "h3_08"})  # ключ оставляем 'h3_08' для фронта
    )
    hex_agg["ppm2"] = hex_agg["ppm2"].round(0)
    hex_agg["price_median"] = hex_agg["price_median"].round(-2)
    hex_agg.to_parquet(OUT / "map_hex.parquet", index=False)
    print(f"районов: {len(districts)}, метро: {len(metros)}, "
          f"гексагонов на карте (res {MAP_H3_RES}): {len(hex_agg)}, "
          f"оферов покрыто: {int(hex_agg['n'].sum())}, макс/гекс: {int(hex_agg['n'].max())}")


if __name__ == "__main__":
    main()
