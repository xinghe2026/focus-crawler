"""字段解析：CSS / XPath / 正则 / JSONPath / 属性 / 变换。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

from parsel import Selector, SelectorList

from ..config.schema import DetailConfig, FieldSpec
from ..models.parse_context import XhrResponse
from ..utils.jsonpath import JsonPathError, query
from ..utils.text import clean_text, parse_date_text, to_float, to_int


@dataclass
class ParsedPage:
    data: dict[str, Any] = field(default_factory=dict)
    hits: dict[str, bool] = field(default_factory=dict)

    @property
    def hit_rate(self) -> float:
        if not self.hits:
            return 0.0
        return sum(1 for value in self.hits.values() if value) / len(self.hits)

    @property
    def missing(self) -> list[str]:
        return [name for name, hit in self.hits.items() if not hit]


def _is_xpath(expression: str) -> bool:
    stripped = expression.lstrip()
    return stripped.startswith(("/", "(", "//")) or stripped.startswith("./")


def _select(root: Selector, expression: str) -> SelectorList:
    return root.xpath(expression) if _is_xpath(expression) else root.css(expression)


def _node_text(node: Selector) -> str:
    value = node.xpath("string(.)").get()
    if not value:
        raw = node.get()
        if raw and "<" not in raw:
            value = raw
    return clean_text(value or "")


def _apply_transforms(value: Any, transforms: list[str], page_url: str) -> Any:
    for name in transforms:
        if value is None:
            return value
        if name == "strip":
            value = clean_text(value)
        elif name == "date":
            value = parse_date_text(value)
        elif name == "lower":
            value = str(value).lower()
        elif name == "upper":
            value = str(value).upper()
        elif name == "int":
            value = to_int(value)
        elif name == "float":
            value = to_float(value)
        elif name == "absolute_url":
            value = urljoin(page_url, str(value))
    return value


def _json_source(spec: FieldSpec, responses: list[XhrResponse]) -> Any:
    if not responses:
        return None
    try:
        payload = responses[spec.response_index].json()
    except (IndexError, ValueError):
        return None
    if spec.jsonpath:
        try:
            values = query(payload, spec.jsonpath)
        except JsonPathError:
            return None
        return values[0] if values else None
    return payload


def extract_field(html: str, page_url: str, spec: FieldSpec, responses: list[XhrResponse] | None = None) -> Any:
    responses = responses or []
    value: Any = None

    if spec.type == "current_url" or spec.source == "url":
        value = page_url
    elif spec.source == "response":
        value = _json_source(spec, responses)
    else:
        root = Selector(text=html or "")
        expression = spec.selector or ""
        if expression:
            nodes = _select(root, expression)
            node = nodes[0] if len(nodes) else None
        else:
            node = None

        if node is not None:
            if spec.type == "attr":
                value = node.attrib.get(spec.attribute or "src")
            elif spec.type == "html":
                value = node.get()
            elif spec.type == "json":
                text = _node_text(node)
                try:
                    value = json.loads(text) if text else None
                except json.JSONDecodeError:
                    value = None
            else:
                value = _node_text(node)

        if spec.regex:
            text = value if isinstance(value, str) else (html or "")
            match = re.search(spec.regex, text, re.S)
            if match:
                value = match.group(1) if match.groups() else match.group(0)
            else:
                value = None

        if isinstance(value, str) and spec.type == "json" and spec.jsonpath:
            try:
                payload = json.loads(value)
                values = query(payload, spec.jsonpath)
                value = values[0] if values else None
            except (json.JSONDecodeError, JsonPathError):
                value = None

    if value is None or (isinstance(value, str) and not value.strip()):
        value = spec.default
    value = _apply_transforms(value, spec.transform, page_url)
    return value


def parse_detail(
    html: str,
    page_url: str,
    detail: DetailConfig,
    responses: list[XhrResponse] | None = None,
) -> ParsedPage:
    result = ParsedPage()
    for name, spec in detail.fields.items():
        value = extract_field(html, page_url, spec, responses)
        result.data[name] = value
        if isinstance(value, str):
            result.hits[name] = bool(value.strip())
        else:
            result.hits[name] = value not in (None, {}, [])
    return result


def summarize_hits(pages: list[ParsedPage]) -> dict[str, dict[str, Any]]:
    """汇总多个页面的字段命中率与示例值。"""
    summary: dict[str, dict[str, Any]] = {}
    total = len(pages)
    if total == 0:
        return summary
    field_names = {name for page in pages for name in page.hits}
    for name in sorted(field_names):
        hits = sum(1 for page in pages if page.hits.get(name))
        example = next((page.data.get(name) for page in pages if page.hits.get(name)), "")
        summary[name] = {
            "hit_rate": hits / total,
            "hits": hits,
            "total": total,
            "example": example,
        }
    return summary
