"""URL 记录模型。"""

from __future__ import annotations

from dataclasses import dataclass

URL_STATUSES = ("pending", "fetching", "done", "failed", "skipped")
PAGE_TYPES = ("seed", "discovered", "list", "detail")


@dataclass
class UrlRecord:
    id: int
    task_id: int
    url: str
    url_hash: str
    parent_url: str | None
    depth: int
    page_type: str
    status: str
    http_status: int | None = None
    retry_count: int = 0
    error: str | None = None
    fetched_at: str | None = None
    content_hash: str | None = None
