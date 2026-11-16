"""命令行入口：预览 / 运行 / 导出。"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

from .config.loader import load_config
from .config.schema import ConfigError, TaskConfig
from .core.exporter import export_excel
from .core.fetcher import Fetcher
from .core.preview import preview_task
from .core.dynamic_sniffer import playwright_available
from .core.scheduler import Scheduler
from .core.subpage_collector import SubpageCollector
from .core.storage import Storage
from .core.ytdlp_engine import probe as ytdlp_probe
from .utils.ffmpeg import describe as describe_ffmpeg
from .utils.logging import setup_logging
from .utils.paths import app_home


def _build_fetcher(task: TaskConfig, use_robots: bool = True) -> Fetcher:
    crawl = task.crawl
    return Fetcher(
        timeout_ms=crawl.timeout_ms,
        retry=crawl.retry,
        delay_ms=crawl.delay_ms,
        respect_robots=crawl.respect_robots and use_robots,
    )


def _open_db(path: str | None):
    return Storage(Path(path) if path else app_home() / "focuscrawler.db")


def cmd_preview(args: argparse.Namespace) -> int:
    task = load_config(args.config)
    fetcher = _build_fetcher(task)
    try:
        result = preview_task(task, fetcher, subpage_limit=args.subpages)
    finally:
        fetcher.close()
    payload = result.as_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


def cmd_run(args: argparse.Namespace) -> int:
    task = load_config(args.config)
    base_dir = Path(args.config).resolve().parent
    fetcher = _build_fetcher(task)
    store = _open_db(args.db)
    try:
        task_id = store.upsert_task(task.task_name, json.dumps(task.to_dict(), ensure_ascii=False))
        seed = 0
        if args.retry_failed:
            seed = store.reset_failed_urls(task_id)
        collector = None
        if task.subpage_collect.enabled:
            collector = SubpageCollector(
                task=task,
                store=store,
                task_id=task_id,
                fetcher=fetcher,
                base_dir=base_dir,
                copyright_confirmed=args.confirm_copyright,
            )
        scheduler = Scheduler(task, store, task_id, fetcher, collector)
        scheduler.seed()
        stats = scheduler.run()
        summary = {
            "task_id": task_id,
            "pages": stats.pages,
            "items": stats.items,
            "discovered": stats.discovered,
            "failures": stats.failures,
            "media": stats.media,
            "retried": seed,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if args.export is not None:
            target = args.export or None
            path = export_excel(store, task_id, task, target)
            print(f"已导出 Excel: {path}")
        return 0
    finally:
        store.close()
        fetcher.close()


def cmd_export(args: argparse.Namespace) -> int:
    task = load_config(args.config)
    store = _open_db(args.db)
    try:
        task_id = store.upsert_task(task.task_name, json.dumps(task.to_dict(), ensure_ascii=False))
        path = export_excel(store, task_id, task, args.output)
    finally:
        store.close()
    print(f"已导出 Excel: {path}")
    return 0


def cmd_spider(args: argparse.Namespace) -> int:
    """运行一个 Spider 类并导出结果。"""
    from .spiders import export_items

    from .spiders.loader import load_spider_class

    spider_class = load_spider_class(args.target)
    settings: dict = json.loads(args.settings) if args.settings else {}
    if args.concurrency:
        settings["concurrency"] = args.concurrency
    if args.dev_mode:
        settings["dev_mode"] = True
    if args.robots:
        settings["robots_txt_obey"] = True
    spider = spider_class(settings=settings, crawldir=args.crawldir)
    items = spider.start()
    output = args.output or f"{spider.name or 'spider'}.json"
    path = export_items(items, output, args.format)
    print(
        json.dumps(
            {
                "spider": getattr(spider, "name", "spider"),
                "items": len(items),
                "stats": spider.stats,
                "output": str(path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """环境自检：yt-dlp / ffmpeg / playwright 等可选依赖是否就绪。"""
    payload = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "ytdlp": ytdlp_probe().as_dict(),
        # 与运行期一致：配置 → PATH → Scoop/Chocolatey → imageio-ffmpeg → Playwright
        "ffmpeg": describe_ffmpeg(args.ffmpeg),
        "playwright": playwright_available(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="focus-crawler", description="FocusCrawler 配置驱动爬虫")
    sub = parser.add_subparsers(dest="command", required=True)

    preview = sub.add_parser("preview", help="预览：发现子页并统计字段/媒体")
    preview.add_argument("config", help="任务 JSON 配置路径")
    preview.add_argument("--subpages", type=int, default=None, help="预览子页数量")
    preview.set_defaults(func=cmd_preview)

    run = sub.add_parser("run", help="运行任务")
    run.add_argument("config", help="任务 JSON 配置路径")
    run.add_argument("--db", default=None, help="SQLite 路径（默认 %%APPDATA%%/FocusCrawler）")
    run.add_argument("--export", nargs="?", const="", default=None, help="运行后导出 Excel（可指定路径）")
    run.add_argument("--retry-failed", action="store_true", help="先把失败 URL 重新入队")
    run.add_argument("--confirm-copyright", action="store_true", help="确认媒体版权提示（允许自动下载）")
    run.set_defaults(func=cmd_run)

    export = sub.add_parser("export", help="导出 Excel")
    export.add_argument("config", help="任务 JSON 配置路径")
    export.add_argument("--db", default=None)
    export.add_argument("--output", default=None, help="输出 xlsx 路径")
    export.set_defaults(func=cmd_export)

    spider = sub.add_parser("spider", help="运行 Spider 类（类 Scrapy 的爬虫 API）")
    spider.add_argument("target", help="Spider 目标，如 my_spider.py:MySpider")
    spider.add_argument("--output", default=None, help="导出文件（默认 <name>.json）")
    spider.add_argument("--format", default=None, help="json / jsonl / csv / xml")
    spider.add_argument("--crawldir", default=None, help="断点目录（暂停 / 续跑）")
    spider.add_argument("--settings", default=None, help="JSON 字符串，覆盖 SpiderSettings")
    spider.add_argument("--concurrency", type=int, default=None)
    spider.add_argument("--dev-mode", action="store_true", help="缓存响应到磁盘并重放")
    spider.add_argument("--robots", action="store_true", help="遵守 robots.txt")
    spider.set_defaults(func=cmd_spider)

    doctor = sub.add_parser("doctor", help="环境自检：yt-dlp / ffmpeg / playwright")
    doctor.add_argument("--ffmpeg", default=None, help="自定义 ffmpeg 可执行文件路径")
    doctor.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        for message in exc.errors:
            print(f"配置错误: {message}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"文件不存在: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
