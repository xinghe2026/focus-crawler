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


def test_collector_refreshes_signed_audio_before_download(tmp_path, monkeypatch):
    """自动下载音频前必须刷新 B 站签名地址，否则旧地址会 403。"""
    from focus_crawler.core import subpage_collector as sc
    from focus_crawler.core.media_common import MediaCandidate
    from focus_crawler.core.storage import Storage
    from focus_crawler.models.media import MediaRecord
    from focus_crawler.utils.hash import media_hash

    store = Storage(tmp_path / "t.db")
    task = TaskConfig.from_dict(
        {
            "task_name": "t",
            "start_urls": ["https://www.bilibili.com/video/BV1xx411c7mD"],
            "allowed_domains": ["bilibili.com"],
            "audio": {"enabled": True, "adapter": {"bilibili": True}},
            "video": {"enabled": False},
            "image": {"enabled": False},
        }
    )
    task_id = store.upsert_task(task.task_name, "{}")
    store.add_media(
        MediaRecord(
            task_id=task_id,
            source_url="https://www.bilibili.com/video/BV1xx411c7mD",
            parent_url=None,
            media_url="https://cdn.test/old-signed.m4s",
            media_hash=media_hash("https://cdn.test/old-signed.m4s"),
            media_type="audio",
            format="m4s",
            bitrate=192000,
            source_kind="adapter:bilibili",
        )
    )
    row = store.list_media(task_id)[0]

    captured: dict = {}
    monkeypatch.setattr(
        sc,
        "refresh_audio",
        lambda task, row, fetcher: MediaCandidate(
            url="https://cdn.test/fresh-signed.m4s",
            kind="audio",
            format="m4s",
            extra={"headers": {"Referer": "https://www.bilibili.com/"}},
        ),
    )

    def fake_download_audio(fetcher, url, target, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        path = tmp_path / "a.m4s"
        path.write_bytes(b"x")
        return path, 1

    monkeypatch.setattr(sc, "download_audio", fake_download_audio)
    collector = sc.SubpageCollector(task=task, store=store, task_id=task_id, fetcher=object())
    collector._download_media(row, "audio", sc.SubpageStats())

    assert captured["url"] == "https://cdn.test/fresh-signed.m4s"
    assert captured["headers"]["Referer"] == "https://www.bilibili.com/"
    assert store.get_media_by_id(int(row["id"]))["media_url"] == "https://cdn.test/fresh-signed.m4s"
    store.close()


def test_collector_refreshes_signed_video_before_download(tmp_path, monkeypatch):
    """缺口 12：视频直链同样在下载前刷新签名。"""
    from focus_crawler.core import subpage_collector as sc
    from focus_crawler.core.media_common import MediaCandidate
    from focus_crawler.core.storage import Storage
    from focus_crawler.models.media import MediaRecord
    from focus_crawler.utils.hash import media_hash

    store = Storage(tmp_path / "t.db")
    task = TaskConfig.from_dict(
        {
            "task_name": "t",
            "start_urls": ["https://www.bilibili.com/video/BV1xx411c7mD"],
            "allowed_domains": ["bilibili.com"],
            "video": {"enabled": True, "adapter": {"bilibili": True}},
            "audio": {"enabled": False},
            "image": {"enabled": False},
        }
    )
    task_id = store.upsert_task(task.task_name, "{}")
    store.add_media(
        MediaRecord(
            task_id=task_id,
            source_url="https://www.bilibili.com/video/BV1xx411c7mD",
            parent_url=None,
            media_url="https://cdn.test/old-video.mp4",
            media_hash=media_hash("https://cdn.test/old-video.mp4"),
            media_type="video",
            format="mp4",
            height=720,
            source_kind="adapter:bilibili",
        )
    )
    row = store.list_media(task_id)[0]

    captured: dict = {}
    monkeypatch.setattr(
        sc,
        "refresh_video",
        lambda task, row, fetcher: MediaCandidate(
            url="https://cdn.test/fresh-video.mp4",
            kind="video",
            format="mp4",
            height=720,
            extra={"headers": {"Referer": "https://www.bilibili.com/"}},
        ),
    )

    def fake_download_video(fetcher, url, target, **kwargs):
        captured["url"] = url
        path = tmp_path / "v.mp4"
        path.write_bytes(b"v")
        return path, 1

    monkeypatch.setattr(sc, "download_video", fake_download_video)
    collector = sc.SubpageCollector(task=task, store=store, task_id=task_id, fetcher=object())
    collector._download_media(row, "video", sc.SubpageStats())

    assert captured["url"] == "https://cdn.test/fresh-video.mp4"
    assert store.get_media_by_id(int(row["id"]))["media_url"] == "https://cdn.test/fresh-video.mp4"
    store.close()
