"""媒体分片管线：拉取、解密、断点复用、ffmpeg 合并与字节拼接。"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from ..utils.aes import cbc_decrypt, strip_pkcs7
from ..utils.hash import media_hash
from ..utils.logging import get_logger
from .hls import HlsMedia, HlsSegment, parse_playlist, pick_audio, pick_variant, segment_iv
from .media_common import MediaDownloadError

logger = get_logger("media_pipeline")

MergeFn = Callable[[list[Path], Path, str], None]
SUPPORTED_KEY_METHODS = {"NONE", "AES-128"}


def fetch_text(fetcher: Any, url: str, headers: dict[str, str] | None) -> str:
    result = fetcher.fetch(url, headers=headers or None)
    if not result.ok or not result.text:
        raise MediaDownloadError(
            result.error or f"HTTP {result.status_code}", result.failure_type or "media_download_error"
        )
    return result.text


def fetch_bytes(fetcher: Any, url: str, headers: dict[str, str] | None) -> bytes:
    result = fetcher.fetch(url, headers=headers or None, want_bytes=True)
    if not result.ok or result.content is None:
        raise MediaDownloadError(
            result.error or f"HTTP {result.status_code}", result.failure_type or "media_download_error"
        )
    return result.content


def wait_if_paused(
    should_pause: Callable[[], bool] | None,
    should_stop: Callable[[], bool] | None,
    pause_interval: float,
) -> None:
    while should_pause and should_pause():
        if should_stop and should_stop():
            raise MediaDownloadError("下载已停止", "media_download_error")
        time.sleep(pause_interval)


def record_segment(
    store: Any,
    media_id: int | None,
    index: int,
    segment_url: str,
    path: Path | None,
    status: str,
    error: str | None = None,
) -> None:
    if store is None or media_id is None:
        return
    store.upsert_media_segment(media_id, index, segment_url, str(path) if path else None, status, error)


def download_hls(
    fetcher: Any,
    url: str,
    target: Path,
    headers: dict[str, str] | None = None,
    audio: bool = False,
    store: Any = None,
    media_id: int | None = None,
    merge_fn: MergeFn | None = None,
    ffmpeg_path: str = "ffmpeg",
    preferred: str = "highest",
    should_stop: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
    pause_interval: float = 0.15,
) -> tuple[Path, int]:
    """下载 HLS 播放列表（视频或纯音频），返回 (输出路径, 字节数)。"""
    target = Path(target)
    text = fetch_text(fetcher, url, headers)
    playlist = parse_playlist(text, url)

    media: HlsMedia | None = pick_audio(playlist) if audio else None
    if media is not None:
        text = fetch_text(fetcher, media.uri, headers)
        playlist = parse_playlist(text, media.uri)
    elif playlist.is_master:
        if audio:
            raise MediaDownloadError("m3u8 主列表未提供独立音频轨道", "media_download_error")
        variant = pick_variant(playlist, preferred)
        if variant is None:
            raise MediaDownloadError("m3u8 主列表没有可用清晰度")
        text = fetch_text(fetcher, variant.url, headers)
        playlist = parse_playlist(text, variant.url)

    if not playlist.segments:
        raise MediaDownloadError("m3u8 播放列表没有分片")

    work_dir = target.parent / f".hls_{media_hash(url)[:12]}"
    work_dir.mkdir(parents=True, exist_ok=True)
    keys: dict[str, bytes] = {}
    segment_paths: list[Path] = []

    for index, map_url in enumerate(playlist.maps):
        path = work_dir / f"map_{index:04d}.mp4"
        if not (path.exists() and path.stat().st_size > 0):
            path.write_bytes(fetch_bytes(fetcher, map_url, headers))
        record_segment(store, media_id, index, map_url, path, "done")
        segment_paths.append(path)

    offset = len(playlist.maps)
    for index, segment in enumerate(playlist.segments):
        wait_if_paused(should_pause, should_stop, pause_interval)
        if should_stop and should_stop():
            raise MediaDownloadError("下载已停止", "media_download_error")
        suffix = Path(segment.url.split("?", 1)[0]).suffix or ".ts"
        path = work_dir / f"segment_{index + offset:05d}{suffix}"
        try:
            if not (path.exists() and path.stat().st_size > 0):
                data = fetch_bytes(fetcher, segment.url, headers)
                if segment.key and segment.key.method == "AES-128":
                    data = _decrypt_segment(data, segment, keys, fetcher, headers)
                elif segment.key and segment.key.method not in SUPPORTED_KEY_METHODS:
                    raise MediaDownloadError(
                        f"暂不支持的分片加密方式：{segment.key.method}", "media_download_error"
                    )
                path.write_bytes(data)
            record_segment(store, media_id, index + offset, segment.url, path, "done")
        except MediaDownloadError as exc:
            record_segment(store, media_id, index + offset, segment.url, None, "failed", str(exc))
            raise
        segment_paths.append(path)

    output = target.with_suffix(".m4a" if audio else ".mp4")
    merge = merge_fn or merge_with_ffmpeg
    merge(segment_paths, output, ffmpeg_path)
    if not output.exists() or output.stat().st_size == 0:
        raise MediaDownloadError("合并后的媒体文件为空", "media_download_error")
    shutil.rmtree(work_dir, ignore_errors=True)
    logger.info("HLS 下载完成 %s -> %s", url, output)
    return output, output.stat().st_size


def _decrypt_segment(
    data: bytes, segment: HlsSegment, keys: dict[str, bytes], fetcher: Any, headers: dict[str, str] | None
) -> bytes:
    key = segment.key
    if key is None or not key.uri:
        raise MediaDownloadError("AES-128 分片缺少密钥地址", "media_download_error")
    if key.uri not in keys:
        keys[key.uri] = fetch_bytes(fetcher, key.uri, headers)
    key_bytes = keys[key.uri]
    if len(key_bytes) != 16:
        raise MediaDownloadError("HLS 密钥长度不是 16 字节", "media_download_error")
    return strip_pkcs7(cbc_decrypt(data, key_bytes, segment_iv(segment)))


def merge_with_ffmpeg(segment_paths: list[Path], output: Path, ffmpeg_path: str = "ffmpeg") -> None:
    if not segment_paths:
        raise MediaDownloadError("没有可合并的分片", "media_download_error")
    output.parent.mkdir(parents=True, exist_ok=True)
    list_file = output.parent / f".{output.stem}_concat.txt"
    lines = []
    for path in segment_paths:
        escaped = str(path).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{escaped}'\n")
    list_file.write_text("".join(lines), encoding="utf-8")
    command = [
        ffmpeg_path,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
        str(output),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=3600,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except FileNotFoundError as exc:
        raise MediaDownloadError(
            "未找到 ffmpeg，请安装并在设置中指定 ffmpeg 路径", "media_download_error"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaDownloadError("ffmpeg 合并超时", "media_download_error") from exc
    finally:
        list_file.unlink(missing_ok=True)
    if completed.returncode != 0 or not output.exists():
        detail = (completed.stderr or "").strip().splitlines()
        reason = detail[-1] if detail else f"ffmpeg 返回码 {completed.returncode}"
        raise MediaDownloadError(f"ffmpeg 合并失败：{reason}", "media_download_error")


def concat_files(paths: list[Path], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as handle:
        for path in paths:
            with path.open("rb") as source:
                shutil.copyfileobj(source, handle)
