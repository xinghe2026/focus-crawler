"""平台适配器注册表：统一域名匹配、模块加载与返回值校验。

audio_adapter / video_adapter 原先各自复制了一份 `adapter_for`、`_load_adapter`、
`_validate_results`，这里收敛成单一实现，新增平台只需改本文件。
"""

from __future__ import annotations

from typing import Any

from ..utils.urls import host_matches as _host_matches_pattern, host_of
from .media_common import MediaCandidate, guess_format

#: 平台 -> 域名后缀
ADAPTER_HOSTS: dict[str, tuple[str, ...]] = {
    "bilibili": ("bilibili.com", "b23.tv"),
    "music_163": ("music.163.com", "y.music.163.com"),
}


class AdapterError(RuntimeError):
    """适配器请求或返回值校验失败。"""


def host_matches(page_url: str, domains: tuple[str, ...]) -> bool:
    host = host_of(page_url)
    return bool(host) and any(_host_matches_pattern(host, domain) for domain in domains)


def adapter_for(page_url: str, cfg: Any) -> str | None:
    """按域名 + 配置开关选择平台适配器（cfg 为 VideoAdapterConfig / AudioAdapterConfig）。"""
    for name, domains in ADAPTER_HOSTS.items():
        if getattr(cfg, name, False) and host_matches(page_url, domains):
            return name
    return None


def load_adapter(name: str, kind: str):
    """按平台名与媒体类型加载适配器模块（audio / video）。"""
    if kind == "audio":
        if name == "bilibili":
            from ..adapters.bilibili import audio_adapter

            return audio_adapter
        if name == "music_163":
            from ..adapters.music_163 import audio_adapter

            return audio_adapter
        return None
    if kind == "video" and name == "bilibili":
        from ..adapters.bilibili import video_adapter

        return video_adapter
    return None


def build_candidates(
    results: Any,
    name: str,
    *,
    url_field: str,
    kind: str,
    default_format: str,
    quality_field: str = "title",
) -> list[MediaCandidate]:
    """校验适配器返回值并转成 MediaCandidate（audio / video 共用）。"""
    if not isinstance(results, list):
        raise AdapterError("适配器返回值必须是列表")

    candidates: list[MediaCandidate] = []
    for item in results:
        if not isinstance(item, dict):
            raise AdapterError("适配器返回项必须是字典")
        media_url = str(item.get(url_field) or "").strip()
        if not media_url:
            raise AdapterError(f"适配器返回项缺少 {url_field}")
        fmt = str(item.get("format") or "").strip().lower() or guess_format(media_url) or default_format
        headers = item.get("headers") or {}
        if not isinstance(headers, dict):
            raise AdapterError("适配器 headers 必须是字典")
        headers = {str(key): str(value) for key, value in headers.items()}
        duration = item.get("duration")
        bitrate = item.get("bitrate")
        width = item.get("width")
        height = item.get("height")
        candidates.append(
            MediaCandidate(
                url=media_url,
                kind=kind,
                format=fmt,
                width=int(width) if isinstance(width, (int, float)) else None,
                height=int(height) if isinstance(height, (int, float)) else None,
                quality=str(
                    item.get(quality_field) or item.get("title") or item.get("quality") or ""
                ).strip()
                or None,
                source_kind=f"adapter:{name}",
                extra={
                    "duration": float(duration) if isinstance(duration, (int, float)) else None,
                    "bitrate": int(bitrate) if isinstance(bitrate, (int, float)) else None,
                    "headers": headers,
                },
            )
        )
    return candidates


__all__ = ["ADAPTER_HOSTS", "AdapterError", "adapter_for", "build_candidates", "host_matches", "load_adapter"]
