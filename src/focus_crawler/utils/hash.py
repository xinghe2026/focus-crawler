"""哈希工具：URL / 内容 / 媒体去重键。"""

from __future__ import annotations

import hashlib
from urllib.parse import urldefrag, urlsplit, urlunsplit


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_url(url: str) -> str:
    """去掉 fragment，规整 host 大小写，保留 query。"""
    url = (url or "").strip()
    url, _ = urldefrag(url)
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def url_hash(url: str) -> str:
    return sha256_text(normalize_url(url))


def content_hash(html: str) -> str:
    """对正文做轻量归一化后再哈希，避免空白差异导致重复抓取。"""
    from .text import clean_text

    return sha256_text(clean_text(html))


def media_hash(media_url: str) -> str:
    return sha256_text(normalize_url(media_url))
