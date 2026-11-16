"""Cookie 持久化：httpx 用 Netscape cookies.txt，浏览器用 Playwright storage_state。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

HEADER = "# Netscape HTTP Cookie File\n# Saved by FocusCrawler\n"


def load_netscape(path: str | Path, jar: Any = None) -> list[dict[str, str]]:
    """读取 Netscape cookies.txt，返回 cookie 列表。"""
    target = Path(path)
    if not target.is_file():
        return []
    entries: list[dict[str, str]] = []
    try:
        for line in target.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, flag, path_value, secure, expiry, name, value = parts[:7]
            entries.append(
                {
                    "domain": domain,
                    "path": path_value or "/",
                    "secure": secure.upper() == "TRUE",
                    "expires": expiry,
                    "name": name,
                    "value": value,
                }
            )
    except OSError:
        return []
    if jar is not None:
        for entry in entries:
            try:
                jar.set(entry["name"], entry["value"], domain=entry["domain"], path=entry["path"])
            except Exception:  # noqa: BLE001 - 单条坏 cookie 不影响其它
                continue
    return entries


def save_netscape(cookies: httpx.Cookies, path: str | Path) -> Path:
    """把 httpx 的 cookie jar 写成 Netscape cookies.txt。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [HEADER]
    jar = getattr(cookies, "jar", None)
    if jar is not None:
        for cookie in jar:
            domain = cookie.domain or ""
            include_sub = "TRUE" if domain.startswith(".") else "FALSE"
            secure = "TRUE" if cookie.secure else "FALSE"
            expires = str(int(cookie.expires or 0))
            lines.append(
                "\t".join(
                    [
                        domain,
                        include_sub,
                        cookie.path or "/",
                        secure,
                        expires,
                        cookie.name,
                        cookie.value or "",
                    ]
                )
                + "\n"
            )
    target.write_text("".join(lines), encoding="utf-8")
    return target


def storage_state_from_cookies(entries: list[dict[str, str]], url: str = "") -> dict[str, Any]:
    """把 cookie 列表转成 Playwright storage_state 结构（便于跨后端复用）。"""
    return {
        "cookies": [
            {
                "name": entry["name"],
                "value": entry["value"],
                "domain": entry["domain"],
                "path": entry["path"],
                "expires": float(entry["expires"]) if entry.get("expires") else -1,
                "httpOnly": False,
                "secure": entry.get("secure", False),
                "sameSite": "Lax",
            }
            for entry in entries
        ],
        "origins": [],
        "_savedAt": time.time(),
        "_url": url,
    }


__all__ = ["load_netscape", "save_netscape", "storage_state_from_cookies"]
