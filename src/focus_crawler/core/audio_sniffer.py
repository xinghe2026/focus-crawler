"""音频嗅探（当前阶段：静态 HTML 提取；动态 Playwright 嗅探未启用）。"""

from __future__ import annotations

from ..config.schema import AudioConfig
from .media_common import MediaCandidate, static_audio_candidates


def sniff_static(html: str, page_url: str, cfg: AudioConfig) -> list[MediaCandidate]:
    if not cfg.enabled:
        return []
    return static_audio_candidates(html, page_url, cfg)
