"""PySide6 桌面界面。

GUI 只负责交互与调度，抓取、解析、存储逻辑全部复用 core 包，
保证 CLI 与界面行为一致。
"""

from __future__ import annotations

__all__ = ["main"]


def main() -> int:
    from .app import main as _main

    return _main()
