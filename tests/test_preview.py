"""预览测试：链接发现 + 字段命中率 + 媒体统计。"""

from __future__ import annotations

from focus_crawler.core.preview import preview_task


def test_preview_summary(task_config, fetcher):
    result = preview_task(task_config, fetcher)
    assert result.ok is True
    assert result.discovered_links == 3
    assert len(result.link_examples) == 3
    assert len(result.subpages) == 3

    first = result.subpages[0]
    assert first.ok is True
    assert first.hits["title"] is True
    assert first.images == 2
    assert first.videos == 1
    assert first.audios == 1

    assert result.fields["title"]["hit_rate"] == 1.0
    assert result.fields["date"]["hit_rate"] == 1.0
    assert result.media_total == {"image": 4, "video": 2, "audio": 1}


def test_preview_entry_failure(task_config, tmp_path):
    from focus_crawler.core.fetcher import Fetcher
    import httpx

    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404, text="no")))
    fetcher = Fetcher(
        retry=0,
        delay_ms=(0, 0),
        respect_robots=False,
        client=client,
        sleep=lambda _s: None,
        random_uniform=lambda low, high: low,
    )
    result = preview_task(task_config, fetcher)
    assert result.ok is False
    assert result.error is not None
