"""媒体通用能力：URL 归一化、格式猜测、静态提取、直链下载。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

from parsel import Selector

from ..config.schema import AudioConfig, ImageConfig, MediaFallbackConfig, VideoConfig
from ..utils.hash import media_hash, normalize_url
from ..utils.logging import get_logger
from .errors import MediaDownloadError
from .host_fallback import fallback_urls, is_dns_error, preferred_url
from .segmented_downloader import download_file

IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "avif", "svg"}

logger = get_logger("media_common")
VIDEO_EXTS = {"mp4", "flv", "m3u8", "ts", "webm", "mkv", "mpd", "mov", "avi"}
AUDIO_EXTS = {"mp3", "m4a", "aac", "flac", "wav", "ogg", "opus", "oga", "wma"}

_MIME_TO_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "application/vnd.apple.mpegurl": "m3u8",
    "application/x-mpegurl": "m3u8",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/aac": "aac",
    "audio/flac": "flac",
    "audio/wav": "wav",
    "audio/ogg": "ogg",
    "audio/opus": "opus",
}


@dataclass
class MediaCandidate:
    url: str
    kind: str
    format: str | None = None
    width: int | None = None
    height: int | None = None
    quality: str | None = None
    source_kind: str = ""
    extra: dict = field(default_factory=dict)


def absolute_url(base_url: str, href: str) -> str | None:
    href = (href or "").strip()
    if not href or href.startswith(("data:", "javascript:", "#")):
        return None
    absolute = normalize_url(urljoin(base_url, href))
    if not absolute.lower().startswith(("http://", "https://")):
        return None
    return absolute


def guess_format(url: str, content_type: str | None = None) -> str | None:
    path = urlsplit(url).path.lower()
    ext = path.rsplit(".", 1)[-1] if "." in path else ""
    if ext in IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS:
        return ext
    if content_type:
        mime = content_type.split(";", 1)[0].strip().lower()
        if mime in _MIME_TO_EXT:
            return _MIME_TO_EXT[mime]
    return None


def _keyword_match(url: str, include: list[str], exclude: list[str]) -> bool:
    lowered = url.lower()
    if include and not any(word.lower() in lowered for word in include if word):
        return False
    if exclude and any(word.lower() in lowered for word in exclude if word):
        return False
    return True


def _tag_attribute_values(html: str, tags: list[str], attributes: list[str]) -> list[str]:
    root = Selector(text=html or "")
    values: list[str] = []
    for tag in tags:
        for node in root.css(tag):
            for attribute in attributes:
                value = node.attrib.get(attribute)
                if value:
                    values.append(value)
    return values


def _direct_links(html: str, extensions: set[str]) -> list[str]:
    pattern = re.compile(
        r"""https?://[^\s"'<>()\\]+?\.(?:""" + "|".join(sorted(extensions)) + r""")(?:\?[^\s"'<>()\\]*)?""",
        re.I,
    )
    return pattern.findall(html or "")


def _meta_content(html: str, key: str) -> list[str]:
    root = Selector(text=html or "")
    values = root.css(f'meta[property="{key}"]::attr(content)').getall()
    values.extend(root.css(f'meta[name="{key}"]::attr(content)').getall())
    return [value for value in values if value]


def _dedupe(candidates: list[MediaCandidate]) -> list[MediaCandidate]:
    seen: set[str] = set()
    unique: list[MediaCandidate] = []
    for candidate in candidates:
        key = media_hash(candidate.url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def static_video_candidates(html: str, page_url: str, cfg: VideoConfig) -> list[MediaCandidate]:
    raw: list[tuple[str, str]] = []
    for value in _tag_attribute_values(html, ["video", "source"], ["src"]):
        raw.append((value, "video_tag"))
    for value in _meta_content(html, "og:video"):
        raw.append((value, "og_video"))
    for value in _direct_links(html, VIDEO_EXTS):
        raw.append((value, "direct_link"))

    candidates: list[MediaCandidate] = []
    for href, kind in raw:
        absolute = absolute_url(page_url, href)
        if not absolute:
            continue
        fmt = guess_format(absolute)
        if fmt is None:
            continue
        if cfg.sniff_types and fmt not in cfg.sniff_types:
            continue
        candidates.append(MediaCandidate(url=absolute, kind="video", format=fmt, source_kind=kind))
    return _dedupe(candidates)[: cfg.max_count]


def static_audio_candidates(html: str, page_url: str, cfg: AudioConfig) -> list[MediaCandidate]:
    raw: list[tuple[str, str]] = []
    for value in _tag_attribute_values(html, ["audio", "source"], ["src"]):
        raw.append((value, "audio_tag"))
    for value in _meta_content(html, "og:audio"):
        raw.append((value, "og_audio"))
    for value in _direct_links(html, AUDIO_EXTS):
        raw.append((value, "direct_link"))

    candidates: list[MediaCandidate] = []
    for href, kind in raw:
        absolute = absolute_url(page_url, href)
        if not absolute:
            continue
        fmt = guess_format(absolute)
        if fmt is None:
            continue
        sniff = set(cfg.sniff_types)
        if sniff and fmt not in sniff and not (fmt == "m3u8" and "hls_audio" in sniff):
            continue
        if not _keyword_match(absolute, cfg.include_keywords, cfg.exclude_keywords):
            continue
        candidates.append(MediaCandidate(url=absolute, kind="audio", format=fmt, source_kind=kind))
    return _dedupe(candidates)[: cfg.max_count]


def sanitize_filename(name: str, default: str = "file") -> str:
    name = unquote(name).strip()
    name = re.sub(r"[\\/:*?\"<>|\s]+", "_", name)
    name = name.strip("._")
    return name[:120] or default


def filename_for_url(url: str, fmt: str | None) -> str:
    path = urlsplit(url).path
    base = path.rsplit("/", 1)[-1]
    if base:
        return sanitize_filename(base)
    suffix = f".{fmt}" if fmt else ""
    return f"{media_hash(url)[:12]}{suffix}"


def filename_with_format(url: str, fmt: str | None, fallback: str) -> str:
    """按链接生成文件名，缺少扩展名时补上格式或默认后缀。"""
    name = filename_for_url(url, fmt)
    if not Path(name).suffix:
        name = f"{name}.{fmt or fallback}"
    return name


def media_headers(row: Any) -> dict[str, str] | None:
    """读取媒体记录里的 headers JSON，供适配器下载时携带 Referer / UA。"""
    try:
        raw = row["headers"]
    except (IndexError, KeyError, TypeError):
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return {str(key): str(value) for key, value in payload.items()}


def media_source_kind(row: Any) -> str | None:
    """读取媒体记录里的来源标记（静态嗅探 / 适配器 / yt-dlp），老库可能没有该列。"""
    try:
        value = row["source_kind"]
    except (IndexError, KeyError, TypeError):
        return None
    return str(value) if value else None


def media_origin_url(row: Any) -> str | None:
    """读取媒体记录里"下载前重新解析"的页面地址（yt-dlp 记录才有）。"""
    try:
        value = row["origin_url"]
    except (IndexError, KeyError, TypeError):
        return None
    return str(value) if value else None


def _direct_attempt_urls(fallback: MediaFallbackConfig | None, url: str) -> list[str]:
    """按顺序给出直链下载要尝试的 URL：能解析时先用原地址，解析失败则先用镜像。"""
    if fallback is None:
        return [url]
    attempts = [preferred_url(fallback, url)]
    for candidate in [url, *fallback_urls(fallback, url)]:
        if candidate not in attempts:
            attempts.append(candidate)
    return attempts


def download_direct(
    fetcher,
    url: str,
    dest_dir: str | Path,
    filename: str | None = None,
    headers: dict[str, str] | None = None,
    fallback: MediaFallbackConfig | None = None,
    *,
    connections: int = 4,
    rate_limit: int = 0,
    resume: bool = True,
    segmented: bool = True,
    should_stop: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int | None, float], None] | None = None,
) -> tuple[Path, int]:
    """直链下载，返回 (本地路径, 字节数)。

    * 服务器支持 Range 时走分段并发下载（动态拆分 + 断点续传）；
    * 不支持时回退单连接流式下载（不再整包读进内存）；
    * ``fallback`` 启用时：域名解析失败会切换配置中的镜像主机重试，
      其它错误（403 / 404 / 超时等）不换主机，直接抛出。
    """
    folder = Path(dest_dir)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (filename or filename_for_url(url, guess_format(url)))
    client = getattr(fetcher, "client", None)
    if client is None:
        # 兼容只实现 fetch() 的注入式 fetcher（测试 / 自定义传输）
        return _fetch_once(fetcher, url, target, headers, fallback)

    # 关键：合并 Fetcher 的默认请求头（UA / Accept-Language）。
    # 直连 CDN 时缺 UA 会被大量站点判为爬虫直接 403。
    merged_headers: dict[str, str] = dict(getattr(fetcher, "headers", None) or {})
    merged_headers.update({str(k): str(v) for k, v in (headers or {}).items() if v})

    last_error: MediaDownloadError | None = None
    for attempt_url in _direct_attempt_urls(fallback, url):
        try:
            report = download_file(
                client,
                attempt_url,
                target,
                headers=merged_headers,
                connections=connections if segmented else 1,
                rate_limit=rate_limit,
                resume=resume,
                should_stop=should_stop,
                on_progress=on_progress,
            )
            return report.path, report.size
        except MediaDownloadError as exc:
            last_error = exc
            logger.debug("直链下载失败 %s：%s", attempt_url, exc)
            if exc.failure_type == "http_403":
                # 有些 CDN 对 Range / 分段请求返回 403，但允许普通整包 GET，再试一次
                try:
                    report = download_file(
                        client,
                        attempt_url,
                        target,
                        headers=merged_headers,
                        connections=1,
                        segmented=False,
                        resume=False,
                        should_stop=should_stop,
                        on_progress=on_progress,
                    )
                    return report.path, report.size
                except MediaDownloadError as retry_error:
                    last_error = retry_error
            # 仅域名解析失败才换镜像主机，其余错误不重试
            if exc.failure_type != "connection_error" or not is_dns_message(str(exc)):
                raise
    if last_error is not None:
        raise last_error
    raise MediaDownloadError("下载失败", "media_download_error")


def is_dns_message(text: str) -> bool:
    lowered = (text or "").lower()
    return any(
        hint in lowered
        for hint in ("getaddrinfo", "11001", "name or service not known", "nodename nor servname", "dns")
    )


def _fetch_once(fetcher, url: str, target: Path, headers: dict[str, str] | None, fallback) -> tuple[Path, int]:
    """旧式一次性下载（fetcher 没有 .client 时使用）。"""
    result = None
    for attempt_url in _direct_attempt_urls(fallback, url):
        result = fetcher.fetch(attempt_url, headers=headers or None, want_bytes=True)
        if result.ok and result.content is not None:
            break
        if not is_dns_error(result):
            break
    if result is None:
        raise MediaDownloadError("下载失败", "media_download_error")
    if not result.ok or result.content is None:
        raise MediaDownloadError(result.error or "下载失败", result.failure_type or "media_download_error")
    target.write_bytes(result.content)
    return target, len(result.content)


def image_candidates_from_config(cfg: ImageConfig) -> tuple[list[str], list[str]]:
    return cfg.include_keywords, cfg.exclude_keywords


def dynamic_media_candidates(
    urls: list[str],
    kind: str,
    headers: dict[str, str] | None = None,
) -> list[MediaCandidate]:
    """把 Playwright 嗅探到的 URL 转成媒体候选。"""
    allowed = VIDEO_EXTS if kind == "video" else AUDIO_EXTS
    candidates: list[MediaCandidate] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        fmt = guess_format(url)
        if fmt not in allowed:
            continue
        extra = {"headers": dict(headers)} if headers else {}
        candidates.append(
            MediaCandidate(url=url, kind=kind, format=fmt, source_kind="dynamic_sniff", extra=extra)
        )
    return candidates
