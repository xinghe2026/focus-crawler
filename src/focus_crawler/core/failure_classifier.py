"""失败分类：把异常 / 状态码映射为稳定的 failure_type。"""

from __future__ import annotations

import httpx

FAILURE_TYPES = (
    "http_403",
    "http_429",
    "http_404",
    "http_5xx",
    "http_error",
    "timeout",
    "connection_error",
    "robots_disallowed",
    "parse_error",
    "custom_parser_missing",
    "custom_parser_import_error",
    "custom_parser_runtime_error",
    "custom_parser_timeout",
    "custom_parser_invalid_return",
    "media_download_error",
    "unknown",
)


def classify_http_status(status_code: int) -> str | None:
    if status_code == 403:
        return "http_403"
    if status_code == 429:
        return "http_429"
    if status_code == 404:
        return "http_404"
    if 400 <= status_code < 600:
        return "http_5xx" if status_code >= 500 else "http_error"
    return None


def classify_exception(exc: BaseException) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.TransportError):
        return "connection_error"
    if isinstance(exc, httpx.HTTPError):
        return "http_error"
    return "unknown"
