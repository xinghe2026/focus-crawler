"""防盗链插件：按配置为媒体下载补充 Referer / 请求头。"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from ..config.schema import HotlinkConfig, HotlinkRule

BROWSER_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "cross-site",
}


def host_matches(host: str, pattern: str) -> bool:
    """兼容入口：统一走 utils.urls.host_matches。"""
    from ..utils.urls import host_matches as _host_matches

    return _host_matches(host, pattern)


def _rule_matches(rule: HotlinkRule, host: str, media_url: str) -> bool:
    if any(host_matches(host, pattern) for pattern in rule.hosts):
        return True
    for pattern in rule.patterns:
        try:
            if re.search(pattern, media_url):
                return True
        except re.error:
            continue
    return False


def _has_header(headers: dict[str, str], name: str) -> bool:
    lowered = name.lower()
    return any(key.lower() == lowered for key in headers)


def resolve_headers(
    cfg: HotlinkConfig,
    media_url: str,
    source_url: str | None = None,
    existing: dict[str, str] | None = None,
) -> dict[str, str]:
    """合并防盗链请求头；已有的适配器请求头优先级最高。"""
    existing_headers = {str(k): str(v) for k, v in (existing or {}).items()}
    if not cfg.enabled:
        return existing_headers

    headers: dict[str, str] = {}
    if cfg.browser_headers:
        headers.update(BROWSER_HEADERS)
    headers.update({str(k): str(v) for k, v in cfg.headers.items()})
    host = (urlsplit(media_url).hostname or "").lower()
    referer = ""
    for rule in cfg.rules:
        if not _rule_matches(rule, host, media_url):
            continue
        headers.update({str(k): str(v) for k, v in rule.headers.items()})
        if rule.referer:
            referer = (
                source_url
                if rule.referer.strip().lower() == "auto" and source_url
                else rule.referer.strip()
            )

    if referer and not _has_header(headers, "Referer") and not _has_header(existing_headers, "Referer"):
        headers["Referer"] = referer
    if (
        cfg.auto_referer
        and source_url
        and not _has_header(headers, "Referer")
        and not _has_header(existing_headers, "Referer")
    ):
        headers["Referer"] = source_url

    if cfg.auto_origin and source_url:
        parsed = urlsplit(source_url)
        origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
        if origin and not _has_header(headers, "Origin") and not _has_header(existing_headers, "Origin"):
            headers["Origin"] = origin

    headers.update(existing_headers)
    return headers
