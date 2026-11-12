"""媒体记录模型。"""

from __future__ import annotations

from dataclasses import dataclass

MEDIA_TYPES = ("image", "video", "audio")
MEDIA_STATUSES = ("discovered", "downloading", "done", "failed", "skipped")


@dataclass
class MediaRecord:
    task_id: int
    source_url: str
    parent_url: str | None
    media_url: str
    media_hash: str
    media_type: str
    format: str | None = None
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    bitrate: int | None = None
    sample_rate: int | None = None
    quality: str | None = None
    file_size: int | None = None
    local_path: str | None = None
    headers: str | None = None
    source_kind: str | None = None
    # yt-dlp 记录：下载前用于重新解析的页面地址（播放列表条目与来源页可能不同）
    origin_url: str | None = None
    status: str = "discovered"
    error: str | None = None
    discovered_at: str | None = None
