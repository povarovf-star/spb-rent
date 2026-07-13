"""Geo features: distance to the center, H3 hexagons.

Distance to the metro is NOT computed from station coordinates: CIAN already
provides metro_time_min + metro_transport from the listing itself, which is more
accurate than a straight line (it reflects real accessibility). It is converted
to a single "minutes on foot" scale (transport time is roughly 3x walking).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

try:
    import h3
except ImportError:  # h3 is only needed for hexagons
    h3 = None

# City landmarks
DVORTSOVAYA = (59.9390, 30.3158)   # Palace Square, the "center"
MOSCOW_STATION = (59.9298, 30.3623)  # Moskovsky railway station, a transport hub

H3_RESOLUTION = 8  # ~0.7 km2 per hexagon, a block/microdistrict


def haversine_km(lat1, lon1, lat2: float, lon2: float):
    """Vectorized haversine: (Series, Series, float, float) -> Series."""
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

    # single metro accessibility scale: minutes on foot
    # transport time is converted x3 (empirical: 10 min by transport ~ 30 on foot)
    walk_min = df["metro_time_min"].where(
        df["metro_transport"] == "walk", df["metro_time_min"] * 3
    )
    df["metro_walk_min"] = walk_min.fillna(90.0)  # no metro nearby = "very far"
    df["has_metro_nearby"] = df["metro_time_min"].notna().astype(int)

    if h3 is not None:
        df["h3_08"] = [
            h3.latlng_to_cell(lat, lon, H3_RESOLUTION)
            for lat, lon in zip(df["lat"], df["lon"])
        ]
    return df
