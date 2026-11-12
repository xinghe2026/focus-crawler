"""视频下载：直链、HLS（m3u8 / ts / AES-128）、DASH（fMP4）。

HLS 分片通过 ffmpeg 合并为 mp4；DASH 的 fMP4 分片按字节顺序拼接。
不处理 DRM（ContentProtection / SAMPLE-AES）。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

from ..config.schema import VideoConfig
from ..utils.hash import media_hash
from ..utils.logging import get_logger
from .dash import parse_mpd
from .media_common import MediaDownloadError, download_direct, guess_format
from .media_pipeline import (
    MergeFn,
    concat_files,
    download_hls,
    fetch_bytes,
    fetch_text,
    merge_with_ffmpeg,
    record_segment,
    wait_if_paused,
)

logger = get_logger("video_download")

__all__ = ["download", "download_video", "merge_with_ffmpeg"]


def download(url: str, fetcher: Any, dest_dir: str | Path) -> tuple[Path, int]:
    return download_direct(fetcher, url, dest_dir)


def download_video(
    fetcher: Any,
    url: str,
    target: Path,
    config: VideoConfig | None = None,
    store: Any = None,
    media_id: int | None = None,
    ffmpeg_path: str | None = None,
    should_stop: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
    merge_fn: MergeFn | None = None,
    pause_interval: float = 0.15,
    headers: dict[str, str] | None = None,
) -> tuple[Path, int]:
    """按链接类型下载视频，返回 (本地路径, 字节数)。"""
    target = Path(target)
    merged_headers = dict(config.headers) if config and config.headers else {}
    if headers:
        merged_headers.update(headers)
    fmt = guess_format(url)
    if fmt == "m3u8":
        return download_hls(
            fetcher,
            url,
            target,
            headers=merged_headers,
            store=store,
            media_id=media_id,
            merge_fn=merge_fn,
            ffmpeg_path=ffmpeg_path or (config.ffmpeg_path if config else "ffmpeg"),
            preferred=(config.preferred_quality if config else None) or "highest",
            should_stop=should_stop,
            should_pause=should_pause,
            pause_interval=pause_interval,
        )
    if fmt == "mpd":
        return _download_dash(
            fetcher, url, target, store, media_id, merged_headers, should_stop, should_pause, pause_interval
        )
    return download_direct(fetcher, url, target.parent, target.name, headers=merged_headers or None)


def _download_dash(
    fetcher: Any,
    url: str,
    target: Path,
    store: Any,
    media_id: int | None,
    headers: dict[str, str],
    should_stop: Callable[[], bool] | None,
    should_pause: Callable[[], bool] | None,
    pause_interval: float,
) -> tuple[Path, int]:
    segments = parse_mpd(fetch_text(fetcher, url, headers), url, "video")
    urls: list[str] = []
    if segments.init_url:
        urls.append(segments.init_url)
    urls.extend(segments.segment_urls)

    work_dir = target.parent / f".dash_{media_hash(url)[:12]}"
    work_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    try:
        for index, segment_url in enumerate(urls):
            wait_if_paused(should_pause, should_stop, pause_interval)
            if should_stop and should_stop():
                raise MediaDownloadError("下载已停止", "media_download_error")
            suffix = Path(segment_url.split("?", 1)[0]).suffix or (".mp4" if index == 0 else ".m4s")
            path = work_dir / f"part_{index:05d}{suffix}"
            try:
                if not (path.exists() and path.stat().st_size > 0):
                    path.write_bytes(fetch_bytes(fetcher, segment_url, headers))
                record_segment(store, media_id, index, segment_url, path, "done")
            except MediaDownloadError as exc:
                record_segment(store, media_id, index, segment_url, None, "failed", str(exc))
                raise
            paths.append(path)

        output = target.with_suffix(".mp4")
        concat_files(paths, output)
        if not output.exists() or output.stat().st_size == 0:
            raise MediaDownloadError("DASH 合并后的视频文件为空", "media_download_error")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    logger.info("DASH 下载完成 %s -> %s", url, output)
    return output, output.stat().st_size
