"""ffmpeg 定位与能力探测。

Windows 上 ffmpeg 常常"已经下载但不在 PATH 上"（例如随 Playwright、Trae/VSCode
一类编辑器内置）。这里按「显式配置 → PATH → 常见安装位置」的顺序查找，
并探测该构建是否具备合并所需的 concat 解复用器与 mp4 封装器，
避免拿到一个被裁剪过的 ffmpeg 后合并静默失败。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .paths import app_home

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
_PROBE_TIMEOUT = 10


@dataclass(frozen=True)
class FfmpegInfo:
    """一次探测结果。"""

    path: str | None = None
    source: str = "未找到"
    version: str = ""
    has_concat: bool = False
    has_mp4_mux: bool = False
    has_mpegts_demux: bool = False

    @property
    def found(self) -> bool:
        return bool(self.path)

    @property
    def can_merge_mp4(self) -> bool:
        """能否把 ts / fMP4 分片重新封装成 mp4（yt-dlp 合流也依赖这个）。"""
        return bool(self.path) and self.has_mp4_mux

    def summary(self) -> str:
        if not self.path:
            return "未找到 ffmpeg"
        flags = []
        if self.has_concat:
            flags.append("concat")
        if self.has_mp4_mux:
            flags.append("mp4")
        detail = f"（{self.version}，{'+'.join(flags)}）" if flags else f"（{self.version}）"
        return f"{self.path} [{self.source}]{detail}"


_SOURCE_LABELS = {
    "configured": "配置",
    "app_tools": "应用目录 tools",
    "path": "PATH",
    "playwright": "Playwright 内置（功能受限）",
    "editor": "编辑器内置（功能受限）",
    "scoop": "Scoop",
    "chocolatey": "Chocolatey",
    "common": "常见安装路径",
    "imageio": "imageio-ffmpeg 包",
}

_INFO_CACHE: dict[str, FfmpegInfo] = {}


def _run(exe: str, *args: str) -> str:
    try:
        completed = subprocess.run(
            [exe, "-hide_banner", *args],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return f"{completed.stdout}\n{completed.stderr}"


def _probe(path: Path) -> FfmpegInfo:
    exe = str(path)
    version_text = _run(exe, "-version")
    if not version_text:
        return FfmpegInfo(path=exe)
    version = ""
    for line in version_text.splitlines():
        if line.lower().startswith("ffmpeg version"):
            version = line.split("version", 1)[1].strip().split("Copyright", 1)[0].strip()
            break
    demuxers = _run(exe, "-demuxers")
    muxers = _run(exe, "-muxers")
    return FfmpegInfo(
        path=exe,
        version=version,
        has_concat="concat" in demuxers,
        has_mp4_mux=" mp4 " in muxers,
        has_mpegts_demux="mpegts" in demuxers,
    )


def _glob_first(pattern: str, source: str) -> list[tuple[Path, str]]:
    try:
        matches = sorted(Path().glob(pattern)) if not os.path.isabs(pattern) else []
    except (OSError, ValueError):
        return []
    return [(item, source) for item in matches if item.is_file()]


def _candidates() -> list[tuple[Path, str]]:
    """按优先级给出候选 ffmpeg（完整构建优先，内置裁剪版排后面）。"""
    items: list[tuple[Path, str]] = []
    # 应用自带的 tools 目录（%APPDATA%/FocusCrawler/tools/ffmpeg.exe）
    try:
        tools = app_home() / "tools"
        for name in ("ffmpeg.exe", "ffmpeg"):
            candidate = tools / name
            if candidate.is_file():
                items.append((candidate, "app_tools"))
    except OSError:
        pass
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        home = os.environ.get("USERPROFILE", "")
        program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        items += [
            (Path(home) / "scoop" / "shims" / "ffmpeg.exe", "scoop"),
            (Path(program_data) / "chocolatey" / "bin" / "ffmpeg.exe", "chocolatey"),
            (Path("C:/ffmpeg/bin/ffmpeg.exe"), "common"),
            (Path(program_files) / "ffmpeg" / "bin" / "ffmpeg.exe", "common"),
        ]
        # Playwright / 编辑器内置的裁剪版：能力受限，放在最后兜底
        if local:
            items += _glob_first(str(Path(local) / "Programs" / "*" / "resources" / "app" / "bin" / "ffmpeg.exe"), "editor")
        items += [
            (Path(__file__).resolve(), "imageio"),  # 占位，下面单独处理
        ]
    else:
        items += [
            (Path("/usr/bin/ffmpeg"), "common"),
            (Path("/usr/local/bin/ffmpeg"), "common"),
            (Path("/opt/homebrew/bin/ffmpeg"), "common"),
        ]
    return [(item, source) for item, source in items if source != "imageio" and item.is_file()]


def _playwright_candidates() -> list[tuple[Path, str]]:
    local = os.environ.get("LOCALAPPDATA", "")
    if sys.platform != "win32" or not local:
        return []
    root = Path(local) / "ms-playwright"
    if not root.is_dir():
        return []
    return [
        (item, "playwright")
        for item in sorted(root.glob("ffmpeg-*/*.exe"), reverse=True)
        if item.is_file()
    ]


def _imageio_candidate() -> list[tuple[Path, str]]:
    try:
        import imageio_ffmpeg  # noqa: PLC0415 - 可选依赖

        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - 没装或调用失败都按"没有"处理
        return []
    path = Path(exe)
    return [(path, "imageio")] if path.is_file() else []


def _label(info: FfmpegInfo, source_key: str) -> FfmpegInfo:
    return FfmpegInfo(
        path=info.path,
        source=_SOURCE_LABELS.get(source_key, source_key),
        version=info.version,
        has_concat=info.has_concat,
        has_mp4_mux=info.has_mp4_mux,
        has_mpegts_demux=info.has_mpegts_demux,
    )


def discover(configured: str | None = None) -> FfmpegInfo:
    """按优先级定位 ffmpeg 并返回探测结果（带缓存）。"""
    key = (configured or "").strip()
    cached = _INFO_CACHE.get(key)
    if cached is not None:
        return cached

    info = _discover_uncached(key)
    _INFO_CACHE[key] = info
    return info


def _discover_uncached(configured: str) -> FfmpegInfo:
    if configured:
        candidate = Path(configured)
        if candidate.is_file():
            return _label(_probe(candidate), "configured")
        if candidate.is_dir():
            for name in ("ffmpeg.exe", "ffmpeg"):
                nested = candidate / name
                if nested.is_file():
                    return _label(_probe(nested), "configured")
        which = shutil.which(configured)
        if which:
            return _label(_probe(Path(which)), "configured")

    which = shutil.which("ffmpeg")
    if which:
        return _label(_probe(Path(which)), "path")

    probed: list[FfmpegInfo] = []
    for path, source in [*_candidates(), *_imageio_candidate(), *_playwright_candidates()]:
        info = _probe(path)
        if info.found:
            probed.append(_label(info, source))
    if not probed:
        return FfmpegInfo()
    # 优先选能封装 mp4 的完整构建
    for info in probed:
        if info.can_merge_mp4:
            return info
    return probed[0]


def resolve(configured: str | None = None) -> str | None:
    """返回可执行文件绝对路径；找不到返回 None。"""
    return discover(configured).path


def describe(configured: str | None = None) -> dict:
    """给 CLI / GUI 展示的结构化信息。"""
    info = discover(configured)
    return {
        "found": info.found,
        "path": info.path or "",
        "source": info.source,
        "version": info.version,
        "concat": info.has_concat,
        "mp4_mux": info.has_mp4_mux,
        "mpegts_demux": info.has_mpegts_demux,
        "can_merge_mp4": info.can_merge_mp4,
        "summary": info.summary(),
    }
