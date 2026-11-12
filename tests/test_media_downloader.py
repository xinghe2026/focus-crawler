"""媒体批量下载测试：并发、去重、跳过、失败、暂停 / 停止。"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from focus_crawler.core.media_common import guess_format
from focus_crawler.core.media_downloader import MediaDownloader, download_to_target
from focus_crawler.core.storage import Storage
from focus_crawler.models.media import MediaRecord
from focus_crawler.utils.hash import media_hash

SOURCE = "http://fixture.test/sub/1.html"
IMAGES = [
    "http://fixture.test/media/a.jpg",
    "http://fixture.test/media/b.png",
    "http://fixture.test/media/c.jpg",
]


def add_media(store, task_id: int, urls: list[str], media_type: str = "image") -> list[int]:
    ids: list[int] = []
    for url in urls:
        record = MediaRecord(
            task_id=task_id,
            source_url=SOURCE,
            parent_url="http://fixture.test/entry.html",
            media_url=url,
            media_hash=media_hash(url),
            media_type=media_type,
            format=guess_format(url),
        )
        assert store.add_media(record)
        ids.append(int(store.get_media(task_id, record.media_hash)["id"]))
    return ids


def make_downloader(store, task_id, fetcher, tmp_path, **kwargs) -> MediaDownloader:
    return MediaDownloader(
        store=store,
        task_id=task_id,
        task_name="测试任务",
        fetcher=fetcher,
        media_type="image",
        media_root=tmp_path,
        download_fn=download_to_target,
        pause_interval=0.05,
        **kwargs,
    )


def test_download_all_images(fetcher, store, task_id, tmp_path):
    add_media(store, task_id, IMAGES)
    summary = make_downloader(store, task_id, fetcher, tmp_path, concurrency=3).run()

    assert summary.as_dict() == {"total": 3, "downloaded": 3, "skipped": 0, "failed": 0, "stopped": False}
    rows = store.list_media(task_id, "image")
    assert all(row["status"] == "done" for row in rows)
    sizes = {Path(row["local_path"]).stat().st_size for row in rows}
    assert sizes == {len(b"JPEG-DATA-A"), len(b"PNG-DATA-B"), len(b"JPEG-DATA-C")}
    for row in rows:
        assert Path(row["local_path"]).parent.parent.parent.name == "images"
        assert row["file_size"] == Path(row["local_path"]).stat().st_size


def test_rerun_skips_done_and_keeps_files(fetcher, store, task_id, tmp_path):
    add_media(store, task_id, IMAGES)
    make_downloader(store, task_id, fetcher, tmp_path).run()
    paths_before = {row["local_path"] for row in store.list_media(task_id, "image")}

    summary = make_downloader(store, task_id, fetcher, tmp_path).run()
    assert summary.downloaded == 0
    assert summary.skipped == 3
    assert {row["local_path"] for row in store.list_media(task_id, "image")} == paths_before


def test_same_basename_does_not_collide(fetcher, store, task_id, tmp_path):
    urls = ["http://fixture.test/media/a.jpg", "http://fixture.test/media/a.jpg?v=2"]
    add_media(store, task_id, urls)
    summary = make_downloader(store, task_id, fetcher, tmp_path).run()
    assert summary.downloaded == 2
    names = {Path(row["local_path"]).name for row in store.list_media(task_id, "image")}
    assert len(names) == 2
    assert "a.jpg" in names


def test_failure_classified_and_recorded(fetcher, store, task_id, tmp_path):
    add_media(store, task_id, ["http://fixture.test/media/missing.jpg"])
    summary = make_downloader(store, task_id, fetcher, tmp_path).run()

    assert summary.failed == 1
    row = store.list_media(task_id, "image")[0]
    assert row["status"] == "failed"
    assert row["error"]
    failures = store.list_failures(task_id)
    assert failures[0]["failure_type"] == "http_404"


def test_missing_local_file_is_redownloaded(fetcher, store, task_id, tmp_path):
    add_media(store, task_id, [IMAGES[0]])
    row = store.list_media(task_id, "image")[0]
    store.update_media(row["id"], status="done", local_path=str(tmp_path / "gone.jpg"))

    summary = make_downloader(store, task_id, fetcher, tmp_path).run()
    assert summary.downloaded == 1
    assert Path(store.list_media(task_id, "image")[0]["local_path"]).exists()


def test_stop_leaves_remaining_pending(fetcher, store, task_id, tmp_path):
    urls = IMAGES + ["http://fixture.test/media/d.jpg"]
    add_media(store, task_id, urls)
    state = {"stop": False}

    def progress(done: int, total: int) -> None:
        if done >= 3:
            state["stop"] = True

    downloader = make_downloader(
        store,
        task_id,
        fetcher,
        tmp_path,
        concurrency=1,
        on_progress=progress,
        should_stop=lambda: state["stop"],
    )
    summary = downloader.run()

    assert summary.stopped is True
    assert summary.downloaded == 3
    statuses = [row["status"] for row in store.list_media(task_id, "image")]
    assert statuses.count("done") == 3
    assert statuses.count("discovered") == 1


def test_pause_blocks_then_resumes(fetcher, store, task_id, tmp_path):
    add_media(store, task_id, IMAGES)
    state = {"pause": True, "summary": None}

    def work() -> None:
        local_store = Storage(store.db_path)
        try:
            downloader = make_downloader(
                local_store,
                task_id,
                fetcher,
                tmp_path,
                concurrency=2,
                should_pause=lambda: state["pause"],
            )
            state["summary"] = downloader.run()
        finally:
            local_store.close()

    thread = threading.Thread(target=work)
    thread.start()
    time.sleep(0.2)
    assert thread.is_alive()
    assert state["summary"] is None

    state["pause"] = False
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert state["summary"].downloaded == 3
