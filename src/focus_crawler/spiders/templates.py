"""现成 Spider 模板：规则链接跟踪 / Sitemap / XML / CSV / Shopify。"""

from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
from typing import Any, AsyncIterator
from urllib.parse import urljoin

from ..utils.logging import get_logger
from .items import Item
from .linkextractor import LinkExtractor, Rule
from .request import Request, Response
from .spider import Spider

logger = get_logger("spider.templates")


class CrawlSpider(Spider):
    """按 ``rules`` 跟踪链接：命中 callback 的链接交给回调，``follow=True`` 的继续爬。"""

    rules: list[Rule] = []

    async def parse(self, response: Response) -> AsyncIterator[Any]:
        for rule in self.rules:
            links = rule.link_extractor.extract_links(response)
            for url in links:
                # follow=True 且没指定 callback 时，用本 Spider 的 parse 继续跟踪
                callback = rule.callback or ("parse" if rule.follow else None)
                if callback is None:
                    continue
                yield Request(
                    url=url,
                    callback=callback,
                    priority=rule.priority,
                    meta=dict(rule.meta),
                )


class SitemapSpider(Spider):
    """从 sitemap.xml（含索引）出发，抓取所有 URL 交给 ``parse_page``。"""

    sitemap_urls: list[str] = []
    sitemap_rules: list[tuple[str, str | None]] = []
    sitemap_follow: list[str] = []

    async def start_requests(self) -> AsyncIterator[Request]:
        for url in self.sitemap_urls or self.start_urls:
            yield Request(url=url, callback="parse_sitemap")

    async def parse_sitemap(self, response: Response) -> AsyncIterator[Any]:
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            logger.warning("sitemap 解析失败 %s：%s", response.url, exc)
            return
        namespace = ""
        if root.tag.startswith("{"):
            namespace = root.tag.split("}", 1)[0] + "}"
        tag = root.tag.rsplit("}", 1)[-1]
        if tag == "sitemapindex":
            for node in root.findall(f"{namespace}sitemap"):
                loc = node.findtext(f"{namespace}loc")
                if loc:
                    yield Request(url=urljoin(response.url, loc.strip()), callback="parse_sitemap")
            return
        for node in root.findall(f"{namespace}url"):
            loc = node.findtext(f"{namespace}loc")
            if not loc:
                continue
            url = loc.strip()
            callback = self._callback_for(url)
            yield Request(url=url, callback=callback)

    def _callback_for(self, url: str) -> str:
        import re

        for pattern, callback in self.sitemap_rules:
            if re.search(pattern, url):
                return callback or "parse_page"
        return "parse_page"

    async def parse_page(self, response: Response) -> AsyncIterator[Any]:
        yield Item(url=response.url, status=response.status, title=response.css("title::text").get(default=""))


class XMLFeedSpider(Spider):
    """遍历 XML / RSS / Atom 条目。"""

    iterator: str = "iternodes"      # iternodes | xml | html
    itertag: str = "item"
    namespaces: list[tuple[str, str]] = []
    start_urls: list[str] = []

    async def parse(self, response: Response) -> AsyncIterator[Any]:
        # 注意：不能先 list()，Element 在生成器恢复时会被 clear()
        for node in self._iter_nodes(response):
            item = self.parse_node(response, node)
            if item is not None:
                yield item

    def _iter_nodes(self, response: Response):
        if self.iterator == "html":
            return response.selector.css(self.itertag)
        for _, element in ET.iterparse(io.BytesIO(response.content), events=("end",)):
            if element.tag.rsplit("}", 1)[-1] == self.itertag:
                yield element
                element.clear()

    def parse_node(self, response: Response, node: Any) -> Any:
        if hasattr(node, "tag") and not isinstance(node, str):
            data = {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in list(node)}
            data["url"] = response.url
            return Item(data)
        return Item(text=node.get())


class CSVFeedSpider(Spider):
    """逐行解析 CSV 订阅源。"""

    delimiter: str = ","
    quotechar: str = '"'
    headers: list[str] = []

    async def parse(self, response: Response) -> AsyncIterator[Any]:
        text = response.text
        reader = csv.DictReader(io.StringIO(text), delimiter=self.delimiter, quotechar=self.quotechar)
        for row in reader:
            item = self.parse_row(row, response)
            if item is not None:
                yield item

    def parse_row(self, row: dict[str, Any], response: Response) -> Any:
        row = dict(row)
        row["_source"] = response.url
        return Item(row)


class ShopifySpider(Spider):
    """公开 Shopify 店铺：``/products.json`` 分页，每个变体一条 Item。"""

    shop_url: str = ""
    per_page: int = 250
    max_pages: int = 50
    start_urls: list[str] = []

    async def start_requests(self) -> AsyncIterator[Request]:
        base = self.shop_url or (self.start_urls[0] if self.start_urls else "")
        if not base:
            return
        base = base.rstrip("/")
        for page in range(1, self.max_pages + 1):
            yield Request(
                url=f"{base}/products.json?limit={self.per_page}&page={page}",
                callback="parse_products",
                meta={"page": page},
            )

    async def parse_products(self, response: Response) -> AsyncIterator[Any]:
        try:
            payload = json.loads(response.text)
        except ValueError:
            logger.warning("Shopify 返回非 JSON：%s", response.url)
            return
        products = payload.get("products") or []
        if not products:
            return
        for product in products:
            for variant in product.get("variants") or [{}]:
                yield Item(
                    product_id=product.get("id"),
                    title=product.get("title"),
                    handle=product.get("handle"),
                    vendor=product.get("vendor"),
                    product_type=product.get("product_type"),
                    tags=product.get("tags"),
                    variant_id=variant.get("id"),
                    sku=variant.get("sku"),
                    price=variant.get("price"),
                    compare_at_price=variant.get("compare_at_price"),
                    available=variant.get("available"),
                    url=f"{response.url.split('/products.json')[0]}/products/{product.get('handle')}",
                )


__all__ = [
    "CSVFeedSpider",
    "CrawlSpider",
    "ShopifySpider",
    "SitemapSpider",
    "XMLFeedSpider",
]
