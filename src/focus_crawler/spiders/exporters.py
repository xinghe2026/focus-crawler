"""导出器：JSON / JSONL / CSV / XML（对应 Scrapling 的内置导出）。"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable, Sequence
from xml.sax.saxutils import escape

from .items import Item, fieldnames_of

FORMATS = ("json", "jsonl", "csv", "xml")


def _as_item(value: Any) -> Item:
    if isinstance(value, Item):
        return value
    if isinstance(value, dict):
        return Item(value)
    return Item(value=value)


def to_json(items: Iterable[Any], indent: int = 2) -> str:
    data = [dict(_as_item(item)) for item in items]
    return json.dumps(data, ensure_ascii=False, indent=indent)


def to_jsonl(items: Iterable[Any]) -> str:
    return "".join(
        json.dumps(dict(_as_item(item)), ensure_ascii=False) + "\n" for item in items
    )


def to_csv(items: Iterable[Any], fieldnames: Sequence[str] | None = None) -> str:
    import io

    rows = [_as_item(item) for item in items]
    fields = list(fieldnames or fieldnames_of(rows))
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_value(row.get(key, "")) for key in fields})
    return buffer.getvalue()


def to_xml(items: Iterable[Any], root: str = "items", item_tag: str = "item") -> str:
    parts = [f'<?xml version="1.0" encoding="utf-8"?>\n<{root}>']
    for row in items:
        parts.append(f"<{item_tag}>")
        for key, value in dict(_as_item(row)).items():
            parts.append(f"<{key}>{escape(_xml_value(value))}</{key}>")
        parts.append(f"</{item_tag}>")
    parts.append(f"</{root}>")
    return "".join(parts)


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _xml_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def export_items(items: Iterable[Any], path: str | Path, fmt: str | None = None) -> Path:
    """把 items 写文件；``fmt`` 缺省时按扩展名推断。"""
    materialized = [_as_item(item) for item in items]
    target = Path(path)
    fmt = (fmt or target.suffix.lstrip(".") or "json").lower()
    if fmt in ("ndjson", "jl"):
        fmt = "jsonl"
    if fmt not in FORMATS:
        raise ValueError(f"不支持的导出格式：{fmt}，可选 {FORMATS}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        payload = to_json(materialized)
    elif fmt == "jsonl":
        payload = to_jsonl(materialized)
    elif fmt == "csv":
        payload = to_csv(materialized)
    else:
        payload = to_xml(materialized)
    target.write_text(payload, encoding="utf-8")
    return target


def export_items_stream(items: Iterable[Any], path: str | Path, fmt: str | None = None) -> Path:
    """流式写出：不把全部结果物化到内存（适合十万级 Item）。"""
    target = Path(path)
    fmt = (fmt or target.suffix.lstrip(".") or "json").lower()
    if fmt in ("ndjson", "jl"):
        fmt = "jsonl"
    if fmt not in FORMATS:
        raise ValueError(f"不支持的导出格式：{fmt}，可选 {FORMATS}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        if fmt == "json":
            handle.write("[")
            first = True
            for item in items:
                payload = json.dumps(dict(_as_item(item)), ensure_ascii=False)
                handle.write(("" if first else ",") + "\n" + payload)
                first = False
            handle.write("\n]")
        elif fmt == "jsonl":
            for item in items:
                handle.write(json.dumps(dict(_as_item(item)), ensure_ascii=False) + "\n")
        elif fmt == "csv":
            writer = None
            for item in items:
                row = _as_item(item)
                if writer is None:
                    fields = fieldnames_of([row])
                    writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
                    writer.writeheader()
                writer.writerow({key: _csv_value(row.get(key, "")) for key in writer.fieldnames})
        else:  # xml
            handle.write('<?xml version="1.0" encoding="utf-8"?>\n<items>')
            for item in items:
                handle.write("<item>")
                for key, value in dict(_as_item(item)).items():
                    handle.write(f"<{key}>{escape(_xml_value(value))}</{key}>")
                handle.write("</item>")
            handle.write("</items>")
    return target


__all__ = [
    "FORMATS",
    "export_items",
    "export_items_stream",
    "to_csv",
    "to_json",
    "to_jsonl",
    "to_xml",
]
