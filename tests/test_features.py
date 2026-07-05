"""Тесты feature-пайплайна."""

import numpy as np
import pandas as pd

from src.features.geo import haversine_km, add_geo_features
from src.features.text import extract_text_features
from src.features.build import build_features, CAT_FEATURES, NUM_FEATURES


def test_haversine_known_distance():
    # Дворцовая -> Московский вокзал ≈ 2.8 км
    d = haversine_km(pd.Series([59.9390]), pd.Series([30.3158]), 59.9298, 30.3623)
    assert 2.0 < d.iloc[0] < 3.5


def test_text_features():
    desc = ("Уютная квартира с евроремонтом, есть посудомойка и стиральная "
            "машина. Можно с животными. Балкон застеклён.")
    f = extract_text_features(desc)
    assert f["renov_euro"] == 1
    assert f["dishwasher"] == 1
    assert f["washer"] == 1
    assert f["pets_ok"] == 1
    assert f["balcony"] == 1
    assert f["renov_needed"] == 0
    assert f["has_description"] == 1


def test_text_features_empty():
    f = extract_text_features(None)
    assert f["has_description"] == 0
    assert f["desc_len"] == 0


def _sample_df():
    return pd.DataFrame({
        "offer_id": [1, 2], "snapshot_date": ["2026-07-02"] * 2,
        "price": [40000, 25000], "deposit": [40000, None],
        "client_fee_pct": [0, 50], "utilities_included": [None, 1],
        "rooms": [1, None], "rooms_n": [1, 0], "is_studio": [0, 1],
        "flat_type": ["rooms", "studio"], "is_apartments": [None, 1],
        "total_area": [35.0, 22.0], "living_area": [18.0, None],
        "kitchen_area": [9.0, None], "floor": [1, 9], "floors_total": [9, 9],
        "build_year": [1980, None], "material_type": ["panel", None],
        "lat": [59.94, 60.05], "lon": [30.32, 30.44],
        "district": ["Центральный", None], "okrug": ["Дворцовый", None],
        "address": ["a", "b"], "metro_name": ["Невский проспект", None],
        "metro_time_min": [5.0, 10.0], "metro_transport": ["walk", "transport"],
        "is_by_homeowner": [1.0, None], "published_ts": [1, 2],
        "photos_count": [10, 0], "region": [2, 4588], "is_lenobl": [0, 1],
        "url": ["u1", "u2"], "is_suspicious_cheap": [0, 0],
        "description": ["Косметический ремонт, холодильник", None],
    })


def test_build_features_shapes_and_values():
    out = build_features(_sample_df())
    assert len(out) == 2
    for c in CAT_FEATURES + NUM_FEATURES:
        assert c in out.columns, f"нет колонки {c}"
    r = out.iloc[0]
    assert r["floor_first"] == 1 and r["floor_last"] == 0
    assert abs(r["dist_center_km"]) < 1.0          # Дворцовый округ — центр
    assert r["metro_walk_min"] == 5.0              # walk как есть
    assert out.iloc[1]["metro_walk_min"] == 30.0   # transport ×3
    assert r["renov_cosmetic"] == 1 and r["fridge"] == 1
    assert r["building_age"] == 46
    # категории не содержат NaN
    assert (out[CAT_FEATURES].isna().sum() == 0).all()
    # у второй строки district заполнен заглушкой
    assert out.iloc[1]["district"] == "unknown"
