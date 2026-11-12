"""yt-dlp 通用解析集成测试：配置、候选解析、下载路由、降级与来源落库。

测试全部使用假的 yt_dlp 模块（或直接替换引擎里的模块引用），
不依赖真实网络与 yt-dlp 安装状态。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from focus_crawler.config.schema import ConfigError, TaskConfig, YtdlpConfig
from focus_crawler.core import ytdlp_engine
from focus_crawler.core.audio_adapter import AdapterContext
from focus_crawler.core.media_common import MediaCandidate, MediaDownloadError
from focus_crawler.core.storage import Storage
from focus_crawler.core.subpage_collector import SubpageCollector, SubpageStats
from focus_crawler.core.video_adapter import resolve_video
from focus_crawler.core.video_downloader import download_video

PAGE = "https://video.test/watch/abc"
INFO = {
    "id": "abc",
    "title": "示例视频",
    "ext": "mp4",
    "duration": 120,
    "webpage_url": PAGE,
    "extractor_key": "Test",
    "formats": [
        {
            "format_id": "140",
            "ext": "m4a",
            "vcodec": "none",
            "acodec": "mp4a.40.2",
            "abr": 128,
            "url": "https://cdn.test/audio.m4a",
            "http_headers": {"Referer": PAGE},
        },
        {"format_id": "136", "ext": "mp4", "vcodec": "avc1", "acodec": "none", "height": 720,
         "tbr": 2500, "url": "https://cdn.test/v720.mp4"},
        {"format_id": "137", "ext": "mp4", "vcodec": "avc1", "acodec": "none", "height": 1080,
         "tbr": 4000, "url": "https://cdn.test/v1080.mp4"},
    ],
}


class FakeYoutubeDL:
    """模拟 yt_dlp.YoutubeDL 的最小子集。"""

    calls: list[dict] = []

    default_info = INFO

    def __init__(self, params, info=None, payload=b"x" * 4096):
        self.params = params
        self.info = info if info is not None else type(self).default_info
        self.payload = payload
        FakeYoutubeDL.calls.append(params)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def extract_info(self, url, download=False):
        if not download:
            return self.info
        for hook in self.params.get("progress_hooks") or []:
            hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": len(self.payload)})
        home = Path(self.params["paths"]["home"])
        home.mkdir(parents=True, exist_ok=True)
        (home / "示例视频 [abc].mp4").write_bytes(self.payload)
        return {"title": "示例视频", "id": "abc", "ext": "mp4"}


class FakeModule:
    YoutubeDL = FakeYoutubeDL
    version = type("V", (), {"__version__": "9999.1.1"})()


@pytest.fixture()
def fake_ytdlp(monkeypatch):
    FakeYoutubeDL.calls = []
    monkeypatch.setattr(ytdlp_engine, "_MODULE", FakeModule)
    monkeypatch.setattr(ytdlp_engine, "_IMPORT_ERROR", None)
    return FakeYoutubeDL


# ---------- 配置 ----------
def test_config_parses_ytdlp_sections():
    task = TaskConfig.from_dict(
        {
            "task_name": "t",
            "start_urls": [PAGE],
            "allowed_domains": ["video.test"],
            "video": {"adapter": {"ytdlp": {"mode": "force", "quality": "720p", "proxy": "http://p:1"}}},
            "audio": {"adapter": {"ytdlp": {"mode": "off", "cookies_from_browser": "edge"}}},
        }
    )
    assert task.video.adapter.ytdlp.mode == "force"
    assert task.video.adapter.ytdlp.quality == "720p"
    assert task.video.adapter.ytdlp.proxy == "http://p:1"
    assert task.video.adapter.ytdlp.enabled is True
    assert task.audio.adapter.ytdlp.mode == "off"
    assert task.audio.adapter.ytdlp.enabled is False
    assert task.audio.adapter.ytdlp.cookies_from_browser == "edge"


def test_config_rejects_unknown_ytdlp_mode():
    with pytest.raises(ConfigError) as excinfo:
        TaskConfig.from_dict(
            {
                "task_name": "t",
                "start_urls": [PAGE],
                "allowed_domains": ["video.test"],
                "video": {"adapter": {"ytdlp": {"mode": "sometimes"}}},
            }
        )
    assert any("video.adapter.ytdlp.mode" in message for message in excinfo.value.errors)


def test_config_defaults_when_absent():
    cfg = YtdlpConfig.from_dict(None, [])
    assert cfg.mode == "auto"
    assert cfg.quality == "highest"
    assert cfg.max_entries == 20


# ---------- 格式选择 ----------
def test_format_selector_presets_and_custom():
    assert ytdlp_engine.format_selector(YtdlpConfig(), "video") == "bv*+ba/b"
    assert "height<=720" in ytdlp_engine.format_selector(YtdlpConfig(quality="720p"), "video")
    assert ytdlp_engine.format_selector(YtdlpConfig(quality="highest"), "audio") == "ba/b"
    assert ytdlp_engine.format_selector(YtdlpConfig(format="best[height<=480]"), "video") == "best[height<=480]"


# ---------- 依赖缺失降级 ----------
def test_missing_dependency_degrades(monkeypatch):
    monkeypatch.setattr(ytdlp_engine, "_MODULE", None)
    monkeypatch.setattr(ytdlp_engine, "_IMPORT_ERROR", ytdlp_engine.INSTALL_HINT)
    assert ytdlp_engine.is_available() is False
    assert ytdlp_engine.version() is None
    assert "未安装" in ytdlp_engine.describe()
    assert ytdlp_engine.maybe_resolve(PAGE, YtdlpConfig(), kind="video") == []
    with pytest.raises(ytdlp_engine.YtdlpUnavailable):
        ytdlp_engine.resolve_candidates(PAGE, YtdlpConfig())


# ---------- 候选解析 ----------
def test_resolve_video_candidates(fake_ytdlp):
    candidates = ytdlp_engine.resolve_candidates(PAGE, YtdlpConfig(), kind="video")
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.url == "https://cdn.test/v1080.mp4"
    assert candidate.height == 1080
    assert candidate.format == "mp4"
    assert candidate.source_kind == "ytdlp"
    assert candidate.extra["page_url"] == PAGE
    assert candidate.extra["duration"] == 120
    assert candidate.quality


def test_resolve_audio_candidates(fake_ytdlp):
    candidates = ytdlp_engine.resolve_candidates(PAGE, YtdlpConfig(), kind="audio")
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.url == "https://cdn.test/audio.m4a"
    assert candidate.format == "m4a"
    assert candidate.extra["bitrate"] == 128000
    assert candidate.extra["headers"]["Referer"] == PAGE


def test_playlist_entries_limited(fake_ytdlp):
    entry = dict(INFO)
    second = dict(INFO)
    second["id"] = "def"
    second["webpage_url"] = "https://video.test/watch/def"
    second["formats"] = [dict(fmt, url=fmt["url"].replace(".test/", ".test/def-")) for fmt in INFO["formats"]]
    playlist = {"_type": "playlist", "entries": [entry, second]}
    FakeYoutubeDL.default_info = playlist
    try:
        single = ytdlp_engine.resolve_candidates(PAGE, YtdlpConfig(), kind="video")
        assert len(single) == 1
        several = ytdlp_engine.resolve_candidates(
            PAGE, YtdlpConfig(playlist=True, max_entries=2), kind="video"
        )
        assert len(several) == 2
        assert several[1].extra["page_url"].endswith("/def")
    finally:
        FakeYoutubeDL.default_info = INFO


def test_unsupported_page_is_skip(monkeypatch):
    class ErrorYDL(FakeYoutubeDL):
        def extract_info(self, url, download=False):
            raise RuntimeError("Unsupported URL: https://video.test/plain")

    class ErrorModule(FakeModule):
        YoutubeDL = ErrorYDL

    monkeypatch.setattr(ytdlp_engine, "_MODULE", ErrorModule)
    with pytest.raises(ytdlp_engine.YtdlpSkip):
        ytdlp_engine.resolve_candidates(PAGE, YtdlpConfig(), kind="video")
    # 适配器入口会把 skip 降级成空列表
    assert ytdlp_engine.maybe_resolve(PAGE, YtdlpConfig(), kind="video") == []


def test_maybe_resolve_modes(fake_ytdlp, monkeypatch):
    monkeypatch.setattr(ytdlp_engine, "supports", lambda url: True)
    assert ytdlp_engine.maybe_resolve(PAGE, YtdlpConfig(mode="off"), kind="video") == []
    assert ytdlp_engine.maybe_resolve(PAGE, YtdlpConfig(mode="auto"), kind="video", existing_count=1) == []
    assert FakeYoutubeDL.calls == []
    assert ytdlp_engine.maybe_resolve(PAGE, YtdlpConfig(mode="auto"), kind="video") != []
    assert ytdlp_engine.maybe_resolve(PAGE, YtdlpConfig(mode="force"), kind="video") != []


def test_auto_mode_skips_unknown_sites(fake_ytdlp, monkeypatch):
    monkeypatch.setattr(ytdlp_engine, "supports", lambda url: False)
    assert ytdlp_engine.maybe_resolve(PAGE, YtdlpConfig(mode="auto"), kind="video") == []
    assert FakeYoutubeDL.calls == []
    # force 模式不看站点是否被支持
    assert ytdlp_engine.maybe_resolve(PAGE, YtdlpConfig(mode="force"), kind="video") != []


def test_video_adapter_uses_ytdlp_fallback(fake_ytdlp):
    task = TaskConfig.from_dict(
        {
            "task_name": "t",
            "start_urls": [PAGE],
            "allowed_domains": ["video.test"],
            "video": {"enabled": True},
        }
    )
    ctx = AdapterContext(fetcher=None)
    candidates = resolve_video(PAGE, task.video, ctx)
    assert candidates and candidates[0].source_kind == "ytdlp"
    # 已嗅到候选时 fallback 模式不再调用 yt-dlp
    FakeYoutubeDL.calls = []
    resolve_video(PAGE, task.video, AdapterContext(fetcher=None, existing=3))
    assert FakeYoutubeDL.calls == []


# ---------- 下载 ----------
def test_download_moves_and_renames(fake_ytdlp, tmp_path):
    target = tmp_path / "watch.mp4"
    path, size = ytdlp_engine.download(PAGE, target, YtdlpConfig(), kind="video")
    assert path.name == "示例视频.mp4"
    assert path.exists() and size == 4096
    leftovers = [item for item in tmp_path.iterdir() if item.name.startswith(".ytdlp_")]
    assert leftovers == []


def test_download_respects_stop(fake_ytdlp, tmp_path):
    with pytest.raises(MediaDownloadError):
        ytdlp_engine.download(
            PAGE, tmp_path / "watch.mp4", YtdlpConfig(), kind="video", should_stop=lambda: True
        )
    assert not (tmp_path / "示例视频.mp4").exists()


def test_video_download_routes_to_ytdlp(monkeypatch, tmp_path):
    captured = {}

    def fake_download(page_url, target, cfg, **kwargs):
        captured.update({"page_url": page_url, "target": Path(target), "kind": kwargs.get("kind")})
        path = Path(target).with_suffix(".mp4")
        path.write_bytes(b"ok")
        return path, 2

    monkeypatch.setattr("focus_crawler.core.video_downloader.ytdlp_download", fake_download)
    task = TaskConfig.from_dict(
        {
            "task_name": "t",
            "start_urls": [PAGE],
            "allowed_domains": ["video.test"],
            "video": {"enabled": True},
        }
    )
    path, size = download_video(
        None,
        "https://cdn.test/v1080.mp4",
        tmp_path / "watch.mp4",
        config=task.video,
        source_kind="ytdlp",
        page_url=PAGE,
    )
    assert captured["page_url"] == PAGE
    assert captured["kind"] == "video"
    assert path.exists() and size == 2


def test_video_download_falls_back_when_ytdlp_fails(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise ytdlp_engine.YtdlpError("解析失败")

    class Result:
        ok = True
        content = b"direct"
        status_code = 200
        error = None
        failure_type = None

    class Fetcher:
        def fetch(self, url, headers=None, want_bytes=False):
            return Result()

    monkeypatch.setattr("focus_crawler.core.video_downloader.ytdlp_download", boom)
    task = TaskConfig.from_dict(
        {
            "task_name": "t",
            "start_urls": [PAGE],
            "allowed_domains": ["video.test"],
            "video": {"enabled": True},
        }
    )
    path, size = download_video(
        Fetcher(),
        "https://cdn.test/v1080.mp4",
        tmp_path / "watch.mp4",
        config=task.video,
        source_kind="ytdlp",
        page_url=PAGE,
    )
    assert path.read_bytes() == b"direct"
    assert size == len(b"direct")


# ---------- 落库 ----------
def test_save_media_keeps_source_kind_and_kind_specific_hash(tmp_path):
    store = Storage(tmp_path / "t.db")
    task = TaskConfig.from_dict(
        {"task_name": "t", "start_urls": [PAGE], "allowed_domains": ["video.test"]}
    )
    collector = SubpageCollector(task=task, store=store, task_id=1)
    stats = SubpageStats()
    video = MediaCandidate(
        url=PAGE,
        kind="video",
        format="mp4",
        source_kind="ytdlp",
        extra={"dedupe_key": "ytdlp-video"},
    )
    audio = MediaCandidate(
        url=PAGE,
        kind="audio",
        format="m4a",
        source_kind="ytdlp",
        extra={"dedupe_key": "ytdlp-audio"},
    )
    assert collector._save_media([video], PAGE, None, "video", stats) == 1
    assert collector._save_media([audio], PAGE, None, "audio", stats) == 1
    rows = store.list_media(1)
    assert {row["media_type"] for row in rows} == {"video", "audio"}
    assert {row["source_kind"] for row in rows} == {"ytdlp"}
    store.close()


def test_save_media_records_origin_page_for_ytdlp(tmp_path):
    """播放列表条目与来源页不同：origin_url 记录真正要重新解析的页面。"""
    store = Storage(tmp_path / "t.db")
    task = TaskConfig.from_dict(
        {"task_name": "t", "start_urls": [PAGE], "allowed_domains": ["video.test"]}
    )
    collector = SubpageCollector(task=task, store=store, task_id=1)
    entry_page = "https://video.test/watch/entry-2"
    candidate = MediaCandidate(
        url="https://cdn.test/v-entry-2.mp4",
        kind="video",
        format="mp4",
        source_kind="ytdlp",
        extra={"page_url": entry_page},
    )
    collector._save_media([candidate], PAGE, None, "video", SubpageStats())
    row = store.list_media(1)[0]
    assert row["origin_url"] == entry_page
    store.close()


def test_media_source_helpers_tolerate_old_rows():
    from focus_crawler.core.media_common import media_origin_url, media_source_kind

    assert media_source_kind({"media_url": "https://x/y.mp4"}) is None
    assert media_origin_url({"media_url": "https://x/y.mp4"}) is None
    assert media_source_kind({"source_kind": "ytdlp"}) == "ytdlp"
    assert media_origin_url({"origin_url": "https://x/page"}) == "https://x/page"


def test_storage_migrates_source_kind(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE media (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          task_id INTEGER NOT NULL,
          source_url TEXT NOT NULL,
          parent_url TEXT,
          media_url TEXT NOT NULL,
          media_hash TEXT NOT NULL,
          media_type TEXT NOT NULL,
          format TEXT,
          quality TEXT,
          status TEXT NOT NULL DEFAULT 'discovered',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()
    store = Storage(db)
    columns = {row["name"] for row in store.conn.execute("PRAGMA table_info(media)")}
    assert "source_kind" in columns
    store.close()


def test_task_to_dict_contains_ytdlp(tmp_path):
    task = TaskConfig.from_dict(
        {"task_name": "t", "start_urls": [PAGE], "allowed_domains": ["video.test"]}
    )
    payload = json.loads(json.dumps(task.to_dict(), ensure_ascii=False))
    assert payload["video"]["adapter"]["ytdlp"]["mode"] == "auto"
    # to_dict -> from_dict 往返必须通过校验
    TaskConfig.from_dict(payload).validate()


@pytest.mark.real_ytdlp
@pytest.mark.skipif(not ytdlp_engine.is_available(), reason="需要真实安装 yt-dlp")
def test_supports_detects_known_sites():
    assert ytdlp_engine.supports("https://www.youtube.com/watch?v=aqz-KE-bpKQ") is True
    assert ytdlp_engine.supports("https://www.bilibili.com/video/BV1xx411c7mD") is True
    assert ytdlp_engine.supports("https://example.com/plain/article") is False
    # 结果应被缓存
    assert ytdlp_engine.supports("https://example.com/plain/article") is False


@pytest.mark.real_ytdlp
@pytest.mark.skipif(not ytdlp_engine.is_available(), reason="需要真实安装 yt-dlp")
def test_probe_reports_version():
    result = ytdlp_engine.probe()
    assert result.available is True
    assert result.version
    assert result.as_dict()["available"] is True


def test_preview_counts_ytdlp_candidates(monkeypatch, task_config, fetcher):
    """预览与运行时一致：静态嗅探为空时，yt-dlp 结果也计入预览统计。"""
    from focus_crawler.core import preview as preview_mod
    from focus_crawler.core.preview import preview_task

    task_config.video.enabled = True
    task_config.audio.enabled = False
    task_config.subpage_collect.preview_limit = 1
    captured: list[str] = []

    def fake_resolve(page_url, cfg, *, kind, headers=None, existing_count=0):
        captured.append(page_url)
        if existing_count:
            return []
        return [MediaCandidate(url="https://cdn.test/only-ytdlp.mp4", kind=kind, format="mp4", source_kind="ytdlp")]

    monkeypatch.setattr(preview_mod, "ytdlp_resolve", fake_resolve)
    result = preview_task(task_config, fetcher)
    assert result.ok is True
    assert captured
    assert result.subpages[0].videos >= 1


def test_build_params_maps_config(tmp_path):
    ffmpeg_dir = tmp_path / "ffmpeg"
    ffmpeg_dir.mkdir()
    cfg = YtdlpConfig(
        cookies_file=str(tmp_path / "cookies.txt"),
        cookies_from_browser="edge",
        proxy="http://127.0.0.1:7890",
        user_agent="UA-Test/1.0",
        socket_timeout=12,
        retries=5,
        playlist=True,
        max_entries=3,
    )
    params = ytdlp_engine.build_params(
        cfg,
        headers={"Referer": PAGE},
        ffmpeg_path=str(ffmpeg_dir),
        download=True,
        home=tmp_path,
    )
    assert params["cookiefile"] == str(tmp_path / "cookies.txt")
    assert params["cookiesfrombrowser"] == ("edge",)
    assert params["proxy"] == "http://127.0.0.1:7890"
    assert params["http_headers"]["User-Agent"] == "UA-Test/1.0"
    assert params["http_headers"]["Referer"] == PAGE
    assert params["socket_timeout"] == 12
    assert params["retries"] == 5
    assert params["noplaylist"] is False
    assert params["playlistend"] == 3
    assert params["ffmpeg_location"] == str(ffmpeg_dir)
    assert params["paths"]["home"] == str(tmp_path)
    assert "%(ext)s" in params["outtmpl"]["default"]
    assert params["cachedir"] is False

    # 未指定 ffmpeg 时不传位置参数（交给 yt-dlp 自己在 PATH 里找）
    assert "ffmpeg_location" not in ytdlp_engine.build_params(YtdlpConfig(), ffmpeg_path="ffmpeg")
