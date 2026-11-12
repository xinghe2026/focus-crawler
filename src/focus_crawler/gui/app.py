"""GUI 入口：创建 QApplication 并展示主窗口。"""

from __future__ import annotations

import os
import sys

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from ..utils.logging import setup_logging
from .main_window import MainWindow

_UI_FONTS = ("Microsoft YaHei UI", "Microsoft YaHei", "SimSun")


def _apply_font(app: QApplication) -> None:
    """优先使用系统中文字体，避免界面出现方块。"""
    families = set(QFontDatabase.families())
    for family in _UI_FONTS:
        if family in families:
            app.setFont(QFont(family, 9))
            return


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    app = QApplication(argv if argv is not None else sys.argv)
    _apply_font(app)
    app.setApplicationName("FocusCrawler")
    app.setOrganizationName("FocusCrawler")
    app.setApplicationDisplayName("FocusCrawler 配置驱动爬虫")
    window = MainWindow()
    window.show()
    if os.environ.get("FOCUSCRAWLER_SMOKE_TEST") == "1":
        # 打包验证用：窗口创建成功后立即退出
        window.close()
        return 0
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
