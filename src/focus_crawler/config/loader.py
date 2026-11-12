"""配置文件读写。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schema import ConfigError, TaskConfig


def load_config(path: str | Path) -> TaskConfig:
    file_path = Path(path)
    if not file_path.exists():
        raise ConfigError([f"配置文件不存在: {file_path}"])
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError([f"JSON 解析失败（{file_path}）：{exc}"]) from exc
    return TaskConfig.from_dict(raw)


def load_config_dict(raw: dict[str, Any]) -> TaskConfig:
    return TaskConfig.from_dict(raw)


def save_config(config: TaskConfig, path: str | Path) -> Path:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = config.to_dict()
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return file_path


def list_config_files(directory: str | Path) -> list[Path]:
    folder = Path(directory)
    if not folder.exists():
        return []
    return sorted(folder.glob("*.json"))


def resolve_relative(base_config_path: str | Path, relative: str) -> Path:
    """把脚本等相对路径解析到配置文件所在目录。"""
    if not relative:
        return Path("")
    candidate = Path(relative)
    if candidate.is_absolute():
        return candidate
    return (Path(base_config_path).parent / candidate).resolve()
