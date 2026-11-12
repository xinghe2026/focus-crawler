"""PyInstaller 打包入口：启动 FocusCrawler 桌面界面。"""

from __future__ import annotations

from focus_crawler.gui.app import main

if __name__ == "__main__":
    raise SystemExit(main())
