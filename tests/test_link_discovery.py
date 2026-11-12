"""通用链接发现测试。"""

from __future__ import annotations

from pathlib import Path

from focus_crawler.config.schema import TaskConfig
from focus_crawler.core.link_discovery import discover, url_allowed

ENTRY = (Path(__file__).resolve().parents[1] / "fixtures" / "entry.html").read_text(encoding="utf-8")


def test_auto_mode_filters_and_dedupes(task_config):
    result = discover(ENTRY, "http://fixture.test/entry.html", task_config)
    assert result.links == [
        "http://fixture.test/sub/1.html",
        "http://fixture.test/sub/2.html",
        "http://fixture.test/sub/3.html",
    ]
    assert result.raw_count == 8
    assert result.duplicate_count == 1
    assert result.truncated is False


def test_selector_mode_restricts_links(task_config):
    task_config.link_discovery.mode = "selector"
    task_config.link_discovery.selector = ".items a"
    result = discover(ENTRY, "http://fixture.test/entry.html", task_config)
    assert len(result.links) == 3


def test_max_links_truncates(task_config):
    html = "".join(f'<a href="/sub/{i}.html">{i}</a>' for i in range(6))
    task_config.link_discovery.max_links_per_page = 2
    result = discover(html, "http://fixture.test/entry.html", task_config)
    assert len(result.links) == 2
    assert result.truncated is True


def test_follow_next_page(task_config):
    task_config.link_discovery.follow_next_page = True
    task_config.list.next_page_selector = "a.next"
    html = '<a href="/sub/1.html">1</a><a class="next" href="/entry2.html">下一页</a>'
    result = discover(html, "http://fixture.test/entry.html", task_config)
    assert result.next_page == "http://fixture.test/entry2.html"


def test_include_rules(task_config):
    task_config.url_rules.include = ["/sub/2\\.html"]
    result = discover(ENTRY, "http://fixture.test/entry.html", task_config)
    assert result.links == ["http://fixture.test/sub/2.html"]


def test_domain_allowed():
    assert url_allowed("https://example.com/a", TaskConfig.from_dict({
        "task_name": "t", "start_urls": ["https://example.com"], "allowed_domains": ["example.com"]
    }))
    assert not url_allowed("https://evil.test/a", TaskConfig.from_dict({
        "task_name": "t", "start_urls": ["https://example.com"], "allowed_domains": ["example.com"]
    }))
