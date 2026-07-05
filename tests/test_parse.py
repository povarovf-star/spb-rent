"""Тесты парсера ответа API ЦИАН (без сети)."""

from src.scraping.parse import extract_offers, parse_offer, total_count

SAMPLE_OFFER = {
    "id": 123456789,
    "fullUrl": "https://spb.cian.ru/rent/flat/123456789/",
    "roomsCount": 2,
    "flatType": "rooms",
    "isApartments": False,
    "totalArea": "54.3",
    "livingArea": "30,1",
    "kitchenArea": None,
    "floorNumber": 4,
    "isByHomeowner": True,
    "addedTimestamp": 1719900000,
    "description": "Уютная квартира",
    "photos": [{}, {}, {}],
    "bargainTerms": {
        "priceRur": 45000,
        "deposit": 45000,
        "clientFee": 0,
        "agentFee": 50,
        "utilitiesTerms": {"includedInPrice": False},
    },
    "building": {"floorsCount": 9, "buildYear": 1978, "materialType": "panel"},
    "geo": {
        "userInput": "Санкт-Петербург, Гражданский проспект, 15",
        "coordinates": {"lat": 60.012, "lng": 30.398},
        "address": [
            {"type": "location", "geoType": "location", "name": "Санкт-Петербург"},
            {"type": "raion", "geoType": "district", "name": "Калининский"},
            {"type": "okrug", "geoType": "district", "name": "Академическое"},
            {"type": "street", "geoType": "street", "name": "Гражданский"},
        ],
        "undergrounds": [
            {"name": "Академическая", "time": 12, "transportType": "walk"},
            {"name": "Политехническая", "time": 5, "transportType": "transport"},
        ],
    },
}


def make_payload(offers, count=None):
    return {"data": {"offersSerialized": offers, "offerCount": count or len(offers)}}


def test_parse_offer_basic_fields():
    rec = parse_offer(SAMPLE_OFFER)
    assert rec["offer_id"] == 123456789
    assert rec["price"] == 45000
    assert rec["rooms"] == 2
    assert rec["total_area"] == 54.3
    assert rec["living_area"] == 30.1  # запятая как разделитель
    assert rec["kitchen_area"] is None
    assert rec["floors_total"] == 9
    assert rec["build_year"] == 1978
    assert rec["district"] == "Калининский"
    assert rec["okrug"] == "Академическое"
    assert rec["photos_count"] == 3
    assert rec["utilities_included"] is False


def test_metro_prefers_walking():
    rec = parse_offer(SAMPLE_OFFER)
    # пешком 12 мин приоритетнее, чем 5 мин на транспорте
    assert rec["metro_name"] == "Академическая"
    assert rec["metro_time_min"] == 12
    assert rec["metro_transport"] == "walk"


def test_parse_offer_missing_everything():
    rec = parse_offer({"id": 1})
    assert rec["offer_id"] == 1
    assert rec["price"] is None
    assert rec["metro_name"] is None
    assert rec["district"] is None


def test_extract_and_count():
    payload = make_payload([SAMPLE_OFFER, {"id": 2}], count=9804)
    offers = extract_offers(payload)
    assert len(offers) == 2
    assert total_count(payload) == 9804


def test_empty_payload():
    assert extract_offers({}) == []
    assert total_count({}) == -1
