"""ffmpeg 探测：配置路径 / 应用 tools 目录 / 探测缓存。"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from focus_crawler.utils import ffmpeg


@pytest.fixture(autouse=True)
def clear_cache():
    ffmpeg._INFO_CACHE.clear()
    yield
    ffmpeg._INFO_CACHE.clear()


def _fake_exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(sys.executable, path)
    return path


def test_discover_uses_configured_path(tmp_path):
    exe = _fake_exe(tmp_path / "bin" / "ffmpeg.exe")
    info = ffmpeg.describe(str(exe))
    assert info["found"] is True
    assert info["path"] == str(exe)
    assert info["source"] == "配置"
    # python 不是真的 ffmpeg，探测不到能力但也应返回路径
    assert info["can_merge_mp4"] is False


def test_discover_accepts_directory(tmp_path):
    directory = tmp_path / "tools"
    exe = _fake_exe(directory / "ffmpeg.exe")
    info = ffmpeg.describe(str(directory))
    assert info["found"] is True
    assert info["path"] == str(exe)


def test_discover_finds_app_tools_dir(tmp_path, monkeypatch):
    """%APPDATA%/FocusCrawler/tools/ffmpeg.exe 无需配置即可被找到。"""
    home = tmp_path / "app_home"
    exe = _fake_exe(home / "tools" / "ffmpeg.exe")
    monkeypatch.setenv("FOCUSCRAWLER_HOME", str(home))
    # 隔离环境：不落到 PATH / imageio / Playwright 的其它 ffmpeg
    monkeypatch.setattr(ffmpeg.shutil, "which", lambda name: None)
    monkeypatch.setattr(ffmpeg, "_imageio_candidate", lambda: [])
    monkeypatch.setattr(ffmpeg, "_playwright_candidates", lambda: [])
    ffmpeg._INFO_CACHE.clear()
    info = ffmpeg.describe(None)
    assert info["path"] == str(exe)
    assert info["source"] == "应用目录 tools"


def test_discover_caches_results(monkeypatch):
    calls = {"n": 0}

    def fake_uncached(configured: str):
        calls["n"] += 1
        return ffmpeg.FfmpegInfo(path="C:/fake/ffmpeg.exe", source="配置")

    monkeypatch.setattr(ffmpeg, "_discover_uncached", fake_uncached)
    ffmpeg._INFO_CACHE.clear()
    first = ffmpeg.discover("C:/fake/ffmpeg.exe")
    second = ffmpeg.discover("C:/fake/ffmpeg.exe")
    assert first is second
    assert calls["n"] == 1


def test_merge_falls_back_to_binary_concat(tmp_path, monkeypatch):
    """裁剪版 ffmpeg（无 concat 解复用器）时改走二进制拼接 + 重封装。"""
    from focus_crawler.core import media_pipeline

    seg1 = tmp_path / "seg1.ts"
    seg2 = tmp_path / "seg2.ts"
    seg1.write_bytes(b"AAA")
    seg2.write_bytes(b"BBB")

    monkeypatch.setattr(
        media_pipeline,
        "discover_ffmpeg",
        lambda configured=None: ffmpeg.FfmpegInfo(path="ffmpeg", has_concat=False, has_mp4_mux=True),
    )
    used = {}

    def fake_binary(paths, output, exe="ffmpeg"):
        used["paths"] = paths
        output.write_bytes(b"merged")

    monkeypatch.setattr(media_pipeline, "merge_by_binary_concat", fake_binary)
    output = tmp_path / "out.mp4"
    media_pipeline.merge_with_ffmpeg([seg1, seg2], output, "ffmpeg")
    assert used["paths"] == [seg1, seg2]
    assert output.read_bytes() == b"merged"
