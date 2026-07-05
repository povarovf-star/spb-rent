"""Слияние источников (ЦИАН + Яндекс) в единый сырой датасет.

Проблема: у Яндекса из карточки берутся только цена/комнаты/площадь/этаж/
координаты. Модель же обучена на признаках ЦИАН (район, метро, тип дома...).
Решение:
  - гео-признаки (район, ближайшее метро и время до него) для Яндекса
    ДЕРИВИМ по ближайшим соседям из ЦИАН (KNN по координатам) — данные,
    а не догадки: рядом стоящие квартиры в одном районе и у одного метро;
  - остальные поля (год, материал, текстовые) остаются пустыми → в модели
    закроются нейтральными дефолтами (как в inference-пути).

Кросс-платформенную дедупликацию делает существующий clean.py: одна квартира
с ЦИАН и Яндекса совпадёт по ключу (координаты+площадь+этаж+комнаты).

Запуск (после сбора Яндекса):
    python -m src.cleaning.merge_sources
Выход: data/processed/all_sources_raw.parquet (колонка source).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

from src.cleaning.clean import COLUMNS as CIAN_COLUMNS

CIAN_DB = "data/cian.sqlite"
YANDEX_DB = "data/yandex.sqlite"
OUT = "data/processed/all_sources_raw.parquet"


def load_cian() -> pd.DataFrame:
    conn = sqlite3.connect(CIAN_DB)
    snap = conn.execute("SELECT MAX(snapshot_date) FROM offers").fetchone()[0]
    df = pd.read_sql(
        f"SELECT {', '.join(CIAN_COLUMNS)} FROM offers WHERE snapshot_date = ?",
        conn, params=(snap,))
    conn.close()
    df["source"] = "cian"
    return df


def load_yandex() -> pd.DataFrame:
    conn = sqlite3.connect(YANDEX_DB)
    df = pd.read_sql(
        "SELECT offer_id, snapshot_date, price, rooms, flat_type, total_area, "
        "floor, floors_total, lat, lon, url FROM yandex_offers "
        "WHERE price IS NOT NULL AND lat IS NOT NULL", conn)
    conn.close()
    df["source"] = "yandex"
    return df


def derive_geo_from_cian(yx: pd.DataFrame, cian: pd.DataFrame, k: int = 7) -> pd.DataFrame:
    """Заполняет район/метро для Яндекса по k ближайшим объявлениям ЦИАН.

    district — по большинству среди соседей; metro_name/time — от самого
    близкого соседа. Координаты в радианах, метрика haversine (BallTree).
    """
    ref = cian.dropna(subset=["lat", "lon"]).reset_index(drop=True)
    tree = BallTree(np.radians(ref[["lat", "lon"]].values), metric="haversine")
    q = np.radians(yx[["lat", "lon"]].values)
    dist, idx = tree.query(q, k=k)

    districts, okrugs, metro_names, metro_times = [], [], [], []
    for row_idx in idx:
        neigh = ref.iloc[row_idx]
        # район — мода среди соседей
        d = neigh["district"].mode()
        districts.append(d.iloc[0] if len(d) else None)
        o = neigh["okrug"].mode()
        okrugs.append(o.iloc[0] if len(o) else None)
        # метро — от ближайшего соседа (первый в списке)
        metro_names.append(neigh["metro_name"].iloc[0])
        metro_times.append(neigh["metro_time_min"].iloc[0])
    yx = yx.copy()
    yx["district"] = districts
    yx["okrug"] = okrugs
    yx["metro_name"] = metro_names
    yx["metro_time_min"] = metro_times
    yx["metro_transport"] = "walk"
    return yx


def normalize_yandex(yx: pd.DataFrame, cian: pd.DataFrame) -> pd.DataFrame:
    """Приводит Яндекс к сырой схеме ЦИАН (недостающее = NaN)."""
    yx = derive_geo_from_cian(yx, cian)
    out = pd.DataFrame(index=yx.index)
    # прямые соответствия
    out["offer_id"] = yx["offer_id"]
    out["snapshot_date"] = yx["snapshot_date"]
    out["price"] = yx["price"]
    out["rooms"] = yx["rooms"].where(yx["flat_type"] == "rooms", 0)
    out["flat_type"] = yx["flat_type"]
    out["total_area"] = yx["total_area"]
    out["floor"] = yx["floor"]
    out["floors_total"] = yx["floors_total"]
    out["lat"] = yx["lat"]
    out["lon"] = yx["lon"]
    out["district"] = yx["district"]
    out["okrug"] = yx["okrug"]
    out["metro_name"] = yx["metro_name"]
    out["metro_time_min"] = yx["metro_time_min"]
    out["metro_transport"] = yx["metro_transport"]
    out["url"] = yx["url"]
    out["region"] = 2                       # только СПб
    out["source"] = "yandex"
    # поля, которых у Яндекса нет — заполнятся дефолтами в build_features
    for col in ["deposit", "client_fee_pct", "agent_fee_pct", "utilities_included",
                "is_apartments", "living_area", "kitchen_area", "build_year",
                "material_type", "address", "is_by_homeowner", "published_ts",
                "photos_count", "description"]:
        out[col] = np.nan
    return out[CIAN_COLUMNS + ["source"]]


def main() -> None:
    cian = load_cian()
    yx = load_yandex()
    print(f"ЦИАН: {len(cian)} | Яндекс: {len(yx)}")
    yx_norm = normalize_yandex(yx, cian)
    combined = pd.concat([cian, yx_norm], ignore_index=True)
    combined["offer_id"] = combined["offer_id"].astype(str)  # int(cian)+str(yandex)
    Path(OUT).parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(OUT, index=False)
    print(f"объединено: {len(combined)} строк ({dict(combined.source.value_counts())})")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
