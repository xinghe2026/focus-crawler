"""自定义解析脚本引擎测试。"""

from __future__ import annotations

from pathlib import Path

from focus_crawler.core.script_engine import ScriptEngine
from focus_crawler.core.subpage_collector import SubpageCollector

FIXTURE_SUB = (
    Path(__file__).resolve().parents[1] / "fixtures" / "sub" / "1.html"
).read_text(encoding="utf-8")


def write_script(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


GOOD_SCRIPT = """
def parse_detail(html, url, ctx):
    return {
        "title": ctx.css("h1::text").get(),
        "content": ctx.css(".content::text").get(),
        "url": url,
        "from_script": True,
    }
"""


def test_good_script(tmp_path):
    path = write_script(tmp_path, "good.py", GOOD_SCRIPT)
    engine = ScriptEngine(timeout_ms=1000)
    result = engine.run(path, "parse_detail", FIXTURE_SUB, "http://fixture.test/sub/1.html")
    assert result.ok is True
    assert result.data["title"] == "子页一标题"
    assert result.data["from_script"] is True
    assert result.duration_ms >= 0


def test_missing_script(tmp_path):
    engine = ScriptEngine()
    result = engine.run(tmp_path / "nope.py", "parse_detail", "", "http://x")
    assert result.ok is False
    assert result.error_type == "custom_parser_missing"


def test_missing_function(tmp_path):
    path = write_script(tmp_path, "empty.py", "VALUE = 1\n")
    result = ScriptEngine().run(path, "parse_detail", "", "http://x")
    assert result.error_type == "custom_parser_missing"
    assert "parse_detail" in (result.message or "")


def test_import_error(tmp_path):
    path = write_script(tmp_path, "broken.py", "def parse_detail(:\n    pass\n")
    result = ScriptEngine().run(path, "parse_detail", "", "http://x")
    assert result.error_type == "custom_parser_import_error"


def test_runtime_error(tmp_path):
    path = write_script(
        tmp_path,
        "boom.py",
        "def parse_detail(html, url, ctx):\n    raise RuntimeError('炸了')\n",
    )
    result = ScriptEngine().run(path, "parse_detail", FIXTURE_SUB, "http://x")
    assert result.error_type == "custom_parser_runtime_error"
    assert "炸了" in (result.message or "")
    assert result.traceback_text


def test_invalid_return(tmp_path):
    path = write_script(tmp_path, "list.py", "def parse_detail(html, url, ctx):\n    return []\n")
    result = ScriptEngine().run(path, "parse_detail", "", "http://x")
    assert result.error_type == "custom_parser_invalid_return"


def test_timeout(tmp_path):
    path = write_script(
        tmp_path,
        "slow.py",
        "import time\ndef parse_detail(html, url, ctx):\n    time.sleep(0.5)\n    return {}\n",
    )
    result = ScriptEngine(timeout_ms=100).run(path, "parse_detail", "", "http://x")
    assert result.error_type == "custom_parser_timeout"


def test_hybrid_falls_back_to_selector(task_config, store, task_id, tmp_path):
    task_config.parser.mode = "hybrid"
    task_config.parser.detail_script = "boom.py"
    write_script(
        tmp_path,
        "boom.py",
        "def parse_detail(html, url, ctx):\n    raise RuntimeError('脚本失败')\n",
    )
    collector = SubpageCollector(
        task=task_config,
        store=store,
        task_id=task_id,
        base_dir=tmp_path,
    )
    stats = collector.collect(
        "http://fixture.test/sub/1.html", FIXTURE_SUB, "http://fixture.test/entry.html"
    )
    assert stats.data_items == 1
    runs = store.list_parser_runs(task_id)
    assert runs and runs[0]["error_type"] == "custom_parser_runtime_error"
    item = store.list_items(task_id)[0]
    assert "子页一标题" in item["data_json"]


def test_custom_without_fallback_records_failure(task_config, store, task_id, tmp_path):
    task_config.parser.mode = "custom"
    task_config.parser.fallback_to_selector = False
    task_config.parser.detail_script = "boom.py"
    write_script(
        tmp_path,
        "boom.py",
        "def parse_detail(html, url, ctx):\n    raise RuntimeError('脚本失败')\n",
    )
    collector = SubpageCollector(task_config, store, task_id, base_dir=tmp_path)
    stats = collector.collect(
        "http://fixture.test/sub/1.html", FIXTURE_SUB, "http://fixture.test/entry.html"
    )
    assert stats.data_items == 0
    assert stats.errors == 1
    assert store.count_items(task_id) == 0
    assert store.failure_stats(task_id).get("custom_parser_runtime_error") == 1


def test_custom_script_used_in_preview(task_config, store, task_id, tmp_path):
    """custom 模式能覆盖选择器结果。"""
    task_config.parser.mode = "custom"
    task_config.parser.detail_script = "good.py"
    write_script(tmp_path, "good.py", GOOD_SCRIPT)
    collector = SubpageCollector(task_config, store, task_id, base_dir=tmp_path)
    stats = collector.collect(
        "http://fixture.test/sub/1.html", FIXTURE_SUB, "http://fixture.test/entry.html"
    )
    assert stats.data_items == 1
    assert store.list_parser_runs(task_id)[0]["status"] == "ok"
