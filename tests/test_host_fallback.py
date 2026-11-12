"""主机回退测试：解析失败时按配置切换镜像主机。"""

from __future__ import annotations

import pytest

from focus_crawler.config.schema import ConfigError, MediaFallbackConfig, TaskConfig
from focus_crawler.core.fetcher import FetchResult
from focus_crawler.core.host_fallback import fallback_urls, is_dns_error, preferred_url
from focus_crawler.core.media_common import MediaDownloadError, download_direct

DNS_FAILED = FetchResult(
    url="https://cdn6.mmdb.cc/a.jpg",
    error="[Errno 11001] getaddrinfo failed",
    failure_type="connection_error",
)
IMAGE_OK = FetchResult(url="https://cdn1.mmdb.cc/a.jpg", status_code=200, content=b"\xff\xd8\xffimage")


def _config(**overrides) -> MediaFallbackConfig:
    raw = {
        "enabled": True,
        "rules": [
            {"hosts": ["cdn6.mmdb.cc"], "candidates": ["cdn1.mmdb.cc", "cdn2.mmdb.cc"]}
        ],
    }
    raw.update(overrides)
    errors: list[str] = []
    cfg = MediaFallbackConfig.from_dict(raw, errors, "media_fallback")
    assert not errors
    return cfg


class _FakeFetcher:
    def __init__(self, responses: dict[str, FetchResult]):
        self.responses = responses
        self.calls: list[str] = []

    def fetch(self, url: str, headers=None, want_bytes: bool = False) -> FetchResult:
        self.calls.append(url)
        return self.responses.get(
            url, FetchResult(url=url, error="HTTP 404", failure_type="http_404")
        )


def test_fallback_urls_keeps_path_and_query():
    urls = fallback_urls(_config(), "https://cdn6.mmdb.cc/file/20250620/1/05375015.jpg?sign=abc")
    assert urls == [
        "https://cdn1.mmdb.cc/file/20250620/1/05375015.jpg?sign=abc",
        "https://cdn2.mmdb.cc/file/20250620/1/05375015.jpg?sign=abc",
    ]


def test_fallback_urls_requires_host_match():
    assert fallback_urls(_config(), "https://cdn9.mmdb.cc/a.jpg") == []


def test_fallback_urls_supports_pattern_rule():
    cfg = _config(rules=[{"patterns": [r"cdn\d+\.mmdb\.cc"], "candidates": ["cdn1.mmdb.cc"]}])
    assert fallback_urls(cfg, "https://cdn6.mmdb.cc/a.jpg") == ["https://cdn1.mmdb.cc/a.jpg"]


def test_fallback_disabled_returns_nothing():
    assert fallback_urls(_config(enabled=False), "https://cdn6.mmdb.cc/a.jpg") == []


def test_is_dns_error():
    assert is_dns_error(DNS_FAILED)
    assert not is_dns_error(FetchResult(url="u", error="HTTP 403", failure_type="http_403"))
    assert not is_dns_error(FetchResult(url="u", error="timed out", failure_type="timeout"))


def test_preferred_url_uses_resolvable_candidate(monkeypatch):
    monkeypatch.setattr(
        "focus_crawler.core.host_fallback.host_resolves",
        lambda host, port=None: host == "cdn1.mmdb.cc",
    )
    assert preferred_url(_config(), "https://cdn6.mmdb.cc/a.jpg") == "https://cdn1.mmdb.cc/a.jpg"


def test_preferred_url_keeps_original_when_resolved(monkeypatch):
    monkeypatch.setattr(
        "focus_crawler.core.host_fallback.host_resolves", lambda host, port=None: True
    )
    assert preferred_url(_config(), "https://cdn6.mmdb.cc/a.jpg") == "https://cdn6.mmdb.cc/a.jpg"


def test_download_direct_retries_mirror_on_dns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "focus_crawler.core.host_fallback.host_resolves", lambda host, port=None: True
    )
    fetcher = _FakeFetcher(
        {"https://cdn6.mmdb.cc/a.jpg": DNS_FAILED, "https://cdn1.mmdb.cc/a.jpg": IMAGE_OK}
    )
    path, size = download_direct(
        fetcher, "https://cdn6.mmdb.cc/a.jpg", tmp_path, "a.jpg", fallback=_config()
    )
    assert fetcher.calls == ["https://cdn6.mmdb.cc/a.jpg", "https://cdn1.mmdb.cc/a.jpg"]
    assert path.read_bytes() == IMAGE_OK.content
    assert size == len(IMAGE_OK.content)


def test_download_direct_does_not_retry_on_http_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "focus_crawler.core.host_fallback.host_resolves", lambda host, port=None: True
    )
    fetcher = _FakeFetcher({"https://cdn1.mmdb.cc/a.jpg": IMAGE_OK})
    with pytest.raises(MediaDownloadError):
        download_direct(fetcher, "https://cdn6.mmdb.cc/a.jpg", tmp_path, "a.jpg", fallback=_config())
    assert fetcher.calls == ["https://cdn6.mmdb.cc/a.jpg"]


def test_task_config_parses_media_fallback():
    config = TaskConfig.from_dict(
        {
            "task_name": "回退示例",
            "start_urls": ["https://example.com/list.html"],
            "allowed_domains": ["example.com"],
            "media_fallback": {
                "enabled": True,
                "rules": [{"hosts": ["cdn6.mmdb.cc"], "candidates": ["cdn1.mmdb.cc"]}],
            },
        }
    )
    assert config.media_fallback.enabled is True
    assert config.media_fallback.rules[0].hosts == ["cdn6.mmdb.cc"]
    assert config.media_fallback.rules[0].candidates == ["cdn1.mmdb.cc"]


def test_task_config_rejects_empty_candidates():
    with pytest.raises(ConfigError):
        TaskConfig.from_dict(
            {
                "task_name": "回退示例",
                "start_urls": ["https://example.com/list.html"],
                "allowed_domains": ["example.com"],
                "media_fallback": {"enabled": True, "rules": [{"hosts": ["cdn6.mmdb.cc"]}]},
            }
        )
