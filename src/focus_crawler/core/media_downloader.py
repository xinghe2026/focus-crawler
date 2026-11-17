"""媒体批量下载：并发、按文件名去重、跳过已完成、暂停 / 停止、失败入库。

里程碑 9 用于图片完整采集；视频 / 音频通过注入 download_fn 复用同一调度逻辑。
"""

from __future__ import annotations

import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..utils.hash import media_hash
from ..utils.logging import get_logger
from ..utils.paths import media_dir
from .media_common import (
    MediaDownloadError,
    download_direct,
    filename_with_format,
    guess_format,
    media_headers,
)

logger = get_logger("media_download")

MEDIA_KIND = {"image": "images", "video": "videos", "audio": "audios"}

# 下载函数签名：(fetcher, 媒体记录, 目标文件) -> (本地路径, 字节数)
DownloadFn = Callable[[Any, Any, Path], tuple[Path, int]]


@dataclass
class DownloadSummary:
    total: int = 0
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    stopped: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "downloaded": self.downloaded,
            "skipped": self.skipped,
            "failed": self.failed,
            "stopped": self.stopped,
        }


def download_to_target(fetcher: Any, row: Any, target: Path) -> tuple[Path, int]:
    """直链下载到指定文件（供默认下载器使用，携带记录中的 Referer 等请求头）。"""
    return download_direct(fetcher, row["media_url"], target.parent, target.name, headers=media_headers(row))


class MediaDownloader:
    def __init__(
        self,
        store: Any,
        task_id: int,
        task_name: str,
        fetcher: Any,
        media_type: str = "image",
        concurrency: int = 3,
        media_root: str | Path | None = None,
        download_fn: DownloadFn | None = None,
        should_stop: Callable[[], bool] | None = None,
        should_pause: Callable[[], bool] | None = None,
        pause_interval: float = 0.15,
        on_progress: Callable[[int, int], None] | None = None,
    ):
        if media_type not in MEDIA_KIND:
            raise ValueError(f"未知媒体类型: {media_type}")
        self.store = store
        self.task_id = task_id
        self.task_name = task_name
        self.fetcher = fetcher
        self.media_type = media_type
        self.concurrency = max(1, int(concurrency))
        self.media_root = Path(media_root) if media_root else None
        self.download_fn = download_fn or download_to_target
        self.should_stop = should_stop or (lambda: False)
        self.should_pause = should_pause or (lambda: False)
        self.pause_interval = pause_interval
        self.on_progress = on_progress

    # ---------- public ----------
    def run(self, media_ids: list[int] | None = None) -> DownloadSummary:
        rows = self.store.list_media(self.task_id, media_type=self.media_type)
        if media_ids is not None:
            wanted = {int(item) for item in media_ids}
            rows = [row for row in rows if int(row["id"]) in wanted]
        summary = DownloadSummary(total=len(rows))
        if not rows:
            return summary

        folder = media_dir(MEDIA_KIND[self.media_type], self.task_name, home=self.media_root)
        queued = self._plan(rows, folder, summary)
        if not queued:
            self._emit_progress(summary)
            return summary

        self._emit_progress(summary)
        processed = 0
        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            pending = deque(queued)
            futures: dict[Future, Any] = {}
            while futures or (pending and not self.should_stop()):
                if pending and not self.should_stop() and not self.should_pause():
                    while pending and len(futures) < self.concurrency:
                        row, target = pending.popleft()
                        self.store.update_media(int(row["id"]), status="downloading")
                        futures[executor.submit(self._fetch_one, row, target)] = row
                if not futures:
                    if self.should_stop():
                        break
                    time.sleep(self.pause_interval)
                    continue
                done, _ = wait(
                    futures, timeout=self.pause_interval, return_when=FIRST_COMPLETED
                )
                for future in done:
                    row = futures.pop(future)
                    self._apply_result(row, future, summary)
                    processed += 1
                    self._emit_progress(summary, processed)
            if pending and self.should_stop():
                summary.stopped = True
        return summary

    # ---------- 计划 ----------
    def _plan(self, rows: list[Any], folder: Path, summary: DownloadSummary) -> list[tuple[Any, Path]]:
        """过滤已完成的记录并给每个待下载记录分配唯一文件名。"""
        reserved: dict[str, int] = {}
        for row in self.store.list_media(self.task_id, media_type=self.media_type, status="done"):
            local = row["local_path"]
            if local:
                reserved[Path(local).name] = int(row["id"])

        queued: list[tuple[Any, Path]] = []
        for row in rows:
            media_id = int(row["id"])
            local = row["local_path"]
            if row["status"] == "done" and local and Path(local).exists():
                summary.skipped += 1
                continue
            name = self._unique_name(row, reserved)
            reserved[name] = media_id
            queued.append((row, folder / name))
        return queued

    def _unique_name(self, row: Any, reserved: dict[str, int]) -> str:
        url = row["media_url"]
        fmt = row["format"] or guess_format(url)
        base = filename_with_format(url, fmt, "bin")
        media_id = int(row["id"])
        if reserved.get(base, media_id) == media_id:
            return base
        path = Path(base)
        stem, suffix = (path.stem, path.suffix) if path.suffix else (base, "")
        short = media_hash(url)[:8]
        candidate = f"{stem}_{short}{suffix}"
        counter = 1
        while reserved.get(candidate, media_id) != media_id:
            candidate = f"{stem}_{short}_{counter}{suffix}"
            counter += 1
        return candidate

    # ---------- 执行 ----------
    def _fetch_one(self, row: Any, target: Path) -> tuple[Path | None, int, str | None, str | None]:
        try:
            path, size = self.download_fn(self.fetcher, row, target)
            return path, size, None, None
        except MediaDownloadError as exc:
            return None, 0, str(exc), exc.failure_type
        except Exception as exc:  # noqa: BLE001 - 单条媒体失败不能中断整批
            return None, 0, str(exc), "media_download_error"

    def _apply_result(self, row: Any, future: Future, summary: DownloadSummary) -> None:
        path, size, error, failure_type = future.result()
        media_id = int(row["id"])
        if error:
            self.store.update_media(media_id, status="failed", error=error)
            self.store.add_failure(
                self.task_id, row["media_url"], error, failure_type or "media_download_error", 0
            )
            summary.failed += 1
            logger.warning("媒体下载失败 %s：%s", row["media_url"], error)
            return
        self.store.update_media(media_id, status="done", local_path=str(path), file_size=size)
        summary.downloaded += 1
        logger.info("媒体已下载 %s -> %s（%d 字节）", row["media_url"], path, size)

    def _emit_progress(self, summary: DownloadSummary, processed: int = 0) -> None:
        if self.on_progress is None:
            return
        total = summary.total
        done = processed + summary.skipped
        self.on_progress(min(done, total), total)
