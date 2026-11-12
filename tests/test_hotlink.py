"""防盗链插件测试：自动 Referer、域名规则、通配符、禁用与已有请求头。"""

from __future__ import annotations

from focus_crawler.config.schema import HotlinkConfig, HotlinkRule
from focus_crawler.core.hotlink import resolve_headers


def test_auto_referer_default():
    cfg = HotlinkConfig()
    headers = resolve_headers(cfg, "https://cdn.test/a.jpg", "https://example.com/page")
    assert headers == {"Referer": "https://example.com/page"}


def test_disabled_returns_existing_only():
    cfg = HotlinkConfig(enabled=False)
    headers = resolve_headers(cfg, "https://cdn.test/a.jpg", "https://example.com/page", {"X": "1"})
    assert headers == {"X": "1"}


def test_rule_host_and_explicit_referer():
    cfg = HotlinkConfig(
        rules=[
            HotlinkRule(
                hosts=["mmdb.cc"],
                referer="https://www.meirentu.net/",
                headers={"Accept": "image/webp,image/*"},
            )
        ]
    )
    headers = resolve_headers(cfg, "https://cdn2.mmdb.cc/file/a.jpg", "https://example.com/page")
    assert headers["Referer"] == "https://www.meirentu.net/"
    assert headers["Accept"] == "image/webp,image/*"


def test_rule_wildcard_and_auto_referer():
    cfg = HotlinkConfig(rules=[HotlinkRule(hosts=["*.hdslb.com"], referer="auto")])
    headers = resolve_headers(cfg, "https://i0.hdslb.com/bfs/a.jpg", "https://example.com/page")
    assert headers["Referer"] == "https://example.com/page"


def test_global_headers_and_existing_priority():
    cfg = HotlinkConfig(headers={"User-Agent": "TestUA"})
    headers = resolve_headers(
        cfg,
        "https://cdn.test/a.jpg",
        "https://example.com/page",
        {"Referer": "https://existing.test/"},
    )
    assert headers["User-Agent"] == "TestUA"
    assert headers["Referer"] == "https://existing.test/"


def test_rule_matches_url_pattern_without_host():
    cfg = HotlinkConfig(
        rules=[
            HotlinkRule(
                patterns=[r"/uploads/.*\.jpg$"],
                referer="https://example.com/",
                headers={"X-Rule": "1"},
            )
        ]
    )
    headers = resolve_headers(cfg, "https://other-cdn.test/uploads/a.jpg", "https://example.com/page")
    assert headers["Referer"] == "https://example.com/"
    assert headers["X-Rule"] == "1"


def test_browser_headers_toggle():
    cfg = HotlinkConfig(browser_headers=True)
    headers = resolve_headers(cfg, "https://cdn.test/a.jpg", "https://example.com/page")
    assert headers["Sec-Fetch-Site"] == "cross-site"
    assert headers["Accept-Language"].startswith("zh-CN")

    cfg.browser_headers = False
    headers = resolve_headers(cfg, "https://cdn.test/a.jpg", "https://example.com/page")
    assert "Sec-Fetch-Site" not in headers


def test_auto_origin_and_existing_origin_priority():
    cfg = HotlinkConfig(auto_origin=True)
    headers = resolve_headers(cfg, "https://cdn.test/a.jpg", "https://example.com/page")
    assert headers["Origin"] == "https://example.com"

    headers = resolve_headers(
        cfg,
        "https://cdn.test/a.jpg",
        "https://example.com/page",
        {"Origin": "https://existing.test"},
    )
    assert headers["Origin"] == "https://existing.test"


def test_cookie_header_can_be_configured():
    cfg = HotlinkConfig(headers={"Cookie": "session=abc"})
    headers = resolve_headers(cfg, "https://cdn.test/a.jpg", "https://example.com/page")
    assert headers["Cookie"] == "session=abc"
