"""Spider 运行页：加载 .py、配置参数、后台运行、实时日志与导出。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..spiders.exporters import export_items_stream
from ..spiders.loader import SpiderLoadError, discover_spiders, load_spider_class
from . import theme


class _LogBridge(QObject):
    message = Signal(str)


class _Handler(logging.Handler):
    def __init__(self, bridge: _LogBridge):
        super().__init__()
        self.bridge = bridge
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.bridge.message.emit(self.format(record))
        except Exception:  # noqa: BLE001 - 日志不能影响抓取
            pass


class SpiderWorker(QThread):
    """在后台线程里跑 asyncio 事件循环，运行 Spider 并流式回传 Item。"""

    log = Signal(str)
    item = Signal(dict)
    progress = Signal(int, int)
    stats = Signal(dict)
    failed = Signal(str)

    def __init__(self, spider, parent=None):
        super().__init__(parent)
        self.spider = spider
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True
        try:
            self.spider.stop()
        except Exception:  # noqa: BLE001
            pass

    def run(self) -> None:
        bridge = _LogBridge()
        bridge.message.connect(self.log.emit)
        handler = _Handler(bridge)
        root = logging.getLogger("focus_crawler")
        root.addHandler(handler)
        try:
            async def runner() -> None:
                count = 0
                async for item in self.spider.stream():
                    if self._stop_requested:
                        break
                    count += 1
                    self.item.emit(dict(item))
                    if count % 10 == 0:
                        self.progress.emit(count, 0)

            asyncio.run(runner())
            self.stats.emit(self.spider.stats)
        except Exception as exc:  # noqa: BLE001 - 线程内异常要回传界面
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            root.removeHandler(handler)


class SpiderPage(QWidget):
    """Spider 运行页（主窗口 Tab）。"""

    MAX_PREVIEW_ROWS = 500

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._worker: SpiderWorker | None = None
        self._spider = None
        self._items: list[dict[str, Any]] = []
        self._build_ui()

    # -- UI -------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        file_group = QGroupBox("Spider 文件")
        file_row = QHBoxLayout(file_group)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("选择 .py 文件，例如 demo_spider.py")
        browse = QPushButton("浏览")
        browse.clicked.connect(self._browse)
        self.class_combo = QComboBox()
        self.class_combo.setMinimumWidth(180)
        reload_button = QPushButton("识别类")
        reload_button.clicked.connect(self._scan_classes)
        file_row.addWidget(self.path_edit, 1)
        file_row.addWidget(browse)
        file_row.addWidget(QLabel("类"))
        file_row.addWidget(self.class_combo)
        file_row.addWidget(reload_button)
        outer.addWidget(file_group)

        option_group = QGroupBox("运行参数")
        form = QFormLayout(option_group)
        self.concurrency = QSpinBox()
        self.concurrency.setRange(1, 64)
        self.concurrency.setValue(8)
        self.delay = QDoubleSpinBox()
        self.delay.setRange(0, 60)
        self.delay.setSingleStep(0.1)
        self.delay.setValue(0.3)
        self.robots = QCheckBox("遵守 robots.txt")
        self.dev_mode = QCheckBox("开发模式（缓存响应并重放）")
        self.crawldir_edit = QLineEdit()
        self.crawldir_edit.setPlaceholderText("可留空；填写后支持断点续跑")
        pick_dir = QPushButton("选择")
        pick_dir.clicked.connect(self._browse_crawldir)
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.crawldir_edit)
        dir_row.addWidget(pick_dir)
        form.addRow("并发数", self.concurrency)
        form.addRow("请求间隔(秒)", self.delay)
        form.addRow("", self.robots)
        form.addRow("", self.dev_mode)
        form.addRow("断点目录", dir_row)
        outer.addWidget(option_group)

        action_row = QHBoxLayout()
        self.run_button = QPushButton("运行")
        self.run_button.clicked.connect(self._run)
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop)
        self.export_button = QPushButton("导出结果")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self._export)
        self.status_label = QLabel("待运行")
        self.status_label.setProperty("role", "hint")
        action_row.addWidget(self.run_button)
        action_row.addWidget(self.stop_button)
        action_row.addWidget(self.export_button)
        action_row.addStretch(1)
        action_row.addWidget(self.status_label)
        outer.addLayout(action_row)

        splitter = QSplitter(Qt.Orientation.Vertical)

        self.result_table = QTableWidget(0, 2)
        self.result_table.setHorizontalHeaderLabels(["#", "Item（JSON）"])
        self.result_table.horizontalHeader().setStretchLastSection(True)
        self.result_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        splitter.addWidget(self.result_table)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        splitter.addWidget(self.log_view)
        splitter.setSizes([420, 260])
        outer.addWidget(splitter, 1)

    # -- 文件 -----------------------------------------------------
    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 Spider 文件", str(Path.cwd()), "Python (*.py)")
        if path:
            self.path_edit.setText(path)
            self._scan_classes()

    def _browse_crawldir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择断点目录", str(Path.cwd()))
        if directory:
            self.crawldir_edit.setText(directory)

    def _scan_classes(self) -> None:
        path = self.path_edit.text().strip()
        self.class_combo.clear()
        if not path:
            return
        try:
            found = discover_spiders(path)
        except SpiderLoadError as exc:
            self._append_log(f"加载失败：{exc}")
            return
        for name, _cls in found:
            self.class_combo.addItem(name)
        if not found:
            self._append_log("该文件里没有找到 Spider 子类")

    # -- 运行 -----------------------------------------------------
    def _run(self) -> None:
        path = self.path_edit.text().strip()
        class_name = self.class_combo.currentText().strip()
        if not path or not class_name:
            self._append_log("请先选择 Spider 文件并识别类")
            return
        try:
            spider_class = load_spider_class(f"{path}:{class_name}")
        except SpiderLoadError as exc:
            self._append_log(f"加载失败：{exc}")
            return

        settings = {
            "concurrency": self.concurrency.value(),
            "delay": self.delay.value(),
            "robots_txt_obey": self.robots.isChecked(),
            "dev_mode": self.dev_mode.isChecked(),
        }
        crawldir = self.crawldir_edit.text().strip() or None
        self._spider = spider_class(settings=settings, crawldir=crawldir)
        self._items.clear()
        self.result_table.setRowCount(0)

        self._worker = SpiderWorker(self._spider)
        self._worker.log.connect(self._append_log)
        self._worker.item.connect(self._append_item)
        self._worker.stats.connect(self._on_stats)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_finished)

        self.run_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.export_button.setEnabled(False)
        self.status_label.setText("运行中…")
        self._append_log(f"启动 {class_name}（并发 {settings['concurrency']}，延迟 {settings['delay']}s）")
        self._worker.start()

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self.status_label.setText("停止中…")

    def _on_finished(self) -> None:
        self.run_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.export_button.setEnabled(bool(self._items))
        self.status_label.setText(f"已结束：{len(self._items)} 条")
        self._append_log(f"运行结束，共 {len(self._items)} 条")

    def _on_failed(self, message: str) -> None:
        self._append_log(f"运行失败：{message}")
        self.status_label.setText("失败")

    def _on_stats(self, stats: dict) -> None:
        self._append_log(f"统计：{stats}")

    def _append_item(self, payload: dict) -> None:
        self._items.append(payload)
        if self.result_table.rowCount() >= self.MAX_PREVIEW_ROWS:
            return
        import json

        row = self.result_table.rowCount()
        self.result_table.insertRow(row)
        self.result_table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        text = json.dumps(payload, ensure_ascii=False)
        item = QTableWidgetItem(text[:400])
        item.setToolTip(text)
        self.result_table.setItem(row, 1, item)

    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    # -- 导出 -----------------------------------------------------
    def _export(self) -> None:
        if not self._items:
            return
        path, selected = QFileDialog.getSaveFileName(
            self, "导出结果", "spider_items.json", "JSON (*.json);;JSONL (*.jsonl);;CSV (*.csv);;XML (*.xml)"
        )
        if not path:
            return
        fmt = Path(path).suffix.lstrip(".").lower() or "json"
        if selected:
            for name, suffix in (("JSON", "json"), ("JSONL", "jsonl"), ("CSV", "csv"), ("XML", "xml")):
                if name in selected:
                    fmt = suffix
        try:
            target = export_items_stream(iter(self._items), path, fmt)
            self._append_log(f"已导出 {len(self._items)} 条到 {target}")
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"导出失败：{exc}")

    def shutdown(self) -> None:
        """窗口关闭时停止后台线程。"""
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)
