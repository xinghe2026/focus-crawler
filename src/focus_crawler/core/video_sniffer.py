"""视频嗅探（当前阶段：静态 HTML 提取；动态 Playwright 嗅探未启用）。"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from ..config.schema import VideoConfig
from .media_common import MediaCandidate, static_video_candidates

_EXTINF = re.compile(r"#EXTINF:([0-9.]+)")
_VARIANT = re.compile(r"#EXT-X-STREAM-INF:.*?(?:BANDWIDTH=(\d+))?.*\$?\n([^#\n]+)")


def sniff_static(html: str, page_url: str, cfg: VideoConfig) -> list[MediaCandidate]:
    if not cfg.enabled:
        return []
    return static_video_candidates(html, page_url, cfg)


def parse_m3u8(text: str, base_url: str) -> dict:
    """极简 m3u8 解析：区分主列表 / 子列表，返回变体与分片。"""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    variants: list[dict] = []
    segments: list[dict] = []
    duration: float | None = None

    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("#EXT-X-STREAM-INF"):
            bandwidth_match = re.search(r"BANDWIDTH=(\d+)", line)
            if index + 1 < len(lines):
                variants.append(
                    {
                        "url": urljoin(base_url, lines[index + 1]),
                        "bandwidth": int(bandwidth_match.group(1)) if bandwidth_match else None,
                    }
                )
                index += 2
                continue
            index += 1
            continue
        if line.startswith("#EXTINF"):
            match = _EXTINF.search(line)
            duration = float(match.group(1)) if match else None
            index += 1
            continue
        if line.startswith("#"):
            index += 1
            continue
        segments.append({"url": urljoin(base_url, line), "duration": duration})
        duration = None
        index += 1

    return {
        "is_master": bool(variants),
        "variants": variants,
        "segments": segments,
    }


def pick_variant(variants: list[dict], preferred: str = "highest") -> dict | None:
    if not variants:
        return None
    ordered = sorted(variants, key=lambda item: item.get("bandwidth") or 0)
    return ordered[0] if preferred == "lowest" else ordered[-1]
