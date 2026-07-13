"""HTTP client for CIAN's internal JSON search API.

Main ideas:
- curl_cffi with impersonate="chrome" gives a real browser's TLS fingerprint,
  which is the primary way to avoid the antibot at the connection level;
- random delays between requests (a polite pace);
- retries with exponential backoff; a long pause on 429/403;
- every response is checked for "validity" (presence of offersSerialized),
  so an antibot stub is not stored as data.
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
    """A 200 response whose structure does not look like data, likely an antibot stub."""


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
        """One search request. Returns the parsed JSON of the API response.

        Raises AntibotSuspected / RuntimeError once retries are exhausted.
        """
        backoff = 20.0
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._polite_sleep()
            try:
                resp = self.session.post(
                    API_URL, json={"jsonQuery": json_query}, timeout=30
                )
            except Exception as e:  # network errors, timeouts
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
                last_err = RuntimeError(f"HTTP {resp.status_code}, slowing down")
                log.warning(
                    "HTTP %d (attempt %d/%d), long pause %.0f sec",
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
    """Builds the jsonQuery for long-term flat rentals."""
    return {
        "_type": "flatrent",
        "engine_version": {"type": "term", "value": 2},
        "region": {"type": "terms", "value": [region]},
        "for_day": {"type": "term", "value": "!1"},  # exclude daily rentals
        "room": {"type": "terms", "value": rooms},
        "price": {"type": "range", "value": {"gte": price_gte, "lte": price_lte}},
        "page": {"type": "term", "value": page},
    }
