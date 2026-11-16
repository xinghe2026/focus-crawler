"""顶部 HUD 状态条：终端风格的系统状态、任务计数与时钟。

纯代码绘制，不依赖位图，任何 DPI 下都清晰；配色取自 :mod:`focus_crawler.gui.theme`。
"""

from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import theme


class StatusHud(QWidget):
    """一条细长的 HUD：左侧品牌、中间运行状态、右侧时钟。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(34)
        self.setObjectName("statusHud")
        self._status = "SYSTEM ONLINE"
        self._status_color = theme.ACCENT
        self._tasks = 0
        self._running = 0
        self._blink = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # -- 外部接口 -------------------------------------------------
    def set_status(self, text: str, color: str | None = None) -> None:
        self._status = text.upper()
        self._status_color = color or theme.ACCENT
        self.update()

    def set_counts(self, tasks: int, running: int = 0) -> None:
        self._tasks = tasks
        self._running = running
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        return QSize(640, 34)

    # -- 绘制 -----------------------------------------------------
    def _tick(self) -> None:
        self._blink = (self._blink + 0.5) % 2.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = QRectF(self.rect())

        painter.fillRect(rect, QColor(theme.BG))
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)

        # 左上角装饰角标
        pen = QPen(QColor(theme.ACCENT_DIM), 1)
        painter.setPen(pen)
        painter.drawLine(0, 0, 18, 0)
        painter.drawLine(0, 0, 0, 12)
        painter.drawLine(self.width() - 18, self.height() - 1, self.width(), self.height() - 1)
        painter.drawLine(self.width() - 1, self.height() - 12, self.width() - 1, self.height() - 1)

        mono = QFont(self.font())
        mono.setFamily(self.font().family())
        mono.setPointSize(9)
        mono.setBold(True)
        painter.setFont(mono)

        # 品牌
        painter.setPen(QColor(theme.ACCENT))
        painter.drawText(14, 22, "FOCUS//CRAWLER")

        painter.setFont(QFont(mono.family(), 8, QFont.Weight.Normal))
        painter.setPen(QColor(theme.TEXT_DIM))
        painter.drawText(148, 22, "DEEP WEB EXTRACTION TERMINAL")

        # 状态灯（呼吸）
        dot_x = self.width() // 2 - 120
        alpha = 255 if self._blink < 1.0 else 140
        color = QColor(self._status_color)
        color.setAlpha(alpha)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(QRectF(dot_x, 13, 8, 8))
        painter.setPen(QColor(self._status_color))
        painter.drawText(dot_x + 16, 22, self._status)

        # 计数与时钟
        painter.setPen(QColor(theme.TEXT_DIM))
        counts = f"TASKS {self._tasks:03d}  RUN {self._running:02d}"
        painter.drawText(self.width() // 2 + 40, 22, counts)

        painter.setPen(QColor(theme.ACCENT))
        clock = datetime.now().strftime("%H:%M:%S")
        painter.drawText(self.width() - 110, 22, clock)
        painter.end()
