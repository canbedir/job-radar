"""Shared HTTP layer: request budget, jitter and rate-limit detection.

LinkedIn throttles bursts of requests from a single IP, answering with either 429
or its own non-standard 999. This layer paces requests closer to human speed and
turns a block into an explicit error instead of swallowing it, so the caller can
end the run cleanly and warn the user.
"""

from __future__ import annotations

import random
import time

import requests

# Status codes that mean we are being blocked. 999 is LinkedIn-specific and not
# part of the HTTP standard.
BLOCK_CODES = {429, 999, 403}

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}


class RateLimited(Exception):
    """The source blocked us; no further requests should be made this run."""


class BudgetExhausted(Exception):
    """The request budget allocated to this run is used up."""


class Fetcher:
    def __init__(
        self,
        max_requests: int = 60,
        delay_range: tuple[float, float] = (1.0, 3.0),
        timeout: int = 15,
        max_retries: int = 2,
    ) -> None:
        self.max_requests = max_requests
        self.delay_range = delay_range
        self.timeout = timeout
        self.max_retries = max_retries
        self.count = 0
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def _throttle(self) -> None:
        # No wait before the first request; random delay between later ones.
        if self.count:
            time.sleep(random.uniform(*self.delay_range))

    def get(self, url: str, params: dict | None = None) -> requests.Response:
        if self.count >= self.max_requests:
            raise BudgetExhausted(f"request budget exhausted ({self.max_requests})")

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            self.count += 1
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(2 ** attempt)
                continue

            if resp.status_code in BLOCK_CODES:
                raise RateLimited(f"HTTP {resp.status_code} <- {resp.url}")
            if resp.status_code >= 500:
                # Transient server error: back off and retry.
                last_error = RuntimeError(f"HTTP {resp.status_code}")
                time.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                raise RuntimeError(f"unexpected HTTP {resp.status_code} <- {resp.url}")

            resp.encoding = resp.encoding or "utf-8"
            return resp

        raise RuntimeError(f"request failed: {url} ({last_error})")
