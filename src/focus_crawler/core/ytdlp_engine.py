"""yt-dlp 通用解析引擎（可选依赖）。

把 yt-dlp 当作"万能站点解析器"接进爬虫：

* :func:`resolve_candidates` —— 只解析不下载，把页面折算成 :class:`MediaCandidate`，
  交给现有的媒体入库 / 预览 / 批量下载流程；
* :func:`download` —— 真正下载时再按页面地址重新解析一次，由 yt-dlp 自己完成
  签名刷新、分片、音视频合流与后处理，避免使用过期直链。

yt-dlp 是可选依赖（``pip install -e ".[ytdlp]"``）。未安装时所有入口都会抛出
:class:`YtdlpUnavailable`，由调用方降级到内置嗅探 / 适配器，不影响基础抓取。
"""

from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..config.schema import YtdlpConfig
from ..utils.ffmpeg import discover as discover_ffmpeg
from ..utils.hash import media_hash
from ..utils.logging import get_logger
from .media_common import MediaCandidate, MediaDownloadError, sanitize_filename

logger = get_logger("ytdlp")

INSTALL_HINT = '未安装 yt-dlp，可执行 pip install -e ".[ytdlp]"（或 pip install yt-dlp）'

# 清晰度偏好 -> yt-dlp 格式选择表达式
VIDEO_FORMATS = {
    "highest": "bv*+ba/b",
    "best": "bv*+ba/b",
    "worst": "wv*+wa/w",
    "2160p": "bv*[height<=2160]+ba/b[height<=2160]",
    "1440p": "bv*[height<=1440]+ba/b[height<=1440]",
    "1080p": "bv*[height<=1080]+ba/b[height<=1080]",
    "720p": "bv*[height<=720]+ba/b[height<=720]",
    "480p": "bv*[height<=480]+ba/b[height<=480]",
    "360p": "bv*[height<=360]+ba/b[height<=360]",
}
AUDIO_FORMATS = {
    "highest": "ba/b",
    "best": "ba/b",
    "worst": "wa/w",
}

_TEMP_SUFFIXES = (".part", ".ytdl", ".temp", ".tmp")
_SKIP_HINTS = (
    "unsupported url",
    "no video formats found",
    "no formats found",
    "no downloadable",
    "there's no video",
    "is not a valid url",
)

_MODULE: Any = None
_IMPORT_ERROR: str | None = None
_LOCK = threading.Lock()
_SUPPORT_CACHE: dict[str, bool] = {}
_SUPPORT_CACHE_LIMIT = 2048


class YtdlpError(RuntimeError):
    """yt-dlp 解析 / 下载失败。"""

    failure_type = "ytdlp_error"

    def __init__(self, message: str, failure_type: str | None = None):
        super().__init__(message)
        if failure_type:
            self.failure_type = failure_type


class YtdlpUnavailable(YtdlpError):
    """运行环境缺少 yt-dlp 依赖。"""

    failure_type = "ytdlp_missing"


class YtdlpSkip(YtdlpError):
    """页面不是 yt-dlp 能识别的媒体页（属于正常跳过，不应记为失败）。"""

    failure_type = "ytdlp_skip"


class _Cancelled(Exception):
    """进度回调里抛出，用于中断 yt-dlp 下载。"""


class _Logger:
    """把 yt-dlp 的日志转接到本项目的 logger（默认压到 debug，避免刷屏）。"""

    def debug(self, msg: str) -> None:
        logger.debug("yt-dlp: %s", str(msg).strip())

    def info(self, msg: str) -> None:
        logger.debug("yt-dlp: %s", str(msg).strip())

    def warning(self, msg: str) -> None:
        logger.debug("yt-dlp: %s", str(msg).strip())

    def error(self, msg: str) -> None:
        logger.debug("yt-dlp: %s", str(msg).strip())


def load_module() -> Any:
    """惰性导入 yt_dlp；失败缓存错误信息，避免反复触发 import 开销。"""
    global _MODULE, _IMPORT_ERROR
    if _MODULE is not None:
        return _MODULE
    with _LOCK:
        if _MODULE is not None:
            return _MODULE
        if _IMPORT_ERROR is not None:
            raise YtdlpUnavailable(_IMPORT_ERROR)
        try:
            import yt_dlp  # noqa: PLC0415 - 可选依赖，按需导入
        except Exception as exc:  # noqa: BLE001 - 缺依赖 / 依赖损坏都按不可用处理
            _IMPORT_ERROR = f"{INSTALL_HINT}（{exc}）"
            raise YtdlpUnavailable(_IMPORT_ERROR) from exc
        _MODULE = yt_dlp
        return yt_dlp


def is_available() -> bool:
    try:
        load_module()
    except YtdlpUnavailable:
        return False
    return True


def version() -> str | None:
    """返回 yt-dlp 版本号；未安装返回 None。"""
    try:
        module = load_module()
    except YtdlpUnavailable:
        return None
    return str(getattr(getattr(module, "version", None), "__version__", "") or "") or None


def describe() -> str:
    """给 GUI / 日志用的一行状态描述。"""
    current = version()
    if current:
        return f"yt-dlp {current}"
    return "yt-dlp 未安装"


def _classify(exc: BaseException) -> YtdlpError:
    message = str(exc).strip() or "yt-dlp 执行失败"
    lowered = message.lower()
    if any(hint in lowered for hint in _SKIP_HINTS):
        return YtdlpSkip(message)
    return YtdlpError(f"yt-dlp 解析失败：{message}")


def _ffmpeg_location(ffmpeg_path: str | None) -> str | None:
    """复用 utils.ffmpeg 的探测结果：PATH / Scoop / imageio-ffmpeg / Playwright 都会被找到。

    yt-dlp 的 ``ffmpeg_location`` 既接受可执行文件也接受目录，这里统一给出绝对路径。
    """
    try:
        info = discover_ffmpeg(ffmpeg_path)
    except Exception:  # noqa: BLE001 - 探测失败不影响下载（yt-dlp 会退回 PATH 查找）
        return None
    return info.path or None


def build_params(
    cfg: YtdlpConfig | None,
    *,
    headers: dict[str, str] | None = None,
    ffmpeg_path: str | None = None,
    download: bool = False,
    outtmpl: str | None = None,
    home: str | Path | None = None,
    progress_hook: Callable[[dict], None] | None = None,
) -> dict[str, Any]:
    """把任务配置折算成 yt-dlp 参数。"""
    cfg = cfg or YtdlpConfig()
    http_headers = {str(k): str(v) for k, v in (headers or {}).items()}
    if cfg.user_agent:
        http_headers.setdefault("User-Agent", cfg.user_agent)
    params: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _Logger(),
        "socket_timeout": cfg.socket_timeout,
        "retries": cfg.retries,
        "fragment_retries": cfg.retries,
        "extractor_retries": 1,
        "cachedir": False,
        "geo_bypass": True,
        "noplaylist": not cfg.playlist,
        "playlistend": max(1, cfg.max_entries) if cfg.playlist else 1,
    }
    if http_headers:
        params["http_headers"] = http_headers
    if cfg.cookies_file:
        params["cookiefile"] = cfg.cookies_file
    if cfg.cookies_from_browser:
        params["cookiesfrombrowser"] = (cfg.cookies_from_browser,)
    if cfg.proxy:
        params["proxy"] = cfg.proxy
    if download:
        location = _ffmpeg_location(ffmpeg_path)
        if location:
            params["ffmpeg_location"] = location
        params["outtmpl"] = {"default": outtmpl or "%(title).120B [%(id)s].%(ext)s"}
        params["paths"] = {"home": str(home)} if home else {}
        params["overwrites"] = True
        params["continuedl"] = True
        if progress_hook is not None:
            params["progress_hooks"] = [progress_hook]
    return params


def format_selector(cfg: YtdlpConfig | None, kind: str) -> str:
    """按配置给出格式选择表达式，显式 format 优先。"""
    cfg = cfg or YtdlpConfig()
    explicit = (cfg.format or "").strip()
    if explicit:
        return explicit
    preset = (cfg.quality or "highest").strip().lower()
    table = AUDIO_FORMATS if kind == "audio" else VIDEO_FORMATS
    return table.get(preset, table["highest"])


def _iter_entries(info: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    if not isinstance(info, dict):
        return []
    entries = info.get("entries")
    if entries is None:
        return [info]
    result: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        # 频道 / 播放列表可能存在嵌套
        if entry.get("entries") is not None:
            result.extend(_iter_entries(entry, limit - len(result)))
        else:
            result.append(entry)
        if len(result) >= limit:
            break
    return result[:limit]


def _best_format(entry: dict[str, Any], kind: str) -> dict[str, Any] | None:
    formats = [item for item in (entry.get("formats") or []) if isinstance(item, dict)]
    if not formats:
        return None
    if kind == "audio":
        pool = [
            item
            for item in formats
            if (item.get("acodec") not in (None, "none"))
            and (item.get("vcodec") in (None, "none"))
        ]
        if not pool:
            pool = [item for item in formats if item.get("acodec") not in (None, "none")]
        score = lambda item: (item.get("abr") or item.get("tbr") or 0, item.get("filesize") or 0)  # noqa: E731
    else:
        pool = [item for item in formats if item.get("vcodec") not in (None, "none")]
        if not pool:
            pool = list(formats)
        score = lambda item: (  # noqa: E731
            item.get("height") or 0,
            item.get("tbr") or 0,
            item.get("filesize") or item.get("filesize_approx") or 0,
        )
    return max(pool, key=score)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _entry_candidates(info: dict[str, Any], page_url: str, kind: str, cfg: YtdlpConfig) -> list[MediaCandidate]:
    limit = max(1, cfg.max_entries) if cfg.playlist else 1
    candidates: list[MediaCandidate] = []
    for entry in _iter_entries(info, limit):
        page = str(entry.get("webpage_url") or entry.get("original_url") or page_url)
        chosen = _best_format(entry, kind)
        direct = ""
        if chosen:
            direct = str(chosen.get("url") or "")
        if not direct:
            direct = str(entry.get("url") or "")
        if not direct.startswith(("http://", "https://")):
            # 没有可直链的地址（例如必须由 yt-dlp 合流）：退回页面地址，下载时再解析
            direct = page
        ext = str((chosen or {}).get("ext") or entry.get("ext") or "").strip().lower()
        title = str(entry.get("title") or "").strip()
        quality = str(
            (chosen or {}).get("format_note")
            or (chosen or {}).get("resolution")
            or entry.get("format_note")
            or ""
        ).strip()
        headers = (chosen or {}).get("http_headers") or entry.get("http_headers") or {}
        if not isinstance(headers, dict):
            headers = {}
        duration = _float_or_none(entry.get("duration"))
        if duration is None and chosen is not None:
            duration = _float_or_none(chosen.get("duration"))
        if kind == "audio":
            bitrate = _int_or_none((chosen or {}).get("abr"))
            if bitrate is None:
                bitrate = _int_or_none((chosen or {}).get("tbr"))
            if bitrate:
                bitrate = int(bitrate * 1000)
            sample_rate = _int_or_none((chosen or {}).get("asr"))
        else:
            bitrate = _int_or_none((chosen or {}).get("tbr"))
            if bitrate:
                bitrate = int(bitrate * 1000)
            sample_rate = None
        fallback_ext = "m4a" if kind == "audio" else "mp4"
        candidates.append(
            MediaCandidate(
                url=direct,
                kind=kind,
                format=ext or fallback_ext,
                width=_int_or_none((chosen or {}).get("width")),
                height=_int_or_none((chosen or {}).get("height")),
                quality=quality or title or None,
                source_kind="ytdlp",
                extra={
                    "duration": duration,
                    "bitrate": bitrate,
                    "sample_rate": sample_rate,
                    "headers": {str(k): str(v) for k, v in headers.items()},
                    "ytdlp": True,
                    "page_url": page,
                    "format_id": str((chosen or {}).get("format_id") or ""),
                    "extractor": str(entry.get("extractor_key") or entry.get("extractor") or ""),
                    "title": title,
                    # 直链退回页面地址时补一个去重后缀，避免同页音/视频互相顶掉
                    "dedupe_key": f"ytdlp-{kind}" if direct == page else "",
                },
            )
        )
    unique: list[MediaCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.extra.get("dedupe_key") or candidate.url
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def resolve_candidates(
    page_url: str,
    cfg: YtdlpConfig | None,
    *,
    kind: str = "video",
    headers: dict[str, str] | None = None,
) -> list[MediaCandidate]:
    """解析页面并返回媒体候选；不是媒体页时返回空列表。"""
    if not page_url.lower().startswith(("http://", "https://")):
        return []
    module = load_module()
    params = build_params(cfg, headers=headers, download=False)
    try:
        with module.YoutubeDL(params) as ydl:
            info = ydl.extract_info(page_url, download=False)
    except Exception as exc:  # noqa: BLE001 - 统一转成 yt-dlp 错误分类
        raise _classify(exc) from exc
    if not isinstance(info, dict):
        return []
    candidates = _entry_candidates(info, page_url, kind, cfg or YtdlpConfig())
    if not candidates:
        raise YtdlpSkip("yt-dlp 未解析到可用格式")
    logger.info("yt-dlp 解析 %s -> %d 个%s候选", page_url, len(candidates), kind)
    return candidates


def supports(page_url: str) -> bool:
    """判断站点是否被 yt-dlp 的专用提取器覆盖（排除通用提取器）。

    ``auto`` 模式用它避免在普通文章页上白跑一次解析请求。
    结果按 URL 缓存，单次判断约 1~3 ms。
    """
    key = page_url.strip()
    cached = _SUPPORT_CACHE.get(key)
    if cached is not None:
        return cached
    module = load_module()
    try:
        extractors = module.extractor.gen_extractor_classes()
    except Exception:  # noqa: BLE001 - 无法判断时保守放行
        return True
    matched = False
    for extractor in extractors:
        if str(getattr(extractor, "IE_NAME", "")).lower() in ("generic", ""):
            continue
        try:
            if extractor.suitable(page_url):
                matched = True
                break
        except Exception:  # noqa: BLE001 - 个别提取器匹配异常不影响整体判断
            continue
    if len(_SUPPORT_CACHE) >= _SUPPORT_CACHE_LIMIT:
        _SUPPORT_CACHE.clear()
    _SUPPORT_CACHE[key] = matched
    return matched


def maybe_resolve(
    page_url: str,
    cfg: YtdlpConfig | None,
    *,
    kind: str = "video",
    headers: dict[str, str] | None = None,
    existing_count: int = 0,
) -> list[MediaCandidate]:
    """适配器入口：按 mode 判断是否调用 yt-dlp，并把各类失败降级为"跳过"。

    * ``off``   —— 不解析
    * ``auto``  —— 本页已有候选时跳过；且仅对 yt-dlp 已知站点解析（默认）
    * ``force`` —— 本页已有候选时跳过；未知站点也交给通用提取器试一次
    """
    if cfg is None or not cfg.enabled:
        return []
    if existing_count > 0:
        return []
    if cfg.mode == "auto":
        try:
            if not supports(page_url):
                logger.debug("yt-dlp 无该站点提取器，跳过：%s", page_url)
                return []
        except YtdlpUnavailable as exc:
            logger.warning("已跳过 yt-dlp 通用解析：%s", exc)
            return []
    try:
        return resolve_candidates(page_url, cfg, kind=kind, headers=headers)
    except YtdlpUnavailable as exc:
        logger.warning("已跳过 yt-dlp 通用解析：%s", exc)
    except YtdlpSkip as exc:
        logger.debug("yt-dlp 跳过 %s：%s", page_url, exc)
    except YtdlpError as exc:
        logger.warning("yt-dlp 解析失败 %s：%s", page_url, exc)
    return []


def _pick_output(folder: Path) -> Path | None:
    produced = [
        path
        for path in folder.rglob("*")
        if path.is_file() and not path.name.endswith(_TEMP_SUFFIXES) and path.stat().st_size > 0
    ]
    if not produced:
        return None
    return max(produced, key=lambda item: item.stat().st_size)


def _final_path(page_url: str, target: Path, title: str | None, suffix: str) -> Path:
    stem = sanitize_filename(title or "", "") or target.stem
    destination = target.parent / f"{stem}{suffix.lower()}"
    if destination.exists():
        destination = target.parent / f"{stem}_{media_hash(page_url)[:8]}{suffix.lower()}"
    return destination


def download(
    page_url: str,
    target: str | Path,
    cfg: YtdlpConfig | None = None,
    *,
    kind: str = "video",
    headers: dict[str, str] | None = None,
    ffmpeg_path: str | None = None,
    transcode_to: str | None = None,
    should_stop: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
    pause_interval: float = 0.2,
) -> tuple[Path, int]:
    """用 yt-dlp 下载到 target 所在目录，返回 (最终路径, 字节数)。

    先下载到同目录临时子目录，再按标题重命名并移动到目标目录：
    这样合并 / 后处理产物命名由 yt-dlp 决定，不会和分片管线混淆。
    """
    if not page_url.lower().startswith(("http://", "https://")):
        raise YtdlpError(f"不是可下载的页面地址：{page_url}")
    module = load_module()
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    work = target.parent / f".ytdlp_{kind}_{media_hash(page_url)[:12]}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    def hook(status: dict[str, Any]) -> None:
        if should_stop and should_stop():
            raise _Cancelled("下载已停止")
        while should_pause and should_pause():
            if should_stop and should_stop():
                raise _Cancelled("下载已停止")
            time.sleep(pause_interval)
        if should_stop and should_stop():
            raise _Cancelled("下载已停止")

    params = build_params(
        cfg,
        headers=headers,
        ffmpeg_path=ffmpeg_path,
        download=True,
        home=work,
        progress_hook=hook,
    )
    params["noplaylist"] = True
    params["playlistend"] = 1
    params["format"] = format_selector(cfg, kind)
    if kind == "audio":
        params["merge_output_format"] = "m4a"
        if transcode_to:
            params["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": transcode_to,
                    "preferredquality": "0",
                }
            ]
    else:
        params["merge_output_format"] = "mp4"

    info: dict[str, Any] | None = None
    try:
        with module.YoutubeDL(params) as ydl:
            info = ydl.extract_info(page_url, download=True)
            if info is None:
                raise YtdlpError("yt-dlp 没有返回下载结果")
            if isinstance(info, dict) and isinstance(info.get("entries"), list):
                entries = [item for item in info["entries"] if isinstance(item, dict)]
                info = entries[0] if entries else info
    except _Cancelled as exc:
        shutil.rmtree(work, ignore_errors=True)
        raise MediaDownloadError("下载已停止", "media_download_error") from exc
    except YtdlpError:
        shutil.rmtree(work, ignore_errors=True)
        raise
    except Exception as exc:  # noqa: BLE001 - 统一分类给上层入库
        shutil.rmtree(work, ignore_errors=True)
        raise _classify(exc) from exc

    produced = _pick_output(work)
    if produced is None:
        shutil.rmtree(work, ignore_errors=True)
        raise YtdlpError("yt-dlp 下载结束但没有产出文件")

    title = str((info or {}).get("title") or "").strip() or None
    destination = _final_path(page_url, target, title, produced.suffix or ".mp4")
    try:
        produced.replace(destination)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not destination.exists() or destination.stat().st_size == 0:
        raise YtdlpError("yt-dlp 输出文件为空")
    logger.info("yt-dlp 下载完成 %s -> %s", page_url, destination)
    return destination, destination.stat().st_size


@dataclass
class YtdlpProbe:
    """环境自检结果，供 GUI / CLI 展示。"""

    available: bool
    version: str | None
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"available": self.available, "version": self.version, "message": self.message}


def probe() -> YtdlpProbe:
    current = version()
    if current:
        return YtdlpProbe(True, current, f"yt-dlp {current} 可用")
    return YtdlpProbe(False, None, INSTALL_HINT)

