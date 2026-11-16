"""主窗口背景：数字雨 / 代码流（服务器扫盘感）。

纯 QPainter 自绘，不依赖位图；以低透明度绘制，保证前景文字与表格可读。
"""

from __future__ import annotations

import random

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import theme

GLYPHS = "01" + "{}[]()<>=+-*/#%$&|;:,.?!" + "ABCDEF0123456789"
_COLUMN_WIDTH = 15
_TAIL = 9


class MatrixRain(QWidget):
    """一列列下落的字符；头部亮、尾部衰减，底部叠一层扫描线。"""

    def __init__(self, parent: QWidget | None = None, fps: int = 22):
        super().__init__(parent)
        self.setObjectName("matrixRain")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._rng = random.Random(20261116)
        self._columns: list[dict] = []
        self._font = QFont("Consolas", 10)
        self._font.setStyleHint(QFont.StyleHint.Monospace)
        self._tick = 0
        self._timer = QTimer(self)
        self._timer.setInterval(max(16, int(1000 / max(1, fps))))
        self._timer.timeout.connect(self._step)
        self._timer.start()

    # -- 布局 -----------------------------------------------------
    def _ensure_columns(self) -> None:
        count = max(1, self.width() // _COLUMN_WIDTH)
        if len(self._columns) == count:
            return
        self._columns = [
            {
                "y": self._rng.uniform(-self.height(), self.height()),
                "speed": self._rng.uniform(6.0, 22.0),
                "tail": self._rng.randint(4, _TAIL),
                "chars": [self._rng.choice(GLYPHS) for _ in range(_TAIL + 2)],
            }
            for _ in range(count)
        ]

    def _step(self) -> None:
        self._ensure_columns()
        self._tick += 1
        height = max(1, self.height())
        for column in self._columns:
            column["y"] += column["speed"]
            if column["y"] - column["tail"] * 16 > height:
                column["y"] = self._rng.uniform(-height * 0.8, -20)
                column["speed"] = self._rng.uniform(6.0, 22.0)
                column["tail"] = self._rng.randint(4, _TAIL)
            if self._tick % 3 == 0:
                column["chars"][self._rng.randrange(len(column["chars"]))] = self._rng.choice(GLYPHS)
        self.update()

    # -- 绘制 -----------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, False)
        rect = QRectF(self.rect())

        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, QColor(theme.BG))
        gradient.setColorAt(1.0, QColor(theme.BG_ALT))
        painter.fillRect(rect, gradient)

        if not self._columns:
            self._ensure_columns()

        painter.setFont(self._font)
        line_height = 16
        for index, column in enumerate(self._columns):
            x = index * _COLUMN_WIDTH + 3
            head_y = column["y"]
            for offset in range(column["tail"]):
                y = head_y - offset * line_height
                if y < -line_height or y > self.height() + line_height:
                    continue
                glyph = column["chars"][offset % len(column["chars"])]
                if offset == 0:
                    color = QColor(theme.ACCENT)
                    color.setAlpha(200)
                elif offset == 1:
                    color = QColor(theme.ACCENT)
                    color.setAlpha(130)
                else:
                    fade = max(0.0, 1.0 - offset / column["tail"])
                    color = QColor(theme.ACCENT_DIM)
                    color.setAlpha(int(20 + 55 * fade))
                painter.setPen(color)
                painter.drawText(x, int(y), glyph)

        # 扫描线：一行缓慢下移的高亮
        scan_y = (self._tick * 3) % max(1, self.height())
        scan = QPen(QColor(theme.ACCENT_DIM))
        scan.setWidth(1)
        painter.setPen(scan)
        painter.drawLine(0, scan_y, self.width(), scan_y)
        painter.end()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._timer.stop()
        super().closeEvent(event)
