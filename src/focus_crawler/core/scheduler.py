"""调度器：URL 队列、深度控制、失败重试、断点续爬。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config.schema import TaskConfig
from ..utils.hash import content_hash
from ..utils.logging import get_logger
from . import link_discovery
from .dynamic_sniffer import DynamicPage, DynamicSniffError, playwright_available, sniff_page
from .adaptive import AdaptiveStore
from .parser import parse_detail
from .subpage_collector import SubpageCollector

logger = get_logger("scheduler")


@dataclass
class RunStats:
    pages: int = 0
    discovered: int = 0
    items: int = 0
    failures: int = 0
    media: dict[str, int] = field(default_factory=dict)


class Scheduler:
    def __init__(
        self,
        task: TaskConfig,
        store: Any,
        task_id: int,
        fetcher: Any,
        collector: SubpageCollector | None = None,
        should_stop: Callable[[], bool] | None = None,
        should_pause: Callable[[], bool] | None = None,
        pause_interval: float = 0.2,
        dynamic_sniffer: Callable[[str, TaskConfig], DynamicPage] | None = None,
    ):
        self.task = task
        self.store = store
        self.task_id = task_id
        self.fetcher = fetcher
        self.should_stop = should_stop or (lambda: False)
        self.should_pause = should_pause
        self.pause_interval = pause_interval
        self.collector = collector
        self._subpages_collected = 0
        if dynamic_sniffer is not None:
            self.dynamic_sniffer = dynamic_sniffer
        else:
            self.dynamic_sniffer = sniff_page if playwright_available() else None
        self._dynamic_pages = 0
        self._dynamic_warned = False
        if self.collector is None and task.subpage_collect.enabled:
            self.collector = SubpageCollector(task=task, store=store, task_id=task_id, fetcher=fetcher)

    def seed(self) -> int:
        added = 0
        for url in self.task.start_urls:
            if self.store.add_url(self.task_id, url, None, 0, "seed", "pending"):
                added += 1
        return added

    def _wait_if_paused(self) -> None:
        """暂停时原地等待；停止优先级高于暂停。"""
        if self.should_pause is None:
            return
        while self.should_pause() and not self.should_stop():
            time.sleep(self.pause_interval)

    def run(self) -> RunStats:
        stats = RunStats()
        self.seed()
        max_pages = self.task.crawl.max_pages
        while stats.pages < max_pages and not self.should_stop():
            self._wait_if_paused()
            if self.should_stop():
                break
            batch = self.store.next_pending_urls(self.task_id, limit=5, max_depth=self.task.crawl.max_depth)
            if not batch:
                break
            for row in batch:
                if stats.pages >= max_pages or self.should_stop():
                    break
                self._wait_if_paused()
                if self.should_stop():
                    break
                self._process(row, stats)
        stats.media = self.store.stats(self.task_id)["media"]
        return stats

    def _process(self, row: Any, stats: RunStats) -> None:
        url = row["url"]
        page_type = row["page_type"]
        depth = int(row["depth"])

        dynamic = self._sniff_dynamic(url, page_type)
        if dynamic is not None:
            stats.pages += 1
            content = dynamic.html
            status_code = dynamic.status_code or 200
            self.store.update_url(row["id"], "done", status_code, content_hash=content_hash(content))
            logger.info("动态抓取成功 %s (%s)", url, status_code)
            if page_type in ("seed", "list"):
                self._discover_links(content, url, depth, stats)
                if page_type == "seed" and self.task.crawl.max_depth == 0:
                    self._collect_page(url, row, content, dynamic, stats, dynamic)
            elif page_type in ("discovered", "detail"):
                self._collect_page(url, row, content, dynamic, stats, dynamic)
            return

        result = self.fetcher.fetch(url)
        stats.pages += 1
        if not result.ok:
            failure_type = result.failure_type or "http_error"
            self.store.update_url(
                row["id"],
                "failed",
                result.status_code,
                result.error or f"HTTP {result.status_code}",
                retry_count=result.retry_count,
            )
            self.store.add_failure(self.task_id, url, result.error or "抓取失败", failure_type, result.retry_count)
            stats.failures += 1
            logger.info("抓取失败 %s (%s)", url, failure_type)
            return

        content = result.text or ""
        self.store.update_url(row["id"], "done", result.status_code, content_hash=content_hash(content))
        logger.info("抓取成功 %s (%s)", url, result.status_code)

        if page_type in ("seed", "list"):
            self._discover_links(content, url, depth, stats)
            if page_type == "seed" and self.task.crawl.max_depth == 0:
                self._collect_page(url, row, content, result, stats)

        if page_type in ("discovered", "detail"):
            self._collect_page(url, row, content, result, stats)

    def _discover_links(self, content: str, url: str, depth: int, stats: RunStats) -> None:
        discovery = link_discovery.discover(content, url, self.task)
        added = 0
        if depth + 1 <= self.task.crawl.max_depth:
            for link in discovery.links:
                if self.store.add_url(self.task_id, link, url, depth + 1, "discovered", "pending"):
                    added += 1
            if discovery.next_page:
                if self.store.add_url(self.task_id, discovery.next_page, url, depth + 1, "list", "pending"):
                    added += 1
        stats.discovered += added

    def _sniff_dynamic(self, url: str, page_type: str) -> DynamicPage | None:
        """render_for_media 开启时用 Playwright 抓取页面（含种子/列表页）并嗅探网络媒体。"""
        cfg = self.task.subpage_collect
        if not (cfg.enabled and cfg.render_for_media):
            return None
        if page_type not in ("seed", "list", "discovered", "detail"):
            return None
        if self.dynamic_sniffer is None:
            if not self._dynamic_warned:
                logger.warning("未安装 playwright，动态媒体嗅探回退为静态抓取")
                self._dynamic_warned = True
            return None
        is_subpage = page_type in ("discovered", "detail")
        if is_subpage and (
            self._dynamic_pages >= cfg.max_subpages or self._subpages_collected >= cfg.max_subpages
        ):
            return None
        try:
            page = self.dynamic_sniffer(url, self.task)
        except DynamicSniffError as exc:
            logger.warning("动态媒体嗅探失败，回退静态抓取 %s：%s", url, exc)
            return None
        if is_subpage:
            self._dynamic_pages += 1
        return page

    def _collect_page(
        self,
        url: str,
        row: Any,
        content: str,
        result: Any,
        stats: RunStats,
        dynamic: DynamicPage | None = None,
    ) -> None:
        parent_url = row["parent_url"]
        if self.task.subpage_collect.enabled:
            if self.collector is None:
                return
            if self._subpages_collected >= self.task.subpage_collect.max_subpages:
                return
            self._subpages_collected += 1
            sub = self.collector.collect(
                url,
                content,
                parent_url,
                result.status_code or 200,
                result.headers,
                dynamic=dynamic,
            )
            stats.items += sub.data_items
            stats.failures += sub.errors
            return
        if self.task.detail.fields:
            parsed = parse_detail(
                content,
                url,
                self.task.detail,
                adaptive_store=AdaptiveStore(self.store, self.task_id),
            )
            self.store.add_item(self.task_id, url, parsed.data, content_hash(content))
            stats.items += 1
