"""GUI 后台线程：预览与运行。

所有网络请求都在工作线程内完成，界面通过信号接收结果，保持响应。
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..config.schema import TaskConfig
from ..core.audio_downloader import download_audio
from ..core.dynamic_sniffer import playwright_available, sniff_page
from ..core.fetcher import Fetcher
from ..core.hotlink import resolve_headers
from ..core.media_common import download_direct, media_headers, media_origin_url, media_source_kind
from ..core.media_downloader import MediaDownloader
from ..core.media_refresh import refresh_audio
from ..core.preview import preview_task
from ..core.scheduler import Scheduler
from ..core.storage import Storage
from ..core.subpage_collector import SubpageCollector
from ..core.video_downloader import download_video


def build_fetcher(task: TaskConfig) -> Fetcher:
    crawl = task.crawl
    return Fetcher(
        timeout_ms=crawl.timeout_ms,
        retry=crawl.retry,
        delay_ms=crawl.delay_ms,
        respect_robots=crawl.respect_robots,
    )


class PreviewWorker(QObject):
    """运行前预览：链接发现 + 字段命中率 + 媒体统计。"""

    done = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, task: TaskConfig, subpage_limit: int = 10):
        super().__init__()
        self.task = task
        self.subpage_limit = subpage_limit

    def run(self) -> None:
        fetcher = build_fetcher(self.task)
        sniffer = None
        if self.task.subpage_collect.render_for_media and playwright_available():
            sniffer = sniff_page
        try:
            result = preview_task(
                self.task,
                fetcher,
                subpage_limit=self.subpage_limit,
                dynamic_sniffer=sniffer,
            )
            self.done.emit(result)
        except Exception as exc:  # noqa: BLE001 - 界面需要展示所有异常
            self.failed.emit(f"预览失败：{exc}")
        finally:
            fetcher.close()
            self.finished.emit()


class RunWorker(QObject):
    """执行一次抓取任务，支持暂停 / 继续 / 停止。"""

    done = Signal(dict)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        task: TaskConfig,
        db_path: str | Path,
        config_dir: str | Path | None = None,
        copyright_confirmed: bool = False,
        ffmpeg_path: str | None = None,
        media_root: str | Path | None = None,
    ):
        super().__init__()
        self.task = task
        self.db_path = Path(db_path)
        self.config_dir = Path(config_dir) if config_dir else None
        self.copyright_confirmed = copyright_confirmed
        self.ffmpeg_path = ffmpeg_path
        self.media_root = media_root
        self._stop = False
        self._pause = False

    # ---------- 控制 ----------
    def pause(self) -> None:
        self._pause = True

    def resume(self) -> None:
        self._pause = False

    def stop(self) -> None:
        self._stop = True
        self._pause = False

    @property
    def paused(self) -> bool:
        return self._pause

    # ---------- 执行 ----------
    def run(self) -> None:
        store: Storage | None = None
        fetcher: Fetcher | None = None
        try:
            store = Storage(self.db_path)
            fetcher = build_fetcher(self.task)
            task_id = store.upsert_task(
                self.task.task_name, json.dumps(self.task.to_dict(), ensure_ascii=False)
            )
            collector = None
            if self.task.subpage_collect.enabled:
                collector = SubpageCollector(
                    task=self.task,
                    store=store,
                    task_id=task_id,
                    fetcher=fetcher,
                    base_dir=self.config_dir,
                    copyright_confirmed=self.copyright_confirmed,
                    ffmpeg_path=self.ffmpeg_path or self.task.video.ffmpeg_path,
                    media_root=self.media_root,
                )
            scheduler = Scheduler(
                self.task,
                store,
                task_id,
                fetcher,
                collector,
                should_stop=lambda: self._stop,
                should_pause=lambda: self._pause,
            )
            scheduler.seed()
            stats = scheduler.run()
            self.done.emit(
                {
                    "task_id": task_id,
                    "task_name": self.task.task_name,
                    "pages": stats.pages,
                    "discovered": stats.discovered,
                    "items": stats.items,
                    "failures": stats.failures,
                    "media": stats.media,
                    "stopped": self._stop,
                }
            )
        except Exception as exc:  # noqa: BLE001 - 界面需要展示所有异常
            self.failed.emit(f"运行失败：{exc}")
        finally:
            if fetcher is not None:
                fetcher.close()
            if store is not None:
                store.close()
            self.finished.emit()


class MediaDownloadWorker(QObject):
    """批量下载已发现的媒体，支持暂停 / 继续 / 停止。"""

    progress = Signal(int, int)
    done = Signal(dict)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        task: TaskConfig,
        db_path: str | Path,
        task_id: int,
        media_ids: list[int],
        media_type: str = "image",
        concurrency: int = 3,
        copyright_confirmed: bool = False,
        ffmpeg_path: str | None = None,
        media_root: str | Path | None = None,
    ):
        super().__init__()
        self.task = task
        self.db_path = Path(db_path)
        self.task_id = task_id
        self.media_ids = list(media_ids)
        self.media_type = media_type
        self.concurrency = concurrency
        self.copyright_confirmed = copyright_confirmed
        self.ffmpeg_path = ffmpeg_path
        self.media_root = media_root
        self._stop = False
        self._pause = False

    def pause(self) -> None:
        self._pause = True

    def resume(self) -> None:
        self._pause = False

    def stop(self) -> None:
        self._stop = True
        self._pause = False

    @property
    def paused(self) -> bool:
        return self._pause

    def run(self) -> None:
        store: Storage | None = None
        fetcher: Fetcher | None = None
        try:
            if not self.copyright_confirmed:
                self.failed.emit("未确认媒体版权与合规用途，已取消下载")
                return
            store = Storage(self.db_path)
            fetcher = build_fetcher(self.task)
            download_fn = None
            if self.media_type == "image":
                download_fn = lambda f, row, target: download_direct(
                    f,
                    row["media_url"],
                    target.parent,
                    target.name,
                    headers=resolve_headers(
                        self.task.hotlink,
                        row["media_url"],
                        row["source_url"],
                        media_headers(row),
                    ),
                    fallback=self.task.media_fallback,
                )
            elif self.media_type == "video":
                ffmpeg = self.ffmpeg_path or self.task.video.ffmpeg_path
                download_fn = lambda f, row, target: download_video(
                    f,
                    row["media_url"],
                    target,
                    config=self.task.video,
                    store=store,
                    media_id=int(row["id"]),
                    ffmpeg_path=ffmpeg,
                    source_kind=media_source_kind(row),
                    page_url=media_origin_url(row) or row["source_url"],
                    headers=resolve_headers(
                        self.task.hotlink,
                        row["media_url"],
                        row["source_url"],
                        media_headers(row),
                    ),
                    should_stop=lambda: self._stop,
                    should_pause=lambda: self._pause,
                )
            elif self.media_type == "audio":
                ffmpeg = self.ffmpeg_path or self.task.video.ffmpeg_path
                refresh_cache: dict[str, dict | None] = {}

                def download_audio_refreshed(f, row, target):
                    source_url = row["source_url"] or ""
                    if source_url not in refresh_cache:
                        refresh_cache[source_url] = refresh_audio(self.task, row, f)
                    fresh = refresh_cache[source_url]
                    media_url = fresh.url if fresh else row["media_url"]
                    existing_headers = (
                        dict(fresh.extra.get("headers") or {}) if fresh else media_headers(row)
                    )
                    headers = resolve_headers(
                        self.task.hotlink,
                        media_url,
                        source_url,
                        existing_headers,
                    )
                    if fresh:
                        store.update_media_source(int(row["id"]), media_url)
                    return download_audio(
                        f,
                        media_url,
                        target,
                        config=self.task.audio,
                        store=store,
                        media_id=int(row["id"]),
                        ffmpeg_path=ffmpeg,
                        headers=headers,
                        source_kind=media_source_kind(row),
                        page_url=media_origin_url(row) or source_url,
                        should_stop=lambda: self._stop,
                        should_pause=lambda: self._pause,
                    )

                download_fn = download_audio_refreshed
            downloader = MediaDownloader(
                store=store,
                task_id=self.task_id,
                task_name=self.task.task_name,
                fetcher=fetcher,
                media_type=self.media_type,
                concurrency=self.concurrency,
                media_root=self.media_root,
                download_fn=download_fn,
                should_stop=lambda: self._stop,
                should_pause=lambda: self._pause,
                on_progress=lambda done, total: self.progress.emit(done, total),
            )
            summary = downloader.run(self.media_ids)
            self.done.emit(summary.as_dict())
        except Exception as exc:  # noqa: BLE001 - 界面需要展示所有异常
            self.failed.emit(f"下载失败：{exc}")
        finally:
            if fetcher is not None:
                fetcher.close()
            if store is not None:
                store.close()
            self.finished.emit()
