"""配方库与 m3u8 主/子列表解析辅助测试。"""

from __future__ import annotations

import zipfile

from focus_crawler.core.recipe_manager import export_recipe, import_recipe

FIXTURES = __import__("pathlib").Path(__file__).resolve().parents[1] / "fixtures"


def make_recipe(directory):
    recipe = directory / "demo_recipe"
    recipe.mkdir()
    (recipe / "config.json").write_text('{"task_name": "demo"}', encoding="utf-8")
    (recipe / "detail_parser.py").write_text("def parse_detail(html, url, ctx):\n    return {}\n", encoding="utf-8")
    (recipe / "README.md").write_text("# demo", encoding="utf-8")
    (recipe / "fixtures").mkdir()
    (recipe / "fixtures" / "page.html").write_text("<html></html>", encoding="utf-8")
    return recipe


def test_export_and_import_recipe(tmp_path):
    recipe = make_recipe(tmp_path)
    archive = tmp_path / "demo.zip"
    export_recipe(recipe, archive)
    assert archive.exists()

    with zipfile.ZipFile(archive) as zf:
        assert "demo_recipe/config.json" in zf.namelist()

    restored = import_recipe(archive, tmp_path / "imported")
    assert (restored / "config.json").exists()
    assert (restored / "fixtures" / "page.html").exists()


def test_import_rejects_path_traversal(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../evil.txt", "x")
    try:
        import_recipe(archive, tmp_path / "out")
    except ValueError as exc:
        assert "非法路径" in str(exc)
    else:
        raise AssertionError("应当拒绝路径穿越的压缩包")
