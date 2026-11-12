"""文本清洗 / 日期规整 / 值类型转换。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

_WS = re.compile(r"\s+")
_DATE_PATTERNS = (
    re.compile(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})"),
    re.compile(r"(\d{4})(\d{2})(\d{2})"),
)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return _WS.sub(" ", str(value)).strip()


def parse_date_text(value: Any) -> str:
    """尽量归一化为 YYYY-MM-DD，失败时返回清洗后的原文。"""
    text = clean_text(value)
    if not text:
        return ""
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        year, month, day = (int(g) for g in match.groups())
        try:
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            continue
    return text


def to_int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def to_float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def shorten(value: Any, limit: int = 120) -> str:
    text = clean_text(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"
