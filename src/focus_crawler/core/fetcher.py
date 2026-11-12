"""HTTP 抓取：httpx + 限速 + 重试 + robots。"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .compliance import RobotsCache
from .failure_classifier import classify_exception, classify_http_status

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 FocusCrawler"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


@dataclass
class FetchResult:
    url: str
    status_code: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    text: str = ""
    content: bytes | None = None
    error: str | None = None
    failure_type: str | None = None
    elapsed_ms: int = 0
    retry_count: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None and self.status_code is not None and 200 <= self.status_code < 300


class Fetcher:
    def __init__(
        self,
        timeout_ms: int = 20000,
        retry: int = 3,
        delay_ms: tuple[int, int] = (800, 2000),
        respect_robots: bool = True,
        user_agent: str | None = None,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Any = time.sleep,
        random_uniform: Any = random.uniform,
    ):
        self.timeout_ms = timeout_ms
        self.retry = max(0, retry)
        self.delay_ms = delay_ms
        self.respect_robots = respect_robots
        self.sleep = sleep
        self.random_uniform = random_uniform
        self.headers = dict(DEFAULT_HEADERS)
        if user_agent:
            self.headers["User-Agent"] = user_agent
        self._owns_client = client is None
        if client is not None:
            self.client = client
        elif transport is not None:
            self.client = httpx.Client(
                transport=transport, follow_redirects=True, timeout=timeout_ms / 1000
            )
        else:
            self.client = httpx.Client(follow_redirects=True, timeout=timeout_ms / 1000)
        self.robots = RobotsCache(self.client, user_agent=self.headers["User-Agent"])

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _delay(self) -> None:
        low, high = self.delay_ms
        if high <= 0:
            return
        self.sleep(self.random_uniform(low, high) / 1000)

    def fetch(self, url: str, headers: dict[str, str] | None = None, want_bytes: bool = False) -> FetchResult:
        if self.respect_robots and not self.robots.is_allowed(url):
            return FetchResult(url=url, error="robots.txt 禁止抓取", failure_type="robots_disallowed")

        merged = dict(self.headers)
        if headers:
            merged.update({k: v for k, v in headers.items() if v})
        attempts = self.retry + 1
        last: FetchResult | None = None
        for attempt in range(attempts):
            self._delay()
            started = time.perf_counter()
            try:
                response = self.client.get(url, headers=merged)
                elapsed = int((time.perf_counter() - started) * 1000)
                failure = classify_http_status(response.status_code)
                result = FetchResult(
                    url=str(response.url),
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    text="" if want_bytes else response.text,
                    content=response.content if want_bytes else None,
                    failure_type=failure,
                    error=None if failure is None else f"HTTP {response.status_code}",
                    elapsed_ms=elapsed,
                    retry_count=attempt,
                )
                if 400 <= response.status_code < 500:
                    return result
                last = result
                if result.ok:
                    return result
            except httpx.HTTPError as exc:
                elapsed = int((time.perf_counter() - started) * 1000)
                last = FetchResult(
                    url=url,
                    error=str(exc),
                    failure_type=classify_exception(exc),
                    elapsed_ms=elapsed,
                    retry_count=attempt,
                )
            if attempt < attempts - 1:
                self.sleep(min(2 ** attempt, 8) * 0.5)
        assert last is not None
        return last
