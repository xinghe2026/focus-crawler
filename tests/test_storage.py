"""SQLite 存储测试：去重、断点、媒体。"""

from __future__ import annotations

from focus_crawler.models.media import MediaRecord
from focus_crawler.utils.hash import media_hash, url_hash


def test_url_dedup_and_queue(store, task_id):
    assert store.add_url(task_id, "https://example.com/a", None, 0, "seed") is True
    assert store.add_url(task_id, "https://example.com/a#frag", None, 0, "seed") is False
    assert store.add_url(task_id, "https://example.com/b", "https://example.com/a", 1, "discovered") is True

    pending = store.next_pending_urls(task_id, limit=10)
    assert [row["url"] for row in pending] == ["https://example.com/a", "https://example.com/b"]

    store.update_url(pending[0]["id"], "done", 200, content_hash="hash")
    assert store.count_urls(task_id, status="done") == 1

    store.update_url(pending[1]["id"], "failed", 500, "boom")
    assert store.reset_failed_urls(task_id) == 1
    assert store.count_urls(task_id, status="pending") == 1


def test_items_upsert(store, task_id):
    store.add_item(task_id, "https://example.com/a", {"title": "old"}, "h1")
    store.add_item(task_id, "https://example.com/a", {"title": "new"}, "h2")
    assert store.count_items(task_id) == 1
    rows = store.list_items(task_id)
    assert '"new"' in rows[0]["data_json"]


def test_media_dedup_and_stats(store, task_id):
    url = "https://example.com/a.jpg"
    record = MediaRecord(
        task_id=task_id,
        source_url="https://example.com/page1",
        parent_url="https://example.com/entry",
        media_url=url,
        media_hash=media_hash(url),
        media_type="image",
        format="jpg",
    )
    assert store.add_media(record) is True
    duplicate = MediaRecord(
        task_id=task_id,
        source_url="https://example.com/page2",
        parent_url="https://example.com/entry",
        media_url=url,
        media_hash=media_hash(url),
        media_type="image",
        format="jpg",
    )
    assert store.add_media(duplicate) is False
    assert store.count_media(task_id, "image") == 1
    assert store.stats(task_id)["media"] == {"image": 1}


def test_failure_and_parser_runs(store, task_id):
    store.add_failure(task_id, "https://example.com/x", "超时", "timeout", 1)
    assert store.failure_stats(task_id) == {"timeout": 1}
    store.add_parser_run(task_id, "https://example.com/x", "detail.py", "error", "custom_parser_timeout", "超时", 120)
    assert len(store.list_parser_runs(task_id)) == 1


def test_task_upsert_by_name(store, task_config):
    first = store.upsert_task(task_config.task_name, "{}")
    second = store.upsert_task(task_config.task_name, '{"updated": true}')
    assert first == second
    assert store.get_task(first)["config_json"] == '{"updated": true}'


def test_delete_task_removes_related_rows(store, task_id):
    keep_id = store.upsert_task("keep_task", "{}")
    store.add_url(task_id, "https://example.com/a", None, 0, "seed")
    store.add_url(keep_id, "https://example.com/keep", None, 0, "seed")
    store.add_item(task_id, "https://example.com/a", {"title": "x"}, "h")
    store.add_failure(task_id, "https://example.com/a", "boom", "http_error", 1)
    store.add_parser_run(task_id, "https://example.com/a", "detail.py", "ok", None, None, 10)
    store.add_log(task_id, "info", "测试日志")

    url = "https://example.com/a.jpg"
    record = MediaRecord(
        task_id=task_id,
        source_url="https://example.com/page1",
        parent_url=None,
        media_url=url,
        media_hash=media_hash(url),
        media_type="image",
        format="jpg",
    )
    store.add_media(record)
    media_id = int(store.get_media(task_id, media_hash(url))["id"])
    store.upsert_media_segment(media_id, 0, "https://example.com/seg.ts", None, "done")

    store.delete_task(task_id)

    assert store.get_task(task_id) is None
    assert store.get_task(keep_id) is not None
    assert store.count_urls(task_id) == 0
    assert store.count_urls(keep_id) == 1
    assert store.count_items(task_id) == 0
    assert store.list_media(task_id, "image") == []
    assert store.list_media_segments(media_id) == []
    assert store.failure_stats(task_id) == {}
    assert store.list_parser_runs(task_id) == []


def test_clear_media_file_resets_record(store, task_id):
    url = "https://example.com/a.jpg"
    record = MediaRecord(
        task_id=task_id,
        source_url="https://example.com/page1",
        parent_url=None,
        media_url=url,
        media_hash=media_hash(url),
        media_type="image",
        format="jpg",
        status="done",
        local_path="C:/tmp/a.jpg",
        file_size=123,
    )
    store.add_media(record)
    media_id = int(store.get_media(task_id, media_hash(url))["id"])
    store.upsert_media_segment(media_id, 0, "https://example.com/seg.ts", "C:/tmp/seg.ts", "done")

    store.clear_media_file(media_id)

    row = store.get_media_by_id(media_id)
    assert row["status"] == "discovered"
    assert row["local_path"] is None
    assert row["file_size"] is None
    assert store.list_media_segments(media_id) == []


def test_url_hash_is_normalized():
    assert url_hash("https://Example.com/a#x") == url_hash("https://example.com/a")
