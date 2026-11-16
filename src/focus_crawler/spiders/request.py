"""Request / Response：爬虫与下载层之间的传输对象。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from parsel import Selector


def canonical_url(url: str) -> str:
    """规范化 URL：小写 scheme/host、去掉默认端口与 fragment、保留 query。"""
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    if parts.username:
        netloc = f"{parts.username}@{netloc}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


@dataclass
class Request:
    """一次待抓取请求。"""

    url: str
    callback: str | None = None
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    dont_filter: bool = False
    session: str = "default"
    retries: int = 0
    errback: str | None = None

    def __post_init__(self) -> None:
        self.url = canonical_url(self.url)

    @property
    def key(self) -> str:
        return f"{self.method}:{self.url}"

    def replace(self, **changes: Any) -> "Request":
        return replace(self, **changes)


@dataclass
class Response:
    """抓取结果，提供给回调解析。"""

    url: str
    status: int
    headers: dict[str, str]
    content: bytes
    request: Request | None = None
    elapsed_ms: int = 0
    from_cache: bool = False
    encoding: str | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def text(self) -> str:
        if self.encoding:
            try:
                return self.content.decode(self.encoding, errors="replace")
            except LookupError:
                pass
        for key in ("content-type", "Content-Type"):
            value = self.headers.get(key, "")
            if "charset=" in value.lower():
                charset = value.lower().split("charset=", 1)[1].split(";")[0].strip()
                try:
                    return self.content.decode(charset, errors="replace")
                except (LookupError, UnicodeDecodeError):
                    break
        return self.content.decode("utf-8", errors="replace")

    @property
    def selector(self) -> Selector:
        return Selector(text=self.text)

    def css(self, query: str):
        return self.selector.css(query)

    def xpath(self, query: str):
        return self.selector.xpath(query)

    def json(self) -> Any:
        import json as _json

        return _json.loads(self.text)

    def urljoin(self, href: str) -> str:
        return urljoin(self.url, href)

    def follow(self, href: str, **kwargs: Any) -> Request:
        return Request(url=self.urljoin(href), **kwargs)


__all__ = ["Request", "Response", "canonical_url"]
