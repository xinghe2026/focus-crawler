"""媒体直链刷新测试：过期 B 站音频地址在下载前重新解析。"""

from __future__ import annotations

import httpx

from focus_crawler.config.schema import TaskConfig
from focus_crawler.core.fetcher import Fetcher
from focus_crawler.core.media_refresh import refresh_audio

PAGE_URL = "https://www.bilibili.com/video/BV1VnHp6CEU4/"


def _task(bilibili: bool = True) -> TaskConfig:
    return TaskConfig.from_dict(
        {
            "task_name": "刷新示例",
            "start_urls": [PAGE_URL],
            "allowed_domains": ["bilibili.com"],
            "audio": {"enabled": True, "adapter": {"bilibili": bilibili}},
        }
    )


def _fetcher() -> Fetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x/web-interface/view":
            return httpx.Response(
                200, json={"code": 0, "data": {"cid": 100, "title": "示例", "duration": 60}}
            )
        if request.url.path == "/x/player/playurl":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "dash": {
                            "audio": [
                                {"baseUrl": "https://cdn.example/low.m4s", "bandwidth": 66000},
                                {"baseUrl": "https://cdn.example/high.m4s", "bandwidth": 132000},
                            ]
                        }
                    },
                },
            )
        return httpx.Response(404)

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


def _row(bitrate: int | None = 132000) -> dict:
    return {
        "id": 1,
        "media_url": "https://old.example/expired-30280.m4s?sign=old",
        "source_url": PAGE_URL,
        "bitrate": bitrate,
    }


def test_refresh_audio_matches_bitrate():
    fetcher = _fetcher()
    try:
        item = refresh_audio(_task(), _row(132000), fetcher)
        assert item is not None
        assert item.url == "https://cdn.example/high.m4s"
        assert (item.extra or {}).get("headers") == {"Referer": "https://www.bilibili.com/"}
        item = refresh_audio(_task(), _row(66000), fetcher)
        assert item is not None
        assert item.url == "https://cdn.example/low.m4s"
    finally:
        fetcher.close()


def test_refresh_audio_prefers_highest_without_bitrate():
    fetcher = _fetcher()
    try:
        item = refresh_audio(_task(), _row(None), fetcher)
        assert item is not None
        assert item.url == "https://cdn.example/high.m4s"
    finally:
        fetcher.close()


def test_refresh_audio_skips_non_bilibili_source():
    fetcher = _fetcher()
    try:
        row = dict(_row(), source_url="https://example.com/video/1")
        assert refresh_audio(_task(), row, fetcher) is None
    finally:
        fetcher.close()


def test_refresh_audio_skips_disabled_adapter():
    fetcher = _fetcher()
    try:
        assert refresh_audio(_task(bilibili=False), _row(), fetcher) is None
    finally:
        fetcher.close()


def test_refresh_audio_returns_none_on_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": -404, "message": "啥都木有"})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    fetcher = Fetcher(
        timeout_ms=5000,
        retry=0,
        delay_ms=(0, 0),
        respect_robots=False,
        client=client,
        sleep=lambda _seconds: None,
        random_uniform=lambda low, high: low,
    )
    try:
        assert refresh_audio(_task(), _row(), fetcher) is None
    finally:
        fetcher.close()
