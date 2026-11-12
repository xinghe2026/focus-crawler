"""Playwright 动态媒体嗅探测试：URL 分类、缺失依赖提示、调度器集成与回退。"""

from __future__ import annotations

import json

import pytest

from focus_crawler.core import dynamic_sniffer
from focus_crawler.core.dynamic_sniffer import DynamicPage, DynamicSniffError, classify_media_url
from focus_crawler.core.preview import preview_task
from focus_crawler.core.scheduler import Scheduler
from focus_crawler.core.subpage_collector import SubpageCollector


@pytest.mark.parametrize(
    ("url", "resource_type", "content_type", "expected"),
    [
        ("https://cdn.test/v/video.m3u8", "xhr", None, "video"),
        ("https://cdn.test/audio/stream.m3u8", "xhr", None, "audio"),
        ("https://cdn.test/music/song.mpd", "fetch", None, "audio"),
        ("https://cdn.test/v/manifest.mpd", "fetch", None, "video"),
        ("https://cdn.test/media/a.mp4", "media", None, "video"),
        ("https://cdn.test/media/a.m4a", "media", None, "audio"),
        ("https://cdn.test/stream/1", "media", "audio/mpeg", "audio"),
        ("https://cdn.test/stream/2", "media", "video/mp4", "video"),
        ("https://cdn.test/media/seg.ts", "media", "video/mp2t", None),
        ("https://cdn.test/media/seg.m4s", "media", None, None),
        ("https://cdn.test/img/a.jpg", "image", "image/jpeg", None),
        ("https://cdn.test/app.js", "script", "application/javascript", None),
    ],
)
def test_classify_media_url(url, resource_type, content_type, expected):
    assert classify_media_url(url, resource_type, content_type) == expected


def test_sniff_page_without_playwright(task_config, monkeypatch):
    monkeypatch.setattr(dynamic_sniffer, "playwright_available", lambda: False)
    with pytest.raises(DynamicSniffError) as info:
        dynamic_sniffer.sniff_page("http://fixture.test/sub/1.html", task_config)
    assert info.value.failure_type == "playwright_missing"
    assert "playwright" in str(info.value)


def make_fake_sniffer(video_suffix="/dynamic/v.m3u8", audio_suffix="/dynamic/a.mp3"):
    def fake_sniff(url, task):
        html = "<html><body></body></html>"
        if url.endswith("/entry.html"):
            html = (
                "<html><body>"
                '<a href="/sub/1.html">1</a>'
                '<a href="/sub/2.html">2</a>'
                '<a href="/sub/3.html">3</a>'
                "</body></html>"
            )
        return DynamicPage(
            html=html,
            status_code=200,
            headers={"Referer": url},
            video_urls=[url + video_suffix],
            audio_urls=[url + audio_suffix],
        )

    return fake_sniff


def test_scheduler_collects_dynamic_media(task_config, fetcher, store, task_id):
    task_config.subpage_collect.render_for_media = True
    scheduler = Scheduler(
        task_config,
        store,
        task_id,
        fetcher,
        dynamic_sniffer=make_fake_sniffer(),
    )
    scheduler.seed()
    stats = scheduler.run()

    assert stats.pages == 4  # 入口页静态 + 3 个子页动态
    assert stats.discovered == 3
    videos = store.list_media(task_id, "video")
    audios = store.list_media(task_id, "audio")
    assert len(videos) == 3
    assert len(audios) == 3
    assert all(row["media_url"].endswith("/dynamic/v.m3u8") for row in videos)
    assert all(row["media_url"].endswith("/dynamic/a.mp3") for row in audios)
    assert all(row["headers"] for row in videos + audios)
    assert len(store.list_media(task_id, "image")) == 0


def test_scheduler_dynamic_respects_max_subpages(task_config, fetcher, store, task_id):
    task_config.subpage_collect.render_for_media = True
    task_config.subpage_collect.max_subpages = 1
    scheduler = Scheduler(
        task_config,
        store,
        task_id,
        fetcher,
        dynamic_sniffer=make_fake_sniffer(),
    )
    scheduler.seed()
    scheduler.run()

    assert scheduler._dynamic_pages == 1
    dynamic_videos = [row for row in store.list_media(task_id, "video") if row["media_url"].endswith(".m3u8")]
    assert len(dynamic_videos) <= 1


def test_scheduler_falls_back_when_sniff_fails(task_config, fetcher, store, task_id):
    def broken(url, task):
        raise DynamicSniffError("模拟 Playwright 启动失败")

    task_config.subpage_collect.render_for_media = True
    scheduler = Scheduler(task_config, store, task_id, fetcher, dynamic_sniffer=broken)
    scheduler.seed()
    stats = scheduler.run()

    assert stats.pages == 4
    assert scheduler._dynamic_pages == 0
    assert store.count_media(task_id, "image") == 3
    assert store.count_media(task_id, "video") == 2
    assert store.count_media(task_id, "audio") == 1


def test_preview_uses_dynamic_for_seed_and_subpages(task_config, fetcher):
    task_config.subpage_collect.render_for_media = True
    result = preview_task(task_config, fetcher, dynamic_sniffer=make_fake_sniffer())

    assert result.ok is True
    assert result.discovered_links == 3
    assert len(result.subpages) == 3
    assert all(item.ok for item in result.subpages)
    assert result.media_total == {"image": 0, "video": 3, "audio": 3}


GALLERY_HTML = """
<html><body>
  <img src="/img/spacer.gif" data-lazy="/img/p1.jpg" width="800" height="600">
  <a data-mfp-src="/img/p2.jpg" href="javascript:void(0);"></a>
</body></html>
"""


def test_collector_extracts_dynamic_gallery(task_config, fetcher, store, task_id):
    task_config.image.exclude_keywords = ["spacer"]
    page = DynamicPage(html=GALLERY_HTML, status_code=200, headers={"Referer": "https://example.com"})
    collector = SubpageCollector(task_config, store, task_id, fetcher=fetcher)
    stats = collector.collect("https://example.com/post", "<html></html>", dynamic=page)

    assert stats.images == 2
    rows = store.list_media(task_id, "image")
    assert {row["media_url"] for row in rows} == {
        "https://example.com/img/p1.jpg",
        "https://example.com/img/p2.jpg",
    }
    assert all(json.loads(row["headers"])["Referer"] == "https://example.com/post" for row in rows)


def test_scheduler_collects_seed_as_detail(task_config, fetcher, store, task_id):
    task_config.crawl.max_depth = 0
    task_config.subpage_collect.render_for_media = True
    task_config.image.exclude_keywords = ["spacer"]

    def fake(url, task):
        return DynamicPage(html=GALLERY_HTML, status_code=200)

    scheduler = Scheduler(task_config, store, task_id, fetcher, dynamic_sniffer=fake)
    scheduler.seed()
    stats = scheduler.run()

    assert stats.pages == 1
    assert stats.discovered == 0
    assert store.count_media(task_id, "image") == 2


def test_preview_single_page_includes_seed(task_config, fetcher):
    task_config.crawl.max_depth = 0
    task_config.subpage_collect.render_for_media = True
    result = preview_task(task_config, fetcher, dynamic_sniffer=make_fake_sniffer())

    assert result.ok is True
    assert result.subpages[0].url == task_config.start_urls[0]
