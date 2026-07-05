"""Гео-признаки: расстояния до центра, H3-гексагоны.

Расстояние до метро НЕ считаем по координатам станций: у ЦИАН уже есть
metro_time_min + metro_transport из самого объявления — это точнее прямой
линии (учитывает реальную доступность). Конвертируем в единую шкалу
«минут пешком» (транспортное время ≈ ×3 пешего по покрытию).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

try:
    import h3
except ImportError:  # h3 нужен только для гексагонов
    h3 = None

# Ориентиры города
DVORTSOVAYA = (59.9390, 30.3158)   # Дворцовая площадь — «центр»
MOSCOW_STATION = (59.9298, 30.3623)  # Московский вокзал — транспортный узел

H3_RESOLUTION = 8  # ~0.7 км² на гексагон — квартал/микрорайон


def haversine_km(lat1, lon1, lat2: float, lon2: float):
    """Векторизованный haversine: (Series, Series, float, float) -> Series."""
    lat1, lon1 = np.radians(lat1), np.radians(lon1)
    lat2, lon2 = np.radians(lat2), np.radians(lon2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def add_geo_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["dist_center_km"] = haversine_km(df["lat"], df["lon"], *DVORTSOVAYA)
    df["dist_moscow_st_km"] = haversine_km(df["lat"], df["lon"], *MOSCOW_STATION)

    # единая шкала доступности метро: минуты пешком
    # transport-время конвертируем ×3 (эмпирика: 10 мин на транспорте ≈ 30 пешком)
    walk_min = df["metro_time_min"].where(
        df["metro_transport"] == "walk", df["metro_time_min"] * 3
    )
    df["metro_walk_min"] = walk_min.fillna(90.0)  # нет метро рядом = «очень далеко»
    df["has_metro_nearby"] = df["metro_time_min"].notna().astype(int)

    if h3 is not None:
        df["h3_08"] = [
            h3.latlng_to_cell(lat, lon, H3_RESOLUTION)
            for lat, lon in zip(df["lat"], df["lon"])
        ]
    return df
