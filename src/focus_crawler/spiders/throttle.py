"""自动油门：按域名自适应延迟（AIMD），并识别 Retry-After。

规则（参考 Scrapling / Scrapy AutoThrottle 的思路，独立实现）：

* 每个域名维护独立延迟：``base_delay`` 起步，跟随响应延迟滑动；
* 命中限速信号（403/429/503、"blocked"、Cloudflare 挑战）→ 延迟翻倍，
  有 ``Retry-After`` 时直接采用服务器的等待时长；
* 连续 N 次正常 → 延迟按比例回落到 ``base_delay``；
* 上限 ``max_delay``，下限 ``base_delay``。
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

_BLOCK_HINTS = (
    "captcha",
    "access denied",
    "blocked",
    "too many requests",
    "rate limit",
    "cloudflare",
    "just a moment",
    "cf-chl",
    "unusual traffic",
)


def parse_retry_after(value: str | None) -> float | None:
    """解析 Retry-After：秒数或 HTTP 日期。"""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    delta = when.timestamp() - time.time()
    return max(0.0, delta)


def looks_blocked(status: int, body: str = "") -> bool:
    if status in (403, 429, 503):
        return True
    lowered = (body or "")[:2000].lower()
    return any(hint in lowered for hint in _BLOCK_HINTS)


@dataclass
class DomainState:
    base_delay: float
    delay: float
    max_delay: float
    last_request: float = 0.0
    success_streak: int = 0
    blocked: int = 0
    requests: int = 0
    wait_until: float = 0.0

    def wait_time(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        due = max(self.last_request + self.delay, self.wait_until)
        return max(0.0, due - now)

    def mark_request(self) -> None:
        self.last_request = time.monotonic()
        self.requests += 1

    def on_success(self, latency: float) -> None:
        self.success_streak += 1
        target = max(self.base_delay, latency)
        if self.success_streak >= 3:
            # 连续正常：缓慢加速，最低回到 base_delay
            self.delay = max(self.base_delay, min(self.delay, target) * 0.9)
            self.success_streak = 0

    def on_block(self, retry_after: float | None = None) -> None:
        self.blocked += 1
        self.success_streak = 0
        if retry_after:
            self.wait_until = time.monotonic() + retry_after
            self.delay = min(self.max_delay, max(self.delay, retry_after))
        else:
            self.delay = min(self.max_delay, max(self.delay * 2, self.base_delay * 2))


class AutoThrottle:
    """按域名的节流器（线程安全）。"""

    def __init__(
        self,
        base_delay: float = 0.5,
        max_delay: float = 60.0,
        respect_retry_after: bool = True,
    ):
        self.base_delay = max(0.0, base_delay)
        self.max_delay = max(self.base_delay, max_delay)
        self.respect_retry_after = respect_retry_after
        self._states: dict[str, DomainState] = {}
        self._lock = threading.Lock()

    def _state(self, domain: str) -> DomainState:
        state = self._states.get(domain)
        if state is None:
            state = DomainState(
                base_delay=self.base_delay,
                delay=self.base_delay,
                max_delay=self.max_delay,
            )
            self._states[domain] = state
        return state

    def delay_for(self, domain: str) -> float:
        with self._lock:
            return self._state(domain).delay

    def wait_time(self, domain: str) -> float:
        with self._lock:
            return self._state(domain).wait_time()

    def mark_request(self, domain: str) -> None:
        with self._lock:
            self._state(domain).mark_request()

    def report(self, domain: str, status: int, latency: float, headers: dict[str, str], body: str = "") -> bool:
        """记录一次响应；返回是否判定为被限速 / 封禁。"""
        retry_after = None
        if self.respect_retry_after:
            for key, value in headers.items():
                if key.lower() == "retry-after":
                    retry_after = parse_retry_after(value)
                    break
        blocked = looks_blocked(status, body)
        with self._lock:
            state = self._state(domain)
            if blocked:
                state.on_block(retry_after)
            else:
                state.on_success(latency)
        return blocked

    def snapshot(self) -> dict[str, dict[str, float]]:
        with self._lock:
            return {
                domain: {
                    "delay": state.delay,
                    "blocked": state.blocked,
                    "requests": state.requests,
                }
                for domain, state in self._states.items()
            }


def domain_of(url: str) -> str:
    from urllib.parse import urlsplit

    return (urlsplit(url).hostname or "").lower()


_RE_CRAWL_DELAY = re.compile(r"(?im)^\s*crawl-delay\s*:\s*([0-9.]+)")
_RE_REQUEST_RATE = re.compile(r"(?im)^\s*request-rate\s*:\s*([0-9.]+)\s*/\s*([0-9.]+)")


def crawl_delay_from_robots(text: str) -> float | None:
    """从 robots.txt 里读 Crawl-delay（秒），没有返回 None。"""
    match = _RE_CRAWL_DELAY.search(text or "")
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def request_rate_from_robots(text: str) -> float | None:
    """Request-rate: N/M → 每请求间隔 = M/N 秒。"""
    match = _RE_REQUEST_RATE.search(text or "")
    if not match:
        return None
    try:
        requests, seconds = float(match.group(1)), float(match.group(2))
    except ValueError:
        return None
    if requests <= 0:
        return None
    return seconds / requests


__all__ = [
    "AutoThrottle",
    "crawl_delay_from_robots",
    "looks_blocked",
    "parse_retry_after",
    "request_rate_from_robots",
]
