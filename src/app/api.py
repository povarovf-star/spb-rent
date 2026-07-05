"""FastAPI: модель как сервис.

POST /predict — параметры квартиры -> справедливая цена, интервал,
факторы (₽), вердикт по фактической цене (если передана).
GET /health — статус и версия модели.

Запуск: uvicorn src.app.api:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.app.inference import FlatInput, features_from_input, load_assets
from src.models.explain import Explainer

STATE: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    STATE["explainer"] = Explainer()
    STATE["assets"] = load_assets()
    yield
    STATE.clear()


app = FastAPI(title="SPb Rent — справедливая цена аренды", lifespan=lifespan)


class PredictRequest(BaseModel):
    district: str = Field(examples=["Калининский"])
    rooms: int = Field(ge=0, le=6, description="0 = студия")
    total_area: float = Field(gt=5, lt=300)
    floor: int = Field(ge=1, le=60)
    floors_total: int = Field(ge=1, le=60)
    metro_walk_min: float = Field(default=15, ge=1, le=90)
    metro_name: str = "unknown"
    build_year: int | None = Field(default=None, ge=1800, le=2026)
    material_type: str = "unknown"
    is_apartments: bool = False
    is_by_homeowner: bool = False
    dishwasher: bool = False
    furnished: bool = True
    renov_euro: bool = False
    balcony: bool = False
    actual_price: float | None = Field(default=None, gt=0,
                                       description="для вердикта о переплате")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model": "catboost", "features_ready": bool(STATE)}


@app.get("/districts")
def districts() -> list[str]:
    return sorted(STATE["assets"]["districts"])


@app.post("/predict")
def predict(req: PredictRequest) -> dict:
    if req.floor > req.floors_total:
        raise HTTPException(422, "этаж больше этажности дома")
    assets = STATE["assets"]
    if req.district not in assets["districts"]:
        raise HTTPException(422, f"район должен быть одним из: "
                                 f"{sorted(assets['districts'])}")
    lo, hi = assets["form_ranges"]["total_area"]
    warning = None
    if not (lo <= req.total_area <= hi):
        warning = (f"площадь {req.total_area} м² — редкая для рынка, "
                   f"оценка менее надёжна")

    flat = FlatInput(**req.model_dump(exclude={"actual_price"}))
    row = features_from_input(flat, assets)
    if req.actual_price:
        row["price"] = req.actual_price
    result = STATE["explainer"].explain(row)
    if warning:
        result["warning"] = warning
    return result
