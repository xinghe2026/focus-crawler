"""Item：抓取到的数据单元，带内置导出（JSON / JSONL / CSV / XML）。"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Iterable
from xml.sax.saxutils import escape


class Item(dict):
    """dict 子类，支持常见导出格式。"""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)

    # -- 导出 -----------------------------------------------------
    def to_json(self, **kwargs: Any) -> str:
        kwargs.setdefault("ensure_ascii", False)
        return json.dumps(dict(self), **kwargs)

    def to_jsonl(self, **kwargs: Any) -> str:
        return self.to_json(**kwargs) + "\n"

    def to_csv_row(self, fieldnames: list[str] | None = None) -> str:
        fields = fieldnames or list(self.keys())
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
        writer.writerow({key: self.get(key, "") for key in fields})
        return buffer.getvalue().rstrip("\r\n")

    def to_xml(self, root: str = "item") -> str:
        parts = [f"<{root}>"]
        for key, value in self.items():
            parts.append(f"<{key}>{escape(_stringify(value))}</{key}>")
        parts.append(f"</{root}>")
        return "".join(parts)


def _stringify(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return ""
    return str(value)


def fieldnames_of(items: Iterable[Item]) -> list[str]:
    """按出现顺序汇总所有字段名。"""
    names: list[str] = []
    seen: set[str] = set()
    for item in items:
        for key in item.keys():
            if key not in seen:
                seen.add(key)
                names.append(key)
    return names


__all__ = ["Item", "fieldnames_of"]
