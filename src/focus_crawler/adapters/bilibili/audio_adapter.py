"""B 站音频轨道适配器：view 取视频信息，playurl 解析 dash.audio。

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

logger = get_logger("adapter.bilibili")


def resolve_audio(page_url: str, ctx: AdapterContext) -> list[dict]:
    headers = headers_for(ctx)
    bvid, aid = resolve_video_id(page_url, ctx, headers)
    info = fetch_view(ctx, headers, bvid, aid)
    cid = info.get("cid")
    title = str(info.get("title") or "").strip() or (bvid or f"av{aid}")
    duration = info.get("duration")

    play_params: dict = {"cid": cid, "fnval": 16, "fnver": 0, "fourk": 1}
    if bvid:
        play_params["bvid"] = bvid
    else:
        play_params["avid"] = aid
    play = request_json(ctx_with(ctx, headers), PLAYURL_API, play_params)
    check_code(play, "播放地址")

    dash = (play.get("data") or {}).get("dash") or {}
    tracks = dash.get("audio") or []
    if not tracks:
        raise AdapterError("playurl 未返回 dash.audio（可能需要登录或该视频无独立音频轨道）")

    items: list[dict] = []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        audio_url = track.get("baseUrl") or track.get("base_url") or track.get("baseURL")
        if not audio_url:
            continue
        bandwidth = track.get("bandwidth")
        bitrate = int(bandwidth) if isinstance(bandwidth, (int, float)) else None
        label = f"{bitrate // 1000} kbps" if bitrate else ""
        items.append(
            {
                "title": f"{title}（音频 {label}）" if label else title,
                "audio_url": str(audio_url),
                "format": "m4a",
                "bitrate": bitrate,
                "duration": duration,
                "headers": dict(headers),
            }
        )
    if not items:
        raise AdapterError("dash.audio 未提供可用的音频地址")
    items.sort(key=lambda item: item.get("bitrate") or 0, reverse=True)
    logger.info("B 站音频轨道解析完成：%s，共 %d 条", page_url, len(items))
    return items
