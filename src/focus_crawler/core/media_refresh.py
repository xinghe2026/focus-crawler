"""媒体直链刷新：下载前重新解析平台适配器，避免过期签名地址返回 403。"""

from __future__ import annotations

from typing import Any

from ..utils.logging import get_logger
from .audio_adapter import AdapterContext, AdapterError, adapter_for, resolve_audio
from .video_adapter import adapter_for as video_adapter_for, resolve_video
from .media_common import MediaCandidate

logger = get_logger("media_refresh")


def _bitrate_of(candidate: MediaCandidate) -> int:
    value = (candidate.extra or {}).get("bitrate")
    return int(value) if isinstance(value, (int, float)) else 0


def _score_by_size(task: Any, row: Any, item: MediaCandidate) -> float:
    """挑最接近原记录尺寸/清晰度的候选。"""
    try:
        target_height = int(row["height"] or 0)
    except (IndexError, KeyError, TypeError, ValueError):
        target_height = 0
    height = item.height or 0
    return -abs(height - target_height) if target_height else height


def refresh_video(task: Any, row: Any, fetcher: Any) -> MediaCandidate | None:
    """按来源页重新解析平台视频直链（签名过期 403 时使用）。

    未启用对应适配器、来源页不是平台页或解析失败时返回 None，调用方继续用原地址。
    """
    source_url = str(row["source_url"] or "")
    if not source_url or not task.video.adapter.bilibili:
        return None
    if video_adapter_for(source_url, task.video) != "bilibili":
        return None
    try:
        items = resolve_video(source_url, task.video, AdapterContext(fetcher=fetcher))
    except AdapterError as exc:
        logger.warning("视频地址刷新失败 %s：%s", source_url, exc)
        return None
    if not items:
        return None
    item = max(items, key=lambda entry: _score_by_size(task, row, entry))
    logger.info("视频地址已刷新 %s -> %s", row["media_url"], item.url)
    return item


def refresh_audio(task: Any, row: Any, fetcher: Any) -> MediaCandidate | None:
    """按来源页重新解析 B 站音频轨道，返回与记录码率最接近的新候选。

    未启用 B 站适配器、来源页不是 B 站或解析失败时返回 None，调用方继续使用原地址。
    """
    source_url = str(row["source_url"] or "")
    if not source_url or not task.audio.adapter.bilibili:
        return None
    if adapter_for(source_url, task.audio) != "bilibili":
        return None
    try:
        items = resolve_audio(source_url, task.audio, AdapterContext(fetcher=fetcher))
    except AdapterError as exc:
        logger.warning("音频地址刷新失败 %s：%s", source_url, exc)
        return None
    if not items:
        return None
    try:
        target_bitrate = int(row["bitrate"] or 0)
    except (IndexError, KeyError, TypeError, ValueError):
        target_bitrate = 0
    if target_bitrate:
        item = min(items, key=lambda entry: abs(_bitrate_of(entry) - target_bitrate))
    else:
        item = max(items, key=_bitrate_of)
    logger.info("音频地址已刷新 %s -> %s", row["media_url"], item.url)
    return item
