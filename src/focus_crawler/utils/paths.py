"""路径工具：统一管理 %APPDATA%/FocusCrawler 下的目录结构。"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

APP_NAME = "FocusCrawler"

MEDIA_KINDS = ("images", "videos", "audios")


def app_home() -> Path:
    """应用根目录，可用 FOCUSCRAWLER_HOME 覆盖（便于测试与便携模式）。"""
    override = os.environ.get("FOCUSCRAWLER_HOME")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_NAME


def db_path(home: Path | None = None) -> Path:
    return (home or app_home()) / "focuscrawler.db"


def safe_task_name(name: str) -> str:
    keep = [c if (c.isalnum() or c in "-_") else "_" for c in name.strip()]
    cleaned = "".join(keep).strip("_")
    return cleaned or "task"


def media_dir(kind: str, task_name: str, day: date | None = None, home: Path | None = None) -> Path:
    """媒体保存目录：media/{images|videos|audios}/{task}/{date}/"""
    if kind not in MEDIA_KINDS:
        raise ValueError(f"未知媒体类型: {kind}")
    root = home or app_home()
    day = day or date.today()
    path = root / "media" / kind / safe_task_name(task_name) / day.isoformat()
    path.mkdir(parents=True, exist_ok=True)
    return path


def export_path(filename: str, home: Path | None = None) -> Path:
    root = home or app_home()
    target = root / "exports" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def log_path(home: Path | None = None) -> Path:
    root = home or app_home()
    target = root / "logs"
    target.mkdir(parents=True, exist_ok=True)
    return target / "focuscrawler.log"
