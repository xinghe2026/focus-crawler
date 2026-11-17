"""视频平台适配器调度：按域名选择适配器并校验返回值。"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from ..config.schema import VideoConfig
from ..utils.logging import get_logger
from .adapter_registry import AdapterError, adapter_for as _adapter_for, build_candidates
from .adapter_registry import load_adapter as _load_adapter_module
from .audio_adapter import AdapterContext
from .media_common import MediaCandidate, guess_format
from .ytdlp_engine import maybe_resolve as ytdlp_resolve

logger = get_logger("video_adapter")

def adapter_for(page_url: str, cfg: VideoConfig) -> str | None:
    """兼容入口：统一走 adapter_registry。"""
    return _adapter_for(page_url, cfg.adapter)


def _load_adapter(name: str):
    return _load_adapter_module(name, "video")


def _validate_results(results: Any, name: str) -> list[MediaCandidate]:
    """视频候选校验（统一实现见 adapter_registry）。"""
    return build_candidates(
        results, name, url_field="video_url", kind="video", default_format="mp4", quality_field="quality"
    )


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
