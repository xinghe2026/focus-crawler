"""开发模式：把响应缓存到磁盘，下次直接重放（不重复请求目标站）。"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from .request import Request, Response


def _key(request: Request) -> str:
    payload = f"{request.method.upper()} {request.url}".encode("utf-8", errors="ignore")
    return hashlib.sha256(payload).hexdigest()


class ResponseCache:
    """``crawldir/cache/<sha256>.json`` 记录状态码 / 头 / 正文。"""

    def __init__(self, directory: str | Path, enabled: bool = True):
        self.directory = Path(directory)
        self.enabled = enabled
        self._lock = threading.Lock()
        if enabled:
            self.directory.mkdir(parents=True, exist_ok=True)

    def load(self, request: Request) -> Response | None:
        if not self.enabled:
            return None
        path = self.directory / f"{_key(request)}.json"
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            body = bytes.fromhex(payload.get("body", ""))
        except (OSError, ValueError):
            return None
        return Response(
            url=payload.get("url", request.url),
            status=int(payload.get("status", 200)),
            headers={str(k): str(v) for k, v in (payload.get("headers") or {}).items()},
            content=body,
            request=request,
            elapsed_ms=int(payload.get("elapsed_ms", 0)),
            from_cache=True,
            encoding=payload.get("encoding"),
        )

    def save(self, request: Request, response: Response) -> None:
        if not self.enabled:
            return
        path = self.directory / f"{_key(request)}.json"
        payload: dict[str, Any] = {
            "url": response.url,
            "status": response.status,
            "headers": response.headers,
            "body": response.content.hex(),
            "elapsed_ms": response.elapsed_ms,
            "encoding": response.encoding,
        }
        try:
            with self._lock:
                path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def count(self) -> int:
        try:
            return len(list(self.directory.glob("*.json")))
        except OSError:
            return 0


__all__ = ["ResponseCache"]
