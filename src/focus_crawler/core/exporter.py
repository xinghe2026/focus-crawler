"""Excel 导出：data / failures / stats / images / videos / audios / parser_runs。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..config.schema import TaskConfig
from ..utils.paths import export_path

_HEADER_FONT = Font(bold=True, color="FFFFFF")
_HEADER_FILL = PatternFill("solid", fgColor="4F81BD")


def _row_value(row: Any, column: str) -> Any:
    """兼容老库：缺列时返回 None，避免导出因迁移前数据报错。"""
    try:
        return row[column]
    except (IndexError, KeyError):
        return None


def _write_sheet(workbook: Workbook, title: str, headers: list[str], rows: list[list[Any]]) -> None:
    sheet = workbook.create_sheet(title)
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center")
    for row in rows:
        sheet.append(list(row))
    sheet.freeze_panes = "A2"
    if rows:
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{sheet.max_row}"
    for index, header in enumerate(headers, start=1):
        longest = len(str(header))
        for row in rows:
            value = row[index - 1]
            longest = max(longest, len(str(value)) if value is not None else 0)
        sheet.column_dimensions[get_column_letter(index)].width = min(max(longest + 2, 10), 60)
    for row_index in range(2, sheet.max_row + 1):
        for col_index in range(1, len(headers) + 1):
            cell = sheet.cell(row=row_index, column=col_index)
            value = cell.value
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                cell.hyperlink = value
                cell.style = "Hyperlink"


def _item_rows(store: Any, task_id: int, columns: list[str]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for item in store.list_items(task_id):
        data = json.loads(item["data_json"])
        row = []
        for column in columns:
            if column == "source_url":
                row.append(item["url"])
            else:
                row.append(data.get(column))
        rows.append(row)
    return rows


def export_excel(
    store: Any,
    task_id: int,
    task: TaskConfig,
    output_path: str | Path | None = None,
) -> Path:
    if output_path:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        target = export_path(task.export.excel.file)

    excel_cfg = task.export.excel
    items = store.list_items(task_id)
    configured = list(excel_cfg.columns or [])
    if not configured:
        seen: list[str] = []
        for item in items:
            for key in json.loads(item["data_json"]).keys():
                if key not in seen:
                    seen.append(key)
        configured = seen
    columns = ["source_url"] + [column for column in configured if column != "source_url"]

    workbook = Workbook()
    workbook.remove(workbook.active)

    _write_sheet(workbook, excel_cfg.sheet or "data", columns, _item_rows(store, task_id, columns))

    failures = store.list_failures(task_id)
    _write_sheet(
        workbook,
        "failures",
        ["url", "failure_type", "error", "retry_count", "last_try_at"],
        [[row["url"], row["failure_type"], row["error"], row["retry_count"], row["last_try_at"]] for row in failures],
    )

    if excel_cfg.include_media_sheets:
        _write_sheet(
            workbook,
            "images",
            ["source_url", "parent_url", "media_url", "format", "width", "height", "file_size", "local_path", "status"],
            [
                [
                    row["source_url"],
                    row["parent_url"],
                    row["media_url"],
                    row["format"],
                    row["width"],
                    row["height"],
                    row["file_size"],
                    row["local_path"],
                    row["status"],
                ]
                for row in store.list_media(task_id, media_type="image")
            ],
        )
        _write_sheet(
            workbook,
            "videos",
            [
                "source_url",
                "parent_url",
                "media_url",
                "format",
                "quality",
                "source_kind",
                "file_size",
                "local_path",
                "status",
            ],
            [
                [
                    row["source_url"],
                    row["parent_url"],
                    row["media_url"],
                    row["format"],
                    row["quality"],
                    _row_value(row, "source_kind"),
                    row["file_size"],
                    row["local_path"],
                    row["status"],
                ]
                for row in store.list_media(task_id, media_type="video")
            ],
        )
        _write_sheet(
            workbook,
            "audios",
            [
                "source_url",
                "parent_url",
                "media_url",
                "format",
                "bitrate",
                "duration",
                "sample_rate",
                "source_kind",
                "file_size",
                "local_path",
                "status",
            ],
            [
                [
                    row["source_url"],
                    row["parent_url"],
                    row["media_url"],
                    row["format"],
                    row["bitrate"],
                    row["duration"],
                    row["sample_rate"],
                    _row_value(row, "source_kind"),
                    row["file_size"],
                    row["local_path"],
                    row["status"],
                ]
                for row in store.list_media(task_id, media_type="audio")
            ],
        )

    stats = store.stats(task_id)
    stats_rows: list[list[Any]] = [
        ["任务名", task.task_name],
        ["导出时间", datetime.now().replace(microsecond=0).isoformat(sep=" ")],
        ["URL 总数", stats["urls_total"]],
        ["已抓取成功", stats["urls_done"]],
        ["抓取失败", stats["urls_failed"]],
        ["待抓取", stats["urls_pending"]],
        ["数据条数", stats["items"]],
        ["图片数", stats["media"].get("image", 0)],
        ["视频数", stats["media"].get("video", 0)],
        ["音频数", stats["media"].get("audio", 0)],
    ]
    for failure_type, count in stats["failures"].items():
        stats_rows.append([f"失败分类·{failure_type}", count])
    _write_sheet(workbook, "stats", ["指标", "值"], stats_rows)

    runs = store.list_parser_runs(task_id)
    _write_sheet(
        workbook,
        "parser_runs",
        ["url", "script_path", "status", "error_type", "error_message", "duration_ms", "created_at"],
        [
            [
                row["url"],
                row["script_path"],
                row["status"],
                row["error_type"],
                row["error_message"],
                row["duration_ms"],
                row["created_at"],
            ]
            for row in runs
        ],
    )

    workbook.save(target)
    return target
