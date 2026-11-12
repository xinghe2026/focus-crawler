"""GUI 入口：创建 QApplication 并展示主窗口。"""

from __future__ import annotations

import json
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


def _selftest_report() -> dict:
    """打包 / 环境自检：yt-dlp 与 ffmpeg 是否真的可用（窗口ed 程序没有控制台，另存 JSON）。"""
    from ..core import ytdlp_engine
    from ..utils.ffmpeg import describe as describe_ffmpeg

    return {
        "frozen": bool(getattr(sys, "frozen", False)),
        "python": sys.version.split()[0],
        "ytdlp": ytdlp_engine.probe().as_dict(),
        "ffmpeg": describe_ffmpeg(),
    }


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
        # 打包验证用：窗口创建成功后输出能力自检并立即退出
        try:
            from ..utils.paths import app_home

            report = _selftest_report()
            target = app_home() / "selftest.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            if sys.stdout is not None:
                print(json.dumps(report, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001 - 自检失败不影响启动验证
            print(f"selftest failed: {exc}", file=sys.stderr)
        finally:
            window.close()
        return 0
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
