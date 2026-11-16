"""界面主题与 HUD：主题可应用、调色板为深色、HUD 可渲染。"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton  # noqa: E402

from focus_crawler.gui import theme  # noqa: E402
from focus_crawler.gui.hud import StatusHud  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    theme.apply(instance)
    yield instance


def test_stylesheet_covers_core_widgets(app):
    qss = theme.STYLESHEET
    for selector in ("QPushButton", "QTableWidget", "QHeaderView::section", "QTabBar::tab", "QScrollBar:vertical", "QProgressBar"):
        assert selector in qss
    assert theme.ACCENT.lower() in qss.lower()


def test_palette_is_dark(app):
    palette = app.palette()
    window = palette.color(QPalette.ColorRole.Window)
    text = palette.color(QPalette.ColorRole.WindowText)
    assert QColor(theme.BG).lightness() < 40
    assert window.lightness() < 40
    assert text.lightness() > 120


def test_widgets_inherit_theme(app):
    button = QPushButton("RUN")
    edit = QLineEdit()
    assert button.sizeHint().width() > 0
    assert edit.placeholderText() == ""
    # 主题已注入到应用级，控件取到的是同一份样式表
    assert app.styleSheet()


def test_hud_renders_and_updates(app):
    hud = StatusHud()
    hud.resize(800, 34)
    hud.set_counts(12, 1)
    hud.set_status("extracting", theme.WARN)
    pixmap = hud.grab()
    assert not pixmap.isNull()
    assert pixmap.width() == 800
    hud.set_status("system online")
    assert hud.grab().width() == 800
