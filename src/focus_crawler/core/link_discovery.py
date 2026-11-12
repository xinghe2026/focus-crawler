"""通用链接发现：selector / self / auto 三种模式。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from parsel import Selector

from ..config.schema import TaskConfig
from ..utils.hash import url_hash, normalize_url

_INVALID_SCHEMES = ("javascript", "mailto", "tel", "data", "about", "ftp")


@dataclass
class DiscoveryResult:
    links: list[str] = field(default_factory=list)
    next_page: str | None = None
    raw_count: int = 0
    filtered_count: int = 0
    duplicate_count: int = 0
    truncated: bool = False


def domain_allowed(url: str, allowed_domains: list[str]) -> bool:
    host = urlsplit(url).netloc.split(":")[0].lower()
    if not host:
        return False
    for domain in allowed_domains:
        domain = domain.lower().strip()
        if not domain or domain == "*":
            return True
        if host == domain or host.endswith("." + domain):
            return True
    return False


def _matches_any(url: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        try:
            if re.search(pattern, url):
                return True
        except re.error:
            continue
    return False


def url_allowed(url: str, task: TaskConfig) -> bool:
    """域名白名单 + 全局 URL 规则。"""
    if not url.lower().startswith(("http://", "https://")):
        return False
    if not domain_allowed(url, task.allowed_domains):
        return False
    rules = task.url_rules
    if rules.include and not _matches_any(url, rules.include):
        return False
    if rules.exclude and _matches_any(url, rules.exclude):
        return False
    return True


def _select_links(root: Selector, expression: str, href_attribute: str) -> list[str]:
    if not expression:
        return []
    nodes = root.xpath(expression) if expression.lstrip().startswith(("/", "(", "./")) else root.css(expression)
    return [node.attrib.get(href_attribute, "") for node in nodes]


def _collect_raw_hrefs(html: str, task: TaskConfig) -> list[str]:
    root = Selector(text=html or "")
    cfg = task.link_discovery
    mode = cfg.mode
    attribute = cfg.href_attribute or "href"

    if mode == "auto" and task.list.item_selector and task.list.link_selector:
        item_nodes = _select_nodes(root, task.list.item_selector)
        hrefs: list[str] = []
        for node in item_nodes:
            if task.list.link_selector == "self":
                href = node.attrib.get(attribute, "")
                if href:
                    hrefs.append(href)
            else:
                inner = _select_nodes(node, task.list.link_selector)
                href = inner[0].attrib.get(attribute, "") if inner else ""
                if href:
                    hrefs.append(href)
        return hrefs

    expression = cfg.selector or "a"
    return _select_links(root, expression, attribute)


def _select_nodes(root: Selector, expression: str):
    if expression.lstrip().startswith(("/", "(", "./")):
        return root.xpath(expression)
    return root.css(expression)


def discover(html: str, page_url: str, task: TaskConfig) -> DiscoveryResult:
    cfg = task.link_discovery
    raw_hrefs = _collect_raw_hrefs(html, task)
    result = DiscoveryResult(raw_count=len(raw_hrefs))

    seen: set[str] = set()
    for href in raw_hrefs:
        href = (href or "").strip()
        if not href:
            continue
        if href.startswith("#"):
            continue
        scheme_probe = href.split(":", 1)[0].lower()
        if ":" in href and scheme_probe in _INVALID_SCHEMES:
            continue
        absolute = normalize_url(urljoin(page_url, href))
        if not absolute.lower().startswith(("http://", "https://")):
            continue
        if not url_allowed(absolute, task):
            continue
        if cfg.include and not _matches_any(absolute, cfg.include):
            continue
        if cfg.exclude and _matches_any(absolute, cfg.exclude):
            continue
        result.filtered_count += 1

        key = url_hash(absolute)
        if cfg.deduplicate and key in seen:
            result.duplicate_count += 1
            continue
        seen.add(key)
        result.links.append(absolute)

    if len(result.links) > cfg.max_links_per_page:
        result.links = result.links[: cfg.max_links_per_page]
        result.truncated = True

    if cfg.follow_next_page and task.list.next_page_selector:
        root = Selector(text=html or "")
        next_nodes = _select_nodes(root, task.list.next_page_selector)
        if next_nodes:
            href = next_nodes[0].attrib.get(cfg.href_attribute or "href", "")
            if href:
                absolute = normalize_url(urljoin(page_url, href))
                if url_allowed(absolute, task):
                    result.next_page = absolute

    return result
