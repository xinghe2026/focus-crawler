"""视频平台适配器调度：按域名选择适配器并校验返回值。"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from ..config.schema import VideoConfig
from ..utils.logging import get_logger
from .audio_adapter import AdapterContext, AdapterError
from .media_common import MediaCandidate, guess_format
from .ytdlp_engine import maybe_resolve as ytdlp_resolve

logger = get_logger("video_adapter")

ADAPTER_HOSTS = {
    "bilibili": ("bilibili.com", "b23.tv"),
}


def _host_matches(page_url: str, domains: tuple[str, ...]) -> bool:
    host = (urlsplit(page_url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def adapter_for(page_url: str, cfg: VideoConfig) -> str | None:
    if cfg.adapter.bilibili and _host_matches(page_url, ADAPTER_HOSTS["bilibili"]):
        return "bilibili"
    return None


def _load_adapter(name: str):
    if name == "bilibili":
        from ..adapters.bilibili import video_adapter

        return video_adapter
    return None


def _validate_results(results: Any, name: str) -> list[MediaCandidate]:
    if not isinstance(results, list):
        raise AdapterError("视频适配器返回值必须是列表")

    candidates: list[MediaCandidate] = []
    for item in results:
        if not isinstance(item, dict):
            raise AdapterError("视频适配器返回项必须是字典")
        video_url = str(item.get("video_url") or "").strip()
        if not video_url:
            raise AdapterError("视频适配器返回项缺少 video_url")
        fmt = str(item.get("format") or "").strip().lower() or guess_format(video_url) or "mp4"
        headers = item.get("headers") or {}
        if not isinstance(headers, dict):
            raise AdapterError("视频适配器 headers 必须是字典")
        headers = {str(key): str(value) for key, value in headers.items()}
        duration = item.get("duration")
        bitrate = item.get("bitrate")
        width = item.get("width")
        height = item.get("height")
        candidates.append(
            MediaCandidate(
                url=video_url,
                kind="video",
                format=fmt,
                width=int(width) if isinstance(width, (int, float)) else None,
                height=int(height) if isinstance(height, (int, float)) else None,
                quality=str(item.get("quality") or "").strip() or None,
                source_kind=f"adapter:{name}",
                extra={
                    "duration": float(duration) if isinstance(duration, (int, float)) else None,
                    "bitrate": int(bitrate) if isinstance(bitrate, (int, float)) else None,
                    "headers": headers,
                },
            )
        )
    if candidates:
        logger.info("视频适配器 %s 解析到 %d 个视频", name, len(candidates))
    return candidates


def _run_platform(page_url: str, cfg: VideoConfig, ctx: AdapterContext):
    """调用域名对应的平台适配器；没有适配器返回 (None, [])。"""
    name = adapter_for(page_url, cfg)
    if name is None:
        return None, []
    module = _load_adapter(name)
    if module is None:
        return name, []
    try:
        results = module.resolve_video(page_url, ctx)
    except AdapterError:
        raise
    except Exception as exc:  # noqa: BLE001 - 适配器内部异常统一分类
        raise AdapterError(f"适配器执行异常：{exc}") from exc
    return name, _validate_results(results, name)


def resolve_video(page_url: str, cfg: VideoConfig, ctx: AdapterContext) -> list[MediaCandidate]:
    """按 ``adapter.bilibili_priority`` 决定 B 站用 yt-dlp 还是内置适配器。

    * ``ytdlp_first``（默认）：B 站先交给 yt-dlp（其 extractor 跟进站点改版更快），
      解析不出结果再回退内置适配器 —— 同一页面只会真正跑一条解析链路；
    * ``adapter_first``：内置适配器优先，yt-dlp 仅在没结果时补漏；
    * ``adapter_only``：B 站只用内置适配器，不调用 yt-dlp（完全避免重复实现）。
    """
    priority = getattr(cfg.adapter, "bilibili_priority", "ytdlp_first")
    headers = dict(ctx.headers or {})
    is_bilibili = adapter_for(page_url, cfg) == "bilibili"

    if is_bilibili and priority == "ytdlp_first":
        candidates = ytdlp_resolve(
            page_url, cfg.adapter.ytdlp, kind="video", headers=headers, force=True
        )
        if candidates:
            logger.info("B 站视频由 yt-dlp 解析（%d 条）", len(candidates))
            return candidates
        logger.info("yt-dlp 未解析出 B 站视频，回退内置适配器")
        _, fallback = _run_platform(page_url, cfg, ctx)
        return fallback

    name, candidates = _run_platform(page_url, cfg, ctx)
    if is_bilibili and priority == "adapter_only":
        return candidates

    existing = len(candidates) + max(0, int(getattr(ctx, "existing", 0) or 0))
    candidates.extend(
        ytdlp_resolve(
            page_url,
            cfg.adapter.ytdlp,
            kind="video",
            headers=headers,
            existing_count=existing,
        )
    )
    return candidates
