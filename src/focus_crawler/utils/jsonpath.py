"""轻量 JSONPath 子集：支持 $.a.b、$.a[0]、$..key、[*]。"""

from __future__ import annotations

import re
from typing import Any

_TOKEN = re.compile(
    r"\s*(?:\.\.([A-Za-z_][\w-]*)|\.([A-Za-z_][\w-]*|\*)|\[\s*([^\]]+?)\s*\])\s*"
)


class JsonPathError(ValueError):
    pass


def _as_token(match: re.Match) -> tuple[str, Any]:
    recursive, dotted, bracket = match.groups()
    if recursive:
        return ("recursive", recursive)
    if dotted:
        return ("wildcard", None) if dotted == "*" else ("key", dotted)
    token = bracket.strip()
    if token == "*":
        return ("wildcard", None)
    if token[:1] in ("'", '"') and token[-1:] == token[:1]:
        return ("key", token[1:-1])
    if re.fullmatch(r"-?\d+", token):
        return ("index", int(token))
    raise JsonPathError(f"不支持的 JSONPath 片段: [{token}]")


def _children(node: Any) -> list[Any]:
    if isinstance(node, dict):
        return list(node.values())
    if isinstance(node, list):
        return list(node)
    return []


def _find_recursive(node: Any, key: str, out: list[Any]) -> None:
    if isinstance(node, dict):
        for name, value in node.items():
            if name == key:
                out.append(value)
            _find_recursive(value, key, out)
    elif isinstance(node, list):
        for value in node:
            _find_recursive(value, key, out)


def query(data: Any, expr: str) -> list[Any]:
    """返回所有匹配值；表达式非法时抛 JsonPathError。"""
    if not isinstance(expr, str) or not expr.strip():
        raise JsonPathError("JSONPath 不能为空")
    text = expr.strip()
    if not text.startswith("$"):
        raise JsonPathError("JSONPath 必须以 $ 开头")

    tokens: list[tuple[str, Any]] = []
    pos = 1
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match or match.start() != pos:
            raise JsonPathError(f"无法解析的 JSONPath 位置: {text[pos:pos + 20]!r}")
        tokens.append(_as_token(match))
        pos = match.end()

    current: list[Any] = [data]
    for kind, value in tokens:
        next_nodes: list[Any] = []
        for node in current:
            if kind == "key":
                if isinstance(node, dict) and value in node:
                    next_nodes.append(node[value])
            elif kind == "index":
                if isinstance(node, list) and -len(node) <= value < len(node):
                    next_nodes.append(node[value])
            elif kind == "wildcard":
                next_nodes.extend(_children(node))
            elif kind == "recursive":
                found: list[Any] = []
                _find_recursive(node, value, found)
                next_nodes.extend(found)
        current = next_nodes
        if not current:
            break
    return current
