"""音频适配器测试：B 站 dash.audio、网易云、域名开关、返回值校验与自定义脚本。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from focus_crawler.config.schema import AudioConfig
from focus_crawler.core.audio_adapter import AdapterContext, AdapterError, adapter_for, resolve_audio
from focus_crawler.core.fetcher import Fetcher
from focus_crawler.core.subpage_collector import SubpageCollector

BI_VIEW = {
    "code": 0,
    "data": {"bvid": "BV1xx411c7mD", "aid": 170001, "cid": 123, "title": "测试视频", "duration": 120},
}
BI_PLAY = {
    "code": 0,
    "data": {
        "dash": {
            "audio": [
                {"id": 30232, "baseUrl": "https://cdn.test/a_30232.m4a", "bandwidth": 96000},
                {"id": 30280, "baseUrl": "https://cdn.test/a_30280.m4a", "bandwidth": 192000},
            ]
        }
    },
}
BI_ERROR = {"code": -404, "message": "啥都木有"}
MUSIC_DETAIL = {
    "code": 200,
    "songs": [{"name": "歌曲名", "duration": 240000, "artists": [{"name": "歌手"}]}],
}


def _fetcher(handler) -> Fetcher:
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return Fetcher(
        timeout_ms=5000,
        retry=0,
        delay_ms=(0, 0),
        respect_robots=False,
        client=client,
        sleep=lambda _seconds: None,
        random_uniform=lambda low, high: low,
    )


def bili_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/x/web-interface/view":
        return httpx.Response(200, json=BI_VIEW)
    if path == "/x/player/playurl":
        return httpx.Response(200, json=BI_PLAY)
    return httpx.Response(404, json={"code": -404})


def bili_error_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/x/web-interface/view":
        return httpx.Response(200, json=BI_ERROR)
    return httpx.Response(404)


def music_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.startswith("/api/song/detail"):
        return httpx.Response(200, json=MUSIC_DETAIL)
    return httpx.Response(404)


def test_adapter_for_respects_switch():
    cfg = AudioConfig(enabled=True)
    assert adapter_for("https://www.bilibili.com/video/BV1xx411c7mD", cfg) == "bilibili"
    assert adapter_for("https://b23.tv/abcd", cfg) == "bilibili"
    assert adapter_for("https://music.163.com/#/song?id=123", cfg) is None
    cfg.adapter.music_163 = True
    assert adapter_for("https://music.163.com/#/song?id=123", cfg) == "music_163"
    cfg.adapter.bilibili = False
    assert adapter_for("https://www.bilibili.com/video/BV1xx411c7mD", cfg) is None


def test_bilibili_resolve_dash_audio():
    fetcher = _fetcher(bili_handler)
    try:
        items = resolve_audio(
            "https://www.bilibili.com/video/BV1xx411c7mD",
            AudioConfig(enabled=True),
            AdapterContext(fetcher=fetcher),
        )
    finally:
        fetcher.close()

    assert len(items) == 2
    top = items[0]
    assert top.url == "https://cdn.test/a_30280.m4a"
    assert top.format == "m4a"
    assert top.source_kind == "adapter:bilibili"
    assert top.extra["bitrate"] == 192000
    assert top.extra["duration"] == 120
    assert top.extra["headers"]["Referer"] == "https://www.bilibili.com/"
    assert "测试视频" in (top.quality or "")
    assert items[1].extra["bitrate"] == 96000


def test_bilibili_resolve_av_url():
    fetcher = _fetcher(bili_handler)
    try:
        items = resolve_audio(
            "https://www.bilibili.com/video/av170001",
            AudioConfig(enabled=True),
            AdapterContext(fetcher=fetcher),
        )
    finally:
        fetcher.close()
    assert items and items[0].format == "m4a"


def test_bilibili_api_error_classified():
    fetcher = _fetcher(bili_error_handler)
    try:
        with pytest.raises(AdapterError) as info:
            resolve_audio(
                "https://www.bilibili.com/video/BV1xx411c7mD",
                AudioConfig(enabled=True),
                AdapterContext(fetcher=fetcher),
            )
    finally:
        fetcher.close()
    assert "啥都木有" in str(info.value)


def test_music_163_resolve():
    fetcher = _fetcher(music_handler)
    cfg = AudioConfig(enabled=True)
    cfg.adapter.music_163 = True
    try:
        items = resolve_audio(
            "https://music.163.com/#/song?id=123",
            cfg,
            AdapterContext(fetcher=fetcher, user_agent="TestUA"),
        )
    finally:
        fetcher.close()

    assert len(items) == 1
    item = items[0]
    assert item.url == "https://music.163.com/song/media/outer/url?id=123.mp3"
    assert item.format == "mp3"
    assert item.quality == "歌手 - 歌曲名"
    assert item.extra["duration"] == 240.0
    assert item.extra["headers"]["Referer"] == "https://music.163.com/"
    assert item.extra["headers"]["User-Agent"] == "TestUA"


def test_custom_script_adapter(tmp_path: Path):
    script = tmp_path / "custom_adapter.py"
    script.write_text(
        "def resolve_audio(page_url, ctx):\n"
        "    return [{'title': '自定义音频', 'audio_url': 'https://cdn.test/custom.mp3',\n"
        "              'format': 'mp3', 'bitrate': 320000, 'duration': 10}]\n",
        encoding="utf-8",
    )
    cfg = AudioConfig(enabled=True)
    cfg.adapter.custom_script = "custom_adapter.py"
    items = resolve_audio(
        "https://example.com/song",
        cfg,
        AdapterContext(fetcher=None, base_dir=tmp_path),
    )
    assert len(items) == 1
    assert items[0].source_kind == "adapter:custom"
    assert items[0].quality == "自定义音频"


def test_custom_script_invalid_return_rejected(tmp_path: Path):
    script = tmp_path / "bad_adapter.py"
    script.write_text(
        "def resolve_audio(page_url, ctx):\n    return [{'title': '缺少地址'}]\n",
        encoding="utf-8",
    )
    cfg = AudioConfig(enabled=True)
    cfg.adapter.custom_script = "bad_adapter.py"
    with pytest.raises(AdapterError) as info:
        resolve_audio(
            "https://example.com/song",
            cfg,
            AdapterContext(fetcher=None, base_dir=tmp_path),
        )
    assert "audio_url" in str(info.value)


def test_collector_saves_adapter_audio(task_config, store, task_id):
    task_config.video.enabled = False
    fetcher = _fetcher(bili_handler)
    collector = SubpageCollector(
        task=task_config,
        store=store,
        task_id=task_id,
        fetcher=fetcher,
    )
    try:
        stats = collector.collect(
            "https://www.bilibili.com/video/BV1xx411c7mD", "<html><body></body></html>"
        )
    finally:
        fetcher.close()

    assert stats.audios == 2
    assert stats.errors == 0
    rows = store.list_media(task_id, "audio")
    assert len(rows) == 2
    assert all(row["status"] == "discovered" for row in rows)
    assert rows[0]["headers"]
    assert rows[0]["duration"] is not None
    assert rows[0]["bitrate"] is not None
    assert "Referer" in rows[0]["headers"]


def test_collector_records_adapter_error(task_config, store, task_id):
    task_config.video.enabled = False
    fetcher = _fetcher(bili_error_handler)
    collector = SubpageCollector(
        task=task_config,
        store=store,
        task_id=task_id,
        fetcher=fetcher,
    )
    try:
        stats = collector.collect(
            "https://www.bilibili.com/video/BV1xx411c7mD", "<html><body></body></html>"
        )
    finally:
        fetcher.close()

    assert stats.audios == 0
    assert stats.errors == 1
    failures = store.list_failures(task_id)
    assert any(row["failure_type"] == "adapter_error" for row in failures)
