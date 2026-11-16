"""子页多类型采集调度：data / image / video / audio。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config.schema import TaskConfig
from ..models.media import MediaRecord
from ..models.parse_context import ParseContext
from ..utils.hash import content_hash, media_hash, normalize_url, sha256_text
from ..utils.logging import get_logger
from ..utils.paths import media_dir
from . import image_crawler
from .adaptive import AdaptiveStore
from .audio_adapter import AdapterContext, AdapterError, resolve_audio
from .audio_downloader import download_audio
from .audio_sniffer import sniff_static as sniff_audio
from .hotlink import resolve_headers
from .media_common import (
    MediaCandidate,
    MediaDownloadError,
    download_direct,
    dynamic_media_candidates,
    filename_with_format,
    guess_format,
    media_headers,
    media_origin_url,
    media_source_kind,
)
from .parser import parse_detail
from .script_engine import ScriptEngine
from .video_adapter import resolve_video
from .video_downloader import download_video
from .video_sniffer import sniff_static as sniff_video

if TYPE_CHECKING:
    from .dynamic_sniffer import DynamicPage

logger = get_logger("subpage")


@dataclass
class SubpageStats:
    data_items: int = 0
    images: int = 0
    videos: int = 0
    audios: int = 0
    downloaded: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "data_items": self.data_items,
            "images": self.images,
            "videos": self.videos,
            "audios": self.audios,
            "downloaded": self.downloaded,
            "errors": self.errors,
        }


class SubpageCollector:
    def __init__(
        self,
        task: TaskConfig,
        store: Any,
        task_id: int,
        fetcher: Any = None,
        script_engine: ScriptEngine | None = None,
        base_dir: str | Path | None = None,
        media_root: str | Path | None = None,
        copyright_confirmed: bool = False,
        ffmpeg_path: str = "ffmpeg",
    ):
        self.task = task
        self.store = store
        self.task_id = task_id
        self.fetcher = fetcher
        self.base_dir = Path(base_dir) if base_dir else None
        self.media_root = Path(media_root) if media_root else None
        self.copyright_confirmed = copyright_confirmed
        self.ffmpeg_path = ffmpeg_path
        self.script_engine = script_engine or ScriptEngine(
            timeout_ms=task.parser.script_timeout_ms, logger=logger
        )

    # ---------- public ----------
    def collect(
        self,
        url: str,
        html: str,
        parent_url: str | None = None,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        dynamic: "DynamicPage | None" = None,
    ) -> SubpageStats:
        cfg = self.task.subpage_collect
        stats = SubpageStats()
        types = cfg.types
        page_html = dynamic.html if dynamic is not None and dynamic.html else html
        if "data" in types:
            self._collect_data(url, page_html, status_code, headers, stats)
        if "image" in types and self.task.image.enabled:
            candidates = image_crawler.scan_page(page_html, url, self.task.image)
            stats.images = self._save_media(candidates, url, parent_url, "image", stats)
        if "video" in types and self.task.video.enabled:
            candidates = sniff_video(page_html, url, self.task.video)
            if dynamic is not None:
                candidates.extend(dynamic_media_candidates(dynamic.video_urls, "video", dynamic.headers))
            candidates.extend(self._adapter_video(url, headers, stats, existing=len(candidates)))
            stats.videos = self._save_media(
                candidates[: self.task.video.max_count], url, parent_url, "video", stats
            )
        if "audio" in types and self.task.audio.enabled:
            candidates = sniff_audio(page_html, url, self.task.audio)
            if dynamic is not None:
                candidates.extend(dynamic_media_candidates(dynamic.audio_urls, "audio", dynamic.headers))
            candidates.extend(self._adapter_audio(url, headers, stats, existing=len(candidates)))
            stats.audios = self._save_media(
                candidates[: self.task.audio.max_count], url, parent_url, "audio", stats
            )
        return stats

    def collect_url(self, url: str, parent_url: str | None = None) -> SubpageStats:
        if self.fetcher is None:
            raise RuntimeError("collect_url 需要 fetcher")
        result = self.fetcher.fetch(url)
        if not result.ok:
            self.store.add_failure(
                self.task_id,
                url,
                result.error or f"HTTP {result.status_code}",
                result.failure_type or "http_error",
                result.retry_count,
            )
            stats = SubpageStats(errors=1)
            return stats
        return self.collect(url, result.text, parent_url, result.status_code, result.headers)

    # ---------- data ----------
    def _collect_data(
        self,
        url: str,
        html: str,
        status_code: int,
        headers: dict[str, str] | None,
        stats: SubpageStats,
    ) -> None:
        parser_cfg = self.task.parser
        mode = parser_cfg.mode
        data: dict[str, Any] | None = None

        if mode in ("custom", "hybrid"):
            script_path = self._resolve_script(parser_cfg.detail_script)
            if script_path:
                ctx = ParseContext(
                    html=html,
                    url=url,
                    page_type="detail",
                    status_code=status_code,
                    headers=headers or {},
                    config=self.task,
                    logger=logger,
                    task_name=self.task.task_name,
                )
                result = self.script_engine.run(
                    script_path, "parse_detail", html, url, ctx=ctx, page_type="detail"
                )
                self.store.add_parser_run(
                    self.task_id,
                    url,
                    str(script_path),
                    "ok" if result.ok else "error",
                    result.error_type,
                    result.message,
                    result.duration_ms,
                )
                if result.ok and isinstance(result.data, dict):
                    data = result.data
                elif mode == "custom" and not parser_cfg.fallback_to_selector:
                    self.store.add_failure(
                        self.task_id, url, result.message or "脚本失败", result.error_type or "parse_error", 0
                    )
                    stats.errors += 1
                    return

        if data is None:
            if not self.task.detail.fields:
                return
            parsed = parse_detail(
                html,
                url,
                self.task.detail,
                adaptive_store=AdaptiveStore(self.store, self.task_id),
            )
            data = parsed.data

        self.store.add_item(self.task_id, url, data, content_hash(html))
        stats.data_items += 1

    def _resolve_script(self, relative: str) -> Path | None:
        if not relative:
            return None
        path = Path(relative)
        if not path.is_absolute():
            if self.base_dir is None:
                return None
            path = self.base_dir / path
        return path if path.exists() else None

    # ---------- media ----------
    def _adapter_video(
        self,
        url: str,
        headers: dict[str, str] | None,
        stats: SubpageStats,
        existing: int = 0,
    ) -> list[MediaCandidate]:
        """调用平台视频适配器 + yt-dlp 通用解析补充候选；失败按 adapter_error 记录。"""
        if self.fetcher is None:
            return []
        ctx = AdapterContext(
            fetcher=self.fetcher,
            headers=dict(headers or {}),
            base_dir=self.base_dir,
            existing=existing,
        )
        try:
            return resolve_video(url, self.task.video, ctx)
        except AdapterError as exc:
            self.store.add_failure(self.task_id, url, str(exc), "adapter_error", 0)
            stats.errors += 1
            logger.warning("视频适配器失败 %s：%s", url, exc)
            return []

    def _adapter_audio(
        self,
        url: str,
        headers: dict[str, str] | None,
        stats: SubpageStats,
        existing: int = 0,
    ) -> list[MediaCandidate]:
        """调用平台适配器 + yt-dlp 通用解析补充音频候选；失败按 adapter_error 记录。"""
        if self.fetcher is None:
            return []
        ctx = AdapterContext(
            fetcher=self.fetcher,
            headers=dict(headers or {}),
            base_dir=self.base_dir,
            existing=existing,
        )
        try:
            return resolve_audio(url, self.task.audio, ctx)
        except AdapterError as exc:
            self.store.add_failure(self.task_id, url, str(exc), "adapter_error", 0)
            stats.errors += 1
            logger.warning("音频适配器失败 %s：%s", url, exc)
            return []

    def _save_media(
        self,
        candidates: list[MediaCandidate],
        source_url: str,
        parent_url: str | None,
        media_type: str,
        stats: SubpageStats,
    ) -> int:
        cfg = self.task.subpage_collect
        inserted = 0
        for candidate in candidates:
            extra = candidate.extra or {}
            candidate_headers = resolve_headers(
                self.task.hotlink,
                candidate.url,
                source_url,
                extra.get("headers"),
            )
            headers_value = (
                json.dumps(candidate_headers, ensure_ascii=False) if candidate_headers else None
            )
            dedupe_key = str(extra.get("dedupe_key") or "")
            record_hash = (
                media_hash(candidate.url)
                if not dedupe_key
                else sha256_text(f"{normalize_url(candidate.url)}::{dedupe_key}")
            )
            # yt-dlp 记录额外保存"下载前重新解析"的页面地址（播放列表条目与来源页可能不同）
            origin_url = (
                str(extra.get("page_url") or "") or None
                if (candidate.source_kind or "").startswith("ytdlp")
                else None
            )
            record = MediaRecord(
                task_id=self.task_id,
                source_url=source_url,
                parent_url=parent_url,
                media_url=candidate.url,
                media_hash=record_hash,
                media_type=media_type,
                format=candidate.format or guess_format(candidate.url),
                width=candidate.width,
                height=candidate.height,
                duration=extra.get("duration"),
                bitrate=extra.get("bitrate"),
                sample_rate=extra.get("sample_rate"),
                quality=candidate.quality,
                headers=headers_value,
                source_kind=candidate.source_kind or None,
                origin_url=origin_url,
                status="discovered",
                discovered_at=datetime.now().replace(microsecond=0).isoformat(sep=" "),
            )
            if not self.store.add_media(record):
                continue
            inserted += 1
            if cfg.media_mode == "auto_download" and self._auto_download_enabled(media_type):
                row = self.store.get_media(self.task_id, record.media_hash)
                self._download_media(row, media_type, stats)
        return inserted

    def _auto_download_enabled(self, media_type: str) -> bool:
        cfg = self.task.subpage_collect
        flag = {
            "image": cfg.auto_download_images,
            "video": cfg.auto_download_videos,
            "audio": cfg.auto_download_audios,
        }.get(media_type, False)
        if not flag:
            return False
        if cfg.copyright_confirm and not self.copyright_confirmed:
            logger.warning("版权未确认，跳过媒体自动下载")
            return False
        return True

    def _download_media(self, row: Any, media_type: str, stats: SubpageStats) -> None:
        if self.fetcher is None or row is None:
            return
        folder_kind = {"image": "images", "video": "videos", "audio": "audios"}[media_type]
        target_dir = media_dir(folder_kind, self.task.task_name, home=self.media_root)
        if media_type == "image":
            target = target_dir / image_crawler.filename_for_image(row["media_url"])
        else:
            fallback = "mp4" if media_type == "video" else "m4a"
            target = target_dir / filename_with_format(row["media_url"], row["format"], fallback)
        media_id = int(row["id"])
        headers = resolve_headers(
            self.task.hotlink,
            row["media_url"],
            row["source_url"],
            media_headers(row),
        )
        self.store.update_media(media_id, status="downloading")
        try:
            if media_type == "image":
                path, size = download_direct(
                    self.fetcher,
                    row["media_url"],
                    target_dir,
                    target.name,
                    headers=headers,
                    fallback=self.task.media_fallback,
                )
            elif media_type == "video":
                path, size = download_video(
                    self.fetcher,
                    row["media_url"],
                    target,
                    config=self.task.video,
                    store=self.store,
                    media_id=media_id,
                    ffmpeg_path=self.ffmpeg_path,
                    headers=headers,
                    source_kind=media_source_kind(row),
                    page_url=media_origin_url(row) or row["source_url"],
                )
            else:
                path, size = download_audio(
                    self.fetcher,
                    row["media_url"],
                    target,
                    config=self.task.audio,
                    store=self.store,
                    media_id=media_id,
                    ffmpeg_path=self.ffmpeg_path,
                    headers=headers,
                    source_kind=media_source_kind(row),
                    page_url=media_origin_url(row) or row["source_url"],
                )
        except MediaDownloadError as exc:
            self.store.update_media(media_id, status="failed", error=str(exc))
            self.store.add_failure(self.task_id, row["media_url"], str(exc), exc.failure_type, 0)
            stats.errors += 1
            return
        self.store.update_media(media_id, status="done", local_path=str(path), file_size=size)
        stats.downloaded += 1
