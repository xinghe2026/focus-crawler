"""主窗口：任务列表、运行监控、日志、数据预览与导出。"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config.loader import load_config
from ..config.schema import ConfigError, TaskConfig
from ..core.exporter import export_excel
from ..core.image_crawler import filename_for_image
from ..core.storage import Storage
from ..utils.paths import media_dir, safe_task_name
from . import settings, theme
from .dialogs import FailureDialog, PreviewDialog, SettingsDialog
from .hud import StatusHud
from .task_editor import TaskEditorDialog
from .workers import MediaDownloadWorker, PreviewWorker, RunWorker

logger = logging.getLogger("focus_crawler.gui")

TASK_COLUMNS = ["任务名称", "起始 URL", "允许域名", "数据条数", "媒体发现", "更新时间"]
DATA_PREVIEW_LIMIT = 200
IMAGE_COLUMNS = ["选择", "缩略图", "文件", "尺寸", "状态", "来源页", "本地路径"]
VIDEO_COLUMNS = ["选择", "文件", "格式", "清晰度", "大小", "时长", "状态", "来源页", "本地路径"]
AUDIO_COLUMNS = ["选择", "文件", "标题", "格式", "时长", "码率", "大小", "状态", "来源页", "本地路径"]
IMAGE_WIDTHS = [48, 88, 200, 90, 160, 220, 280]
VIDEO_WIDTHS = [48, 200, 70, 110, 90, 80, 140, 220, 280]
AUDIO_WIDTHS = [48, 200, 180, 70, 80, 90, 90, 140, 220, 280]
MEDIA_STATUS_TEXT = {
    "discovered": "待下载",
    "downloading": "下载中",
    "done": "已下载",
    "failed": "失败",
    "skipped": "已跳过",
}
THUMB_SIZE = 72
CHECK_COLUMN_WIDTH = 76
THUMB_LIMIT = 300
VIDEO_CONCURRENCY = 2
COMMERCIAL_AUDIO_HOSTS = ("music.163.com", "y.music.163.com")


def _filter_tokens(text: str) -> list[str]:
    normalized = (
        (text or "")
        .replace("，", ",")
        .replace("；", ",")
        .replace(";", ",")
        .replace("\n", ",")
    )
    return [part.strip().lower() for part in normalized.split(",") if part.strip()]


def _parse_index_ranges(text: str, total: int) -> set[int]:
    """解析 1-10, 15 形式的序号范围，返回当前可见行的 1 基序号。"""
    normalized = (
        (text or "")
        .replace("，", ",")
        .replace("；", ",")
        .replace(";", ",")
        .replace("\n", ",")
        .replace("－", "-")
        .replace("–", "-")
    )
    chosen: set[int] = set()
    for chunk in normalized.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start_text, end_text = chunk.split("-", 1)
            try:
                start, end = int(start_text.strip()), int(end_text.strip())
            except ValueError:
                continue
            if start > end:
                start, end = end, start
            for number in range(max(1, start), min(total, end) + 1):
                chosen.add(number)
        else:
            try:
                number = int(chunk)
            except ValueError:
                continue
            if 1 <= number <= total:
                chosen.add(number)
    return chosen


def _checked_ids(table: QTableWidget) -> list[int]:
    """读取固定勾选列中已勾选的媒体 id。"""
    ids: list[int] = []
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        if item is not None and item.checkState() == Qt.CheckState.Checked:
            value = item.data(Qt.ItemDataRole.UserRole)
            if value is not None:
                ids.append(int(value))
    return ids


def _media_file_name(row: Any) -> str:
    local = row["local_path"]
    if local:
        return Path(local).name
    name = Path(urlsplit(row["media_url"]).path).name
    return name or str(row["media_url"])


def _is_file_filtered(row: Any, tokens: list[str]) -> bool:
    if not tokens:
        return False
    name = _media_file_name(row).lower()
    return any(token in name for token in tokens)


def _open_path_location(path: Path) -> None:
    target = Path(path)
    if target.exists():
        if sys.platform == "win32" and target.is_file():
            subprocess.Popen(["explorer.exe", f"/select,{os.path.normpath(str(target))}"])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent if target.is_file() else target)))
        return
    parent = target.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(parent)))


def _format_size(value: int | None) -> str:
    if not value:
        return ""
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


class _LogEmitter(QObject):
    message = Signal(str)


class QtLogHandler(logging.Handler):
    """把 logging 记录转发到界面日志面板。"""

    def __init__(self) -> None:
        super().__init__()
        self.emitter = _LogEmitter()
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.emitter.message.emit(self.format(record))
        except Exception:  # noqa: BLE001 - 日志不能影响主流程
            pass


class MainWindow(QMainWindow):
    POLL_INTERVAL_MS = 1000

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("FocusCrawler 配置驱动爬虫")
        self.resize(1120, 780)
        self.store = Storage(settings.db_file())
        self._run_thread: QThread | None = None
        self._run_worker: RunWorker | None = None
        self._preview_thread: QThread | None = None
        self._preview_worker: PreviewWorker | None = None
        self._media_thread: QThread | None = None
        self._media_worker: MediaDownloadWorker | None = None
        self._media_task_id: int | None = None
        self._media_active = False
        self._media_kind = "image"
        self._running_task_id: int | None = None

        self._log_handler = QtLogHandler()
        self._log_handler.emitter.message.connect(self._append_log)
        logging.getLogger("focus_crawler").addHandler(self._log_handler)

        self._build_ui()
        self._polish_tables()
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll_running_stats)
        self._load_tasks()

    # ---------- UI ----------
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        self.hud = StatusHud()
        outer.addWidget(self.hud)

        task_bar = QHBoxLayout()
        self.new_button = QPushButton("新建任务")
        self.edit_button = QPushButton("编辑")
        self.import_button = QPushButton("导入配置")
        self.export_config_button = QPushButton("导出配置")
        self.copy_button = QPushButton("复制任务")
        self.delete_button = QPushButton("删除任务")
        self.batch_delete_button = QPushButton("批量删除")
        self.refresh_button = QPushButton("刷新")
        self.settings_button = QPushButton("设置")
        for button in (
            self.new_button,
            self.edit_button,
            self.import_button,
            self.export_config_button,
            self.copy_button,
            self.delete_button,
            self.batch_delete_button,
            self.refresh_button,
        ):
            task_bar.addWidget(button)
        task_bar.addStretch(1)
        task_bar.addWidget(self.settings_button)
        outer.addLayout(task_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.task_table = QTableWidget(0, len(TASK_COLUMNS))
        self.task_table.setHorizontalHeaderLabels(TASK_COLUMNS)
        self.task_table.horizontalHeader().setStretchLastSection(True)
        self.task_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.task_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.task_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        splitter.addWidget(self.task_table)
        splitter.addWidget(self._build_tabs())
        splitter.setSizes([520, 600])
        outer.addWidget(splitter, 1)

        run_bar = QHBoxLayout()
        self.preview_button = QPushButton("规则预览")
        self.run_button = QPushButton("开始运行")
        self.pause_button = QPushButton("暂停")
        self.stop_button = QPushButton("停止")
        self.export_button = QPushButton("导出 Excel")
        self.failure_button = QPushButton("失败中心")
        self.copyright_check = QCheckBox("已确认媒体版权与合规用途")
        self.copyright_check.setChecked(True)
        for button in (
            self.preview_button,
            self.run_button,
            self.pause_button,
            self.stop_button,
            self.export_button,
            self.failure_button,
        ):
            run_bar.addWidget(button)
        run_bar.addStretch(1)
        run_bar.addWidget(self.copyright_check)
        outer.addLayout(run_bar)

        self.new_button.clicked.connect(self._on_new_task)
        self.edit_button.clicked.connect(self._on_edit_task)
        self.import_button.clicked.connect(self._on_import_config)
        self.export_config_button.clicked.connect(self._on_export_config)
        self.copy_button.clicked.connect(self._on_copy_task)
        self.delete_button.clicked.connect(self._on_delete_task)
        self.batch_delete_button.clicked.connect(self._on_batch_delete_tasks)
        self.refresh_button.clicked.connect(self._on_refresh_tasks)
        self.settings_button.clicked.connect(self._on_settings)
        self.preview_button.clicked.connect(self._on_preview)
        self.run_button.clicked.connect(self._on_run)
        self.pause_button.clicked.connect(self._on_pause)
        self.stop_button.clicked.connect(self._on_stop)
        self.export_button.clicked.connect(self._on_export_excel)
        self.failure_button.clicked.connect(self._on_failures)
        self.task_table.itemSelectionChanged.connect(self._on_selection_changed)

        self._set_running(False)
        self.statusBar().showMessage(f"数据目录：{settings.data_dir()}")

    def _build_tabs(self) -> QTabWidget:
        self.tabs = QTabWidget()

        monitor = QWidget()
        monitor_layout = QVBoxLayout(monitor)
        self.monitor_task_label = QLabel("当前任务：未选择")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.monitor_stats = QLabel("等待运行")
        self.monitor_stats.setWordWrap(True)
        self.monitor_stats.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        monitor_layout.addWidget(self.monitor_task_label)
        monitor_layout.addWidget(self.progress)
        monitor_layout.addWidget(self.monitor_stats)
        monitor_layout.addStretch(1)
        self.tabs.addTab(monitor, "运行监控")

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.tabs.addTab(self.log_view, "日志")

        data_page = QWidget()
        data_layout = QVBoxLayout(data_page)
        data_bar = QHBoxLayout()
        self.refresh_data_button = QPushButton("刷新")
        self.refresh_data_button.clicked.connect(self._refresh_data_preview)
        data_bar.addWidget(self.refresh_data_button)
        data_bar.addStretch(1)
        data_layout.addLayout(data_bar)
        self.data_table = QTableWidget(0, 0)
        self.data_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        data_layout.addWidget(self.data_table, 1)
        self.tabs.addTab(data_page, "数据预览")

        image_page = QWidget()
        image_layout = QVBoxLayout(image_page)
        image_bar = QHBoxLayout()
        image_bar.addWidget(QLabel("来源页："))
        self.image_source_combo = QComboBox()
        self.image_source_combo.setMinimumWidth(240)
        self.image_source_combo.currentIndexChanged.connect(self._refresh_image_table)
        image_bar.addWidget(self.image_source_combo, 1)
        image_bar.addWidget(QLabel("排除文件名："))
        self.image_filter_edit = QLineEdit()
        self.image_filter_edit.setPlaceholderText("如 0.jpg, cover")
        self.image_filter_edit.setMaximumWidth(200)
        self.image_filter_edit.setClearButtonEnabled(True)
        self.image_filter_edit.textChanged.connect(self._refresh_image_table)
        image_bar.addWidget(self.image_filter_edit)
        image_bar.addWidget(QLabel("序号："))
        self.image_range_edit = QLineEdit()
        self.image_range_edit.setPlaceholderText("1-10, 15")
        self.image_range_edit.setMaximumWidth(120)
        self.image_range_edit.setClearButtonEnabled(True)
        image_bar.addWidget(self.image_range_edit)
        self.image_range_button = QPushButton("按序号勾选")
        image_bar.addWidget(self.image_range_button)
        self.image_refresh_button = QPushButton("刷新")
        image_bar.addWidget(self.image_refresh_button)
        image_layout.addLayout(image_bar)

        image_action_bar = QHBoxLayout()
        self.image_select_all_button = QPushButton("全选")
        self.image_select_none_button = QPushButton("取消全选")
        self.image_download_button = QPushButton("下载选中")
        self.image_pause_button = QPushButton("暂停")
        self.image_stop_button = QPushButton("停止")
        self.image_location_button = QPushButton("打开文件位置")
        self.image_delete_file_button = QPushButton("删除文件")
        for button in (
            self.image_select_all_button,
            self.image_select_none_button,
            self.image_download_button,
            self.image_pause_button,
            self.image_stop_button,
            self.image_location_button,
            self.image_delete_file_button,
        ):
            image_action_bar.addWidget(button)
        image_action_bar.addStretch(1)
        image_layout.addLayout(image_action_bar)

        image_progress_bar = QHBoxLayout()
        self.image_progress = QProgressBar()
        self.image_progress.setRange(0, 1)
        self.image_progress.setValue(0)
        self.image_status_label = QLabel("等待下载")
        image_progress_bar.addWidget(self.image_progress, 1)
        image_progress_bar.addWidget(self.image_status_label)
        image_layout.addLayout(image_progress_bar)

        self.image_table = QTableWidget(0, len(IMAGE_COLUMNS))
        self.image_table.setHorizontalHeaderLabels(IMAGE_COLUMNS)
        self.image_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.image_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.image_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.image_table.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self.image_table.verticalHeader().setDefaultSectionSize(THUMB_SIZE + 8)
        self.image_table.setWordWrap(False)
        self.image_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._apply_column_widths(self.image_table, IMAGE_WIDTHS)
        image_layout.addWidget(self.image_table, 1)
        self.tabs.addTab(image_page, "图片采集")

        self.image_refresh_button.clicked.connect(self._refresh_image_table)
        self.image_select_all_button.clicked.connect(lambda: self._set_all_images(Qt.CheckState.Checked))
        self.image_select_none_button.clicked.connect(lambda: self._set_all_images(Qt.CheckState.Unchecked))
        self.image_download_button.clicked.connect(self._on_image_download)
        self.image_pause_button.clicked.connect(self._on_image_pause)
        self.image_stop_button.clicked.connect(self._on_image_stop)
        self.image_location_button.clicked.connect(
            lambda: self._open_media_location("image", self.image_table, IMAGE_COLUMNS)
        )
        self.image_range_button.clicked.connect(
            lambda: self._select_rows_by_index(self.image_table, self.image_range_edit)
        )
        self.image_delete_file_button.clicked.connect(
            lambda: self._delete_media_file("image", self.image_table)
        )

        video_page = QWidget()
        video_layout = QVBoxLayout(video_page)
        video_bar = QHBoxLayout()
        video_bar.addWidget(QLabel("来源页："))
        self.video_source_combo = QComboBox()
        self.video_source_combo.setMinimumWidth(240)
        self.video_source_combo.currentIndexChanged.connect(self._refresh_video_table)
        video_bar.addWidget(self.video_source_combo, 1)
        video_bar.addWidget(QLabel("排除文件名："))
        self.video_filter_edit = QLineEdit()
        self.video_filter_edit.setPlaceholderText("如 0.jpg, cover")
        self.video_filter_edit.setMaximumWidth(200)
        self.video_filter_edit.setClearButtonEnabled(True)
        self.video_filter_edit.textChanged.connect(self._refresh_video_table)
        video_bar.addWidget(self.video_filter_edit)
        video_bar.addWidget(QLabel("序号："))
        self.video_range_edit = QLineEdit()
        self.video_range_edit.setPlaceholderText("1-10, 15")
        self.video_range_edit.setMaximumWidth(120)
        self.video_range_edit.setClearButtonEnabled(True)
        video_bar.addWidget(self.video_range_edit)
        self.video_range_button = QPushButton("按序号勾选")
        video_bar.addWidget(self.video_range_button)
        self.video_refresh_button = QPushButton("刷新")
        video_bar.addWidget(self.video_refresh_button)
        video_layout.addLayout(video_bar)

        video_action_bar = QHBoxLayout()
        self.video_select_all_button = QPushButton("全选")
        self.video_select_none_button = QPushButton("取消全选")
        self.video_download_button = QPushButton("下载选中")
        self.video_pause_button = QPushButton("暂停")
        self.video_stop_button = QPushButton("停止")
        self.video_location_button = QPushButton("打开文件位置")
        self.video_delete_file_button = QPushButton("删除文件")
        for button in (
            self.video_select_all_button,
            self.video_select_none_button,
            self.video_download_button,
            self.video_pause_button,
            self.video_stop_button,
            self.video_location_button,
            self.video_delete_file_button,
        ):
            video_action_bar.addWidget(button)
        video_action_bar.addStretch(1)
        video_layout.addLayout(video_action_bar)

        video_progress_bar = QHBoxLayout()
        self.video_progress = QProgressBar()
        self.video_progress.setRange(0, 1)
        self.video_progress.setValue(0)
        self.video_status_label = QLabel("等待下载")
        video_progress_bar.addWidget(self.video_progress, 1)
        video_progress_bar.addWidget(self.video_status_label)
        video_layout.addLayout(video_progress_bar)

        self.video_table = QTableWidget(0, len(VIDEO_COLUMNS))
        self.video_table.setHorizontalHeaderLabels(VIDEO_COLUMNS)
        self.video_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.video_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.video_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.video_table.setWordWrap(False)
        self.video_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._apply_column_widths(self.video_table, VIDEO_WIDTHS)
        video_layout.addWidget(self.video_table, 1)
        self.tabs.addTab(video_page, "视频采集")

        self.video_refresh_button.clicked.connect(self._refresh_video_table)
        self.video_select_all_button.clicked.connect(lambda: self._set_all_videos(Qt.CheckState.Checked))
        self.video_select_none_button.clicked.connect(lambda: self._set_all_videos(Qt.CheckState.Unchecked))
        self.video_download_button.clicked.connect(self._on_video_download)
        self.video_pause_button.clicked.connect(self._on_video_pause)
        self.video_stop_button.clicked.connect(self._on_video_stop)
        self.video_location_button.clicked.connect(
            lambda: self._open_media_location("video", self.video_table, VIDEO_COLUMNS)
        )
        self.video_range_button.clicked.connect(
            lambda: self._select_rows_by_index(self.video_table, self.video_range_edit)
        )
        self.video_delete_file_button.clicked.connect(
            lambda: self._delete_media_file("video", self.video_table)
        )

        audio_page = QWidget()
        audio_layout = QVBoxLayout(audio_page)
        audio_bar = QHBoxLayout()
        audio_bar.addWidget(QLabel("来源页："))
        self.audio_source_combo = QComboBox()
        self.audio_source_combo.setMinimumWidth(240)
        self.audio_source_combo.currentIndexChanged.connect(self._refresh_audio_table)
        audio_bar.addWidget(self.audio_source_combo, 1)
        audio_bar.addWidget(QLabel("排除文件名："))
        self.audio_filter_edit = QLineEdit()
        self.audio_filter_edit.setPlaceholderText("如 0.jpg, cover")
        self.audio_filter_edit.setMaximumWidth(200)
        self.audio_filter_edit.setClearButtonEnabled(True)
        self.audio_filter_edit.textChanged.connect(self._refresh_audio_table)
        audio_bar.addWidget(self.audio_filter_edit)
        audio_bar.addWidget(QLabel("序号："))
        self.audio_range_edit = QLineEdit()
        self.audio_range_edit.setPlaceholderText("1-10, 15")
        self.audio_range_edit.setMaximumWidth(120)
        self.audio_range_edit.setClearButtonEnabled(True)
        audio_bar.addWidget(self.audio_range_edit)
        self.audio_range_button = QPushButton("按序号勾选")
        audio_bar.addWidget(self.audio_range_button)
        self.audio_refresh_button = QPushButton("刷新")
        audio_bar.addWidget(self.audio_refresh_button)
        audio_layout.addLayout(audio_bar)

        audio_action_bar = QHBoxLayout()
        self.audio_select_all_button = QPushButton("全选")
        self.audio_select_none_button = QPushButton("取消全选")
        self.audio_download_button = QPushButton("下载选中")
        self.audio_pause_button = QPushButton("暂停")
        self.audio_stop_button = QPushButton("停止")
        self.audio_location_button = QPushButton("打开文件位置")
        self.audio_delete_file_button = QPushButton("删除文件")
        for button in (
            self.audio_select_all_button,
            self.audio_select_none_button,
            self.audio_download_button,
            self.audio_pause_button,
            self.audio_stop_button,
            self.audio_location_button,
            self.audio_delete_file_button,
        ):
            audio_action_bar.addWidget(button)
        audio_action_bar.addStretch(1)
        audio_layout.addLayout(audio_action_bar)

        audio_tip = QLabel("提示：商业音乐平台版权风险高，仅用于个人学习研究，请勿传播或商用。")
        audio_tip.setWordWrap(True)
        audio_layout.addWidget(audio_tip)

        audio_progress_bar = QHBoxLayout()
        self.audio_progress = QProgressBar()
        self.audio_progress.setRange(0, 1)
        self.audio_progress.setValue(0)
        self.audio_status_label = QLabel("等待下载")
        audio_progress_bar.addWidget(self.audio_progress, 1)
        audio_progress_bar.addWidget(self.audio_status_label)
        audio_layout.addLayout(audio_progress_bar)

        self.audio_table = QTableWidget(0, len(AUDIO_COLUMNS))
        self.audio_table.setHorizontalHeaderLabels(AUDIO_COLUMNS)
        self.audio_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.audio_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.audio_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.audio_table.setWordWrap(False)
        self.audio_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._apply_column_widths(self.audio_table, AUDIO_WIDTHS)
        audio_layout.addWidget(self.audio_table, 1)
        self.tabs.addTab(audio_page, "音频采集")

        self.audio_refresh_button.clicked.connect(self._refresh_audio_table)
        self.audio_select_all_button.clicked.connect(lambda: self._set_all_audios(Qt.CheckState.Checked))
        self.audio_select_none_button.clicked.connect(lambda: self._set_all_audios(Qt.CheckState.Unchecked))
        self.audio_download_button.clicked.connect(self._on_audio_download)
        self.audio_pause_button.clicked.connect(self._on_audio_pause)
        self.audio_stop_button.clicked.connect(self._on_audio_stop)
        self.audio_location_button.clicked.connect(
            lambda: self._open_media_location("audio", self.audio_table, AUDIO_COLUMNS)
        )
        self.audio_range_button.clicked.connect(
            lambda: self._select_rows_by_index(self.audio_table, self.audio_range_edit)
        )
        self.audio_delete_file_button.clicked.connect(
            lambda: self._delete_media_file("audio", self.audio_table)
        )

        return self.tabs

    # ---------- 勾选与列宽 ----------
    def _apply_column_widths(self, table: QTableWidget, widths: list[int]) -> None:
        """固定列宽，过长的文件名 / 存储路径按省略号显示部分文本。"""
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionsMovable(False)
        for index, width in enumerate(widths):
            table.setColumnWidth(index, width)
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.horizontalScrollBar().setSingleStep(24)
        table.verticalScrollBar().setSingleStep(24)

    def _add_check_row(self, table: QTableWidget, index: int, media_id: int) -> None:
        item = QTableWidgetItem()
        item.setFlags(
            Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
        )
        item.setCheckState(Qt.CheckState.Unchecked)
        item.setData(Qt.ItemDataRole.UserRole, int(media_id))
        table.setItem(index, 0, item)

    def _apply_check_state(self, table: QTableWidget, row: int, state: Qt.CheckState) -> None:
        item = table.item(row, 0)
        if item is not None and item.checkState() != state:
            item.setCheckState(state)

    def _set_all_check_states(self, table: QTableWidget, state: Qt.CheckState) -> None:
        for row in range(table.rowCount()):
            self._apply_check_state(table, row, state)

    # ---------- 任务列表 ----------
    def _load_tasks(self, select_id: int | None = None) -> None:
        self.task_table.setRowCount(0)
        current = select_id
        if current is None:
            selected = self._selected_task()
            current = selected[0] if selected else None
        for row in self.store.list_tasks():
            task_id = int(row["id"])
            try:
                raw = json.loads(row["config_json"] or "{}")
            except json.JSONDecodeError:
                raw = {}
            stats = self.store.stats(task_id)
            media = stats["media"]
            media_text = f"图{media.get('image', 0)}/视{media.get('video', 0)}/音{media.get('audio', 0)}"
            start_urls = raw.get("start_urls") or []
            values = [
                row["name"],
                start_urls[0] if start_urls else "",
                ", ".join(raw.get("allowed_domains") or []),
                stats["items"],
                media_text,
                row["updated_at"],
            ]
            index = self.task_table.rowCount()
            self.task_table.insertRow(index)
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, task_id)
                    item.setToolTip(row["config_json"] or "")
                self.task_table.setItem(index, column, item)
            if current is not None and task_id == current:
                self.task_table.selectRow(index)
        if self.task_table.rowCount() and self.task_table.currentRow() < 0:
            self.task_table.selectRow(0)
        self._update_buttons()
        self._refresh_hud()

    def _refresh_hud(self) -> None:
        """HUD：任务总数 + 正在运行的任务数 + 系统状态。"""
        if not hasattr(self, "hud"):
            return
        total = self.task_table.rowCount()
        running = 1 if self._running_task_id is not None else 0
        self.hud.set_counts(total, running)
        if running:
            self.hud.set_status("EXTRACTING", theme.WARN)
        else:
            self.hud.set_status("SYSTEM ONLINE", theme.ACCENT)

    def _selected_task(self) -> tuple[int, dict] | None:
        row = self.task_table.currentRow()
        if row < 0:
            return None
        item = self.task_table.item(row, 0)
        if item is None:
            return None
        task_id = item.data(Qt.ItemDataRole.UserRole)
        record = self.store.get_task(int(task_id))
        if record is None:
            return None
        try:
            raw = json.loads(record["config_json"] or "{}")
        except json.JSONDecodeError:
            raw = {}
        return int(task_id), raw

    def _task_config(self, raw: dict) -> TaskConfig | None:
        try:
            return TaskConfig.from_dict(raw)
        except ConfigError as exc:
            QMessageBox.warning(self, "配置错误", "\n".join(exc.errors))
            return None

    def _unique_task_name(
        self, name: str, exclude_id: int | None = None, reserved_files: set[str] | None = None
    ) -> str:
        """重名时按系统重名文件习惯追加（1）（2），避免覆盖已有任务。"""
        base = (name or "").strip() or "新任务"
        existing = {
            str(row["name"])
            for row in self.store.list_tasks()
            if exclude_id is None or int(row["id"]) != exclude_id
        }
        reserved = reserved_files or set()
        candidate = base
        index = 1
        while candidate in existing or safe_task_name(candidate) in reserved:
            candidate = f"{base}（{index}）"
            index += 1
        return candidate

    def _save_task(self, config: TaskConfig, raw: dict | None = None, task_id: int | None = None) -> int:
        payload = dict(raw) if raw else config.to_dict()
        old_name = ""
        reserved_files = {path.stem for path in settings.tasks_dir().glob("*.json")}
        if task_id is not None:
            record = self.store.get_task(task_id)
            try:
                old_raw = json.loads(record["config_json"] or "{}") if record else {}
            except json.JSONDecodeError:
                old_raw = {}
            old_name = str(old_raw.get("task_name") or (record["name"] if record else ""))
            if old_name:
                reserved_files.discard(safe_task_name(old_name))
        name = self._unique_task_name(
            config.task_name, exclude_id=task_id, reserved_files=reserved_files
        )
        renamed = name != config.task_name
        config.task_name = name
        payload["task_name"] = name
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if task_id is None:
            task_id = self.store.upsert_task(name, text)
        else:
            self.store.update_task(task_id, name, text)
        target = settings.tasks_dir() / f"{safe_task_name(name)}.json"
        target.write_text(text, encoding="utf-8")
        if old_name:
            old_file = settings.tasks_dir() / f"{safe_task_name(old_name)}.json"
            if old_file != target and old_file.exists():
                try:
                    old_file.unlink()
                except OSError as exc:
                    logger.warning("删除旧任务配置文件失败 %s：%s", old_file, exc)
        self._load_tasks(select_id=task_id)
        if renamed:
            self.statusBar().showMessage(f"任务名称重复，已保存为：{name}")
        else:
            self.statusBar().showMessage(f"已保存任务：{name}")
        return task_id

    def _on_selection_changed(self) -> None:
        selected = self._selected_task()
        if selected is None:
            self.monitor_task_label.setText("当前任务：未选择")
            return
        task_id, raw = selected
        stats = self.store.stats(task_id)
        self.monitor_task_label.setText(f"当前任务：{raw.get('task_name', '')}")
        self.monitor_stats.setText(self._stats_text(stats))
        self._refresh_data_preview()
        self._refresh_image_sources()
        self._refresh_image_table()
        self._refresh_video_sources()
        self._refresh_video_table()
        self._refresh_audio_sources()
        self._refresh_audio_table()
        self._update_buttons()

    # ---------- 新建 / 编辑 / 导入导出 ----------
    def _on_new_task(self) -> None:
        dialog = TaskEditorDialog(None, self, settings.default_crawl())
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.config is not None:
            self._save_task(dialog.config, dialog.raw)

    def _on_edit_task(self) -> None:
        selected = self._selected_task()
        if selected is None:
            QMessageBox.information(self, "提示", "请先选择一个任务")
            return
        task_id, raw = selected
        old_name = str(raw.get("task_name") or "")
        dialog = TaskEditorDialog(raw, self, settings.default_crawl())
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.config is not None:
            self._save_task(dialog.config, dialog.raw, task_id=task_id)
            if dialog.config.task_name != old_name:
                logger.info("任务已改名：%s -> %s", old_name, dialog.config.task_name)

    def _on_import_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入任务配置", str(settings.tasks_dir()), "JSON 配置 (*.json)"
        )
        if not path:
            return
        try:
            config = load_config(path)
        except ConfigError as exc:
            QMessageBox.warning(self, "配置错误", "\n".join(exc.errors))
            return
        self._save_task(config, config.to_dict())
        self.statusBar().showMessage(f"已导入配置：{Path(path).name}")

    def _on_export_config(self) -> None:
        selected = self._selected_task()
        if selected is None:
            QMessageBox.information(self, "提示", "请先选择一个任务")
            return
        _, raw = selected
        filename = f"{safe_task_name(str(raw.get('task_name') or 'task'))}.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出任务配置", str(settings.tasks_dir() / filename), "JSON 配置 (*.json)"
        )
        if not path:
            return
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        self.statusBar().showMessage(f"已导出配置：{target}")

    def _on_copy_task(self) -> None:
        selected = self._selected_task()
        if selected is None:
            QMessageBox.information(self, "提示", "请先选择一个任务")
            return
        _, raw = selected
        copied = json.loads(json.dumps(raw, ensure_ascii=False))
        copied["task_name"] = f"{raw.get('task_name') or '任务'} 副本"
        self._on_new_from_raw(copied)

    def _remove_task(self, task_id: int, name: str) -> str | None:
        """删除单个任务的数据库记录与配置文件，返回错误信息。"""
        try:
            self.store.delete_task(task_id)
        except Exception as exc:  # noqa: BLE001 - 界面需要展示数据库异常
            return str(exc)
        config_file = settings.tasks_dir() / f"{safe_task_name(name)}.json"
        try:
            if config_file.exists():
                config_file.unlink()
        except OSError as exc:
            logger.warning("删除任务配置文件失败 %s：%s", config_file, exc)
        return None

    def _on_delete_task(self) -> None:
        if self._media_active or (self._run_thread is not None and self._run_thread.isRunning()):
            QMessageBox.information(self, "提示", "任务运行或媒体下载中，无法删除")
            return
        selected = self._selected_task()
        if selected is None:
            QMessageBox.information(self, "提示", "请先选择一个任务")
            return
        task_id, raw = selected
        name = str(raw.get("task_name") or f"任务 {task_id}")
        choice = QMessageBox.warning(
            self,
            "删除任务",
            f"确定删除任务「{name}」？\n\n"
            "将删除该任务的配置、URL、抓取数据、媒体清单、失败与解析记录。\n"
            "已下载到磁盘的媒体文件会保留。\n\n"
            "此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        error = self._remove_task(task_id, name)
        if error:
            QMessageBox.warning(self, "删除失败", error)
            return

        logger.info("已删除任务 %s（id=%s）", name, task_id)
        self._load_tasks()
        if self.task_table.rowCount() == 0:
            self._reset_selection_panels()
        self.statusBar().showMessage(f"已删除任务：{name}")

    def _selected_task_ids(self) -> list[int]:
        model = self.task_table.selectionModel()
        if model is None:
            return []
        ids: list[int] = []
        for index in model.selectedRows(0):
            item = self.task_table.item(index.row(), 0)
            if item is None:
                continue
            value = item.data(Qt.ItemDataRole.UserRole)
            if value is not None:
                ids.append(int(value))
        return ids

    def _on_batch_delete_tasks(self) -> None:
        if self._media_active or (self._run_thread is not None and self._run_thread.isRunning()):
            QMessageBox.information(self, "提示", "任务运行或媒体下载中，无法删除")
            return
        task_ids = self._selected_task_ids()
        if not task_ids:
            QMessageBox.information(
                self, "提示", "请先在任务列表中选择要删除的任务（可按住 Ctrl 或 Shift 多选）"
            )
            return
        names: list[tuple[int, str]] = []
        for task_id in task_ids:
            record = self.store.get_task(task_id)
            if record is None:
                continue
            try:
                raw = json.loads(record["config_json"] or "{}")
            except json.JSONDecodeError:
                raw = {}
            names.append((task_id, str(raw.get("task_name") or record["name"])))
        preview = "\n".join(f"  · {name}" for _, name in names[:10])
        if len(names) > 10:
            preview += f"\n  · 其余 {len(names) - 10} 个任务"
        choice = QMessageBox.warning(
            self,
            "批量删除任务",
            f"确定删除选中的 {len(names)} 个任务？\n\n{preview}\n\n"
            "将删除这些任务的配置、URL、抓取数据、媒体清单、失败与解析记录。\n"
            "已下载到磁盘的媒体文件会保留。\n\n"
            "此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        deleted = 0
        failures: list[str] = []
        for task_id, name in names:
            error = self._remove_task(task_id, name)
            if error:
                failures.append(f"{name}：{error}")
                continue
            deleted += 1
            logger.info("已删除任务 %s（id=%s）", name, task_id)
        self._load_tasks()
        if self.task_table.rowCount() == 0:
            self._reset_selection_panels()
        self.statusBar().showMessage(f"已批量删除 {deleted} 个任务")
        if failures:
            QMessageBox.warning(self, "部分删除失败", "\n".join(failures))

    def _on_refresh_tasks(self) -> None:
        selected = self._selected_task()
        current = selected[0] if selected else None
        self._load_tasks(select_id=current)
        if self.task_table.rowCount() == 0:
            self._reset_selection_panels()
        elif self.task_table.currentRow() >= 0:
            self._on_selection_changed()
        self.statusBar().showMessage("已刷新任务列表")
        logger.info("手动刷新任务列表")

    def _reset_selection_panels(self) -> None:
        self.monitor_task_label.setText("当前任务：未选择")
        self.monitor_stats.clear()
        self._refresh_data_preview()
        self._refresh_image_sources()
        self._refresh_image_table()
        self._refresh_video_sources()
        self._refresh_video_table()
        self._refresh_audio_sources()
        self._refresh_audio_table()
        self._update_buttons()

    def _on_new_from_raw(self, raw: dict) -> None:
        dialog = TaskEditorDialog(raw, self, settings.default_crawl())
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.config is not None:
            self._save_task(dialog.config, dialog.raw)

    # ---------- 设置 ----------
    def _on_settings(self) -> None:
        dialog = SettingsDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.store.close()
        self.store = Storage(settings.db_file())
        self._load_tasks()
        self.statusBar().showMessage(f"数据目录：{settings.data_dir()}")

    # ---------- 预览 ----------
    def _on_preview(self) -> None:
        if self._run_thread is not None and self._run_thread.isRunning():
            QMessageBox.information(self, "提示", "任务运行中，无法预览")
            return
        selected = self._selected_task()
        if selected is None:
            QMessageBox.information(self, "提示", "请先选择一个任务")
            return
        _, raw = selected
        config = self._task_config(raw)
        if config is None:
            return
        self.preview_button.setEnabled(False)
        worker = PreviewWorker(config, subpage_limit=10)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_preview_done)
        worker.failed.connect(self._on_preview_failed)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_preview_thread_finished)
        self._preview_worker = worker
        self._preview_thread = thread
        thread.start()
        self.statusBar().showMessage("正在预览…")

    def _on_preview_done(self, result: Any) -> None:
        self.statusBar().showMessage("预览完成")
        PreviewDialog(result, self).exec()

    def _on_preview_failed(self, message: str) -> None:
        self.statusBar().showMessage("预览失败")
        QMessageBox.warning(self, "预览失败", message)

    def _on_preview_thread_finished(self) -> None:
        self._preview_worker = None
        self._preview_thread = None
        self.preview_button.setEnabled(True)

    # ---------- 运行 ----------
    def _on_run(self) -> None:
        if self._run_thread is not None and self._run_thread.isRunning():
            QMessageBox.information(self, "提示", "已有任务在运行")
            return
        if self._media_active:
            QMessageBox.information(self, "提示", "媒体下载进行中，请等待完成或停止")
            return
        selected = self._selected_task()
        if selected is None:
            QMessageBox.information(self, "提示", "请先选择一个任务")
            return
        _, raw = selected
        config = self._task_config(raw)
        if config is None:
            return
        copyright_ok = self.copyright_check.isChecked()
        if (
            config.subpage_collect.enabled
            and config.subpage_collect.media_mode == "auto_download"
            and not copyright_ok
        ):
            QMessageBox.information(
                self,
                "版权提示",
                "尚未勾选“已确认媒体版权与合规用途”，本次只发现媒体清单，不自动下载。",
            )
        payload = json.dumps(config.to_dict(), ensure_ascii=False, indent=2)
        task_id = self.store.upsert_task(config.task_name, payload)
        self._running_task_id = task_id
        if hasattr(self, "hud"):
            self.hud.set_status("EXTRACTING", theme.WARN)
            self._refresh_hud()

        worker = RunWorker(
            config,
            settings.db_file(),
            settings.tasks_dir(),
            copyright_ok,
            settings.ffmpeg_path(),
            settings.media_root(),
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_run_done)
        worker.failed.connect(self._on_run_failed)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_run_thread_finished)
        self._run_worker = worker
        self._run_thread = thread
        thread.start()
        self._timer.start()
        self._set_running(True)
        self.monitor_task_label.setText(f"当前任务：{config.task_name}")
        self.statusBar().showMessage("任务已启动")
        logger.info("开始运行任务：%s", config.task_name)

    def _on_run_done(self, summary: dict) -> None:
        self._running_task_id = None
        if hasattr(self, "hud"):
            self.hud.set_status("SYSTEM ONLINE", theme.ACCENT)
            self._refresh_hud()
        state = "已停止" if summary.get("stopped") else "已完成"
        message = (
            f"{state}：抓取 {summary.get('pages', 0)} 页，"
            f"发现 {summary.get('discovered', 0)} 个链接，"
            f"入库 {summary.get('items', 0)} 条，失败 {summary.get('failures', 0)} 条"
        )
        logger.info(message)
        self.statusBar().showMessage(message)

    def _on_run_failed(self, message: str) -> None:
        self._running_task_id = None
        if hasattr(self, "hud"):
            self.hud.set_status("FAULT", theme.DANGER)
            self._refresh_hud()
        logger.error(message)
        QMessageBox.warning(self, "运行失败", message)

    def _on_run_thread_finished(self) -> None:
        self._timer.stop()
        self._run_worker = None
        self._run_thread = None
        self._set_running(False)
        self._poll_running_stats()
        self._load_tasks(select_id=self._running_task_id)

    def _on_pause(self) -> None:
        if self._run_worker is None:
            return
        if self._run_worker.paused:
            self._run_worker.resume()
            self.pause_button.setText("暂停")
            self.statusBar().showMessage("已继续")
        else:
            self._run_worker.pause()
            self.pause_button.setText("继续")
            self.statusBar().showMessage("已暂停，当前页面抓取完成后生效")

    def _on_stop(self) -> None:
        if self._run_worker is None:
            return
        self._run_worker.stop()
        self.pause_button.setText("暂停")
        self.stop_button.setEnabled(False)
        self.statusBar().showMessage("正在停止…")
        logger.info("用户请求停止任务")

    def _polish_tables(self) -> None:
        """统一表格观感：交替行 + 紧凑行高 + 等宽数值对齐。"""
        for table in self.findChildren(QTableWidget):
            table.setAlternatingRowColors(True)
            table.verticalHeader().setDefaultSectionSize(26)
            table.horizontalHeader().setHighlightSections(False)
            table.setShowGrid(True)

    def _poll_running_stats(self) -> None:
        if self._running_task_id is None:
            return
        stats = self.store.stats(self._running_task_id)
        total = stats["urls_total"]
        finished = stats["urls_done"] + stats["urls_failed"]
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(min(finished, max(total, 1)))
        self.monitor_stats.setText(self._stats_text(stats))
        if self._run_thread is None or not self._run_thread.isRunning():
            self._load_tasks(select_id=self._running_task_id)

    def _stats_text(self, stats: dict) -> str:
        media = stats["media"]
        failures = stats["failures"]
        failure_text = "，".join(f"{key} {value}" for key, value in failures.items()) or "无"
        return (
            f"页面：已完成 {stats['urls_done']} / 总数 {stats['urls_total']}，"
            f"待抓取 {stats['urls_pending']}，失败 {stats['urls_failed']}\n"
            f"数据：{stats['items']} 条\n"
            f"媒体发现：图片 {media.get('image', 0)}，视频 {media.get('video', 0)}，"
            f"音频 {media.get('audio', 0)}\n"
            f"失败分类：{failure_text}"
        )

    def _set_running(self, running: bool) -> None:
        for button in (
            self.new_button,
            self.edit_button,
            self.import_button,
            self.export_config_button,
            self.copy_button,
            self.preview_button,
            self.run_button,
            self.export_button,
            self.failure_button,
        ):
            button.setEnabled(not running)
        self.pause_button.setEnabled(running)
        self.stop_button.setEnabled(running)
        if not running:
            self.pause_button.setText("暂停")
        self._update_buttons()

    def _update_buttons(self) -> None:
        has_task = self.task_table.currentRow() >= 0
        running = self._run_thread is not None and self._run_thread.isRunning()
        media_running = self._media_active
        for button in (
            self.edit_button,
            self.export_config_button,
            self.copy_button,
            self.delete_button,
            self.batch_delete_button,
        ):
            button.setEnabled(has_task and not running and not media_running)
        for button in (self.preview_button, self.run_button):
            button.setEnabled(has_task and not running and not media_running)
        self.export_button.setEnabled(has_task and not running and not media_running)
        self.failure_button.setEnabled(has_task and not running and not media_running)
        for button in (
            self.image_location_button,
            self.video_location_button,
            self.audio_location_button,
        ):
            button.setEnabled(has_task)
        self.refresh_button.setEnabled(not running and not media_running)
        for button in (
            self.image_refresh_button,
            self.image_select_all_button,
            self.image_select_none_button,
            self.image_download_button,
            self.image_delete_file_button,
            self.image_range_button,
            self.video_refresh_button,
            self.video_select_all_button,
            self.video_select_none_button,
            self.video_download_button,
            self.video_delete_file_button,
            self.video_range_button,
            self.audio_refresh_button,
            self.audio_select_all_button,
            self.audio_select_none_button,
            self.audio_download_button,
            self.audio_delete_file_button,
            self.audio_range_button,
        ):
            button.setEnabled(not running and not media_running)
        if not media_running:
            self.image_pause_button.setEnabled(False)
            self.image_stop_button.setEnabled(False)
            self.video_pause_button.setEnabled(False)
            self.video_stop_button.setEnabled(False)
            self.audio_pause_button.setEnabled(False)
            self.audio_stop_button.setEnabled(False)

    # ---------- 导出 / 失败中心 ----------
    def _on_export_excel(self) -> None:
        selected = self._selected_task()
        if selected is None:
            QMessageBox.information(self, "提示", "请先选择一个任务")
            return
        task_id, raw = selected
        config = self._task_config(raw)
        if config is None:
            return
        filename = f"{safe_task_name(config.task_name)}_{date.today().isoformat()}.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 Excel", str(settings.exports_dir() / filename), "Excel 文件 (*.xlsx)"
        )
        if not path:
            return
        try:
            target = export_excel(self.store, task_id, config, path)
        except Exception as exc:  # noqa: BLE001 - 导出失败需提示用户
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        logger.info("已导出 Excel：%s", target)
        QMessageBox.information(self, "导出完成", f"已导出：{target}")

    def _on_failures(self) -> None:
        selected = self._selected_task()
        if selected is None:
            QMessageBox.information(self, "提示", "请先选择一个任务")
            return
        task_id, _ = selected
        dialog = FailureDialog(self.store, task_id, self)
        dialog.exec()
        if dialog.reset_requested:
            self.statusBar().showMessage("失败 URL 已重新入队，点击“开始运行”即可重跑")
            logger.info("失败 URL 已重新入队（任务 %s）", task_id)

    # ---------- 数据预览 ----------
    def _refresh_data_preview(self) -> None:
        selected = self._selected_task()
        self.data_table.setRowCount(0)
        self.data_table.setColumnCount(0)
        if selected is None:
            return
        task_id, _ = selected
        items = self.store.list_items(task_id, limit=DATA_PREVIEW_LIMIT)
        if not items:
            return
        keys: list[str] = []
        seen: set[str] = set()
        for item in items:
            try:
                data = json.loads(item["data_json"])
            except json.JSONDecodeError:
                continue
            for key in data:
                if key not in seen:
                    seen.add(key)
                    keys.append(key)
        headers = ["source_url"] + keys
        self.data_table.setColumnCount(len(headers))
        self.data_table.setHorizontalHeaderLabels(headers)
        for item in items:
            try:
                data = json.loads(item["data_json"])
            except json.JSONDecodeError:
                data = {}
            row = self.data_table.rowCount()
            self.data_table.insertRow(row)
            self.data_table.setItem(row, 0, QTableWidgetItem(item["url"]))
            for column, key in enumerate(keys, start=1):
                value = data.get(key)
                self.data_table.setItem(
                    row, column, QTableWidgetItem("" if value is None else str(value))
                )
        self.data_table.resizeColumnsToContents()
        self.statusBar().showMessage(f"已加载 {len(items)} 条数据（最多 {DATA_PREVIEW_LIMIT} 条）")

    # ---------- 图片采集 ----------
    def _refresh_image_sources(self) -> None:
        selected = self._selected_task()
        current = self.image_source_combo.currentData()
        self.image_source_combo.blockSignals(True)
        self.image_source_combo.clear()
        self.image_source_combo.addItem("全部来源", None)
        if selected is not None:
            task_id, _ = selected
            for url, count in self.store.list_media_sources(task_id, "image"):
                self.image_source_combo.addItem(f"{url}（{count}）", url)
        index = self.image_source_combo.findData(current)
        self.image_source_combo.setCurrentIndex(index if index >= 0 else 0)
        self.image_source_combo.blockSignals(False)

    def _refresh_image_table(self, *_: Any) -> None:
        selected = self._selected_task()
        self.image_table.setRowCount(0)
        if selected is None:
            self.image_status_label.setText("请选择任务")
            return
        task_id, _ = selected
        source = self.image_source_combo.currentData()
        rows = self.store.list_media(task_id, media_type="image")
        if source:
            rows = [row for row in rows if row["source_url"] == source]
        total_rows = len(rows)
        tokens = _filter_tokens(self.image_filter_edit.text())
        if tokens:
            rows = [row for row in rows if not _is_file_filtered(row, tokens)]
        hidden = total_rows - len(rows)
        thumbs = 0
        for row in rows:
            index = self.image_table.rowCount()
            self.image_table.insertRow(index)
            self._add_check_row(self.image_table, index, int(row["id"]))

            thumb = QTableWidgetItem()
            local = row["local_path"]
            if local and Path(local).exists() and thumbs < THUMB_LIMIT:
                pixmap = QPixmap(local)
                if not pixmap.isNull():
                    thumb.setIcon(
                        QIcon(
                            pixmap.scaled(
                                QSize(THUMB_SIZE, THUMB_SIZE),
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation,
                            )
                        )
                    )
                    thumbs += 1
            self.image_table.setItem(index, 1, thumb)

            file_item = QTableWidgetItem(
                Path(local).name if local else filename_for_image(row["media_url"])
            )
            file_item.setToolTip(row["media_url"])
            self.image_table.setItem(index, 2, file_item)

            size_text = (
                f"{row['width']}×{row['height']}"
                if row["width"] and row["height"]
                else (row["format"] or "")
            )
            self.image_table.setItem(index, 3, QTableWidgetItem(size_text))
            status_text = MEDIA_STATUS_TEXT.get(row["status"], row["status"])
            if row["error"]:
                status_text = f"{status_text}：{row['error']}"
            status_item = QTableWidgetItem(status_text)
            if row["error"]:
                status_item.setToolTip(row["error"])
            self.image_table.setItem(index, 4, status_item)
            self.image_table.setItem(index, 5, QTableWidgetItem(row["source_url"]))
            self.image_table.setItem(index, 6, QTableWidgetItem(local or ""))
        done = sum(1 for row in rows if row["status"] == "done")
        hidden_text = f"（已隐藏 {hidden}）" if hidden else ""
        self.image_status_label.setText(f"图片 {len(rows)} 张{hidden_text}，已下载 {done} 张")

    def _set_all_images(self, state: Qt.CheckState) -> None:
        self._set_all_check_states(self.image_table, state)

    def _checked_media_ids(self) -> list[int]:
        return _checked_ids(self.image_table)

    def _on_image_download(self) -> None:
        if self._media_active:
            return
        if self._run_thread is not None and self._run_thread.isRunning():
            QMessageBox.information(self, "提示", "抓取任务运行中，请稍后再下载媒体")
            return
        selected = self._selected_task()
        if selected is None:
            QMessageBox.information(self, "提示", "请先选择一个任务")
            return
        task_id, raw = selected
        config = self._task_config(raw)
        if config is None:
            return
        media_ids = self._checked_media_ids()
        if not media_ids:
            QMessageBox.information(self, "提示", "请先勾选要下载的图片，或点击“全选”")
            return
        if not self.copyright_check.isChecked():
            QMessageBox.information(
                self, "版权提示", "请先勾选“已确认媒体版权与合规用途”后再批量下载。"
            )
            return

        worker = MediaDownloadWorker(
            config,
            settings.db_file(),
            task_id,
            media_ids,
            "image",
            config.image.concurrency,
            True,
            None,
            settings.media_root(),
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_image_progress)
        worker.done.connect(self._on_image_done)
        worker.failed.connect(self._on_image_failed)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_image_thread_finished)
        self._media_worker = worker
        self._media_thread = thread
        self._media_kind = "image"
        self._media_task_id = task_id
        self.image_progress.setRange(0, max(len(media_ids), 1))
        self.image_progress.setValue(0)
        self.image_status_label.setText(f"准备下载 {len(media_ids)} 张图片…")
        self._media_active = True
        self._set_media_running(True)
        thread.start()
        logger.info("开始批量下载图片：任务 %s，共 %d 张", config.task_name, len(media_ids))

    def _on_image_progress(self, done: int, total: int) -> None:
        self.image_progress.setRange(0, max(total, 1))
        self.image_progress.setValue(min(done, max(total, 1)))
        self.image_status_label.setText(f"正在下载：{done} / {total}")

    def _on_image_done(self, summary: dict) -> None:
        state = "已停止" if summary.get("stopped") else "下载完成"
        message = (
            f"{state}：成功 {summary.get('downloaded', 0)}，"
            f"跳过 {summary.get('skipped', 0)}，失败 {summary.get('failed', 0)}"
        )
        logger.info(message)
        self.image_status_label.setText(message)
        self.statusBar().showMessage(message)

    def _on_image_failed(self, message: str) -> None:
        logger.error(message)
        self.image_status_label.setText(message)
        QMessageBox.warning(self, "下载失败", message)

    def _on_image_thread_finished(self) -> None:
        task_id = self._media_task_id
        self._media_active = False
        self._media_worker = None
        self._media_thread = None
        self._media_task_id = None
        self._set_media_running(False)
        self._refresh_image_sources()
        self._refresh_image_table()
        self._load_tasks(select_id=task_id)

    def _on_image_pause(self) -> None:
        if self._media_worker is None:
            return
        if self._media_worker.paused:
            self._media_worker.resume()
            self.image_pause_button.setText("暂停")
            self.statusBar().showMessage("媒体下载已继续")
        else:
            self._media_worker.pause()
            self.image_pause_button.setText("继续")
            self.statusBar().showMessage("媒体下载已暂停")

    def _on_image_stop(self) -> None:
        if self._media_worker is None:
            return
        self._media_worker.stop()
        self.image_pause_button.setText("暂停")
        self.image_stop_button.setEnabled(False)
        self.statusBar().showMessage("正在停止媒体下载…")
        logger.info("用户请求停止媒体下载")

    def _set_media_running(self, running: bool) -> None:
        for kind in ("image", "video", "audio"):
            pause = getattr(self, f"{kind}_pause_button")
            stop = getattr(self, f"{kind}_stop_button")
            enabled = running and kind == self._media_kind
            pause.setEnabled(enabled)
            stop.setEnabled(enabled)
            if not running:
                pause.setText("暂停")
        self._update_buttons()

    # ---------- 视频采集 ----------
    def _refresh_video_sources(self) -> None:
        selected = self._selected_task()
        current = self.video_source_combo.currentData()
        self.video_source_combo.blockSignals(True)
        self.video_source_combo.clear()
        self.video_source_combo.addItem("全部来源", None)
        if selected is not None:
            task_id, _ = selected
            for url, count in self.store.list_media_sources(task_id, "video"):
                self.video_source_combo.addItem(f"{url}（{count}）", url)
        index = self.video_source_combo.findData(current)
        self.video_source_combo.setCurrentIndex(index if index >= 0 else 0)
        self.video_source_combo.blockSignals(False)

    def _refresh_video_table(self, *_: Any) -> None:
        selected = self._selected_task()
        self.video_table.setRowCount(0)
        if selected is None:
            self.video_status_label.setText("请选择任务")
            return
        task_id, _ = selected
        source = self.video_source_combo.currentData()
        rows = self.store.list_media(task_id, media_type="video")
        if source:
            rows = [row for row in rows if row["source_url"] == source]
        total_rows = len(rows)
        tokens = _filter_tokens(self.video_filter_edit.text())
        if tokens:
            rows = [row for row in rows if not _is_file_filtered(row, tokens)]
        hidden = total_rows - len(rows)
        for row in rows:
            index = self.video_table.rowCount()
            self.video_table.insertRow(index)
            self._add_check_row(self.video_table, index, int(row["id"]))

            local = row["local_path"]
            file_item = QTableWidgetItem(
                Path(local).name if local else Path(row["media_url"]).name or row["media_url"]
            )
            file_item.setToolTip(row["media_url"])
            self.video_table.setItem(index, 1, file_item)
            self.video_table.setItem(index, 2, QTableWidgetItem(row["format"] or ""))
            quality = row["quality"] or (
                f"{row['width']}×{row['height']}" if row["width"] and row["height"] else ""
            )
            self.video_table.setItem(index, 3, QTableWidgetItem(quality))
            self.video_table.setItem(index, 4, QTableWidgetItem(_format_size(row["file_size"])))
            duration = f"{row['duration']:.1f} 秒" if row["duration"] else ""
            self.video_table.setItem(index, 5, QTableWidgetItem(duration))
            status_text = MEDIA_STATUS_TEXT.get(row["status"], row["status"])
            if row["error"]:
                status_text = f"{status_text}：{row['error']}"
            status_item = QTableWidgetItem(status_text)
            if row["error"]:
                status_item.setToolTip(row["error"])
            self.video_table.setItem(index, 6, status_item)
            self.video_table.setItem(index, 7, QTableWidgetItem(row["source_url"]))
            self.video_table.setItem(index, 8, QTableWidgetItem(local or ""))
        done = sum(1 for row in rows if row["status"] == "done")
        hidden_text = f"（已隐藏 {hidden}）" if hidden else ""
        self.video_status_label.setText(f"视频 {len(rows)} 个{hidden_text}，已下载 {done} 个")

    def _set_all_videos(self, state: Qt.CheckState) -> None:
        self._set_all_check_states(self.video_table, state)

    def _checked_video_ids(self) -> list[int]:
        return _checked_ids(self.video_table)

    def _on_video_download(self) -> None:
        if self._media_active:
            return
        if self._run_thread is not None and self._run_thread.isRunning():
            QMessageBox.information(self, "提示", "抓取任务运行中，请稍后再下载媒体")
            return
        selected = self._selected_task()
        if selected is None:
            QMessageBox.information(self, "提示", "请先选择一个任务")
            return
        task_id, raw = selected
        config = self._task_config(raw)
        if config is None:
            return
        media_ids = self._checked_video_ids()
        if not media_ids:
            QMessageBox.information(self, "提示", "请先勾选要下载的视频，或点击“全选”")
            return
        if not self.copyright_check.isChecked():
            QMessageBox.information(
                self, "版权提示", "请先勾选“已确认媒体版权与合规用途”后再批量下载。"
            )
            return

        worker = MediaDownloadWorker(
            config,
            settings.db_file(),
            task_id,
            media_ids,
            "video",
            VIDEO_CONCURRENCY,
            True,
            settings.ffmpeg_path(),
            settings.media_root(),
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_video_progress)
        worker.done.connect(self._on_video_done)
        worker.failed.connect(self._on_video_failed)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_video_thread_finished)
        self._media_worker = worker
        self._media_thread = thread
        self._media_task_id = task_id
        self._media_kind = "video"
        self.video_progress.setRange(0, max(len(media_ids), 1))
        self.video_progress.setValue(0)
        self.video_status_label.setText(f"准备下载 {len(media_ids)} 个视频…")
        self._media_active = True
        self._set_media_running(True)
        thread.start()
        logger.info("开始批量下载视频：任务 %s，共 %d 个", config.task_name, len(media_ids))

    def _on_video_progress(self, done: int, total: int) -> None:
        self.video_progress.setRange(0, max(total, 1))
        self.video_progress.setValue(min(done, max(total, 1)))
        self.video_status_label.setText(f"正在下载：{done} / {total}")

    def _on_video_done(self, summary: dict) -> None:
        state = "已停止" if summary.get("stopped") else "下载完成"
        message = (
            f"{state}：成功 {summary.get('downloaded', 0)}，"
            f"跳过 {summary.get('skipped', 0)}，失败 {summary.get('failed', 0)}"
        )
        logger.info(message)
        self.video_status_label.setText(message)
        self.statusBar().showMessage(message)

    def _on_video_failed(self, message: str) -> None:
        logger.error(message)
        self.video_status_label.setText(message)
        QMessageBox.warning(self, "下载失败", message)

    def _on_video_thread_finished(self) -> None:
        task_id = self._media_task_id
        self._media_active = False
        self._media_worker = None
        self._media_thread = None
        self._media_task_id = None
        self._set_media_running(False)
        self._refresh_video_sources()
        self._refresh_video_table()
        self._load_tasks(select_id=task_id)

    def _on_video_pause(self) -> None:
        if self._media_worker is None:
            return
        if self._media_worker.paused:
            self._media_worker.resume()
            self.video_pause_button.setText("暂停")
            self.statusBar().showMessage("视频下载已继续")
        else:
            self._media_worker.pause()
            self.video_pause_button.setText("继续")
            self.statusBar().showMessage("视频下载已暂停")

    def _on_video_stop(self) -> None:
        if self._media_worker is None:
            return
        self._media_worker.stop()
        self.video_pause_button.setText("暂停")
        self.video_stop_button.setEnabled(False)
        self.statusBar().showMessage("正在停止视频下载…")
        logger.info("用户请求停止视频下载")

    # ---------- 音频采集 ----------
    def _refresh_audio_sources(self) -> None:
        selected = self._selected_task()
        current = self.audio_source_combo.currentData()
        self.audio_source_combo.blockSignals(True)
        self.audio_source_combo.clear()
        self.audio_source_combo.addItem("全部来源", None)
        if selected is not None:
            task_id, _ = selected
            for url, count in self.store.list_media_sources(task_id, "audio"):
                self.audio_source_combo.addItem(f"{url}（{count}）", url)
        index = self.audio_source_combo.findData(current)
        self.audio_source_combo.setCurrentIndex(index if index >= 0 else 0)
        self.audio_source_combo.blockSignals(False)

    def _refresh_audio_table(self, *_: Any) -> None:
        selected = self._selected_task()
        self.audio_table.setRowCount(0)
        if selected is None:
            self.audio_status_label.setText("请选择任务")
            return
        task_id, _ = selected
        source = self.audio_source_combo.currentData()
        rows = self.store.list_media(task_id, media_type="audio")
        if source:
            rows = [row for row in rows if row["source_url"] == source]
        total_rows = len(rows)
        tokens = _filter_tokens(self.audio_filter_edit.text())
        if tokens:
            rows = [row for row in rows if not _is_file_filtered(row, tokens)]
        hidden = total_rows - len(rows)
        for row in rows:
            index = self.audio_table.rowCount()
            self.audio_table.insertRow(index)
            self._add_check_row(self.audio_table, index, int(row["id"]))

            local = row["local_path"]
            file_item = QTableWidgetItem(
                Path(local).name if local else Path(row["media_url"]).name or row["media_url"]
            )
            file_item.setToolTip(row["media_url"])
            self.audio_table.setItem(index, 1, file_item)
            self.audio_table.setItem(index, 2, QTableWidgetItem(row["quality"] or ""))
            self.audio_table.setItem(index, 3, QTableWidgetItem(row["format"] or ""))
            duration = f"{row['duration']:.1f} 秒" if row["duration"] else ""
            self.audio_table.setItem(index, 4, QTableWidgetItem(duration))
            bitrate = f"{int(row['bitrate'] / 1000)} kbps" if row["bitrate"] else ""
            self.audio_table.setItem(index, 5, QTableWidgetItem(bitrate))
            self.audio_table.setItem(index, 6, QTableWidgetItem(_format_size(row["file_size"])))
            status_text = MEDIA_STATUS_TEXT.get(row["status"], row["status"])
            if row["error"]:
                status_text = f"{status_text}：{row['error']}"
            status_item = QTableWidgetItem(status_text)
            if row["error"]:
                status_item.setToolTip(row["error"])
            self.audio_table.setItem(index, 7, status_item)
            self.audio_table.setItem(index, 8, QTableWidgetItem(row["source_url"]))
            self.audio_table.setItem(index, 9, QTableWidgetItem(local or ""))
        done = sum(1 for row in rows if row["status"] == "done")
        hidden_text = f"（已隐藏 {hidden}）" if hidden else ""
        self.audio_status_label.setText(f"音频 {len(rows)} 个{hidden_text}，已下载 {done} 个")

    def _set_all_audios(self, state: Qt.CheckState) -> None:
        self._set_all_check_states(self.audio_table, state)

    def _checked_audio_ids(self) -> list[int]:
        return _checked_ids(self.audio_table)

    def _has_commercial_audio(self, task_id: int, media_ids: list[int]) -> bool:
        wanted = {int(item) for item in media_ids}
        for row in self.store.list_media(task_id, media_type="audio"):
            if int(row["id"]) not in wanted:
                continue
            host = (urlsplit(row["source_url"]).hostname or "").lower()
            if any(host == item or host.endswith("." + item) for item in COMMERCIAL_AUDIO_HOSTS):
                return True
        return False

    def _on_audio_download(self) -> None:
        if self._media_active:
            return
        if self._run_thread is not None and self._run_thread.isRunning():
            QMessageBox.information(self, "提示", "抓取任务运行中，请稍后再下载媒体")
            return
        selected = self._selected_task()
        if selected is None:
            QMessageBox.information(self, "提示", "请先选择一个任务")
            return
        task_id, raw = selected
        config = self._task_config(raw)
        if config is None:
            return
        media_ids = self._checked_audio_ids()
        if not media_ids:
            QMessageBox.information(self, "提示", "请先勾选要下载的音频，或点击“全选”")
            return
        if not self.copyright_check.isChecked():
            QMessageBox.information(
                self, "版权提示", "请先勾选“已确认媒体版权与合规用途”后再批量下载。"
            )
            return
        if self._has_commercial_audio(task_id, media_ids):
            choice = QMessageBox.warning(
                self,
                "高风险版权提示",
                "选中项包含商业音乐平台音频，版权风险较高。\n"
                "仅限个人学习研究使用，请勿传播或商用；工具不提供解密或签名逆向。\n"
                "确认继续下载？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if choice != QMessageBox.StandardButton.Yes:
                return

        worker = MediaDownloadWorker(
            config,
            settings.db_file(),
            task_id,
            media_ids,
            "audio",
            config.audio.concurrency,
            True,
            settings.ffmpeg_path(),
            settings.media_root(),
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_audio_progress)
        worker.done.connect(self._on_audio_done)
        worker.failed.connect(self._on_audio_failed)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_audio_thread_finished)
        self._media_worker = worker
        self._media_thread = thread
        self._media_task_id = task_id
        self._media_kind = "audio"
        self.audio_progress.setRange(0, max(len(media_ids), 1))
        self.audio_progress.setValue(0)
        self.audio_status_label.setText(f"准备下载 {len(media_ids)} 个音频…")
        self._media_active = True
        self._set_media_running(True)
        thread.start()
        logger.info("开始批量下载音频：任务 %s，共 %d 个", config.task_name, len(media_ids))

    def _on_audio_progress(self, done: int, total: int) -> None:
        self.audio_progress.setRange(0, max(total, 1))
        self.audio_progress.setValue(min(done, max(total, 1)))
        self.audio_status_label.setText(f"正在下载：{done} / {total}")

    def _on_audio_done(self, summary: dict) -> None:
        state = "已停止" if summary.get("stopped") else "下载完成"
        message = (
            f"{state}：成功 {summary.get('downloaded', 0)}，"
            f"跳过 {summary.get('skipped', 0)}，失败 {summary.get('failed', 0)}"
        )
        logger.info(message)
        self.audio_status_label.setText(message)
        self.statusBar().showMessage(message)

    def _on_audio_failed(self, message: str) -> None:
        logger.error(message)
        self.audio_status_label.setText(message)
        QMessageBox.warning(self, "下载失败", message)

    def _on_audio_thread_finished(self) -> None:
        task_id = self._media_task_id
        self._media_active = False
        self._media_worker = None
        self._media_thread = None
        self._media_task_id = None
        self._set_media_running(False)
        self._refresh_audio_sources()
        self._refresh_audio_table()
        self._load_tasks(select_id=task_id)

    def _on_audio_pause(self) -> None:
        if self._media_worker is None:
            return
        if self._media_worker.paused:
            self._media_worker.resume()
            self.audio_pause_button.setText("暂停")
            self.statusBar().showMessage("音频下载已继续")
        else:
            self._media_worker.pause()
            self.audio_pause_button.setText("继续")
            self.statusBar().showMessage("音频下载已暂停")

    def _on_audio_stop(self) -> None:
        if self._media_worker is None:
            return
        self._media_worker.stop()
        self.audio_pause_button.setText("暂停")
        self.audio_stop_button.setEnabled(False)
        self.statusBar().showMessage("正在停止音频下载…")
        logger.info("用户请求停止音频下载")

    def _select_rows_by_index(self, table: QTableWidget, edit: QLineEdit) -> None:
        """按当前可见行的 1 基序号范围勾选文件，替换原有勾选。"""
        total = table.rowCount()
        if total == 0:
            QMessageBox.information(self, "提示", "当前列表没有可选择的文件")
            return
        wanted = _parse_index_ranges(edit.text(), total)
        if not wanted:
            QMessageBox.information(self, "提示", "请输入有效序号范围，如 1-10, 15")
            return
        for row in range(total):
            state = Qt.CheckState.Checked if row + 1 in wanted else Qt.CheckState.Unchecked
            self._apply_check_state(table, row, state)
        self.statusBar().showMessage(f"已按序号勾选 {len(wanted)} 个文件")

    def _open_media_location(self, media_type: str, table: QTableWidget, columns: list[str]) -> None:
        """定位选中媒体文件；未选中时打开当前任务该类型的下载目录。"""
        selected = self._selected_task()
        if selected is None:
            QMessageBox.information(self, "提示", "请先选择一个任务")
            return
        _, raw = selected
        row = table.currentRow()
        local = ""
        if row >= 0:
            item = table.item(row, table.columnCount() - 1)
            if item is not None:
                local = item.text().strip()
        if local:
            path = Path(local)
            if not path.exists():
                QMessageBox.information(self, "提示", f"文件不存在，可能尚未下载：\n{path}")
            _open_path_location(path)
            return

        config = self._task_config(raw)
        if config is None:
            return
        folder_kind = {"image": "images", "video": "videos", "audio": "audios"}[media_type]
        folder = media_dir(folder_kind, config.task_name, home=settings.media_root())
        _open_path_location(folder)

    def _delete_media_file(self, media_type: str, table: QTableWidget) -> None:
        """删除选中媒体记录的本地文件，并把记录重置为可重新下载。"""
        if self._media_active or (self._run_thread is not None and self._run_thread.isRunning()):
            QMessageBox.information(self, "提示", "任务运行或媒体下载中，无法删除文件")
            return
        selected = self._selected_task()
        if selected is None:
            QMessageBox.information(self, "提示", "请先选择一个任务")
            return
        row_index = table.currentRow()
        if row_index < 0:
            QMessageBox.information(self, "提示", "请先在列表中选择要删除的文件")
            return
        check_item = table.item(row_index, 0)
        if check_item is None:
            return
        media_id = int(check_item.data(Qt.ItemDataRole.UserRole))
        record = self.store.get_media_by_id(media_id)
        if record is None:
            QMessageBox.information(self, "提示", "媒体记录不存在，可能已被删除")
            getattr(self, f"_refresh_{media_type}_table")()
            return

        local = record["local_path"] or ""
        label = Path(local).name if local else str(record["media_url"])
        if local:
            detail = f"本地文件：\n{local}"
            if not Path(local).exists():
                detail += "\n（文件已不存在）"
        else:
            detail = "该记录目前没有本地文件。"
        choice = QMessageBox.warning(
            self,
            "删除文件",
            f"确定删除「{label}」？\n\n{detail}\n\n"
            "记录会重置为“已发现”，之后可重新下载。\n此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return

        if local:
            path = Path(local)
            if path.exists():
                try:
                    path.unlink()
                except OSError as exc:
                    QMessageBox.warning(self, "删除失败", f"无法删除文件：\n{path}\n\n{exc}")
                    return
        self.store.clear_media_file(media_id)
        getattr(self, f"_refresh_{media_type}_sources")()
        getattr(self, f"_refresh_{media_type}_table")()
        self.statusBar().showMessage(f"已删除文件：{label}")
        logger.info("已删除媒体文件 %s（media_id=%s）", label, media_id)

    # ---------- 日志 ----------
    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 接口命名
        logging.getLogger("focus_crawler").removeHandler(self._log_handler)
        if self._run_worker is not None:
            self._run_worker.stop()
        if self._media_worker is not None:
            self._media_worker.stop()
        if self._run_thread is not None and self._run_thread.isRunning():
            self._run_thread.quit()
            self._run_thread.wait(3000)
        if self._media_thread is not None and self._media_thread.isRunning():
            self._media_thread.quit()
            self._media_thread.wait(3000)
        self.store.close()
        super().closeEvent(event)
