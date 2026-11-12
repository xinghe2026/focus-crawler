"""采集条目模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ItemRecord:
    task_id: int
    url: str
    url_hash: str
    data: dict[str, Any] = field(default_factory=dict)
    content_hash: str | None = None
