"""Async HTTP for harvesting: one shared client, per-host concurrency limits, retry with backoff."""

from __future__ import annotations

import asyncio
import random
from collections import defaultdict
from typing import Any
from urllib.parse import urlsplit

import httpx

from autoapply.logging import get_logger

log = get_logger(__name__)

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/125.0.0.0 Safari/537.36")

_RETRY_STATUS = {429, 500, 502, 503, 504}


class AsyncFetcher:
    """Shared AsyncClient. At most `per_host` requests in flight per host; 429/5xx/timeouts are
    retried with exponential backoff + jitter (honouring Retry-After)."""

    def __init__(self, per_host: int = 4, timeout: float = 20.0, max_retries: int = 3,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.per_host = per_host
        self.max_retries = max_retries
        self._host_limits: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(self.per_host))
        self._client = httpx.AsyncClient(
            timeout=timeout, follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9",
                     "Accept": "application/json, text/html;q=0.9, */*;q=0.8"},
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
            transport=transport,  # tests pass an httpx.MockTransport
        )
        self.requests = 0
        self.failures = 0

    async def __aenter__(self) -> "AsyncFetcher":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._client.aclose()

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response | None:
        """Response for any status < 500 except 429 (callers check status); None after retries fail."""
        host = urlsplit(url).netloc
        for attempt in range(self.max_retries + 1):
            async with self._host_limits[host]:
                self.requests += 1
                try:
                    resp = await self._client.request(method, url, **kwargs)
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    err, resp = f"{e.__class__.__name__}: {e}", None
                else:
                    if resp.status_code not in _RETRY_STATUS:
                        return resp
                    err = f"HTTP {resp.status_code}"
            if attempt == self.max_retries:
                break
            wait = (2 ** attempt) + random.uniform(0.2, 1.0)
            if resp is not None and resp.headers.get("retry-after", "").isdigit():
                wait = max(wait, min(float(resp.headers["retry-after"]), 60.0))
            log.debug("fetch_retry", url=url, error=err, attempt=attempt, wait=round(wait, 1))
            await asyncio.sleep(wait)
        self.failures += 1
        log.warning("fetch_failed", url=url, error=err)
        return None

    async def get(self, url: str, **kwargs: Any) -> httpx.Response | None:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response | None:
        return await self.request("POST", url, **kwargs)

    async def get_json(self, url: str, **kwargs: Any) -> Any | None:
        return _json(await self.get(url, **kwargs), url)

    async def post_json(self, url: str, **kwargs: Any) -> Any | None:
        return _json(await self.post(url, **kwargs), url)

    async def get_text(self, url: str, **kwargs: Any) -> str | None:
        resp = await self.get(url, **kwargs)
        return resp.text if resp is not None and resp.status_code == 200 else None


def _json(resp: httpx.Response | None, url: str) -> Any | None:
    if resp is None or resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        log.warning("fetch_invalid_json", url=url)
        return None
