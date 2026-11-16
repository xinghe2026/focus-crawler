"""Spider 子系统：引擎调度、自动油门、断点、缓存、模板、导出。"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from focus_crawler.spiders import (
    CrawlSpider,
    CSVFeedSpider,
    Item,
    LinkExtractor,
    Request,
    Response,
    Rule,
    ShopifySpider,
    SitemapSpider,
    Spider,
    SpiderSettings,
    XMLFeedSpider,
    export_items,
)
from focus_crawler.spiders.engine import CrawlerEngine
from focus_crawler.spiders.throttle import AutoThrottle, looks_blocked, parse_retry_after


# ---------------------------------------------------------------- 假会话
class FakeSessions:
    """可编排的会话：url -> (status, body, headers)。"""

    def __init__(self, pages: dict[str, tuple[int, str, dict | None]] | None = None, delay: float = 0.0):
        self.pages = pages or {}
        self.delay = delay
        self.calls: list[str] = []
        self.closed = False

    async def fetch(self, request: Request) -> Response:
        self.calls.append(request.url)
        if self.delay:
            await asyncio.sleep(self.delay)
        status, body, headers = self.pages.get(request.url, (404, "not found", None))
        if callable(body):
            status, body, headers = body(request)
        return Response(
            url=request.url,
            status=status,
            headers=headers or {"content-type": "text/html; charset=utf-8"},
            content=body.encode("utf-8") if isinstance(body, str) else body,
            request=request,
        )

    async def close_all(self) -> None:
        self.closed = True


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- 引擎
def test_engine_follows_requests_and_yields_items():
    class Demo(Spider):
        name = "demo"
        start_urls = ["https://site.test/"]

        async def parse(self, response):
            yield Item(url=response.url, title=response.css("h1::text").get())
            for href in response.css("a::attr(href)").getall():
                yield response.follow(href)

    pages = {
        "https://site.test/": (200, '<h1>首页</h1><a href="/a">A</a><a href="/b">B</a>', None),
        "https://site.test/a": (200, "<h1>A</h1>", None),
        "https://site.test/b": (200, "<h1>B</h1>", None),
    }
    spider = Demo(settings={"concurrency": 4})
    items = run(spider.start_with(FakeSessions(pages))) if hasattr(spider, "start_with") else None
    if items is None:
        async def collect():
            return [item async for item in spider.stream(FakeSessions(pages))]

        items = run(collect())
    titles = sorted(item["title"] for item in items)
    assert titles == ["A", "B", "首页"]


def test_engine_dedupes_urls():
    hits: list[str] = []

    class Demo(Spider):
        start_urls = ["https://site.test/"]

        async def parse(self, response):
            hits.append(response.url)
            yield Item(url=response.url)
            yield Request(url="https://site.test/same")
            yield Request(url="https://site.test/same")

    pages = {
        "https://site.test/": (200, "<h1>x</h1>", None),
        "https://site.test/same": (200, "<h1>y</h1>", None),
    }
    async def collect():
        return [item async for item in Demo().stream(FakeSessions(pages))]

    run(collect())
    assert hits.count("https://site.test/same") == 1


def test_engine_retries_blocked_then_succeeds():
    attempts = {"n": 0}

    def body(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return 429, "too many requests", {"retry-after": "0"}
        return 200, "<h1>ok</h1>", None

    class Demo(Spider):
        start_urls = ["https://site.test/"]

        async def parse(self, response):
            yield Item(title=response.css("h1::text").get())

    sessions = FakeSessions({"https://site.test/": (429, body, None)})
    sessions.pages["https://site.test/"] = (200, body, None)
    spider = Demo(settings={"retries": 2, "delay": 0})
    async def collect():
        return [item async for item in spider.stream(sessions)]

    items = run(collect())
    assert attempts["n"] >= 2
    assert items and items[0]["title"] == "ok"


def test_engine_concurrency_limit():
    active = {"now": 0, "peak": 0}

    class Slow(FakeSessions):
        async def fetch(self, request):
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
            await asyncio.sleep(0.05)
            active["now"] -= 1
            return Response(url=request.url, status=200, headers={}, content=b"<h1>x</h1>", request=request)

    class Demo(Spider):
        start_urls = [f"https://site.test/{i}" for i in range(10)]

        async def parse(self, response):
            yield Item(url=response.url)

    spider = Demo(settings={"concurrency": 3, "delay": 0})
    async def collect():
        return [item async for item in spider.stream(Slow())]

    items = run(collect())
    assert len(items) == 10
    assert active["peak"] <= 3


def test_pause_resume_with_crawldir(tmp_path):
    pages = {
        "https://site.test/": (200, '<a href="/a">a</a><a href="/b">b</a>', None),
        "https://site.test/a": (200, "<h1>A</h1>", None),
        "https://site.test/b": (200, "<h1>B</h1>", None),
    }

    class Demo(Spider):
        start_urls = ["https://site.test/"]

        async def parse(self, response):
            yield Item(url=response.url)
            for href in response.css("a::attr(href)").getall():
                yield response.follow(href)

    spider = Demo(settings={"crawldir": str(tmp_path), "concurrency": 1})
    engine = CrawlerEngine(spider, spider.settings, sessions=FakeSessions(pages))
    async def partial():
        out = []
        async for item in engine.crawl():
            out.append(item)
            if len(out) == 1:
                engine.pause()
        return out

    run(partial())
    state_file = tmp_path / "state.json"
    assert state_file.exists()
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["seen"]

    spider2 = Demo(settings={"crawldir": str(tmp_path), "concurrency": 1})
    sessions2 = FakeSessions(pages)
    async def resume():
        return [item async for item in spider2.stream(sessions2)]

    items = run(resume())
    assert items
    assert len(sessions2.calls) >= 1


def test_dev_mode_cache_replay(tmp_path):
    class Demo(Spider):
        start_urls = ["https://site.test/"]

        async def parse(self, response):
            yield Item(title=response.css("h1::text").get())

    pages = {"https://site.test/": (200, "<h1>cached</h1>", None)}
    settings = {"crawldir": str(tmp_path), "dev_mode": True, "delay": 0}
    spider = Demo(settings=settings)
    async def first():
        return [item async for item in spider.stream(FakeSessions(pages))]
    items1 = run(first())
    assert items1[0]["title"] == "cached"

    # 第二次：会话返回 500，但应命中缓存
    spider2 = Demo(settings=settings)
    broken = FakeSessions({"https://site.test/": (500, "boom", None)})
    async def second():
        return [item async for item in spider2.stream(broken)]
    items2 = run(second())
    assert items2[0]["title"] == "cached"
    assert spider2.stats["cache_hits"] >= 1


# ---------------------------------------------------------------- 模板
def test_link_extractor_filters_and_canonicalizes():
    html = """
    <html><body>
      <a href="/post/1">1</a>
      <a href="https://site.test/post/2#frag">2</a>
      <a href="/post/1">dup</a>
      <a href="/img/a.jpg">img</a>
      <a href="https://other.test/post/3">other</a>
    </body></html>
    """
    extractor = LinkExtractor(allow=r"/post/\d+", allow_domains=["site.test"])
    links = extractor.extract_links(html, "https://site.test/")
    assert links == ["https://site.test/post/1", "https://site.test/post/2"]


def test_link_extractor_restrict_css():
    html = '<div class="main"><a href="/keep">k</a></div><div class="side"><a href="/drop">d</a></div>'
    extractor = LinkExtractor(restrict_css="div.main")
    assert extractor.extract_links(html, "https://site.test/") == ["https://site.test/keep"]


def test_crawl_spider_follows_rules():
    class Blog(CrawlSpider):
        name = "blog"
        start_urls = ["https://blog.test/"]
        rules = [Rule(LinkExtractor(allow=r"/post/"), callback="parse_post", follow=True)]

        async def parse_post(self, response):
            yield Item(title=response.css("h1::text").get())

    pages = {
        "https://blog.test/": (200, '<a href="/post/1">1</a><a href="/about">a</a>', None),
        "https://blog.test/post/1": (200, "<h1>第一篇</h1>", None),
    }
    async def collect():
        return [item async for item in Blog(settings={"delay": 0}).stream(FakeSessions(pages))]
    items = run(collect())
    assert [item["title"] for item in items] == ["第一篇"]


def test_sitemap_spider_handles_index_and_urls():
    sitemap_index = """<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://site.test/sitemap-1.xml</loc></sitemap>
    </sitemapindex>"""
    sitemap = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://site.test/a</loc></url>
      <url><loc>https://site.test/b</loc></url>
    </urlset>"""

    class S(SitemapSpider):
        sitemap_urls = ["https://site.test/sitemap.xml"]

    pages = {
        "https://site.test/sitemap.xml": (200, sitemap_index, None),
        "https://site.test/sitemap-1.xml": (200, sitemap, None),
        "https://site.test/a": (200, "<title>A</title>", None),
        "https://site.test/b": (200, "<title>B</title>", None),
    }
    async def collect():
        return [item async for item in S(settings={"delay": 0}).stream(FakeSessions(pages))]
    items = run(collect())
    assert {item["url"] for item in items} == {"https://site.test/a", "https://site.test/b"}


def test_xml_feed_spider():
    feed = """<?xml version="1.0"?><rss><channel>
      <item><title>一</title><link>https://site.test/1</link></item>
      <item><title>二</title><link>https://site.test/2</link></item>
    </channel></rss>"""

    class F(XMLFeedSpider):
        start_urls = ["https://site.test/feed.xml"]
        itertag = "item"

    async def collect():
        return [item async for item in F(settings={"delay": 0}).stream(
            FakeSessions({"https://site.test/feed.xml": (200, feed, None)}))]

    items = run(collect())
    assert [item["title"] for item in items] == ["一", "二"]


def test_csv_feed_spider():
    csv_text = "id,name\n1,张三\n2,李四\n"

    class C(CSVFeedSpider):
        start_urls = ["https://site.test/data.csv"]

    async def collect():
        return [item async for item in C(settings={"delay": 0}).stream(
            FakeSessions({"https://site.test/data.csv": (200, csv_text, None)}))]

    items = run(collect())
    assert [item["name"] for item in items] == ["张三", "李四"]


def test_shopify_spider_emits_one_item_per_variant():
    payload = json.dumps({
        "products": [
            {"id": 1, "title": "Tea", "handle": "tea", "vendor": "V", "product_type": "drink",
             "tags": "a,b", "variants": [{"id": 11, "sku": "S", "price": "9.9"}, {"id": 12, "sku": "M", "price": "10.9"}]},
        ]
    })

    class S(ShopifySpider):
        shop_url = "https://shop.test"
        max_pages = 1

    async def collect():
        return [item async for item in S(settings={"delay": 0}).stream(
            FakeSessions({"https://shop.test/products.json?limit=250&page=1": (200, payload, None)}))]

    items = run(collect())
    assert len(items) == 2
    assert {item["sku"] for item in items} == {"S", "M"}


# ---------------------------------------------------------------- 导出
def test_exporters_all_formats(tmp_path):
    items = [Item(title="一", price=1), Item(title="二", price=2)]
    for fmt in ("json", "jsonl", "csv", "xml"):
        path = export_items(items, tmp_path / f"out.{fmt}")
        text = path.read_text(encoding="utf-8")
        assert "一" in text and "二" in text
        assert text
    assert json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))[0]["title"] == "一"
    assert (tmp_path / "out.xml").read_text(encoding="utf-8").startswith("<?xml")


# ---------------------------------------------------------------- 油门
def test_autothrottle_doubles_on_block_and_recovers():
    throttle = AutoThrottle(base_delay=0.2, max_delay=10)
    assert throttle.delay_for("a.test") == pytest.approx(0.2)
    throttle.report("a.test", 429, 0.1, {"retry-after": "2"})
    assert throttle.delay_for("a.test") >= 2
    for _ in range(6):
        throttle.report("a.test", 200, 0.05, {})
    assert throttle.delay_for("a.test") < 2


def test_parse_retry_after_and_block_detection():
    assert parse_retry_after("5") == 5
    assert parse_retry_after("not-a-date") is None
    assert looks_blocked(403)
    assert looks_blocked(200, "Please complete the captcha")
    assert not looks_blocked(200, "ok")


# ---------------------------------------------------------------- CLI
def test_spider_cli_runs_and_exports(tmp_path, capsys):
    from focus_crawler.__main__ import main

    spider_file = tmp_path / "my_spider.py"
    spider_file.write_text(
        "from focus_crawler.spiders import Item, Spider\n"
        "\n"
        "class MySpider(Spider):\n"
        "    name = 'cli-demo'\n"
        "    start_urls = []\n"
        "    async def parse(self, response):\n"
        "        yield Item(title='x')\n",
        encoding="utf-8",
    )
    output = tmp_path / "out.jsonl"
    code = main(["spider", f"{spider_file}:MySpider", "--output", str(output), "--format", "jsonl"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["spider"] == "cli-demo"
    assert output.exists()


def test_spider_cli_rejects_bad_target(tmp_path):
    from focus_crawler.__main__ import main

    with pytest.raises(ValueError):
        main(["spider", "not-a-file.py", "--output", str(tmp_path / "x.json")])


def test_spider_can_customize_blocked_detection():
    """阻塞判定可自定义：默认 200 也能量到"soft block"，并触发重试。"""
    attempts = {"n": 0}

    class Demo(Spider):
        start_urls = ["https://site.test/"]

        def is_blocked(self, response):
            return "登录后查看" in response.text

        async def parse(self, response):
            yield Item(ok=True)

    class Sessions(FakeSessions):
        async def fetch(self, request):
            attempts["n"] += 1
            body = "登录后查看" if attempts["n"] == 1 else "<h1>ok</h1>"
            return Response(url=request.url, status=200, headers={}, content=body.encode(), request=request)

    spider = Demo(settings={"retries": 2, "delay": 0})
    async def collect():
        return [item async for item in spider.stream(Sessions())]

    items = run(collect())
    assert attempts["n"] >= 2
    assert spider.stats["blocked"] >= 1
    assert items
