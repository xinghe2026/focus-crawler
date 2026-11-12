"""pytest 公共夹具：本地 fixture 站点 + 临时数据目录。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from focus_crawler.config.schema import TaskConfig
from focus_crawler.core.fetcher import Fetcher
from focus_crawler.core.storage import Storage

# GUI 冒烟测试使用离屏平台：无桌面环境可运行，也避免 Windows 下 Qt 退出时崩溃
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
BASE_URL = "http://fixture.test"

_MEDIA = {
    "/media/a.jpg": ("image/jpeg", b"JPEG-DATA-A"),
    "/media/b.png": ("image/png", b"PNG-DATA-B"),
    "/media/c.jpg": ("image/jpeg", b"JPEG-DATA-C"),
    "/media/v1.mp4": ("video/mp4", b"MP4-DATA"),
    "/media/v2.m3u8": ("application/vnd.apple.mpegurl", b"#EXTM3U\n#EXTINF:1.0,\nseg1.ts\n"),
    "/media/a1.mp3": ("audio/mpeg", b"MP3-DATA"),
}


def fixture_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path in _MEDIA:
        content_type, payload = _MEDIA[path]
        return httpx.Response(200, headers={"content-type": content_type}, content=payload)
    relative = path.lstrip("/")
    file_path = FIXTURES_DIR / relative
    if file_path.is_file():
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=file_path.read_text(encoding="utf-8"),
        )
    return httpx.Response(404, text="not found")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "app_home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FOCUSCRAWLER_HOME", str(home))
    return home


@pytest.fixture
def fixture_client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(fixture_handler), follow_redirects=True)


@pytest.fixture
def fetcher(fixture_client) -> Fetcher:
    return Fetcher(
        timeout_ms=5000,
        retry=0,
        delay_ms=(0, 0),
        respect_robots=False,
        client=fixture_client,
        sleep=lambda _seconds: None,
        random_uniform=lambda low, high: low,
    )


@pytest.fixture
def task_config() -> TaskConfig:
    raw = json.loads((FIXTURES_DIR / "site_task.json").read_text(encoding="utf-8"))
    return TaskConfig.from_dict(raw)


@pytest.fixture
def store(tmp_path) -> Storage:
    storage = Storage(tmp_path / "test.db")
    yield storage
    storage.close()


@pytest.fixture
def task_id(store, task_config) -> int:
    return store.upsert_task(task_config.task_name, json.dumps(task_config.to_dict(), ensure_ascii=False))
