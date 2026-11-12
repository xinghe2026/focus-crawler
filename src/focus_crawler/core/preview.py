"""运行前预览：字段命中率、链接发现、子页媒体统计（不写数据库）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config.schema import TaskConfig
from ..utils.text import shorten
from . import image_crawler, link_discovery
from .audio_sniffer import sniff_static as sniff_audio
from .dynamic_sniffer import DynamicPage, DynamicSniffError
from .media_common import dynamic_media_candidates
from .parser import ParsedPage, parse_detail, summarize_hits
from .video_sniffer import sniff_static as sniff_video
from .ytdlp_engine import maybe_resolve as ytdlp_resolve


@dataclass
class SubpagePreview:
    url: str
    status_code: int | None
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    hits: dict[str, bool] = field(default_factory=dict)
    images: int = 0
    videos: int = 0
    audios: int = 0
    image_example: list[str] = field(default_factory=list)
    video_example: list[str] = field(default_factory=list)
    audio_example: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "status_code": self.status_code,
            "ok": self.ok,
            "data": self.data,
            "hits": self.hits,
            "images": self.images,
            "videos": self.videos,
            "audios": self.audios,
            "image_example": self.image_example,
            "video_example": self.video_example,
            "audio_example": self.audio_example,
        }


@dataclass
class PreviewResult:
    entry_url: str
    ok: bool = True
    error: str | None = None
    discovered_links: int = 0
    link_examples: list[str] = field(default_factory=list)
    filtered_count: int = 0
    duplicate_count: int = 0
    truncated: bool = False
    fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    subpages: list[SubpagePreview] = field(default_factory=list)
    media_total: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_url": self.entry_url,
            "ok": self.ok,
            "error": self.error,
            "discovered_links": self.discovered_links,
            "link_examples": self.link_examples,
            "filtered_count": self.filtered_count,
            "duplicate_count": self.duplicate_count,
            "truncated": self.truncated,
            "fields": self.fields,
            "subpages": [item.as_dict() for item in self.subpages],
            "media_total": self.media_total,
        }


def preview_task(
    task: TaskConfig,
    fetcher: Any,
    subpage_limit: int | None = None,
    dynamic_sniffer: Any = None,
) -> PreviewResult:
    entry = task.start_urls[0]
    result = PreviewResult(entry_url=entry)
    use_dynamic = dynamic_sniffer is not None and task.subpage_collect.render_for_media

    if use_dynamic:
        try:
            entry_page = dynamic_sniffer(entry, task)
        except DynamicSniffError as exc:
            result.ok = False
            result.error = str(exc)
            return result
        html = entry_page.html
    else:
        fetched = fetcher.fetch(entry)
        if not fetched.ok:
            result.ok = False
            result.error = fetched.error or f"HTTP {fetched.status_code}"
            return result
        html = fetched.text or ""

    discovery = link_discovery.discover(html, entry, task)
    result.discovered_links = len(discovery.links)
    result.link_examples = [shorten(link, 160) for link in discovery.links[:10]]
    result.filtered_count = discovery.filtered_count
    result.duplicate_count = discovery.duplicate_count
    result.truncated = discovery.truncated

    limit = subpage_limit or task.subpage_collect.preview_limit
    links = discovery.links[:limit]
    if task.crawl.max_depth == 0:
        links = [entry] + links
        links = links[:limit]
    parsed_pages: list[ParsedPage] = []
    totals = {"image": 0, "video": 0, "audio": 0}

    for link in links:
        sub = SubpagePreview(url=link, status_code=None, ok=False)
        dynamic: DynamicPage | None = None
        if use_dynamic:
            try:
                dynamic = dynamic_sniffer(link, task)
            except DynamicSniffError:
                result.subpages.append(sub)
                continue
            sub.status_code = dynamic.status_code
            sub.ok = True
            html = dynamic.html
        else:
            fetched_sub = fetcher.fetch(link)
            sub.status_code = fetched_sub.status_code
            sub.ok = fetched_sub.ok
            if not fetched_sub.ok:
                result.subpages.append(sub)
                continue
            html = fetched_sub.text

        if task.detail.fields:
            parsed = parse_detail(html, link, task.detail)
            sub.data = parsed.data
            sub.hits = parsed.hits
            parsed_pages.append(parsed)
        images = image_crawler.scan_page(html, link, task.image) if task.image.enabled else []
        videos = sniff_video(html, link, task.video) if task.video.enabled else []
        audios = sniff_audio(html, link, task.audio) if task.audio.enabled else []
        if dynamic is not None:
            if task.video.enabled:
                videos.extend(dynamic_media_candidates(dynamic.video_urls, "video", dynamic.headers))
            if task.audio.enabled:
                audios.extend(dynamic_media_candidates(dynamic.audio_urls, "audio", dynamic.headers))
        # 与运行时一致：静态/动态嗅探都没结果时，预览也给出 yt-dlp 的结果
        page_headers = dict((dynamic.headers if dynamic is not None else fetched_sub.headers) or {})
        if task.video.enabled:
            videos.extend(
                ytdlp_resolve(
                    link,
                    task.video.adapter.ytdlp,
                    kind="video",
                    headers=page_headers,
                    existing_count=len(videos),
                )
            )
        if task.audio.enabled:
            audios.extend(
                ytdlp_resolve(
                    link,
                    task.audio.adapter.ytdlp,
                    kind="audio",
                    headers=page_headers,
                    existing_count=len(audios),
                )
            )
        sub.images, sub.videos, sub.audios = len(images), len(videos), len(audios)
        sub.image_example = [item.url for item in images[:5]]
        sub.video_example = [item.url for item in videos[:5]]
        sub.audio_example = [item.url for item in audios[:5]]
        totals["image"] += sub.images
        totals["video"] += sub.videos
        totals["audio"] += sub.audios
        result.subpages.append(sub)

    result.fields = summarize_hits(parsed_pages)
    result.media_total = totals
    return result
