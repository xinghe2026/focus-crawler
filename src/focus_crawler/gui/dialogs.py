"""预览、失败中心、设置对话框。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.preview import PreviewResult
from . import settings


def _table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.horizontalHeader().setStretchLastSection(True)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    return table


def _append(table: QTableWidget, values: list[Any], tooltip: str | None = None) -> None:
    row = table.rowCount()
    table.insertRow(row)
    for column, value in enumerate(values):
        item = QTableWidgetItem("" if value is None else str(value))
        if tooltip and column == 0:
            item.setToolTip(tooltip)
        table.setItem(row, column, item)


class PreviewDialog(QDialog):
    """规则预览：链接发现统计、字段命中率、子页媒体统计。"""

    def __init__(self, result: PreviewResult, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("规则预览")
        self.resize(860, 660)
        layout = QVBoxLayout(self)

        if not result.ok:
            label = QLabel(f"预览失败：{result.error}")
            label.setWordWrap(True)
            layout.addWidget(label)
        else:
            summary = QLabel(
                f"入口页：{result.entry_url}\n"
                f"发现链接 {result.discovered_links} 条，过滤 {result.filtered_count} 条，"
                f"去重 {result.duplicate_count} 条"
                + ("（已截断）" if result.truncated else "")
            )
            summary.setWordWrap(True)
            layout.addWidget(summary)

        tabs = QTabWidget()
        tabs.addTab(self._links_tab(result), "链接发现")
        tabs.addTab(self._fields_tab(result), "字段命中率")
        tabs.addTab(self._subpages_tab(result), "子页与媒体")
        layout.addWidget(tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _links_tab(self, result: PreviewResult) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("前 10 条发现的子页链接："))
        links = QListWidget()
        links.addItems(result.link_examples)
        layout.addWidget(links, 1)
        return page

    def _fields_tab(self, result: PreviewResult) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        table = _table(["字段", "命中率", "命中/总数", "示例值"])
        for name, info in result.fields.items():
            rate = float(info.get("hit_rate") or 0.0)
            example = info.get("example")
            _append(
                table,
                [name, f"{rate:.0%}", f"{info.get('hits', 0)}/{info.get('total', 0)}", example],
                tooltip=None if example is None else str(example),
            )
        if not result.fields:
            table.setRowCount(0)
        layout.addWidget(table, 1)
        layout.addWidget(QLabel("提示：命中率低通常表示选择器不匹配；空值会使用字段默认值。"))
        return page

    def _subpages_tab(self, result: PreviewResult) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        table = _table(["子页 URL", "状态", "数据字段", "图片", "视频", "音频"])
        for sub in result.subpages:
            tooltip = ""
            examples = sub.image_example + sub.video_example + sub.audio_example
            if examples:
                tooltip = "示例：\n" + "\n".join(examples[:8])
            _append(
                table,
                [
                    sub.url,
                    sub.status_code if sub.ok else f"失败 {sub.status_code or ''}".strip(),
                    len(sub.data),
                    sub.images,
                    sub.videos,
                    sub.audios,
                ],
                tooltip or None,
            )
        layout.addWidget(table, 1)
        totals = result.media_total
        layout.addWidget(
            QLabel(
                f"媒体合计：图片 {totals.get('image', 0)}，视频 {totals.get('video', 0)}，"
                f"音频 {totals.get('audio', 0)}"
            )
        )
        return page


class FailureDialog(QDialog):
    """失败中心：查看失败分类，并可一键重新入队。"""

    def __init__(self, store: Any, task_id: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("失败中心")
        self.resize(820, 520)
        self.store = store
        self.task_id = task_id
        self.reset_requested = False

        layout = QVBoxLayout(self)
        stats = store.failure_stats(task_id)
        summary_text = "失败分类：" + (
            "，".join(f"{key} {value} 条" for key, value in stats.items()) if stats else "暂无失败记录"
        )
        layout.addWidget(QLabel(summary_text))

        self.failures = _table(["URL", "失败类型", "错误", "重试次数", "最后尝试时间"])
        for row in store.list_failures(task_id):
            _append(
                self.failures,
                [row["url"], row["failure_type"], row["error"], row["retry_count"], row["last_try_at"]],
                tooltip=row["url"],
            )
        layout.addWidget(self.failures, 1)

        buttons = QHBoxLayout()
        retry_button = QPushButton("全部重新入队")
        retry_button.setToolTip("把失败 URL 重置为待抓取，随后点击“开始运行”即可重跑")
        retry_button.clicked.connect(self._on_retry)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.reject)
        buttons.addStretch(1)
        buttons.addWidget(retry_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    def _on_retry(self) -> None:
        self.store.reset_failed_urls(self.task_id)
        self.reset_requested = True
        self.accept()


class SettingsDialog(QDialog):
    """设置：数据目录、媒体下载目录、ffmpeg、默认抓取参数。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(620, 520)
        layout = QVBoxLayout(self)

        data_group = QGroupBox("数据目录")
        data_form = QFormLayout(data_group)
        self.data_dir_edit = QLineEdit(str(settings.data_dir()))
        browse_row = QHBoxLayout()
        browse_row.addWidget(self.data_dir_edit)
        browse_button = QPushButton("选择")
        browse_button.clicked.connect(self._browse_dir)
        browse_row.addWidget(browse_button)
        data_form.addRow("目录", browse_row)
        data_form.addRow("数据库", QLabel(str(settings.db_file())))
        layout.addWidget(data_group)

        media_group = QGroupBox("媒体下载目录")
        media_form = QFormLayout(media_group)
        self.media_root_edit = QLineEdit(str(settings.media_root()))
        media_row = QHBoxLayout()
        media_row.addWidget(self.media_root_edit)
        media_browse = QPushButton("选择")
        media_browse.clicked.connect(self._browse_media_dir)
        media_row.addWidget(media_browse)
        media_form.addRow("根目录", media_row)
        media_form.addRow(
            "",
            QLabel("保存结构：根目录/media/{images|videos|audios}/{任务名}/{日期}\n仅影响后续下载，已下载文件位置不变。"),
        )
        layout.addWidget(media_group)

        engine_group = QGroupBox("媒体与抓取默认值")
        engine_form = QFormLayout(engine_group)
        self.ffmpeg_edit = QLineEdit(settings.ffmpeg_path())
        self.concurrency = QSpinBox()
        self.concurrency.setRange(1, 10)
        self.delay_min = QSpinBox()
        self.delay_min.setRange(0, 600000)
        self.delay_max = QSpinBox()
        self.delay_max.setRange(0, 600000)
        self.robots = QCheckBox("默认遵守 robots.txt")
        values = settings.read_values()
        self.concurrency.setValue(int(values["default_concurrency"]))
        self.delay_min.setValue(int(values["default_delay_min"]))
        self.delay_max.setValue(int(values["default_delay_max"]))
        self.robots.setChecked(bool(values["default_respect_robots"]))
        engine_form.addRow("ffmpeg 路径", self.ffmpeg_edit)
        engine_form.addRow("默认并发", self.concurrency)
        engine_form.addRow("默认延迟下限(ms)", self.delay_min)
        engine_form.addRow("默认延迟上限(ms)", self.delay_max)
        engine_form.addRow("", self.robots)
        layout.addWidget(engine_group)
        layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择数据目录", self.data_dir_edit.text())
        if directory:
            self.data_dir_edit.setText(directory)

    def _browse_media_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择媒体下载根目录", self.media_root_edit.text())
        if directory:
            self.media_root_edit.setText(directory)

    def _on_save(self) -> None:
        directory = self.data_dir_edit.text().strip()
        default_dir = str(Path(settings.data_dir()))
        media_root = self.media_root_edit.text().strip()
        default_media_root = str(Path(settings.default_media_root()))
        settings.save(
            {
                "data_dir": "" if directory == default_dir else directory,
                "media_root": "" if media_root == default_media_root else media_root,
                "ffmpeg_path": self.ffmpeg_edit.text().strip() or "ffmpeg",
                "default_concurrency": self.concurrency.value(),
                "default_delay_min": self.delay_min.value(),
                "default_delay_max": self.delay_max.value(),
                "default_respect_robots_v2": self.robots.isChecked(),
            }
        )
        self.accept()
