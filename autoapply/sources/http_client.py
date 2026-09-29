"""Shared HTTP client with rate limiting, retries, and error handling."""

from __future__ import annotations

import random
import time
from typing import Any

import httpx

from autoapply.logging import get_logger

log = get_logger(__name__)

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
]


class HttpClient:
    """HTTP client with per-instance rate limiting and exponential-backoff retries."""

    def __init__(
        self,
        requests_per_second: float = 1.0,
        max_retries: int = 3,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        event_hooks: dict[str, list] | None = None,
    ):
        self.min_interval = 1.0 / max(requests_per_second, 0.01)
        self.max_retries = max_retries
        self.timeout = timeout
        self._extra_headers = headers or {}
        self._transport = transport  # tests pass an httpx.MockTransport
        self._event_hooks = event_hooks
        self._last_request: float = 0.0
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                transport=self._transport,
                event_hooks=self._event_hooks,
                headers={
                    "User-Agent": random.choice(_USER_AGENTS),
                    "Accept": "application/json, text/html, */*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    **self._extra_headers,
                },
            )
        return self._client

    def _rate_limit(self) -> None:
        now = time.monotonic()
        wait = self.min_interval - (now - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def get(self, url: str, **kwargs: Any) -> httpx.Response | None:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response | None:
        return self._request("POST", url, **kwargs)

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response | None:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._rate_limit()
            try:
                resp = self._get_client().request(method, url, **kwargs)

                if resp.status_code == 429:
                    wait = (2 ** attempt) * 2 + random.uniform(0.5, 2.0)
                    log.warning("rate_limited", url=url, wait=round(wait, 1), attempt=attempt)
                    time.sleep(wait)
                    continue

                if resp.status_code in (401, 403):
                    log.warning("access_denied", url=url, status=resp.status_code)
                    return None

                if resp.status_code >= 500:
                    wait = (2 ** attempt) + random.uniform(0.5, 1.5)
                    log.warning("server_error", url=url, status=resp.status_code, attempt=attempt)
                    time.sleep(wait)
                    continue

                return resp

            except httpx.TimeoutException as exc:
                last_exc = exc
                log.warning("request_timeout", url=url, attempt=attempt)
            except httpx.HTTPError as exc:
                last_exc = exc
                log.warning("http_error", url=url, error=str(exc), attempt=attempt)

            if attempt < self.max_retries:
                time.sleep((2 ** attempt) + random.uniform(0.3, 1.0))

        log.error("request_failed_all_retries", url=url, error=str(last_exc))
        return None

    def close(self) -> None:
        if self._client and not self._client.is_closed:
            self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


def html_to_text(html: str) -> str:
    """Convert HTML to plain text using BeautifulSoup."""
    from bs4 import BeautifulSoup
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    return soup.get_text(separator="\n", strip=True)


def guess_work_mode(text: str | None) -> str:
    """Guess work mode from a location or description string."""
    t = (text or "").lower()
    if "remote" in t:
        return "remote"
    if "hybrid" in t:
        return "hybrid"
    if t and t != "unknown":
        return "onsite"
    return "unknown"
