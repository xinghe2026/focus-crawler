"""并发爬行引擎：队列、并发、自动油门、重试、robots、断点、开发模式缓存。

设计要点（参考 Scrapy / Scrapling 的调度语义，独立实现）：

* 优先级队列 + 去重集合，``dont_filter=True`` 可绕过去重；
* ``concurrency`` 控制同时在飞的请求数；
* 每个域名独立节流（:class:`AutoThrottle`），命中限流自动加倍等待；
* 失败按 ``retries`` 重试，``Retry-After`` 会被尊重；
* ``robots_txt_obey`` 时读取 robots.txt，并采纳 Crawl-delay / Request-rate；
* ``crawldir`` 存在时周期性快照（已见 URL + 待处理请求），可断点续跑；
* ``dev_mode`` 时响应写盘，下次直接重放，迭代解析逻辑不必反复打站点。
"""

from __future__ import annotations

import asyncio
import heapq
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Iterable
from urllib.parse import urlsplit

from ..utils.logging import get_logger
from .cache import ResponseCache
from .request import Request, Response
from .sessions import SessionError, SessionManager, SessionSettings
from .throttle import AutoThrottle, crawl_delay_from_robots, request_rate_from_robots

logger = get_logger("spider.engine")


@dataclass
class SpiderSettings:
    """爬虫运行参数（Spider 里可用 ``custom_settings`` 覆盖）。"""

    concurrency: int = 8
    delay: float = 0.25
    max_delay: float = 60.0
    retries: int = 3
    timeout: float = 30.0
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    robots_txt_obey: bool = False
    autothrottle: bool = True
    dev_mode: bool = False
    crawldir: str | None = None
    resume: bool = True
    max_requests: int = 0            # 0 = 不限
    max_items: int = 0
    snapshot_interval: float = 5.0
    log_level: str = "INFO"
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "SpiderSettings":
        data = dict(raw or {})
        allowed = {name for name in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{key: value for key, value in data.items() if key in allowed})


@dataclass
class CrawlStats:
    requests: int = 0
    responses: int = 0
    items: int = 0
    retries: int = 0
    blocked: int = 0
    failed: int = 0
    skipped_robots: int = 0
    cache_hits: int = 0
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    @property
    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return max(0.0, end - self.started_at)

    def as_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "responses": self.responses,
            "items": self.items,
            "retries": self.retries,
            "blocked": self.blocked,
            "failed": self.failed,
            "skipped_robots": self.skipped_robots,
            "cache_hits": self.cache_hits,
            "elapsed": round(self.elapsed, 2),
        }


class RobotsPolicy:
    """极简 robots.txt：User-agent: * 的规则 + Crawl-delay / Request-rate。"""

    def __init__(self):
        self._cache: dict[str, tuple[Any, float | None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def load(self, base: str, sessions: SessionManager) -> tuple[Any, float | None]:
        from urllib.robotparser import RobotFileParser

        host = (urlsplit(base).scheme, urlsplit(base).netloc)
        if host in self._cache:
            return self._cache[host]
        lock = self._locks.setdefault(host[1], asyncio.Lock())
        async with lock:
            if host in self._cache:
                return self._cache[host]
            url = f"{host[0]}://{host[1]}/robots.txt"
            parser = RobotFileParser()
            delay: float | None = None
            try:
                response = await sessions.fetch(Request(url=url))
                if response.ok:
                    text = response.text
                    parser.parse(text.splitlines())
                    delay = crawl_delay_from_robots(text) or request_rate_from_robots(text)
            except Exception as exc:  # noqa: BLE001 - robots 取不到就放行
                logger.debug("robots.txt 获取失败 %s：%s", url, exc)
                parser = RobotFileParser()
                parser.parse([])
            self._cache[host] = (parser, delay)
            return self._cache[host]

    async def allowed(self, url: str, user_agent: str, sessions: SessionManager) -> bool:
        parser, _ = await self.load(url, sessions)
        try:
            return parser.can_fetch(user_agent, url)
        except Exception:  # noqa: BLE001
            return True


class CrawlerEngine:
    """把一个 Spider 跑起来：调度 → 抓取 → 回调 → 产出 Item。"""

    def __init__(
        self,
        spider: Any,
        settings: SpiderSettings | None = None,
        sessions: SessionManager | None = None,
        crawldir: str | Path | None = None,
    ):
        self.spider = spider
        self.settings = settings or SpiderSettings()
        self.sessions = sessions or self._build_sessions()
        self.robots = RobotsPolicy()
        self.throttle = AutoThrottle(
            base_delay=self.settings.delay, max_delay=self.settings.max_delay
        )
        self.stats = CrawlStats()
        self.crawldir = Path(crawldir or self.settings.crawldir or spider.crawldir or "") if (
            crawldir or self.settings.crawldir or getattr(spider, "crawldir", "")
        ) else None
        self.cache = (
            ResponseCache(self.crawldir / "cache", enabled=True)
            if self.crawldir and self.settings.dev_mode
            else ResponseCache(Path("."), enabled=False)
        )
        self._seen: set[str] = set()
        self._queue: list[tuple[int, int, Request]] = []
        self._seq = 0
        self._stopping = False
        self._paused = False
        self._completed = False
        self._last_snapshot = 0.0

    # -- 初始化 ---------------------------------------------------
    def _build_sessions(self) -> SessionManager:
        defaults = SessionSettings(timeout=self.settings.timeout, headers={"User-Agent": self.settings.user_agent})
        manager = SessionManager(defaults)
        for session_id, raw in (self.settings.sessions or {}).items():
            manager.configure(session_id, SessionSettings.from_dict({**raw}))
        return manager

    # -- 队列 -----------------------------------------------------
    def push(self, request: Request) -> bool:
        if not request.dont_filter and request.key in self._seen:
            return False
        self._seen.add(request.key)
        self._seq += 1
        heapq.heappush(self._queue, (-request.priority, self._seq, request))
        return True

    def pop(self) -> Request | None:
        if not self._queue:
            return None
        return heapq.heappop(self._queue)[2]

    def pending_count(self) -> int:
        return len(self._queue)

    # -- 状态快照 -------------------------------------------------
    def _state_path(self) -> Path | None:
        return self.crawldir / "state.json" if self.crawldir else None

    def save_state(self) -> None:
        path = self._state_path()
        if path is None:
            return
        payload = {
            "seen": sorted(self._seen),
            "pending": [
                {
                    "url": request.url,
                    "callback": request.callback,
                    "method": request.method,
                    "headers": request.headers,
                    "meta": request.meta,
                    "priority": request.priority,
                    "session": request.session,
                    "retries": request.retries,
                }
                for _, _, request in sorted(self._queue, key=lambda item: (item[0], item[1]))
            ],
            "completed": self._completed,
            "stats": self.stats.as_dict(),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:  # noqa: BLE001 - 快照失败不影响抓取
            logger.debug("保存断点失败：%s", exc)

    def load_state(self) -> bool:
        path = self._state_path()
        if path is None or not path.is_file() or not self.settings.resume:
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if payload.get("completed"):
            # 上次已经跑完：这不是断点，删掉快照重新开始（否则会"空跑一次"）
            try:
                path.unlink()
            except OSError:
                pass
            return False
        self._seen = set(payload.get("seen") or [])
        for raw in payload.get("pending") or []:
            request = Request(
                url=raw.get("url", ""),
                callback=raw.get("callback"),
                method=raw.get("method", "GET"),
                headers=raw.get("headers") or {},
                meta=raw.get("meta") or {},
                priority=int(raw.get("priority", 0)),
                session=raw.get("session", "default"),
                retries=int(raw.get("retries", 0)),
                dont_filter=True,
            )
            self._seq += 1
            heapq.heappush(self._queue, (-request.priority, self._seq, request))
        logger.info("从 %s 恢复：待处理 %d 条", path, len(self._queue))
        return bool(self._queue)

    # -- 控制 -----------------------------------------------------
    def pause(self) -> None:
        self._paused = True

    def stop(self) -> None:
        self._stopping = True

    # -- 主循环 ---------------------------------------------------
    async def crawl(self) -> AsyncIterator[Any]:
        if not self.load_state():
            for request in await self._start_requests():
                self.push(request)

        in_flight: set[asyncio.Task] = set()
        try:
            while True:
                while (
                    not self._stopping
                    and not self._paused
                    and len(in_flight) < max(1, self.settings.concurrency)
                    and self._queue
                ):
                    if self.settings.max_requests and self.stats.requests >= self.settings.max_requests:
                        self._stopping = True
                        break
                    request = self.pop()
                    if request is None:
                        break
                    task = asyncio.create_task(self._handle(request))
                    in_flight.add(task)

                if not in_flight and (self._stopping or self._paused or not self._queue):
                    break

                done, in_flight = await asyncio.wait(
                    in_flight, timeout=0.2, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    try:
                        outputs = task.result()
                    except Exception as exc:  # noqa: BLE001 - 单条失败不终止整体
                        self.stats.failed += 1
                        logger.warning("请求处理异常：%s", exc)
                        continue
                    for output in outputs:
                        if isinstance(output, Request):
                            self.push(output)
                        elif output is not None:
                            self.stats.items += 1
                            yield output
                            if self.settings.max_items and self.stats.items >= self.settings.max_items:
                                self._stopping = True

                self._maybe_snapshot()
        finally:
            self._completed = not self._paused and not self._stopping
            self.stats.finished_at = time.time()
            if in_flight:
                for task in in_flight:
                    task.cancel()
                await asyncio.gather(*in_flight, return_exceptions=True)
            self.save_state()
            await self.sessions.close_all()

    def _maybe_snapshot(self) -> None:
        if self.crawldir is None:
            return
        now = time.monotonic()
        if now - self._last_snapshot < self.settings.snapshot_interval:
            return
        self._last_snapshot = now
        self.save_state()

    async def _start_requests(self) -> list[Request]:
        starter = getattr(self.spider, "start_requests", None)
        if starter is None:
            return [Request(url=url) for url in getattr(self.spider, "start_urls", [])]
        result = starter()
        if hasattr(result, "__aiter__"):
            return [item async for item in result]
        return list(result)

    # -- 单条请求 -------------------------------------------------
    async def _handle(self, request: Request) -> list[Any]:
        domain = (urlsplit(request.url).hostname or "").lower()

        if self.settings.robots_txt_obey:
            parser, delay = await self.robots.load(request.url, self.sessions)
            if delay is not None:
                self.throttle.base_delay = max(self.throttle.base_delay, delay)
            if not await self.robots.allowed(request.url, self.settings.user_agent, self.sessions):
                self.stats.skipped_robots += 1
                logger.info("robots.txt 禁止，跳过 %s", request.url)
                return []

        wait = self.throttle.wait_time(domain)
        if wait > 0:
            await asyncio.sleep(min(wait, self.settings.max_delay))

        cached = self.cache.load(request)
        if cached is not None:
            self.stats.cache_hits += 1
            return await self._dispatch(request, cached)

        self.throttle.mark_request(domain)
        started = time.monotonic()
        self.stats.requests += 1
        try:
            response = await self.sessions.fetch(request)
        except SessionError as exc:
            return self._retry_or_fail(request, str(exc))

        latency = time.monotonic() - started
        blocked = self.throttle.report(
            domain, response.status, latency, response.headers, response.text[:2000] if response.status >= 400 else ""
        )
        if blocked:
            self.stats.blocked += 1
            logger.warning("检测到限速/封禁 %s（HTTP %s），已自动放慢", request.url, response.status)
            if request.retries < self.settings.retries:
                return self._retry_or_fail(request, f"blocked HTTP {response.status}")

        self.cache.save(request, response)
        self.stats.responses += 1
        if response.status >= 400 and response.status >= 500:
            return self._retry_or_fail(request, f"HTTP {response.status}")
        return await self._dispatch(request, response)

    def _retry_or_fail(self, request: Request, reason: str) -> list[Any]:
        if request.retries >= self.settings.retries:
            self.stats.failed += 1
            logger.warning("放弃请求 %s：%s", request.url, reason)
            return []
        self.stats.retries += 1
        retry = request.replace(retries=request.retries + 1, dont_filter=True)
        self.push(retry)
        return []

    async def _dispatch(self, request: Request, response: Response) -> list[Any]:
        callback_name = request.callback or getattr(self.spider, "parse", None)
        if callback_name is None:
            return []
        callback = (
            getattr(self.spider, callback_name, None)
            if isinstance(callback_name, str)
            else callback_name
        )
        if callback is None:
            logger.warning("回调不存在：%s", callback_name)
            return []
        result = callback(response)
        outputs: list[Any] = []
        if hasattr(result, "__aiter__"):
            async for item in result:
                outputs.append(item)
        elif result is not None:
            if isinstance(result, (list, tuple)):
                outputs.extend(result)
            else:
                outputs.append(result)
        return outputs


__all__ = ["CrawlStats", "CrawlerEngine", "RobotsPolicy", "SpiderSettings"]
