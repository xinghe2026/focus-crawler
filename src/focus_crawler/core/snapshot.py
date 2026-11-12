"""页面快照：解析失败时保存 HTML 便于排查。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..utils.hash import url_hash


def save_snapshot(html: str, url: str, directory: str | Path, task_name: str = "") -> Path:
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"{task_name}_" if task_name else ""
    target = folder / f"{prefix}{stamp}_{url_hash(url)[:12]}.html"
    target.write_text(html or "", encoding="utf-8")
    return target
