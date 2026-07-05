"""Извлечение плоских записей объявлений из ответа API ЦИАН.

Только stdlib — модуль используется и в тестах без сетевых зависимостей.
Схема ответа может меняться: каждое поле достаём через безопасные .get,
а сырой JSON объявления сохраняем целиком в storage — перепарсить можно всегда.
"""

from __future__ import annotations

from typing import Any


def _first_underground(geo: dict) -> dict:
    unders = geo.get("undergrounds") or []
    # приоритет: пешком и ближайшее по времени
    walking = [u for u in unders if u.get("transportType") == "walk"]
    pool = walking or unders
    pool = [u for u in pool if isinstance(u.get("time"), (int, float))]
    if not pool:
        return unders[0] if unders else {}
    return min(pool, key=lambda u: u["time"])


def _districts(geo: dict) -> tuple[str | None, str | None]:
    """(район города, муниципальный округ).

    В адресе ЦИАН: type="raion" — городской район (Красногвардейский),
    type="okrug" — муниципальный округ (Малая Охта). Оба с geoType="district".
    """
    district = okrug = None
    for addr in geo.get("address") or []:
        t = addr.get("type")
        name = addr.get("name") or addr.get("fullName")
        if t == "raion":
            district = name
        elif t == "okrug":
            okrug = name
        elif t == "district" and district is None:  # запасной вариант на смену схемы
            district = name
    return district, okrug


def parse_offer(o: dict[str, Any]) -> dict[str, Any]:
    """Один оффер из offersSerialized -> плоская запись."""
    bargain = o.get("bargainTerms") or {}
    geo = o.get("geo") or {}
    building = o.get("building") or {}
    coords = geo.get("coordinates") or {}
    metro = _first_underground(geo)
    utilities = bargain.get("utilitiesTerms") or {}
    district, okrug = _districts(geo)

    return {
        "offer_id": o.get("id") or o.get("cianId"),
        "url": o.get("fullUrl"),
        "price": bargain.get("priceRur") or bargain.get("price"),
        "deposit": bargain.get("deposit"),
        "client_fee_pct": bargain.get("clientFee"),
        "agent_fee_pct": bargain.get("agentFee"),
        "utilities_included": utilities.get("includedInPrice"),
        "rooms": o.get("roomsCount"),
        "flat_type": o.get("flatType"),  # rooms / studio / openPlan
        "is_apartments": o.get("isApartments"),
        "total_area": _to_float(o.get("totalArea")),
        "living_area": _to_float(o.get("livingArea")),
        "kitchen_area": _to_float(o.get("kitchenArea")),
        "floor": o.get("floorNumber"),
        "floors_total": building.get("floorsCount"),
        "build_year": building.get("buildYear"),
        "material_type": building.get("materialType"),
        "lat": coords.get("lat"),
        "lon": coords.get("lng"),
        "district": district,
        "okrug": okrug,
        "address": geo.get("userInput"),
        "metro_name": metro.get("name"),
        "metro_time_min": metro.get("time"),
        "metro_transport": metro.get("transportType"),
        "is_by_homeowner": o.get("isByHomeowner"),
        "published_ts": o.get("addedTimestamp"),
        "description": o.get("description"),
        "photos_count": len(o.get("photos") or []),
    }


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "."))
    except (ValueError, TypeError):
        return None


def extract_offers(api_payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = api_payload.get("data") or {}
    return [parse_offer(o) for o in data.get("offersSerialized") or []]


def total_count(api_payload: dict[str, Any]) -> int:
    data = api_payload.get("data") or {}
    for key in ("offerCount", "aggregatedCount", "totalOffers"):
        v = data.get(key)
        if isinstance(v, int):
            return v
    return -1
