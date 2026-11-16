"""Spider 基类：start_urls / parse / 流式 / 暂停恢复 / 钩子 / 导出。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

from ..utils.logging import get_logger
from .engine import CrawlerEngine, SpiderSettings
from .exporters import export_items
from .items import Item
from .request import Request, Response

logger = get_logger("spider")


class Spider:
    """类 Scrapy 的 Spider。

    >>> class Demo(Spider):
    ...     name = "demo"
    ...     start_urls = ["https://example.com/"]
    ...     async def parse(self, response):
    ...         yield Item(title=response.css("h1::text").get())
    """

    name: str = "spider"
    start_urls: list[str] = []
    custom_settings: dict[str, Any] = {}
    crawldir: str | None = None
    #: 每个会话的配置，例如 {"chrome": {"kind": "browser", "headless": True}}
    session_settings: dict[str, dict[str, Any]] = {}

    def __init__(self, settings: dict[str, Any] | None = None, crawldir: str | Path | None = None):
        merged = {**self.custom_settings, **(settings or {})}
        if self.session_settings:
            merged.setdefault("sessions", {}).update(self.session_settings)
        self.settings = SpiderSettings.from_dict(merged)
        self.crawldir = str(crawldir) if crawldir else self.crawldir
        self.engine: CrawlerEngine | None = None
        self.items: list[Item] = []
        self._pause_requested = False

    # -- 生命周期钩子 ---------------------------------------------
    async def start_requests(self) -> AsyncIterator[Request]:
        for url in self.start_urls:
            yield Request(url=url)

    async def parse(self, response: Response) -> AsyncIterator[Any]:  # pragma: no cover - 由子类实现
        raise NotImplementedError("Spider 必须实现 parse 或指定 callback")
        yield  # noqa: unreachable

    async def on_start(self) -> None:
        """引擎启动前调用（可做登录、预热）。"""

    async def on_close(self) -> None:
        """引擎结束后调用。"""

    def on_error(self, request: Request, error: Exception) -> None:
        logger.warning("请求失败 %s：%s", request.url, error)

    def is_blocked(self, response: Response) -> bool:
        """阻塞判定钩子：默认按状态码 + 页面特征词，可覆盖自定义。"""
        from .throttle import looks_blocked

        return looks_blocked(response.status, response.text[:2000])

    def process_item(self, item: Any) -> Any:
        """Item 流水线钩子：默认原样返回，返回 None 表示丢弃。"""
        return item

    # -- 运行 -----------------------------------------------------
    def _build_engine(self, sessions: Any = None) -> CrawlerEngine:
        engine = CrawlerEngine(self, self.settings, sessions=sessions, crawldir=self.crawldir)
        self.engine = engine
        return engine

    async def stream(self, sessions: Any = None) -> AsyncIterator[Item]:
        """流式产出 Item（``async for item in spider.stream()``）。

        ``sessions`` 可注入自定义会话管理器（测试用假会话、或复用已有连接）。
        """
        engine = self._build_engine(sessions)
        await self.on_start()
        try:
            async for output in engine.crawl():
                processed = self.process_item(output)
                if processed is None:
                    continue
                if not isinstance(processed, Item):
                    processed = Item(processed) if isinstance(processed, dict) else Item(value=processed)
                self.items.append(processed)
                yield processed
        finally:
            await self.on_close()

    def start(self) -> list[Item]:
        """同步跑完，返回全部 Item（Ctrl+C 会优雅保存断点）。"""
        self._pause_requested = False

        async def runner() -> list[Item]:
            collected: list[Item] = []
            async for item in self.stream():
                collected.append(item)
            return collected

        try:
            return asyncio.run(runner())
        except KeyboardInterrupt:
            if self.engine is not None:
                self.engine.save_state()
                logger.info("已中断，断点保存到 %s", self.crawldir or "（未配置 crawldir）")
            return self.items

    # -- 控制 -----------------------------------------------------
    def pause(self) -> None:
        self._pause_requested = True
        if self.engine is not None:
            self.engine.pause()

    def resume_signal(self) -> None:
        self._pause_requested = False
        if self.engine is not None:
            self.engine._paused = False

    def stop(self) -> None:
        if self.engine is not None:
            self.engine.stop()

    # -- 导出 -----------------------------------------------------
    def export(self, path: str | Path, fmt: str = "json") -> Path:
        return export_items(self.items, path, fmt)

    @property
    def stats(self) -> dict[str, Any]:
        return self.engine.stats.as_dict() if self.engine else {}

    def network(self, session_id: str = "default") -> Any:
        """浏览器会话的网络捕获（需 ``record_requests=True``）。"""
        return self.engine.sessions if self.engine else None


__all__ = ["Spider"]
