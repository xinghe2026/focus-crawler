"""Excel 导出测试。"""

from __future__ import annotations

from openpyxl import load_workbook

from focus_crawler.core.exporter import export_excel
from focus_crawler.core.scheduler import Scheduler


def test_export_sheets(task_config, fetcher, store, task_id, tmp_path):
    scheduler = Scheduler(task_config, store, task_id, fetcher)
    scheduler.seed()
    scheduler.run()

    target = tmp_path / "out.xlsx"
    path = export_excel(store, task_id, task_config, target)
    assert path.exists()

    workbook = load_workbook(path)
    assert workbook.sheetnames == ["data", "failures", "images", "videos", "audios", "stats", "parser_runs"]

    assert workbook["data"].max_row == 4  # 表头 + 3 条数据
    data_headers = [cell.value for cell in workbook["data"][1]]
    assert "source_url" in data_headers
    assert "title" in data_headers

    assert workbook["images"].max_row == 4
    assert workbook["videos"].max_row == 3
    assert workbook["audios"].max_row == 2

    stats = {
        row[0].value: row[1].value
        for row in workbook["stats"].iter_rows(min_row=2)
    }
    assert stats["图片数"] == 3
    assert stats["视频数"] == 2
    assert stats["音频数"] == 1


def test_export_creates_parent_dir(task_config, store, task_id, tmp_path):
    target = tmp_path / "nested" / "deep" / "out.xlsx"
    path = export_excel(store, task_id, task_config, target)
    assert path.exists()
