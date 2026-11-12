"""B 站视频适配器测试：durl 完整 MP4、开关、缺失 durl 与采集集成。"""

from __future__ import annotations

import httpx
import pytest

from focus_crawler.config.schema import VideoConfig
from focus_crawler.core.audio_adapter import AdapterContext, AdapterError
from focus_crawler.core.fetcher import Fetcher
from focus_crawler.core.subpage_collector import SubpageCollector
from focus_crawler.core.video_adapter import adapter_for, resolve_video

BI_VIEW = {
    "code": 0,
    "data": {"bvid": "BV1VnHp6CEU4", "aid": 1, "cid": 42476110058,
             "title": "测试视频", "duration": 240},
}
BI_DURL = {
    "code": 0,
    "data": {
        "quality": 16,
        "accept_quality": [16],
        "accept_description": ["360P 流畅"],
        "durl": [{"url": "https://cdn.test/video.mp4", "length": 240000, "size": 5694917}],
    },
}
BI_DASH_ONLY = {"code": 0, "data": {"dash": {"video": []}}}


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


def durl_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/x/web-interface/view":
        return httpx.Response(200, json=BI_VIEW)
    if path == "/x/player/playurl":
        if request.url.params.get("fnval") == "1":
            return httpx.Response(200, json=BI_DURL)
        return httpx.Response(200, json=BI_DASH_ONLY)
    return httpx.Response(404)


def dash_only_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/x/web-interface/view":
        return httpx.Response(200, json=BI_VIEW)
    if request.url.path == "/x/player/playurl":
        return httpx.Response(200, json=BI_DASH_ONLY)
    return httpx.Response(404)


def test_adapter_for_respects_switch():
    cfg = VideoConfig(enabled=True)
    assert adapter_for("https://www.bilibili.com/video/BV1VnHp6CEU4/", cfg) == "bilibili"
    cfg.adapter.bilibili = False
    assert adapter_for("https://www.bilibili.com/video/BV1VnHp6CEU4/", cfg) is None
    assert adapter_for("https://example.com/video", cfg) is None


def test_bilibili_resolve_durl_mp4():
    fetcher = _fetcher(durl_handler)
    try:
        items = resolve_video(
            "https://www.bilibili.com/video/BV1VnHp6CEU4/",
            VideoConfig(enabled=True),
            AdapterContext(fetcher=fetcher),
        )
    finally:
        fetcher.close()

    assert len(items) == 1
    item = items[0]
    assert item.url == "https://cdn.test/video.mp4"
    assert item.format == "mp4"
    assert item.quality == "360P 流畅"
    assert item.extra["duration"] == 240
    assert item.extra["headers"]["Referer"] == "https://www.bilibili.com/"
    assert item.source_kind == "adapter:bilibili"


def test_bilibili_missing_durl_rejected():
    fetcher = _fetcher(dash_only_handler)
    try:
        with pytest.raises(AdapterError) as info:
            resolve_video(
                "https://www.bilibili.com/video/BV1VnHp6CEU4/",
                VideoConfig(enabled=True),
                AdapterContext(fetcher=fetcher),
            )
    finally:
        fetcher.close()
    assert "durl" in str(info.value)


def test_collector_saves_adapter_video(task_config, store, task_id):
    task_config.audio.enabled = False
    task_config.subpage_collect.types = ["video"]
    fetcher = _fetcher(durl_handler)
    collector = SubpageCollector(task_config, store, task_id, fetcher=fetcher)
    try:
        stats = collector.collect(
            "https://www.bilibili.com/video/BV1VnHp6CEU4/", "<html></html>"
        )
    finally:
        fetcher.close()

    assert stats.videos == 1
    rows = store.list_media(task_id, "video")
    assert len(rows) == 1
    assert rows[0]["media_url"] == "https://cdn.test/video.mp4"
    assert rows[0]["format"] == "mp4"
    assert rows[0]["quality"] == "360P 流畅"
    assert rows[0]["headers"]
