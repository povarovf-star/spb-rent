"""Из пользовательского ввода — в вектор признаков модели.

Пользователь заполняет ~10 полей формы; остальные признаки заполняются
нейтральными дефолтами (медианы train), чтобы не сигналить модели
«плохое объявление» нулями.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

try:
    import h3
except ImportError:
    h3 = None

from src.features.build import CAT_FEATURES, NUM_FEATURES
from src.features.geo import DVORTSOVAYA, MOSCOW_STATION, H3_RESOLUTION, haversine_km

ASSETS_PATH = Path("data/processed/app_assets.json")


@dataclass
class FlatInput:
    district: str
    rooms: int                    # 0 = студия
    total_area: float
    floor: int
    floors_total: int
    metro_walk_min: float = 15.0
    metro_name: str = "unknown"
    build_year: int | None = None
    material_type: str = "unknown"
    is_apartments: bool = False
    is_by_homeowner: bool = False
    dishwasher: bool = False
    furnished: bool = True
    renov_euro: bool = False
    balcony: bool = False
    lat: float | None = None      # если нет — центроид района
    lon: float | None = None


def load_assets(path: Path = ASSETS_PATH) -> dict:
    return json.loads(path.read_text())


def features_from_input(inp: FlatInput, assets: dict) -> pd.DataFrame:
    d = assets["defaults"]
    lat, lon = inp.lat, inp.lon
    if lat is None or lon is None:
        c = assets["districts"].get(inp.district)
        if c is None:
            raise ValueError(f"неизвестный район: {inp.district}")
        lat, lon = c["lat"], c["lon"]

    lat_s, lon_s = pd.Series([lat]), pd.Series([lon])
    row = {
        # категориальные
        "district": inp.district,
        "okrug": "unknown",
        "metro_name": inp.metro_name,
        "material_type": inp.material_type,
        "flat_type": "studio" if inp.rooms == 0 else "rooms",
        "h3_08": h3.latlng_to_cell(lat, lon, H3_RESOLUTION) if h3 else "unknown",
        # квартира
        "total_area": inp.total_area,
        "living_area": inp.total_area * d["living_area_ratio"],
        "kitchen_area": inp.total_area * d["kitchen_area_ratio"],
        "rooms_n": inp.rooms,
        "is_studio": int(inp.rooms == 0),
        "floor": inp.floor,
        "floors_total": inp.floors_total,
        "floor_first": int(inp.floor == 1),
        "floor_last": int(inp.floor == inp.floors_total),
        "floor_ratio": min(max(inp.floor / max(inp.floors_total, 1), 0), 1),
        "building_age": (2026 - inp.build_year) if inp.build_year else None,
        # гео
        "dist_center_km": float(haversine_km(lat_s, lon_s, *DVORTSOVAYA).iloc[0]),
        "dist_moscow_st_km": float(haversine_km(lat_s, lon_s, *MOSCOW_STATION).iloc[0]),
        "metro_walk_min": inp.metro_walk_min,
        "has_metro_nearby": int(inp.metro_walk_min < 60),
        "is_lenobl": 0,
        "is_apartments": int(inp.is_apartments),
        "is_by_homeowner": int(inp.is_by_homeowner),
        # условия/качество: нейтральные дефолты
        "photos_count": d["photos_count"],
        "no_deposit": 0,
        "utilities_included": 0,
        "no_client_fee": int(inp.is_by_homeowner),
        # текстовые признаки из чекбоксов, остальные 0
        "renov_euro": int(inp.renov_euro),
        "renov_cosmetic": 0, "renov_needed": 0,
        "furnished": int(inp.furnished), "unfurnished": int(not inp.furnished),
        "dishwasher": int(inp.dishwasher), "washer": 1, "aircon": 0, "fridge": 1,
        "pets_ok": 0, "pets_no": 0, "kids_no": 0,
        "balcony": int(inp.balcony), "nice_view": 0, "parking": 0,
        "concierge": 0, "new_building": 0,
        "has_description": 1, "desc_len": d["desc_len"],
    }
    df = pd.DataFrame([row])
    missing = set(CAT_FEATURES + NUM_FEATURES) - set(df.columns)
    if missing:
        raise RuntimeError(f"не заполнены признаки: {missing}")
    return df
