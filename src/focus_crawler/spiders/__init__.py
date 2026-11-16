"""Spider 子系统：类 Scrapy 的爬虫 API（参考 Scrapling 的能力面，独立实现）。

对外的公开接口：

>>> from focus_crawler.spiders import Spider, Request, Item
>>> class MySpider(Spider):
...     name = "demo"
...     start_urls = ["https://example.com/"]
...     async def parse(self, response):
...         yield Item(title=response.css("h1::text").get())

运行方式：

* ``spider.start()``            —— 同步跑完，返回 items 列表
* ``async for item in spider.stream()`` —— 流式消费
* ``spider.pause()`` / ``resume()``     —— 断点（crawldir）
* CLI: ``focus-crawler spider my_spider.py:MySpider``
"""

from .items import Item
from .linkextractor import LinkExtractor, Rule
from .request import Request, Response
from .spider import Spider, SpiderSettings
from .templates import (
    CrawlSpider,
    CSVFeedSpider,
    ShopifySpider,
    SitemapSpider,
    XMLFeedSpider,
)
from .exporters import export_items

__all__ = [
    "CrawlSpider",
    "CSVFeedSpider",
    "Item",
    "LinkExtractor",
    "Request",
    "Response",
    "Rule",
    "ShopifySpider",
    "SitemapSpider",
    "Spider",
    "SpiderSettings",
    "XMLFeedSpider",
    "export_items",
]
