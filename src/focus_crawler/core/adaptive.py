"""自适应元素定位（借鉴 Scrapling 的 adaptive selector 思路，独立实现）。

爬虫规则最怕页面改版：今天 ``div.title > h1`` 明天变成 ``div.headline > h1``，
选择器一失效整列字段就全空。这里给每个字段保存一份"元素指纹"
（标签、文本、属性、DOM 路径、父节点特征），选择器失效时按相似度在页面上重新
定位同一个元素，并把新指纹回写，下次继续可用。

算法思路参考 Scrapling（BSD-3-Clause）的 relocate / similarity score，
本文件为独立实现，不引入其运行时依赖。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable

import lxml.html
from lxml.html import HtmlElement

TEXT_LIMIT = 120
PATH_LIMIT = 240
_FOCUS_ATTRS = ("class", "id", "href", "src", "data-id", "itemprop")


def clean_text(value: str | None) -> str:
    return " ".join((value or "").split())[:TEXT_LIMIT]


def parse_html(html: str) -> HtmlElement | None:
    try:
        return lxml.html.fromstring(html or "<html></html>")
    except (ValueError, lxml.etree.ParserError):
        return None


def _attrs(element: HtmlElement) -> dict[str, str]:
    return {str(key): str(value) for key, value in (element.attrib or {}).items()}


def _tag(element: HtmlElement | None) -> str:
    if element is None:
        return ""
    tag = element.tag
    return tag.lower() if isinstance(tag, str) else ""


@dataclass
class ElementFingerprint:
    """元素指纹：足够稳定，又不依赖单一属性。"""

    tag: str = ""
    text: str = ""
    attributes: dict[str, str] = field(default_factory=dict)
    path: str = ""
    parent_tag: str = ""
    parent_attributes: dict[str, str] = field(default_factory=dict)
    parent_text: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str | None) -> "ElementFingerprint | None":
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        return cls(
            tag=str(payload.get("tag") or ""),
            text=str(payload.get("text") or ""),
            attributes={str(k): str(v) for k, v in (payload.get("attributes") or {}).items()},
            path=str(payload.get("path") or ""),
            parent_tag=str(payload.get("parent_tag") or ""),
            parent_attributes={
                str(k): str(v) for k, v in (payload.get("parent_attributes") or {}).items()
            },
            parent_text=str(payload.get("parent_text") or ""),
        )


def fingerprint(element: HtmlElement) -> ElementFingerprint:
    """从元素提取指纹。"""
    parent = element.getparent()
    return ElementFingerprint(
        tag=_tag(element),
        text=clean_text(element.text_content()),
        attributes=_attrs(element),
        path=(element.getroottree().getpath(element) if element.getroottree() else "")[:PATH_LIMIT],
        parent_tag=_tag(parent),
        parent_attributes=_attrs(parent) if parent is not None else {},
        parent_text=clean_text(parent.text_content()) if parent is not None else "",
    )


def _ratio(left: str, right: str) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _dict_similarity(left: dict[str, str], right: dict[str, str]) -> float:
    if not left and not right:
        return 1.0
    keys = set(left) | set(right)
    if not keys:
        return 1.0
    matched = 0.0
    for key in keys:
        if key in left and key in right:
            matched += 1.0 if left[key] == right[key] else 0.5
    return matched / len(keys)


def score(original: ElementFingerprint, candidate: ElementFingerprint) -> float:
    """0~100 的相似度。"""
    total = 0.0
    checks = 0

    total += 1.0 if original.tag == candidate.tag else 0.0
    checks += 1

    if original.text:
        total += _ratio(original.text, candidate.text)
        checks += 1

    total += _dict_similarity(original.attributes, candidate.attributes)
    checks += 1

    for name in _FOCUS_ATTRS:
        if original.attributes.get(name):
            total += _ratio(original.attributes[name], candidate.attributes.get(name, ""))
            checks += 1

    total += _ratio(original.path, candidate.path)
    checks += 1

    if original.parent_tag:
        total += 1.0 if original.parent_tag == candidate.parent_tag else 0.0
        checks += 1
        total += _dict_similarity(original.parent_attributes, candidate.parent_attributes)
        checks += 1
        if original.parent_text:
            total += _ratio(original.parent_text, candidate.parent_text)
            checks += 1

    return round(total / max(checks, 1) * 100, 2)


def iter_elements(root: HtmlElement, tag: str = "") -> Iterable[HtmlElement]:
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue  # 跳过注释 / 处理指令
        if tag and element.tag.lower() != tag.lower():
            continue
        yield element


def relocate(
    root: HtmlElement,
    original: ElementFingerprint,
    threshold: float = 40.0,
) -> tuple[HtmlElement | None, float]:
    """在页面上找与指纹最接近的元素，返回 (元素, 最高分)。"""
    best: HtmlElement | None = None
    best_score = 0.0
    # 先只看同标签的元素：剪掉大部分候选，避免对整棵树做字符串比对
    candidates = list(iter_elements(root, original.tag)) or list(iter_elements(root))
    for element in candidates:
        current = score(original, fingerprint(element))
        if current > best_score:
            best, best_score = element, current
            if best_score >= 99.0:
                break
    if best is not None and best_score >= threshold:
        return best, best_score
    return None, best_score


class AdaptiveStore:
    """指纹存取：优先走 SQLite（跨任务、跨进程生效）。"""

    def __init__(self, storage: Any, task_id: int):
        self.storage = storage
        self.task_id = task_id

    def load(self, field_name: str, domain: str) -> ElementFingerprint | None:
        loader = getattr(self.storage, "get_adaptive_fingerprint", None)
        if loader is None:
            return None
        try:
            raw = loader(self.task_id, field_name, domain)
        except Exception:  # noqa: BLE001 - 存储异常不影响解析
            return None
        return ElementFingerprint.from_json(raw)

    def save(self, field_name: str, domain: str, element: HtmlElement) -> None:
        saver = getattr(self.storage, "save_adaptive_fingerprint", None)
        if saver is None:
            return
        try:
            saver(self.task_id, field_name, domain, fingerprint(element).to_json())
        except Exception:  # noqa: BLE001 - 存储异常不影响解析
            pass


def domain_of(page_url: str) -> str:
    try:
        from urllib.parse import urlsplit

        return (urlsplit(page_url).hostname or "").lower()
    except ValueError:
        return ""


__all__ = [
    "AdaptiveStore",
    "ElementFingerprint",
    "domain_of",
    "fingerprint",
    "iter_elements",
    "parse_html",
    "relocate",
    "score",
]
