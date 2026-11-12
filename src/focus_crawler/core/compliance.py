"""合规：robots.txt 检查与版权确认。"""

from __future__ import annotations

from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx


class RobotsCache:
    """按域名缓存 robots.txt 解析结果。获取失败时按“允许”处理并记录。"""

    def __init__(self, client: httpx.Client, user_agent: str = "FocusCrawler"):
        self.client = client
        self.user_agent = user_agent
        self._cache: dict[str, RobotFileParser | None] = {}

    def _load(self, scheme: str, netloc: str) -> RobotFileParser | None:
        key = f"{scheme}://{netloc}"
        if key in self._cache:
            return self._cache[key]
        parser: RobotFileParser | None = None
        try:
            response = self.client.get(f"{key}/robots.txt", timeout=10)
            if response.status_code == 200:
                parser = RobotFileParser()
                parser.parse(response.text.splitlines())
            elif response.status_code in (401, 403):
                parser = RobotFileParser()
                parser.disallow_all = True
        except httpx.HTTPError:
            parser = None
        self._cache[key] = parser
        return parser

    def is_allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        parser = self._load(parts.scheme, parts.netloc)
        if parser is None:
            return True
        return parser.can_fetch(self.user_agent, url)


def require_copyright_confirm(enabled: bool, confirmed: bool = False) -> None:
    """媒体下载前的版权确认闸门。"""
    if enabled and not confirmed:
        raise PermissionError("下载媒体前需要用户确认版权提示")
