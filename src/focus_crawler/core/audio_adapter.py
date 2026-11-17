"""音频适配器注册与统一调用：B 站 DASH 音频、网易云音乐等商业平台。"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from ..config.schema import AudioConfig
from ..utils.logging import get_logger
from .media_common import MediaCandidate, guess_format
from .ytdlp_engine import maybe_resolve as ytdlp_resolve

logger = get_logger("audio_adapter")

ADAPTER_HOSTS = {
    "bilibili": ("bilibili.com", "b23.tv"),
    "music_163": ("music.163.com", "y.music.163.com"),
}
_CUSTOM_ADAPTER_CACHE: dict[str, tuple[float, Any]] = {}


class AdapterError(RuntimeError):
    """适配器请求或返回值校验失败。"""


@dataclass
class AdapterContext:
    fetcher: Any
    headers: dict[str, str] = field(default_factory=dict)
    user_agent: str = ""
    base_dir: str | Path | None = None
    # 当前子页已经嗅到的同类媒体数量：yt-dlp 的 auto/force 模式据此判断是否需要补漏
    existing: int = 0


def request_json(ctx: AdapterContext, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if params:
        joiner = "&" if "?" in url else "?"
        url = f"{url}{joiner}{urlencode(params)}"
    headers = dict(ctx.headers)
    headers.setdefault("Accept", "application/json")
    if ctx.user_agent:
        headers.setdefault("User-Agent", ctx.user_agent)
    try:
        result = ctx.fetcher.fetch(url, headers=headers or None)
    except Exception as exc:  # noqa: BLE001 - 网络异常统一转适配器错误
        raise AdapterError(f"适配器请求异常：{exc}") from exc
    if not result.ok:
        raise AdapterError(f"适配器请求失败：HTTP {result.status_code} {result.error or ''}".strip())
    try:
        payload = json.loads(result.text or "")
    except ValueError as exc:
        raise AdapterError("适配器返回内容不是 JSON") from exc
    if not isinstance(payload, dict):
        raise AdapterError("适配器返回 JSON 结构异常")
    return payload


def adapter_for(page_url: str, cfg: AudioConfig) -> str | None:
    host = (urlsplit(page_url).hostname or "").lower()
    if cfg.adapter.bilibili and any(host == item or host.endswith("." + item) for item in ADAPTER_HOSTS["bilibili"]):
        return "bilibili"
    if cfg.adapter.music_163 and any(
        host == item or host.endswith("." + item) for item in ADAPTER_HOSTS["music_163"]
    ):
        return "music_163"
    return None


def _load_adapter(name: str):
    if name == "bilibili":
        from ..adapters.bilibili import audio_adapter

        return audio_adapter
    if name == "music_163":
        from ..adapters.music_163 import audio_adapter

        return audio_adapter
    return None


def _load_custom_adapter(cfg: AudioConfig, ctx: AdapterContext):
    """加载用户提供的本地适配器脚本，脚本需导出 resolve_audio(page_url, ctx)。"""
    raw = (cfg.adapter.custom_script or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        if ctx.base_dir is None:
            raise AdapterError("自定义适配器脚本请使用绝对路径，或把脚本放在任务配置目录下")
        path = Path(ctx.base_dir) / path
    if not path.is_file():
        raise AdapterError(f"自定义适配器脚本不存在：{path}")
    stamp = path.stat().st_mtime
    cached = _CUSTOM_ADAPTER_CACHE.get(str(path))
    if cached and cached[0] == stamp:
        return cached[1]
    spec = importlib.util.spec_from_file_location("focus_crawler_custom_audio_adapter", path)
    if spec is None or spec.loader is None:
        raise AdapterError(f"无法加载自定义适配器脚本：{path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - 用户脚本异常统一分类
        raise AdapterError(f"自定义适配器脚本执行失败：{exc}") from exc
    if not hasattr(module, "resolve_audio"):
        raise AdapterError("自定义适配器脚本缺少 resolve_audio 函数")
    _CUSTOM_ADAPTER_CACHE[str(path)] = (stamp, module)
    return module


def _run_adapter(module, page_url: str, ctx: AdapterContext, name: str) -> list[MediaCandidate]:
    try:
        results = module.resolve_audio(page_url, ctx)
    except AdapterError:
        raise
    except Exception as exc:  # noqa: BLE001 - 适配器内部异常统一分类
        raise AdapterError(f"适配器执行异常：{exc}") from exc
    return _validate_results(results, name)


def _validate_results(results: Any, name: str) -> list[MediaCandidate]:
    if not isinstance(results, list):
        raise AdapterError("适配器返回值必须是列表")

    candidates: list[MediaCandidate] = []
    for item in results:
        if not isinstance(item, dict):
            raise AdapterError("适配器返回项必须是字典")
        audio_url = str(item.get("audio_url") or "").strip()
        if not audio_url:
            raise AdapterError("适配器返回项缺少 audio_url")
        fmt = str(item.get("format") or "").strip().lower() or guess_format(audio_url) or "mp3"
        headers = item.get("headers") or {}
        if not isinstance(headers, dict):
            raise AdapterError("适配器 headers 必须是字典")
        headers = {str(key): str(value) for key, value in headers.items()}
        duration = item.get("duration")
        bitrate = item.get("bitrate")
        candidates.append(
            MediaCandidate(
                url=audio_url,
                kind="audio",
                format=fmt,
                quality=str(item.get("title") or "").strip() or None,
                source_kind=f"adapter:{name}",
                extra={
                    "duration": float(duration) if isinstance(duration, (int, float)) else None,
                    "bitrate": int(bitrate) if isinstance(bitrate, (int, float)) else None,
                    "headers": headers,
                },
            )
        )
    if candidates:
        logger.info("音频适配器 %s 解析到 %d 条音频", name, len(candidates))
    return candidates


def resolve_audio(page_url: str, cfg: AudioConfig, ctx: AdapterContext) -> list[MediaCandidate]:
    """音频候选解析：自定义脚本 > 平台适配器 / yt-dlp（按 B 站优先级）> 空。

    配置了 custom_script 时优先使用用户脚本，否则按域名选择内置适配器；
    B 站按 ``adapter.bilibili_priority`` 决定由 yt-dlp 还是内置适配器先行。
    """
    custom = _load_custom_adapter(cfg, ctx)
    if custom is not None:
        return _run_adapter(custom, page_url, ctx, "custom")

    priority = getattr(cfg.adapter, "bilibili_priority", "ytdlp_first")
    headers = dict(ctx.headers or {})
    name = adapter_for(page_url, cfg)
    is_bilibili = name == "bilibili"

    if is_bilibili and priority == "ytdlp_first":
        candidates = ytdlp_resolve(
            page_url, cfg.adapter.ytdlp, kind="audio", headers=headers, force=True
        )
        if candidates:
            logger.info("B 站音频由 yt-dlp 解析（%d 条）", len(candidates))
            return candidates
        logger.info("yt-dlp 未解析出 B 站音频，回退内置适配器")

    candidates: list[MediaCandidate] = []
    if name is not None:
        module = _load_adapter(name)
        if module is not None:
            candidates = _run_adapter(module, page_url, ctx, name)

    if is_bilibili and priority == "adapter_only":
        return candidates

    existing = len(candidates) + max(0, int(getattr(ctx, "existing", 0) or 0))
    candidates.extend(
        ytdlp_resolve(
            page_url,
            cfg.adapter.ytdlp,
            kind="audio",
            headers=headers,
            existing_count=existing,
        )
    )
    return candidates
