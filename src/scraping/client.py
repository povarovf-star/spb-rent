"""HTTP-клиент для внутреннего JSON API поисковой выдачи ЦИАН.

Ключевые идеи:
- curl_cffi с impersonate="chrome" даёт TLS-отпечаток настоящего браузера —
  это основной способ не ловить антибот на уровне соединения;
- случайные задержки между запросами (вежливый темп);
- ретраи с экспоненциальным бэкоффом; на 429/403 — длинная пауза;
- каждый ответ проверяется на "валидность" (наличие offersSerialized),
  чтобы антибот-заглушка не записалась как данные.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

from curl_cffi import requests

log = logging.getLogger(__name__)

API_URL = "https://api.cian.ru/search-offers/v2/search-offers-desktop/"

HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
    "Content-Type": "application/json",
    "Origin": "https://spb.cian.ru",
    "Referer": "https://spb.cian.ru/snyat-kvartiru/",
}


class AntibotSuspected(Exception):
    """Ответ 200, но структура не похожа на данные — вероятно, заглушка антибота."""


class CianClient:
    def __init__(
        self,
        min_delay: float = 3.0,
        max_delay: float = 8.0,
        proxy: str | None = None,
        max_retries: int = 5,
    ) -> None:
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_retries = max_retries
        self.session = requests.Session(
            impersonate="chrome", proxy=proxy, headers=HEADERS
        )
        self._last_request_ts = 0.0

    def _polite_sleep(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        delay = random.uniform(self.min_delay, self.max_delay)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request_ts = time.monotonic()

    def search(self, json_query: dict[str, Any]) -> dict[str, Any]:
        """Один запрос к выдаче. Возвращает распарсенный JSON ответа API.

        Бросает AntibotSuspected / RuntimeError после исчерпания ретраев.
        """
        backoff = 20.0
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._polite_sleep()
            try:
                resp = self.session.post(
                    API_URL, json={"jsonQuery": json_query}, timeout=30
                )
            except Exception as e:  # сетевые ошибки, таймауты
                last_err = e
                log.warning("network error (attempt %d/%d): %s", attempt, self.max_retries, e)
                time.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code == 200:
                try:
                    payload = resp.json()
                except Exception as e:
                    last_err = AntibotSuspected(f"200, but not JSON: {e}")
                    log.warning("%s", last_err)
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                data = payload.get("data")
                if isinstance(data, dict) and "offersSerialized" in data:
                    return payload
                last_err = AntibotSuspected(
                    f"200, but no offersSerialized; keys={list(payload)[:10]}"
                )
                log.warning("%s", last_err)
            elif resp.status_code in (403, 429):
                last_err = RuntimeError(f"HTTP {resp.status_code} — притормаживаем")
                log.warning(
                    "HTTP %d (attempt %d/%d) — долгая пауза %.0f сек",
                    resp.status_code, attempt, self.max_retries, backoff * 3,
                )
                time.sleep(backoff * 3)
                backoff *= 2
                continue
            else:
                last_err = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                log.warning("%s", last_err)

            time.sleep(backoff)
            backoff *= 2

        raise last_err if last_err else RuntimeError("search failed")


def build_json_query(
    region: int,
    rooms: list[int],
    price_gte: int,
    price_lte: int,
    page: int,
) -> dict[str, Any]:
    """Собирает jsonQuery для долгосрочной аренды квартир."""
    return {
        "_type": "flatrent",
        "engine_version": {"type": "term", "value": 2},
        "region": {"type": "terms", "value": [region]},
        "for_day": {"type": "term", "value": "!1"},  # исключить посуточную
        "room": {"type": "terms", "value": rooms},
        "price": {"type": "range", "value": {"gte": price_gte, "lte": price_lte}},
        "page": {"type": "term", "value": page},
    }
