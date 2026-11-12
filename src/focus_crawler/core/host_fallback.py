"""主机回退：域名解析失败时改用配置好的镜像主机，路径保持不变。"""

from __future__ import annotations

import re
import socket
from urllib.parse import urlsplit, urlunsplit

from ..config.schema import FallbackRule, MediaFallbackConfig
from .hotlink import host_matches

DNS_MARKERS = (
    "getaddrinfo",
    "11001",
    "name or service not known",
    "nodename nor servname",
    "no address associated with hostname",
    "temporary failure in name resolution",
)

_resolve_cache: dict[str, bool] = {}


def host_resolves(host: str, port: int | None = None) -> bool:
    """主机是否有可用的 DNS 解析结果（带进程内缓存）。"""
    key = f"{host.lower()}:{port or ''}"
    if key in _resolve_cache:
        return _resolve_cache[key]
    try:
        socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
        resolved = True
    except OSError:
        resolved = False
    _resolve_cache[key] = resolved
    return resolved


def is_dns_error(result: object) -> bool:
    """判断 FetchResult / 异常是否为域名解析失败。"""
    failure_type = getattr(result, "failure_type", None)
    if failure_type not in (None, "connection_error"):
        return False
    message = str(getattr(result, "error", "") or "").lower()
    return any(marker in message for marker in DNS_MARKERS)


def _rule_matches(rule: FallbackRule, host: str, url: str) -> bool:
    if any(host_matches(host, pattern) for pattern in rule.hosts):
        return True
    for pattern in rule.patterns:
        try:
            if re.search(pattern, url):
                return True
        except re.error:
            continue
    return False


def _replace_host(url: str, host: str) -> str:
    parts = urlsplit(url)
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def fallback_urls(cfg: MediaFallbackConfig | None, url: str) -> list[str]:
    """按配置返回该 URL 可尝试的镜像 URL（保持路径与参数不变）。"""
    if cfg is None or not cfg.enabled:
        return []
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host:
        return []
    urls: list[str] = []
    for rule in cfg.rules:
        if not _rule_matches(rule, host, url):
            continue
        for candidate in rule.candidates:
            candidate = candidate.strip().lower()
            if not candidate or candidate == host:
                continue
            mirrored = _replace_host(url, candidate)
            if mirrored not in urls:
                urls.append(mirrored)
    return urls


def preferred_url(cfg: MediaFallbackConfig | None, url: str) -> str:
    """原主机解析失败时，优先返回第一个可解析的镜像 URL，避免无谓重试。"""
    alternates = fallback_urls(cfg, url)
    if not alternates:
        return url
    parts = urlsplit(url)
    if host_resolves(parts.hostname or "", parts.port):
        return url
    for alternate in alternates:
        alt_parts = urlsplit(alternate)
        if host_resolves(alt_parts.hostname or "", alt_parts.port):
            return alternate
    return url
