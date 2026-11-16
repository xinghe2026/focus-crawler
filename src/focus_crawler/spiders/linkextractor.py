"""LinkExtractor：可组合的链接抽取原语（参考 Scrapy LinkExtractor 的语义）。

支持：allow / deny 正则、allow_domains / deny_domains、restrict_css / restrict_xpaths、
deny_extensions、process_value 钩子、URL 规范化与去重。
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable
from urllib.parse import urljoin, urlsplit

from parsel import Selector

from .request import Request, canonical_url

DENY_EXTENSIONS = {
    "7z", "apk", "avi", "bmp", "css", "csv", "doc", "docx", "exe", "gif", "gz", "ico",
    "iso", "jpeg", "jpg", "js", "json", "m4a", "m4v", "mkv", "mov", "mp3", "mp4", "mpg",
    "pdf", "png", "ppt", "pptx", "rar", "rss", "svg", "swf", "tar", "tgz", "wav", "webp",
    "wmv", "woff", "woff2", "xls", "xlsx", "xml", "zip", "m3u8", "ts", "webmanifest",
}


def _as_patterns(value: str | Iterable[str] | None) -> list[re.Pattern[str]]:
    if value is None:
        return []
    items = [value] if isinstance(value, str) else list(value)
    patterns: list[re.Pattern[str]] = []
    for item in items:
        try:
            patterns.append(re.compile(item))
        except re.error:
            continue
    return patterns


def _matches(patterns: list[re.Pattern[str]], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


class LinkExtractor:
    """从页面中抽取符合规则的链接，产出 :class:`Request`。"""

    def __init__(
        self,
        allow: str | Iterable[str] | None = None,
        deny: str | Iterable[str] | None = None,
        allow_domains: str | Iterable[str] | None = None,
        deny_domains: str | Iterable[str] | None = None,
        restrict_css: str | Iterable[str] | None = None,
        restrict_xpaths: str | Iterable[str] | None = None,
        deny_extensions: Iterable[str] | None = None,
        tags: Iterable[str] = ("a", "area"),
        attrs: Iterable[str] = ("href",),
        canonicalize: bool = True,
        unique: bool = True,
        process_value: Callable[[str], str | None] | None = None,
        strip: bool = True,
    ):
        self.allow = _as_patterns(allow)
        self.deny = _as_patterns(deny)
        self.allow_domains = {
            item.lower() for item in _iter_str(allow_domains)
        }
        self.deny_domains = {item.lower() for item in _iter_str(deny_domains)}
        self.restrict_css = [restrict_css] if isinstance(restrict_css, str) else list(restrict_css or [])
        self.restrict_xpaths = (
            [restrict_xpaths] if isinstance(restrict_xpaths, str) else list(restrict_xpaths or [])
        )
        self.deny_extensions = set(deny_extensions or DENY_EXTENSIONS)
        self.tags = tuple(tags)
        self.attrs = tuple(attrs)
        self.canonicalize = canonicalize
        self.unique = unique
        self.process_value = process_value
        self.strip = strip

    # -- 过滤 -----------------------------------------------------
    def _domain_ok(self, url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        if not host:
            return False
        if self.allow_domains and not any(
            host == domain or host.endswith("." + domain) for domain in self.allow_domains
        ):
            return False
        if self.deny_domains and any(
            host == domain or host.endswith("." + domain) for domain in self.deny_domains
        ):
            return False
        return True

    def _extension_ok(self, url: str) -> bool:
        path = urlsplit(url).path
        ext = posixpath.splitext(path)[1].lstrip(".").lower()
        return not (ext and ext in self.deny_extensions)

    def _pattern_ok(self, url: str) -> bool:
        if self.allow and not _matches(self.allow, url):
            return False
        if self.deny and _matches(self.deny, url):
            return False
        return True

    def matches(self, url: str) -> bool:
        if not url.lower().startswith(("http://", "https://")):
            return False
        return self._domain_ok(url) and self._extension_ok(url) and self._pattern_ok(url)

    # -- 抽取 -----------------------------------------------------
    def _scopes(self, selector: Selector) -> list[Selector]:
        if not self.restrict_css and not self.restrict_xpaths:
            return [selector]
        scopes: list[Selector] = []
        for css in self.restrict_css:
            scopes.extend(selector.css(css))
        for xpath in self.restrict_xpaths:
            scopes.extend(selector.xpath(xpath))
        return scopes or [selector]

    def extract_links(self, response_or_html, base_url: str | None = None) -> list[str]:
        """返回合格的绝对 URL 列表（保持页面顺序、按需去重）。"""
        if hasattr(response_or_html, "selector"):
            selector = response_or_html.selector
            base = base_url or getattr(response_or_html, "url", "")
        else:
            html = str(response_or_html or "")
            selector = Selector(text=html)
            base = base_url or ""
            if not base:
                href_base = selector.css("base::attr(href)").get()
                base = href_base or ""

        results: list[str] = []
        seen: set[str] = set()
        for scope in self._scopes(selector):
            for tag in self.tags:
                for attr in self.attrs:
                    for value in scope.css(f"{tag}::attr({attr})").getall():
                        href = value.strip() if self.strip else value
                        if not href:
                            continue
                        if self.process_value is not None:
                            href = self.process_value(href)  # type: ignore[assignment]
                            if not href:
                                continue
                        absolute = urljoin(base, href) if base else href
                        if self.canonicalize:
                            absolute = canonical_url(absolute)
                        if not self.matches(absolute):
                            continue
                        if self.unique and absolute in seen:
                            continue
                        seen.add(absolute)
                        results.append(absolute)
        return results

    def extract(self, response_or_html, base_url: str | None = None, **request_kwargs) -> list[Request]:
        return [Request(url=url, **request_kwargs) for url in self.extract_links(response_or_html, base_url)]


def _iter_str(value: str | Iterable[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


@dataclass
class Rule:
    """CrawlSpider 的抓取规则。"""

    link_extractor: LinkExtractor
    callback: str | None = None
    follow: bool = True
    priority: int = 0
    meta: dict = field(default_factory=dict)


__all__ = ["DENY_EXTENSIONS", "LinkExtractor", "Rule"]
