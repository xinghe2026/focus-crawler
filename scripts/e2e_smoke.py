"""端到端冒烟：本地 HTTP 服务上验证 Spider 抓取与分段下载（不依赖外网）。

用法：python scripts/e2e_smoke.py
"""

from __future__ import annotations

import asyncio
import http.server
import json
import socketserver
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from focus_crawler.spiders import Item, LinkExtractor, Spider  # noqa: E402
from focus_crawler.spiders.exporters import export_items_stream  # noqa: E402
from focus_crawler.spiders.sessions import HttpSession, SessionSettings  # noqa: E402
from focus_crawler.spiders.request import Request  # noqa: E402
from focus_crawler.core.segmented_downloader import download_file  # noqa: E402

FILES = {
    "/": b"<html><body><a href='/a'>A</a><a href='/b'>B</a></body></html>",
    "/a": b"<html><body><h1>A</h1><a href='/c'>C</a></body></html>",
    "/b": b"<html><body><h1>B</h1></body></html>",
    "/c": b"<html><body><h1>C</h1></body></html>",
}
BIG = bytes(range(256)) * (8 * 1024 * 1024 // 256)  # 8 MiB，触发分段


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # noqa: A003 - 静默
        pass

    def do_GET(self):  # noqa: N802 - http.server 接口
        if self.path == "/big.bin":
            self._send_range(BIG)
            return
        body = FILES.get(self.path)
        if body is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_range(self, payload: bytes) -> None:
        range_header = self.headers.get("Range", "")
        if not range_header.startswith("bytes="):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        spec = range_header.split("=", 1)[1]
        start_text, _, end_text = spec.partition("-")
        start = int(start_text or 0)
        end = int(end_text) if end_text else len(payload) - 1
        end = min(end, len(payload) - 1)
        chunk = payload[start : end + 1]
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def start_server() -> tuple[ThreadedServer, str]:
    server = ThreadedServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    return ok


async def run_spider(base: str, workdir: Path) -> tuple[int, list[dict]]:
    class E2ESpider(Spider):
        name = "e2e"
        start_urls = [base + "/"]

        custom_settings = {"concurrency": 4, "delay": 0}

        async def parse(self, response):
            yield Item(url=response.url, title=response.css("h1::text").get(default=""))
            extractor = LinkExtractor(allow=r"^" + base.replace(".", r"\."))
            for link in extractor.extract_links(response):
                yield Request(url=link)

    spider = E2ESpider(settings={"crawldir": str(workdir / "crawl"), "concurrency": 4, "delay": 0})
    items: list[dict] = []
    async for item in spider.stream():
        items.append(dict(item))
    return len(items), items


async def main() -> int:
    server, base = start_server()
    workdir = ROOT / "tmp" / "e2e"
    workdir.mkdir(parents=True, exist_ok=True)
    results: list[bool] = []
    try:
        count, items = await run_spider(base, workdir)
        results.append(check("Spider 抓取 4 个页面", count == 4, f"{count} 条"))
        expected = {base + "/", base + "/a", base + "/b", base + "/c"}
        results.append(check("Item 内容正确", {i["url"] for i in items} == expected, str(len(items))))

        json_path = export_items_stream(iter(items), workdir / "items.json")
        results.append(check("导出 JSON", json.loads(json_path.read_text(encoding="utf-8")) and True))

        # 分段下载（真实 Range，同步 httpx 客户端）
        import httpx

        client = httpx.Client(timeout=30, follow_redirects=True)
        target = workdir / "big.bin"
        report = download_file(
            client,
            base + "/big.bin",
            target,
            connections=4,
            min_segmented_size=1024 * 1024,
        )
        results.append(check("分段下载 8MiB", report.segmented and target.stat().st_size == len(BIG), f"{report.size} bytes"))
        results.append(check("分段内容一致", target.read_bytes() == BIG))
        client.close()
    finally:
        server.shutdown()
    passed = sum(1 for r in results if r)
    print(f"\n{passed}/{len(results)} 通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
