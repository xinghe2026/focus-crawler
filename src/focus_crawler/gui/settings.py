"""GUI 设置：数据目录、ffmpeg、默认抓取参数（QSettings 持久化）。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings

from ..utils.paths import app_home

ORG = "FocusCrawler"
APP = "FocusCrawler"


def _store() -> QSettings:
    return QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, ORG, APP)


def data_dir() -> Path:
    value = _store().value("data_dir", "", type=str)
    return Path(value) if value else app_home()


def db_file() -> Path:
    value = _store().value("db_file", "", type=str)
    if value:
        return Path(value)
    return data_dir() / "focuscrawler.db"


def tasks_dir() -> Path:
    path = data_dir() / "tasks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def exports_dir() -> Path:
    path = data_dir() / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_media_root() -> Path:
    """默认媒体根目录：其下创建 media/{images|videos|audios}。"""
    return app_home()


def media_root() -> Path:
    value = _store().value("media_root", "", type=str)
    return Path(value) if value else default_media_root()


def ffmpeg_path() -> str:
    return _store().value("ffmpeg_path", "ffmpeg", type=str) or "ffmpeg"


def default_crawl() -> dict:
    store = _store()
    return {
        "max_depth": store.value("default_max_depth", 0, type=int),
        "max_pages": store.value("default_max_pages", 500, type=int),
        "concurrency": store.value("default_concurrency", 2, type=int),
        "delay_ms": [
            store.value("default_delay_min", 800, type=int),
            store.value("default_delay_max", 2000, type=int),
        ],
        "timeout_ms": 20000,
        "retry": 3,
        "respect_robots": store.value("default_respect_robots_v2", False, type=bool),
    }


def save(values: dict) -> None:
    store = _store()
    for key, value in values.items():
        store.setValue(key, value)
    store.sync()


def read_values() -> dict:
    store = _store()
    return {
        "data_dir": store.value("data_dir", "", type=str),
        "media_root": store.value("media_root", "", type=str),
        "ffmpeg_path": store.value("ffmpeg_path", "ffmpeg", type=str),
        "default_concurrency": store.value("default_concurrency", 2, type=int),
        "default_delay_min": store.value("default_delay_min", 800, type=int),
        "default_delay_max": store.value("default_delay_max", 2000, type=int),
        "default_respect_robots": store.value("default_respect_robots_v2", False, type=bool),
    }
