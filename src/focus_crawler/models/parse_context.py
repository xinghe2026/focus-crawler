"""脚本解析上下文 ParseContext。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from parsel import Selector, SelectorList

from ..utils.jsonpath import JsonPathError, query
from ..utils.paths import app_home

if TYPE_CHECKING:
    from ..config.schema import TaskConfig


@dataclass
class XhrResponse:
    """XHR / Fetch 响应（由 Playwright 或调用方填充）。"""

    url: str
    status_code: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    text: str = ""

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"响应不是合法 JSON: {exc}") from exc


class ParseContext:
    """传给自定义脚本的上下文，只做解析，不提供网络能力。"""

    def __init__(
        self,
        html: str,
        url: str,
        page_type: str = "detail",
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        responses: list[XhrResponse] | None = None,
        config: "TaskConfig | None" = None,
        logger: Any = None,
        task_name: str = "",
    ):
        self.html = html or ""
        self.url = url
        self.page_type = page_type
        self.status_code = status_code
        self.headers = headers or {}
        self.responses = responses or []
        self.config = config
        self.logger = logger
        self.task_name = task_name
        self._selector: Selector | None = None

    @property
    def selector(self) -> Selector:
        if self._selector is None:
            self._selector = Selector(text=self.html)
        return self._selector

    def css(self, expression: str) -> SelectorList:
        return self.selector.css(expression)

    def xpath(self, expression: str) -> SelectorList:
        return self.selector.xpath(expression)

    def regex(self, pattern: str, source: str | None = None) -> list[str]:
        text = self.html if source is None else source
        return re.findall(pattern, text)

    def jsonpath(self, expression: str, index: int = 0) -> list[Any]:
        if not self.responses:
            return []
        try:
            return query(self.responses[index].json(), expression)
        except (IndexError, ValueError, JsonPathError):
            return []

    def absolute_url(self, href: str) -> str:
        return urljoin(self.url, href or "")

    def save_snapshot(self, directory: str | Path | None = None) -> str:
        from ..core.snapshot import save_snapshot

        return str(save_snapshot(self.html, self.url, directory or app_home() / "snapshots", self.task_name))
