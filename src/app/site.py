"""Product frontend for SPb Rent.

This service serves the static dashboard and exposes thin JSON endpoints for
the browser. The prediction endpoint proxies to the model API service so the
client can stay same-origin on port 8501.
"""

from __future__ import annotations

import json
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

try:
    import h3
except ImportError:  # pragma: no cover - deployment image includes h3
    h3 = None


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "processed"
STATIC = Path(__file__).resolve().parent / "static"
API_URL = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")

MODEL_MAE = 8_606
MODEL_MDAPE = 10.4
INTERVAL_COVERAGE = 77

app = FastAPI(title="SPb Rent Dashboard")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _finite(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if pd.isna(value):
        return None
    return value


def _record(row: pd.Series, columns: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col in columns:
        if col not in row.index:
            continue
        value = _finite(row[col])
        if hasattr(value, "item"):
            value = value.item()
        out[col] = value
    return out


def _normalize_interval(data: dict[str, Any]) -> dict[str, Any]:
    low = data.get("price_low")
    high = data.get("price_high")
    if low is None or high is None:
        return data
    try:
        low_f = float(low)
        high_f = float(high)
        fair_f = float(data["fair_price"]) if data.get("fair_price") is not None else None
    except (TypeError, ValueError):
        return data
    values = [low_f, high_f]
    if fair_f is not None and math.isfinite(fair_f):
        values.append(fair_f)
    if all(math.isfinite(v) for v in values):
        data["price_low"], data["price_high"] = min(values), max(values)
    return data


def _quantiles(values: pd.Series, low: float = 0.05, high: float = 0.95) -> tuple[float, float]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return 0.0, 1.0
    vmin, vmax = clean.quantile([low, high])
    if not math.isfinite(float(vmin)) or not math.isfinite(float(vmax)) or vmin == vmax:
        return float(clean.min()), float(clean.max() or clean.min() + 1)
    return float(vmin), float(vmax)


def _boundary(cell: str, lat: float | None = None, lon: float | None = None) -> list[list[float]] | None:
    if h3 is not None:
        try:
            return [[lng, lat] for lat, lng in h3.cell_to_boundary(str(cell))]
        except Exception:
            pass
    if lat is None or lon is None:
        return None
    # Graceful fallback if h3 is unavailable: a small screen-stable diamond.
    size = 0.0065
    return [
        [lon, lat + size],
        [lon + size, lat],
        [lon, lat - size],
        [lon - size, lat],
        [lon, lat + size],
    ]


@lru_cache(maxsize=1)
def load_assets() -> dict[str, Any]:
    assets = json.loads((DATA / "app_assets.json").read_text())
    districts = assets.get("districts", {})
    assets["district_options"] = sorted(districts.keys())
    assets["metro_stations"] = sorted(assets.get("metro_stations", []))
    assets["material_types"] = sorted(assets.get("material_types", []))
    assets["model"] = {
        "mae": MODEL_MAE,
        "mdape": MODEL_MDAPE,
        "interval_coverage": INTERVAL_COVERAGE,
        "n_train": 12_213,
        "n_holdout": 3_054,
        "n_clean": 15_415,
    }
    return assets


@lru_cache(maxsize=1)
def load_map_geojson() -> dict[str, Any]:
    df = pd.read_parquet(DATA / "map_hex.parquet")
    ppm2_low, ppm2_high = _quantiles(df["ppm2"])
    price_low, price_high = _quantiles(df["price_median"])

    features = []
    for _, row in df.iterrows():
        polygon = _boundary(
            str(row.get("h3_08", "")),
            lat=float(row["lat"]) if pd.notna(row.get("lat")) else None,
            lon=float(row["lon"]) if pd.notna(row.get("lon")) else None,
        )
        if not polygon:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [polygon]},
            "properties": _record(row, ["h3_08", "ppm2", "price_median", "n", "lat", "lon"]),
        })

    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "count": len(features),
            "ppm2": {"low": ppm2_low, "high": ppm2_high},
            "price_median": {"low": price_low, "high": price_high},
        },
    }


@lru_cache(maxsize=1)
def load_scan_df() -> pd.DataFrame:
    df = pd.read_parquet(DATA / "market_scan.parquet")
    for col in ["price", "fair_price", "price_low", "price_high", "delta"]:
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(-2)
    if {"price_low", "price_high"}.issubset(df.columns):
        interval_parts = [df["price_low"].copy(), df["price_high"].copy()]
        if "fair_price" in df:
            interval_parts.append(df["fair_price"].copy())
        interval = pd.concat(interval_parts, axis=1)
        df["price_low"] = interval.min(axis=1)
        df["price_high"] = interval.max(axis=1)
    if "delta_pct" in df:
        df["delta_pct"] = pd.to_numeric(df["delta_pct"], errors="coerce").round(1)
    return df


@lru_cache(maxsize=1)
def load_scan_summary() -> dict[str, int]:
    df = load_scan_df()
    counts = df["verdict"].value_counts()
    return {
        "total": int(len(df)),
        "fair": int(counts.get("fair", 0)),
        "overpriced": int(counts.get("overpriced", 0)),
        "suspicious_cheap": int(counts.get("suspicious_cheap", 0)),
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "frontend": "static-fastapi",
        "api_url": API_URL,
        "assets_ready": (DATA / "app_assets.json").exists(),
    }


@app.get("/api/assets")
def assets() -> dict[str, Any]:
    out = load_assets().copy()
    out["scan"] = load_scan_summary()
    return out


@app.get("/api/map")
def map_data() -> dict[str, Any]:
    return load_map_geojson()


@app.get("/api/scan")
def scan(verdict: str = "overpriced", limit: int = 60) -> dict[str, Any]:
    allowed = {"overpriced", "suspicious_cheap", "fair"}
    if verdict not in allowed:
        raise HTTPException(422, f"verdict must be one of {sorted(allowed)}")
    limit = min(max(limit, 1), 200)

    df = load_scan_df()
    subset = df[df["verdict"] == verdict].copy()
    subset = subset.sort_values("delta", ascending=(verdict == "suspicious_cheap")).head(limit)
    columns = [
        "district", "metro_name", "rooms_n", "total_area", "price", "fair_price",
        "price_low", "price_high", "delta", "delta_pct", "url", "offer_id", "lat", "lon",
    ]
    records = [_record(row, columns) for _, row in subset.iterrows()]
    return {"summary": load_scan_summary(), "items": records}


@app.post("/api/predict")
async def predict(request: Request) -> JSONResponse:
    payload = await request.json()
    try:
        response = requests.post(f"{API_URL}/predict", json=payload, timeout=30)
    except requests.RequestException as exc:
        raise HTTPException(503, f"Model API is unavailable: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(502, "Model API returned a non-JSON response") from exc

    if response.status_code >= 400:
        return JSONResponse(data, status_code=response.status_code)
    if isinstance(data, dict):
        data = _normalize_interval(data)
    return JSONResponse(data)
