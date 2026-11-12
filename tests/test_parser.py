"""字段解析测试。"""

from __future__ import annotations

from focus_crawler.config.schema import DetailConfig, FieldSpec
from focus_crawler.core.parser import parse_detail, summarize_hits
from focus_crawler.models.parse_context import XhrResponse

HTML = """
<html><head><meta property="og:image" content="/img/a.jpg"></head>
<body>
  <h1 class="title">示例标题</h1>
  <div class="content"><p>第一段</p><p>第二段</p></div>
  <span class="date">发布时间：2026/10/05</span>
  <a class="link" href="/detail/2.html" data-id="42">详情</a>
</body></html>
"""

FIELDS = {
    "title": {"selector": "h1.title", "type": "text", "transform": ["strip"]},
    "content": {"selector": "div.content", "type": "html"},
    "date": {"selector": ".date", "type": "text", "transform": ["strip", "date"]},
    "link": {"selector": "a.link", "type": "attr", "attribute": "href", "transform": ["absolute_url"]},
    "id": {"selector": "a.link", "type": "attr", "attribute": "data-id", "transform": ["int"]},
    "url": {"selector": None, "type": "current_url"},
    "xpath_title": {"selector": "//h1/text()", "type": "text"},
    "regex_href": {"selector": None, "regex": "/detail/(\\d+).html", "transform": ["int"]},
    "missing": {"selector": ".not-exist", "default": "默认值"},
}


def test_parse_fields():
    detail = DetailConfig.from_dict(FIELDS)
    page = parse_detail(HTML, "https://example.com/page/1.html", detail)
    assert page.data["title"] == "示例标题"
    assert "<p>第一段</p>" in page.data["content"]
    assert page.data["date"] == "2026-10-05"
    assert page.data["link"] == "https://example.com/detail/2.html"
    assert page.data["id"] == 42
    assert page.data["url"] == "https://example.com/page/1.html"
    assert page.data["xpath_title"] == "示例标题"
    assert page.data["regex_href"] == 2
    assert page.data["missing"] == "默认值"
    assert page.hit_rate > 0.7


def test_jsonpath_from_xhr_response():
    responses = [
        XhrResponse(
            url="https://example.com/api",
            text='{"data": {"title": "接口标题", "tags": ["a", "b"]}}',
        )
    ]
    spec = FieldSpec.from_dict(
        "api_title",
        {"source": "response", "jsonpath": "$.data.title"},
        [],
        "detail",
    )
    detail = DetailConfig(fields={"api_title": spec})
    page = parse_detail(HTML, "https://example.com/page", detail, responses)
    assert page.data["api_title"] == "接口标题"
    assert page.hits["api_title"] is True


def test_jsonpath_missing_returns_default():
    spec = FieldSpec.from_dict("x", {"source": "response", "jsonpath": "$.nope"}, [], "detail")
    detail = DetailConfig(fields={"x": spec})
    page = parse_detail(HTML, "https://example.com", detail, [])
    assert page.data["x"] == ""


def test_summarize_hits():
    detail = DetailConfig.from_dict(FIELDS)
    pages = [parse_detail(HTML, "https://example.com/a", detail) for _ in range(3)]
    summary = summarize_hits(pages)
    assert summary["title"]["hit_rate"] == 1.0
    assert summary["missing"]["hits"] == 3
    assert summary["title"]["example"] == "示例标题"
