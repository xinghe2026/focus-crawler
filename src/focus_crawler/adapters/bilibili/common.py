"""B 站接口公共能力：视频编号解析、请求头与视频详情。"""

from __future__ import annotations

import re

from ...core.audio_adapter import AdapterContext, AdapterError, request_json
from ...utils.logging import get_logger

logger = get_logger("adapter.bilibili")

VIEW_API = "https://api.bilibili.com/x/web-interface/view"
PLAYURL_API = "https://api.bilibili.com/x/player/playurl"
REFERER = "https://www.bilibili.com/"

_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
_AV_RE = re.compile(r"(?:^|[/?&=])av(\d+)", re.I)


def parse_video_id(text: str) -> tuple[str | None, int | None]:
    text = text or ""
    match = _BV_RE.search(text)
    if match:
        return match.group(1), None
    match = _AV_RE.search(text)
    if match:
        return None, int(match.group(1))
    return None, None


def headers_for(ctx: AdapterContext) -> dict[str, str]:
    headers = {"Referer": REFERER}
    if ctx.user_agent:
        headers["User-Agent"] = ctx.user_agent
    return headers


def ctx_with(ctx: AdapterContext, headers: dict[str, str]) -> AdapterContext:
    return AdapterContext(fetcher=ctx.fetcher, headers=headers, user_agent=ctx.user_agent)


def check_code(payload: dict, action: str) -> None:
    code = payload.get("code")
    if code not in (0, None):
        message = payload.get("message") or f"code={code}"
        raise AdapterError(f"B 站{action}接口返回错误：{message}")


def resolve_video_id(
    page_url: str, ctx: AdapterContext, headers: dict[str, str]
) -> tuple[str | None, int | None]:
    bvid, aid = parse_video_id(page_url)
    if bvid or aid:
        return bvid, aid
    result = ctx.fetcher.fetch(page_url, headers=headers or None)
    if not result.ok:
        raise AdapterError(f"B 站页面解析失败：HTTP {result.status_code} {result.error or ''}".strip())
    bvid, aid = parse_video_id(result.url)
    if not bvid and not aid:
        bvid, aid = parse_video_id(result.text)
    if not bvid and not aid:
        raise AdapterError("无法从 B 站页面解析出视频编号（BV/av）")
    return bvid, aid


def fetch_view(
    ctx: AdapterContext, headers: dict[str, str], bvid: str | None, aid: int | None
) -> dict:
    view_params: dict = {"bvid": bvid} if bvid else {"aid": aid}
    payload = request_json(ctx_with(ctx, headers), VIEW_API, view_params)
    check_code(payload, "视频详情")
    info = payload.get("data") or {}
    if not info.get("cid"):
        raise AdapterError("B 站视频详情缺少 cid")
    return info
