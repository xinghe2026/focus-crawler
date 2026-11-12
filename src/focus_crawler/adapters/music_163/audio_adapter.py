"""网易云音乐适配器：歌曲详情 + 外链音频地址。

商业音乐平台版权风险高，仅下载原始文件，不提供解密或签名逆向。
"""

from __future__ import annotations

import re

from ...core.audio_adapter import AdapterContext, AdapterError, request_json
from ...utils.logging import get_logger

logger = get_logger("adapter.music_163")

DETAIL_API = "https://music.163.com/api/song/detail/"
OUTER_URL = "https://music.163.com/song/media/outer/url?id={song_id}.mp3"
REFERER = "https://music.163.com/"

_SONG_ID_RE = re.compile(r"[?&#]id=(\d+)")
_SONG_PATH_RE = re.compile(r"/song/(\d+)")


def _parse_song_id(page_url: str) -> int | None:
    match = _SONG_ID_RE.search(page_url or "")
    if match:
        return int(match.group(1))
    match = _SONG_PATH_RE.search(page_url or "")
    if match:
        return int(match.group(1))
    return None


def _headers(ctx: AdapterContext) -> dict[str, str]:
    headers = {"Referer": REFERER}
    if ctx.user_agent:
        headers["User-Agent"] = ctx.user_agent
    return headers


def _ctx_with(ctx: AdapterContext, headers: dict[str, str]) -> AdapterContext:
    return AdapterContext(fetcher=ctx.fetcher, headers=headers, user_agent=ctx.user_agent)


def _artist_names(song: dict) -> str:
    artists = song.get("artists") or song.get("ar") or []
    names = [str(item.get("name")) for item in artists if isinstance(item, dict) and item.get("name")]
    return "/".join(names)


def resolve_audio(page_url: str, ctx: AdapterContext) -> list[dict]:
    song_id = _parse_song_id(page_url)
    if song_id is None:
        raise AdapterError("无法从网易云页面地址解析出歌曲 ID")
    headers = _headers(ctx)

    detail = request_json(_ctx_with(ctx, headers), DETAIL_API, {"id": song_id, "ids": f"[{song_id}]"})
    code = detail.get("code")
    if code not in (200, None):
        message = detail.get("message") or f"code={code}"
        raise AdapterError(f"网易云歌曲详情接口返回错误：{message}")

    songs = detail.get("songs") or []
    if not songs or not isinstance(songs[0], dict):
        raise AdapterError("网易云歌曲详情为空，可能需要登录或提供 Cookie")
    song = songs[0]

    name = str(song.get("name") or "").strip() or f"song_{song_id}"
    artists = _artist_names(song)
    title = f"{artists} - {name}" if artists else name
    duration_ms = song.get("duration") or song.get("dt")
    duration = round(float(duration_ms) / 1000, 3) if isinstance(duration_ms, (int, float)) else None

    logger.info("网易云音频解析完成：%s（%s）", page_url, title)
    return [
        {
            "title": title,
            "audio_url": OUTER_URL.format(song_id=song_id),
            "format": "mp3",
            "bitrate": None,
            "duration": duration,
            "headers": dict(headers),
        }
    ]
