"""示例站点详情页解析脚本。"""

from __future__ import annotations


def parse_detail(html: str, url: str, ctx) -> dict:
    title = ctx.css("h1::text").get(default="").strip()
    content = " ".join(ctx.css(".content ::text").getall()).strip()
    date = ctx.css(".date::text").get(default="").strip()
    return {"title": title, "content": content, "date": date, "url": url}
