"""示例站点列表页解析脚本。"""

from __future__ import annotations


def parse_list(html: str, url: str, ctx) -> dict:
    links = []
    for node in ctx.css("a.item-link"):
        href = node.attrib.get("href")
        if href:
            links.append(ctx.absolute_url(href))

    next_page = None
    next_node = ctx.css("a.next-page")
    if len(next_node):
        href = next_node[0].attrib.get("href")
        if href:
            next_page = ctx.absolute_url(href)

    return {"links": links, "next_page": next_page, "items": []}
