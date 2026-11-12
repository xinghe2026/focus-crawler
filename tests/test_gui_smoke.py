"""GUI 冒烟测试：离屏创建对话框与主窗口，验证配置加载与校验。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PySide6 = pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QAbstractItemView, QApplication, QMessageBox  # noqa: E402

from focus_crawler.config.loader import load_config  # noqa: E402
from focus_crawler.config.schema import TaskConfig  # noqa: E402
from focus_crawler.core.storage import Storage  # noqa: E402
from focus_crawler.gui import settings  # noqa: E402
from focus_crawler.gui.dialogs import SettingsDialog  # noqa: E402
from focus_crawler.gui.main_window import MainWindow, _parse_index_ranges  # noqa: E402
from focus_crawler.gui.task_editor import TaskEditorDialog, default_task_raw  # noqa: E402
from focus_crawler.gui.workers import MediaDownloadWorker, RunWorker  # noqa: E402
from focus_crawler.models.media import MediaRecord  # noqa: E402
from focus_crawler.utils.hash import media_hash  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    # 显式回收窗口与事件队列，避免进程退出时 Qt 原生对象析构崩溃
    app.closeAllWindows()
    app.processEvents()
    for widget in app.topLevelWidgets():
        widget.deleteLater()
    app.processEvents()
    app.quit()


def test_editor_roundtrip(qapp, task_config):
    dialog = TaskEditorDialog(task_config.to_dict())
    assert dialog.config is None
    dialog.accept()
    assert dialog.config is not None
    assert dialog.config.task_name == task_config.task_name
    assert dialog.config.start_urls == task_config.start_urls
    assert set(dialog.config.detail.fields) == set(task_config.detail.fields)


def test_editor_reports_errors(qapp):
    dialog = TaskEditorDialog({"task_name": "", "start_urls": [], "allowed_domains": []})
    dialog.accept()
    assert dialog.config is None
    assert dialog.error_list.count() > 0
    assert dialog.error_list.isVisibleTo(dialog) is True


def test_main_window_lists_tasks(qapp, task_config):
    store = Storage(settings.db_file())
    task_id = store.upsert_task(task_config.task_name, json.dumps(task_config.to_dict(), ensure_ascii=False))
    store.close()
    window = MainWindow()
    try:
        assert window.task_table.rowCount() == 1
        assert window.store.get_task(task_id) is not None
        window.task_table.selectRow(0)
        assert window.monitor_task_label.text().startswith("当前任务：")
        assert "页面：" in window.monitor_stats.text()
        assert window.delete_button.isEnabled() is True
        assert window.refresh_button.isEnabled() is True
    finally:
        window.close()
        window.deleteLater()


def test_refresh_reloads_external_task(qapp):
    window = MainWindow()
    try:
        before = window.task_table.rowCount()
        store = Storage(settings.db_file())
        store.upsert_task("refresh_external_task", "{}")
        store.close()
        window._on_refresh_tasks()
        assert window.task_table.rowCount() == before + 1
    finally:
        window.close()
        window.deleteLater()


def test_editor_saves_detail_fields(qapp):
    raw = json.loads((FIXTURES_DIR / "site_task.json").read_text(encoding="utf-8"))
    dialog = TaskEditorDialog(raw)
    dialog.accept()
    assert dialog.config is not None
    assert dialog.raw["detail"]["fields"]["title"]["selector"] == "h1"


def test_run_worker_resume_state():
    worker = RunWorker(load_config(FIXTURES_DIR / "site_task.json"), "dummy.db")
    worker.pause()
    assert worker.paused is True
    worker.resume()
    assert worker.paused is False
    worker.stop()
    assert worker.paused is False


def test_media_worker_resume_state():
    worker = MediaDownloadWorker(load_config(FIXTURES_DIR / "site_task.json"), "dummy.db", 1, [1])
    worker.pause()
    assert worker.paused is True
    worker.resume()
    assert worker.paused is False
    worker.stop()
    assert worker.paused is False


def test_main_window_image_tab(qapp, task_config):
    store = Storage(settings.db_file())
    task_id = store.upsert_task(task_config.task_name, json.dumps(task_config.to_dict(), ensure_ascii=False))
    record = MediaRecord(
        task_id=task_id,
        source_url="http://fixture.test/sub/1.html",
        parent_url="http://fixture.test/entry.html",
        media_url="http://fixture.test/media/a.jpg",
        media_hash=media_hash("http://fixture.test/media/a.jpg"),
        media_type="image",
        format="jpg",
    )
    store.add_media(record)
    media_id = int(store.get_media(task_id, record.media_hash)["id"])
    video = MediaRecord(
        task_id=task_id,
        source_url="http://fixture.test/sub/1.html",
        parent_url="http://fixture.test/entry.html",
        media_url="http://fixture.test/media/v.m3u8",
        media_hash=media_hash("http://fixture.test/media/v.m3u8"),
        media_type="video",
        format="m3u8",
        quality="720p",
        duration=12.5,
        width=1280,
        height=720,
    )
    store.add_media(video)
    video_id = int(store.get_media(task_id, video.media_hash)["id"])
    audio = MediaRecord(
        task_id=task_id,
        source_url="http://fixture.test/sub/1.html",
        parent_url="http://fixture.test/entry.html",
        media_url="http://fixture.test/media/a1.mp3",
        media_hash=media_hash("http://fixture.test/media/a1.mp3"),
        media_type="audio",
        format="mp3",
        quality="测试音频",
        duration=95.0,
        bitrate=128000,
    )
    store.add_media(audio)
    audio_id = int(store.get_media(task_id, audio.media_hash)["id"])
    store.close()

    window = MainWindow()
    try:
        assert window.tabs.count() == 6
        window.task_table.selectRow(0)
        assert window.image_table.rowCount() == 1
        assert window.image_source_combo.count() == 2
        window.image_filter_edit.setText("a.jpg")
        assert window.image_table.rowCount() == 0
        window.image_filter_edit.clear()
        assert window.image_table.rowCount() == 1
        window._set_all_images(Qt.CheckState.Checked)
        assert window._checked_media_ids() == [media_id]
        assert window.image_pause_button.isEnabled() is False
        assert window.video_table.rowCount() == 1
        window.video_range_edit.setText("1")
        window.video_range_button.click()
        assert window._checked_video_ids() == [video_id]
        window._set_all_videos(Qt.CheckState.Checked)
        assert window._checked_video_ids() == [video_id]
        assert window.video_pause_button.isEnabled() is False
        assert window.audio_table.rowCount() == 1
        window._set_all_audios(Qt.CheckState.Checked)
        assert window._checked_audio_ids() == [audio_id]
        assert window.audio_pause_button.isEnabled() is False
        assert window.image_location_button.text() == "打开文件位置"
        assert window.image_delete_file_button.text() == "删除文件"
        assert window.video_location_button.isEnabled() is True
        assert window.audio_location_button.isEnabled() is True
        assert window.copyright_check.isChecked() is True
        assert window.image_table.item(0, 0).checkState() == Qt.CheckState.Checked
        assert window.video_table.item(0, 0).checkState() == Qt.CheckState.Checked
        assert window.audio_table.item(0, 0).checkState() == Qt.CheckState.Checked
        assert window.image_table.textElideMode() == Qt.TextElideMode.ElideRight
        assert window.image_table.columnWidth(2) == 200
        assert window.image_table.columnWidth(6) == 280
        assert window.audio_table.columnWidth(9) == 280
        assert window.image_table.horizontalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel
        assert window.image_table.verticalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel
    finally:
        window.close()
        window.deleteLater()


def test_settings_dialog_media_root(qapp):
    dialog = SettingsDialog()
    try:
        assert dialog.media_root_edit.text() == str(settings.media_root())
        assert dialog.ffmpeg_edit.text() == settings.ffmpeg_path()
    finally:
        dialog.deleteLater()


def test_parse_index_ranges():
    assert _parse_index_ranges("1-3, 5", 10) == {1, 2, 3, 5}
    assert _parse_index_ranges("3-1", 5) == {1, 2, 3}
    assert _parse_index_ranges("8-20", 5) == set()
    assert _parse_index_ranges("0, 6", 5) == set()
    assert _parse_index_ranges("", 5) == set()


def test_delete_media_file(qapp, tmp_path, monkeypatch):
    file = tmp_path / "photo.jpg"
    file.write_bytes(b"JPEG-DATA")
    store = Storage(settings.db_file())
    task_id = store.upsert_task("delete_file_task", "{}")
    record = MediaRecord(
        task_id=task_id,
        source_url="https://example.com/page",
        parent_url=None,
        media_url="https://example.com/photo.jpg",
        media_hash=media_hash("https://example.com/photo.jpg"),
        media_type="image",
        format="jpg",
        status="done",
        local_path=str(file),
        file_size=file.stat().st_size,
    )
    store.add_media(record)
    media_id = int(store.get_media(task_id, record.media_hash)["id"])
    store.close()

    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    window = MainWindow()
    try:
        window.task_table.selectRow(0)
        window.image_table.selectRow(0)
        window._delete_media_file("image", window.image_table)

        assert not file.exists()
        row = window.store.get_media_by_id(media_id)
        assert row["status"] == "discovered"
        assert row["local_path"] is None
        assert window.image_table.rowCount() == 1
    finally:
        window.close()
        window.deleteLater()


def _task_raw(name: str) -> dict:
    return {
        "task_name": name,
        "start_urls": ["https://example.com/list.html"],
        "allowed_domains": ["example.com"],
    }


def test_default_task_raw_supports_single_page_media(qapp):
    raw = default_task_raw()
    assert raw["crawl"]["max_depth"] == 0
    assert raw["crawl"]["respect_robots"] is False
    assert raw["subpage_collect"]["enabled"] is True
    assert set(raw["subpage_collect"]["types"]) == {"data", "image", "video", "audio"}
    assert raw["image"]["enabled"] is True
    assert raw["video"]["enabled"] is True
    assert raw["audio"]["enabled"] is True
    assert raw["video"]["adapter"]["bilibili"] is True


def test_save_task_appends_suffix_for_duplicate_name(qapp):
    window = MainWindow()
    try:
        first = TaskConfig.from_dict(_task_raw("重名任务"))
        window._save_task(first)
        second = TaskConfig.from_dict(_task_raw("重名任务"))
        window._save_task(second)
        third = TaskConfig.from_dict(_task_raw("重名任务"))
        window._save_task(third)
        assert second.task_name == "重名任务（1）"
        assert third.task_name == "重名任务（2）"
        names = {row["name"] for row in window.store.list_tasks()}
        assert {"重名任务", "重名任务（1）", "重名任务（2）"} <= names
        assert window.task_table.rowCount() == 3
    finally:
        window.close()
        window.deleteLater()


def test_save_edit_keeps_task_identity(qapp):
    window = MainWindow()
    try:
        first = TaskConfig.from_dict(_task_raw("任务甲"))
        first_id = window._save_task(first)
        second = TaskConfig.from_dict(_task_raw("任务乙"))
        second_id = window._save_task(second)
        renamed = TaskConfig.from_dict(_task_raw("任务乙"))
        window._save_task(renamed, task_id=first_id)
        assert renamed.task_name == "任务乙（1）"
        assert window.store.get_task(first_id)["name"] == "任务乙（1）"
        assert window.store.get_task(second_id)["name"] == "任务乙"
        assert window.task_table.rowCount() == 2
    finally:
        window.close()
        window.deleteLater()


def test_batch_delete_tasks(qapp, monkeypatch):
    window = MainWindow()
    try:
        task_ids = []
        for name in ("批量一", "批量二"):
            task_ids.append(window._save_task(TaskConfig.from_dict(_task_raw(name))))
        window.task_table.selectAll()
        assert set(window._selected_task_ids()) == set(task_ids)
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
        )
        window._on_batch_delete_tasks()
        assert window.task_table.rowCount() == 0
        assert all(window.store.get_task(task_id) is None for task_id in task_ids)
    finally:
        window.close()
        window.deleteLater()
