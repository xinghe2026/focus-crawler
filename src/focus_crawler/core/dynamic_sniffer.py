"""Playwright 动态媒体嗅探：加载子页并监听网络响应，收集视频/音频地址。"""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass, field
from typing import Any

from ..config.schema import TaskConfig
from ..utils.logging import get_logger
from .media_common import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS, guess_format

logger = get_logger("dynamic_sniffer")

# 页面加载后额外等待，给动态请求留出触发时间
SETTLE_MS = 1500
_AUDIO_HINT = re.compile(r"(audio|music|sound|song|voice|mp3|aac|flac)", re.I)
_SKIP_RESOURCE_TYPES = {"image", "font", "stylesheet", "script"}
_SKIP_FORMATS = {"ts", "m4s", "jpg", "jpeg", "png", "gif", "webp", "bmp", "avif", "svg", "css", "js"}


class DynamicSniffError(RuntimeError):
    def __init__(self, message: str, failure_type: str = "playwright_error"):
        super().__init__(message)
        self.failure_type = failure_type


@dataclass
class DynamicPage:
    html: str
    status_code: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    video_urls: list[str] = field(default_factory=list)
    audio_urls: list[str] = field(default_factory=list)


def playwright_available() -> bool:
    return importlib.util.find_spec("playwright") is not None


def classify_media_url(
    url: str, resource_type: str = "", content_type: str | None = None
) -> str | None:
    """按扩展名 / Content-Type 判断网络响应属于 video 还是 audio。"""
    if not url.lower().startswith(("http://", "https://")):
        return None
    if resource_type in _SKIP_RESOURCE_TYPES:
        return None
    fmt = guess_format(url, content_type)
    if fmt in _SKIP_FORMATS:
        return None
    if fmt in IMAGE_EXTS:
        return None
    if fmt in AUDIO_EXTS:
        return "audio"
    if fmt in VIDEO_EXTS:
        if fmt in ("m3u8", "mpd") and _AUDIO_HINT.search(url):
            return "audio"
        return "video"
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    return None


def _dedupe(urls: list[str]) -> list[str]:
    """去重（保持顺序）；复用 utils 层的通用实现。"""
    seen: set[str] = set()
    return [url for url in urls if not (url in seen or seen.add(url))]


def _page_headers(task: TaskConfig) -> dict[str, str]:
    headers: dict[str, str] = {}
    for source in (task.video.headers, task.audio.headers):
        if source:
            headers.update({str(key): str(value) for key, value in source.items()})
    return headers


def sniff_page(
    url: str,
    task: TaskConfig,
    timeout_ms: int | None = None,
    settle_ms: int = SETTLE_MS,
) -> DynamicPage:
    """用 Playwright 加载页面，返回 HTML 与监听到的视频/音频地址。"""
    if not playwright_available():
        raise DynamicSniffError(
            "未安装 playwright，无法进行动态媒体嗅探；请安装 playwright 后执行 playwright install chromium",
            "playwright_missing",
        )
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    found: dict[str, list[str]] = {"video": [], "audio": []}

    def on_response(response: Any) -> None:
        try:
            request = response.request
            kind = classify_media_url(
                response.url,
                getattr(request, "resource_type", ""),
                response.headers.get("content-type"),
            )
        except Exception:  # noqa: BLE001 - 单个响应解析失败不影响整体嗅探
            return
        if kind:
            found[kind].append(response.url)

    timeout = timeout_ms or task.crawl.timeout_ms
    status_code: int | None = None
    headers = _page_headers(task)
    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True)
            except PlaywrightError as exc:
                raise DynamicSniffError(f"启动 Playwright 浏览器失败：{exc}") from exc
            try:
                context = browser.new_context(extra_http_headers=headers or None)
                page = context.new_page()
                page.on("response", on_response)
                response = page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                if response is not None:
                    status_code = response.status
                if task.render.wait_selector:
                    page.wait_for_selector(task.render.wait_selector, timeout=timeout)
                if task.render.scroll:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(400)
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                if settle_ms > 0:
                    page.wait_for_timeout(settle_ms)
                html = page.content()
            finally:
                browser.close()
    except DynamicSniffError:
        raise
    except PlaywrightError as exc:
        raise DynamicSniffError(f"Playwright 加载页面失败：{exc}") from exc

    videos = _dedupe(found["video"])
    audios = _dedupe(found["audio"])
    logger.info("动态嗅探 %s：视频 %d，音频 %d", url, len(videos), len(audios))
    return DynamicPage(
        html=html,
        status_code=status_code,
        headers={"Referer": url} if url else {},
        video_urls=videos,
        audio_urls=audios,
    )
