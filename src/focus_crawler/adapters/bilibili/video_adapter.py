"""B 站视频适配器：playurl durl 返回的完整 MP4 文件。

仅个人学习研究使用，请尊重 UP 主版权。
"""

from __future__ import annotations

from ...core.audio_adapter import AdapterContext, AdapterError, request_json
from ...utils.logging import get_logger
from .common import (
    PLAYURL_API,
    check_code,
    ctx_with,
    fetch_view,
    headers_for,
    resolve_video_id,
)

logger = get_logger("adapter.bilibili.video")


def _quality_label(data: dict) -> str:
    quality = data.get("quality")
    qualities = data.get("accept_quality") or []
    descriptions = data.get("accept_description") or []
    if quality in qualities:
        index = qualities.index(quality)
        if index < len(descriptions):
            return str(descriptions[index] or "")
    return f"qn{quality}" if quality else ""


def resolve_video(page_url: str, ctx: AdapterContext) -> list[dict]:
    headers = headers_for(ctx)
    bvid, aid = resolve_video_id(page_url, ctx, headers)
    info = fetch_view(ctx, headers, bvid, aid)
    cid = info.get("cid")
    title = str(info.get("title") or "").strip() or (bvid or f"av{aid}")
    duration = info.get("duration")

    params: dict = {"cid": cid, "fnval": 1, "fnver": 0}
    if bvid:
        params["bvid"] = bvid
    else:
        params["avid"] = aid
    play = request_json(ctx_with(ctx, headers), PLAYURL_API, params)
    check_code(play, "播放地址")

    data = play.get("data") or {}
    parts = data.get("durl") or []
    if not parts:
        raise AdapterError("playurl 未返回完整 MP4（durl），该视频可能需要登录或仅提供 DASH 流")

    label = _quality_label(data)
    items: list[dict] = []
    for index, part in enumerate(parts):
        if not isinstance(part, dict):
            continue
        url = part.get("url")
        if not url:
            continue
        length_ms = part.get("length")
        part_duration = (
            round(float(length_ms) / 1000, 3)
            if isinstance(length_ms, (int, float)) and len(parts) > 1
            else duration
        )
        suffix = f"（P{index + 1}）" if len(parts) > 1 else ""
        quality = f"{label} {suffix}".strip() if label else suffix
        items.append(
            {
                "title": f"{title} {suffix}".strip(),
                "video_url": str(url),
                "format": "mp4",
                "width": None,
                "height": None,
                "bitrate": None,
                "duration": part_duration,
                "quality": quality or None,
                "headers": dict(headers),
            }
        )
    if not items:
        raise AdapterError("durl 未提供可用的视频地址")
    logger.info("B 站视频解析完成：%s，共 %d 个文件", page_url, len(items))
    return items
