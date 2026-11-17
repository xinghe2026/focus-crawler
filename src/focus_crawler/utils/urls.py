"""URL 辅助：统一 host 解析，避免各模块各写一份。"""

from __future__ import annotations

from urllib.parse import urlsplit


def host_of(url: str) -> str:
    """返回小写 host（无端口）；解析失败返回空串。"""
    try:
        return (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""


def host_matches(host: str, pattern: str) -> bool:
    """域名后缀匹配：支持精确、*.example.com、example.* 与 *。"""
    host = (host or "").lower()
    pattern = (pattern or "").strip().lower()
    if not host or not pattern:
        return False
    if pattern == "*":
        return True
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return host == pattern[2:] or host.endswith(suffix)
    if pattern.endswith("*"):
        return host.startswith(pattern[:-1])
    return host == pattern or host.endswith("." + pattern)


__all__ = ["host_matches", "host_of"]
