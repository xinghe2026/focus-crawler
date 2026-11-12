"""配置加载与校验测试。"""

from __future__ import annotations

import json

import pytest

from focus_crawler.config.loader import load_config, save_config
from focus_crawler.config.schema import ConfigError, TaskConfig

MINIMAL = {
    "task_name": "demo",
    "start_urls": ["https://example.com/"],
    "allowed_domains": ["example.com"],
}


def test_minimal_config_defaults():
    task = TaskConfig.from_dict(MINIMAL)
    assert task.crawl.max_depth == 1
    assert task.link_discovery.mode == "auto"
    assert task.subpage_collect.enabled is False
    assert task.subpage_collect.types == ["data", "image"]
    assert task.subpage_collect.media_mode == "discover_only"
    assert task.image.enabled is False
    assert task.video.enabled is False
    assert task.audio.enabled is False


def test_invalid_config_collects_errors():
    bad = {
        "task_name": "",
        "start_urls": ["ftp://example.com"],
        "allowed_domains": [],
        "crawl": {"delay_ms": [1]},
        "link_discovery": {"mode": "magic"},
        "subpage_collect": {"types": ["data", "video", "pdf"]},
        "parser": {"mode": "auto"},
    }
    with pytest.raises(ConfigError) as excinfo:
        TaskConfig.from_dict(bad)
    messages = " ".join(excinfo.value.errors)
    assert "task_name" in messages
    assert "start_urls" in messages
    assert "allowed_domains" in messages
    assert "delay_ms" in messages
    assert "link_discovery.mode" in messages
    assert "pdf" in messages


def test_save_and_load_roundtrip(tmp_path):
    task = TaskConfig.from_dict(MINIMAL)
    target = tmp_path / "task.json"
    save_config(task, target)
    loaded = load_config(target)
    assert loaded.task_name == "demo"
    assert loaded.to_dict()["subpage_collect"]["media_mode"] == "discover_only"
    assert json.dumps(loaded.to_dict(), ensure_ascii=False)


def test_relative_script_path_is_accepted():
    raw = dict(MINIMAL)
    raw["parser"] = {"mode": "custom", "detail_script": "detail_parser.py"}
    task = TaskConfig.from_dict(raw)
    assert task.parser.mode == "custom"
    assert task.parser.detail_script == "detail_parser.py"
