"""子页多类型采集 + 调度器验收测试。"""

from __future__ import annotations

from pathlib import Path

from focus_crawler.core.scheduler import Scheduler


def run_site(task, fetcher, store, task_id):
    scheduler = Scheduler(task, store, task_id, fetcher)
    scheduler.seed()
    return scheduler.run()


def test_discover_and_collect_all_types(task_config, fetcher, store, task_id):
    stats = run_site(task_config, fetcher, store, task_id)

    assert stats.pages == 4  # 入口页 + 3 个子页
    assert stats.discovered == 3

    discovered = [
        row for row in store.list_items(task_id)
    ]
    assert len(discovered) == 3
    assert store.count_urls(task_id, page_type="discovered") == 3

    images = store.list_media(task_id, "image")
    videos = store.list_media(task_id, "video")
    audios = store.list_media(task_id, "audio")
    assert len(images) == 3  # a.jpg 被子页 1、3 引用，去重后 3 张
    assert len(videos) == 2
    assert len(audios) == 1
    assert all(row["status"] == "discovered" for row in images + videos + audios)

    for row in images + videos + audios:
        assert row["source_url"].startswith("http://fixture.test/sub/")
        assert row["parent_url"] == "http://fixture.test/entry.html"

    image_urls = {row["media_url"] for row in images}
    assert "http://fixture.test/media/a.jpg" in image_urls
    assert store.count_media(task_id, "image") == 3

    items = [row for row in store.list_items(task_id)]
    assert items[0]["url"].startswith("http://fixture.test/sub/")
    assert '"title"' in items[0]["data_json"]


def test_types_image_only(task_config, fetcher, store, task_id):
    task_config.subpage_collect.types = ["image"]
    stats = run_site(task_config, fetcher, store, task_id)
    assert stats.items == 0
    assert store.count_items(task_id) == 0
    assert store.count_media(task_id, "image") == 3
    assert store.count_media(task_id, "video") == 0
    assert store.count_media(task_id, "audio") == 0


def test_auto_download_images_only(task_config, fetcher, store, task_id):
    cfg = task_config.subpage_collect
    cfg.media_mode = "auto_download"
    cfg.auto_download_images = True
    cfg.auto_download_videos = False
    cfg.auto_download_audios = False
    cfg.copyright_confirm = False

    run_site(task_config, fetcher, store, task_id)

    images = store.list_media(task_id, "image")
    assert all(row["status"] == "done" for row in images)
    for row in images:
        assert row["local_path"] and Path(row["local_path"]).exists()

    assert all(row["status"] == "discovered" for row in store.list_media(task_id, "video"))
    assert all(row["status"] == "discovered" for row in store.list_media(task_id, "audio"))


def test_copyright_not_confirmed_blocks_download(task_config, fetcher, store, task_id):
    cfg = task_config.subpage_collect
    cfg.media_mode = "auto_download"
    cfg.auto_download_images = True
    cfg.copyright_confirm = True

    run_site(task_config, fetcher, store, task_id)
    assert all(row["status"] == "discovered" for row in store.list_media(task_id, "image"))
    assert all(row["local_path"] is None for row in store.list_media(task_id, "image"))


def test_max_subpages_limits_collection(task_config, fetcher, store, task_id):
    task_config.subpage_collect.max_subpages = 1
    run_site(task_config, fetcher, store, task_id)
    assert store.count_items(task_id) == 1
    assert store.count_media(task_id, "image") == 2
    assert store.count_media(task_id, "video") == 1


def test_resume_does_not_recrawl(task_config, fetcher, store, task_id):
    first = run_site(task_config, fetcher, store, task_id)
    assert first.pages == 4
    items_before = store.count_items(task_id)
    media_before = store.count_media(task_id)

    second = run_site(task_config, fetcher, store, task_id)
    assert second.pages == 0
    assert store.count_items(task_id) == items_before
    assert store.count_media(task_id) == media_before


def test_retry_failed_urls(task_config, fetcher, store, task_id):
    scheduler = Scheduler(task_config, store, task_id, fetcher)
    scheduler.seed()
    scheduler.run()

    seed_row = store.get_url(task_id, "http://fixture.test/entry.html")
    assert seed_row["status"] == "done"
    store.update_url(seed_row["id"], "failed", 500, "模拟失败")
    assert store.reset_failed_urls(task_id) == 1

    retried = Scheduler(task_config, store, task_id, fetcher)
    stats = retried.run()
    assert stats.pages >= 1
    assert store.get_url(task_id, "http://fixture.test/entry.html")["status"] == "done"
