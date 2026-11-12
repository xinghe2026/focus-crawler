"""任务记录模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TaskRecord:
    id: int
    name: str
    config_json: str
    created_at: str
    updated_at: str
