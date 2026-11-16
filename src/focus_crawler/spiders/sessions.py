"""会话层：HTTP / 浏览器会话、按 ID 路由、代理轮换、域名拦截、DoH、API 捕获。

一个 Spider 里可以同时用多种会话，用 ``Request.session = "chrome"`` 指定：

* ``http``    —— httpx（可选 curl_cffi 的 TLS 指纹 / HTTP2）
* ``browser`` —— Playwright（可选 stealth、CDP 远程浏览器、域名/广告拦截、网络捕获）
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..utils.logging import get_logger
from .request import Request, Response

logger = get_logger("spider.sessions")


class SessionError(RuntimeError):
    """会话创建 / 请求失败。"""


# ---------------------------------------------------------------- 代理轮换
class ProxyRotator:
    """轮流或随机使用代理；支持按请求覆盖。"""

    def __init__(self, proxies: Iterable[str] | None = None, strategy: str = "round_robin"):
        self.proxies = [item for item in (proxies or []) if item]
        self.strategy = strategy if strategy in ("round_robin", "random") else "round_robin"
        self._index = 0

    def next(self) -> str | None:
        if not self.proxies:
            return None
        if self.strategy == "random":
            return random.choice(self.proxies)
        proxy = self.proxies[self._index % len(self.proxies)]
        self._index += 1
        return proxy


# ---------------------------------------------------------------- DoH
class DoHResolver:
    """通过 Cloudflare DoH 解析域名，避免 DNS 泄漏到本地解析器。"""

    def __init__(self, endpoint: str = "https://cloudflare-dns.com/dns-query", timeout: float = 5.0):
        self.endpoint = endpoint
        self.timeout = timeout
        self._cache: dict[str, list[str]] = {}

    async def resolve(self, host: str) -> list[str]:
        if not host:
            return []
        cached = self._cache.get(host)
        if cached is not None:
            return cached
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    self.endpoint,
                    params={"name": host, "type": "A"},
                    headers={"accept": "application/dns-json"},
                )
                payload = response.json()
            answers = [item.get("data") for item in payload.get("Answer") or [] if item.get("type") == 1]
        except Exception as exc:  # noqa: BLE001 - DoH 失败时回退系统解析
            logger.debug("DoH 解析失败 %s：%s", host, exc)
            answers = []
        self._cache[host] = answers
        return answers


# ---------------------------------------------------------------- 广告 / 域名拦截
AD_DOMAINS = (
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "google-analytics.com",
    "googletagmanager.com",
    "adservice.google.com",
    "amazon-adsystem.com",
    "adsrvr.org",
    "criteo.com",
    "criteo.net",
    "facebook.net",
    "hotjar.com",
    "scorecardresearch.com",
    "taboola.com",
    "outbrain.com",
    "quantserve.com",
    "adnxs.com",
    "pubmatic.com",
    "rubiconproject.com",
    "openx.net",
    "moatads.com",
    "yieldmo.com",
    "segment.io",
    "mixpanel.com",
)


def host_blocked(url: str, blocked: Iterable[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == item or host.endswith("." + item) for item in blocked if item)


# ---------------------------------------------------------------- 会话配置
@dataclass
class SessionSettings:
    kind: str = "http"
    impersonate: str = ""
    proxy: str = ""
    proxies: list[str] = field(default_factory=list)
    proxy_strategy: str = "round_robin"
    headers: dict[str, str] = field(default_factory=dict)
    cookies_file: str = ""
    timeout: float = 30.0
    verify: bool = True
    headless: bool = True
    cdp_url: str = ""
    executable_path: str = ""
    block_domains: list[str] = field(default_factory=list)
    block_ads: bool = False
    dns_over_https: bool = False
    record_requests: bool = False
    max_recorded: int = 200
    stealth: bool = True
    wait_until: str = "domcontentloaded"
    wait_selector: str = ""
    wait_timeout_ms: int = 20000

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "SessionSettings":
        data = dict(raw or {})
        allowed = {field_name for field_name in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{key: value for key, value in data.items() if key in allowed})

    def blocked_hosts(self) -> list[str]:
        blocklist = list(self.block_domains)
        if self.block_ads:
            blocklist.extend(AD_DOMAINS)
        return blocklist


# ---------------------------------------------------------------- HTTP 会话
class HttpSession:
    """基于 httpx 的异步会话；装了 curl_cffi 时可指定浏览器 TLS 指纹。"""

    def __init__(self, settings: SessionSettings, rotator: ProxyRotator | None = None, client: Any = None):
        self.settings = settings
        self.rotator = rotator
        self._client: Any = client
        self._injected = client is not None
        self._curl = False
        self._doh = DoHResolver() if settings.dns_over_https else None

    async def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        proxy = self.rotator.next() if self.rotator else (self.settings.proxy or None)
        if self.settings.impersonate:
            try:
                from curl_cffi.requests import AsyncSession  # noqa: PLC0415 - 可选依赖

                self._client = AsyncSession(
                    impersonate=self.settings.impersonate,
                    timeout=self.settings.timeout,
                    proxies={"http": proxy, "https": proxy} if proxy else None,
                )
                self._curl = True
                return self._client
            except Exception as exc:  # noqa: BLE001 - 没装 curl_cffi 就退回 httpx
                logger.debug("curl_cffi 不可用，使用 httpx：%s", exc)
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=self.settings.timeout,
            verify=self.settings.verify,
            proxy=proxy,
        )
        self._curl = False
        return self._client

    async def fetch(self, request: Request) -> Response:
        blocked = self.settings.blocked_hosts()
        if blocked and host_blocked(request.url, blocked):
            return Response(
                url=request.url,
                status=403,
                headers={"x-focus-blocked": "1"},
                content=b"blocked by policy",
                request=request,
            )
        headers = {**self.settings.headers, **request.headers}
        started = time.perf_counter()
        client = await self._ensure_client()
        try:
            if self._curl:
                result = await client.request(
                    request.method, request.url, headers=headers, data=request.body
                )
                content = result.content
                status = result.status_code
                resp_headers = dict(result.headers)
            else:
                result = await client.request(
                    request.method, request.url, headers=headers, content=request.body
                )
                content = result.content
                status = result.status_code
                resp_headers = {str(k): str(v) for k, v in result.headers.items()}
        except Exception as exc:  # noqa: BLE001 - 网络异常转成会话错误
            raise SessionError(f"HTTP 请求失败 {request.url}：{exc}") from exc
        return Response(
            url=str(getattr(result, "url", request.url)),
            status=status,
            headers=resp_headers,
            content=content,
            request=request,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    async def close(self) -> None:
        if self._client is not None and not self._injected:
            try:
                await self._client.aclose()
            except Exception:  # noqa: BLE001 - curl_cffi 用 close()
                try:
                    self._client.close()
                except Exception:  # noqa: BLE001
                    pass
        self._client = None


# ---------------------------------------------------------------- 浏览器会话
@dataclass
class NetworkRecord:
    url: str
    status: int
    text: str


class BrowserSession:
    """Playwright 会话：反检测参数、域名拦截、CDP 远程浏览器、网络捕获。"""

    def __init__(self, settings: SessionSettings, rotator: ProxyRotator | None = None):
        self.settings = settings
        self.rotator = rotator
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._records: list[NetworkRecord] = []
        self._lock = asyncio.Lock()

    async def _ensure_page(self):
        if self._page is not None:
            return self._page
        try:
            from playwright.async_api import async_playwright  # noqa: PLC0415 - 可选依赖
        except ImportError as exc:  # pragma: no cover - 依赖缺失
            raise SessionError("未安装 playwright，无法使用浏览器会话：pip install playwright") from exc

        proxy = self.rotator.next() if self.rotator else (self.settings.proxy or None)
        self._playwright = await async_playwright().start()
        if self.settings.cdp_url:
            self._browser = await self._playwright.chromium.connect_over_cdp(self.settings.cdp_url)
        else:
            launch_kwargs: dict[str, Any] = {"headless": self.settings.headless}
            if proxy:
                launch_kwargs["proxy"] = {"server": proxy}
            if self.settings.executable_path:
                launch_kwargs["executable_path"] = self.settings.executable_path
            if self.settings.stealth:
                launch_kwargs["args"] = [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                ]
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)

        context_kwargs: dict[str, Any] = {
            "user_agent": self.settings.headers.get("User-Agent")
            or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "locale": "zh-CN",
            "viewport": {"width": 1366, "height": 768},
        }
        if self.settings.cookies_file:
            context_kwargs["storage_state"] = self.settings.cookies_file
        self._context = await self._browser.new_context(**context_kwargs)

        blocked = self.settings.blocked_hosts()
        if blocked:
            async def _route(route, request):
                if host_blocked(request.url, blocked):
                    await route.abort()
                else:
                    await route.continue_()

            await self._context.route("**/*", _route)

        if self.settings.record_requests:
            self._context.on("response", self._on_response)

        self._page = await self._context.new_page()
        return self._page

    def _on_response(self, response) -> None:
        async def _store() -> None:
            try:
                if len(self._records) >= self.settings.max_recorded:
                    return
                text = ""
                content_type = (response.headers or {}).get("content-type", "")
                if any(kind in content_type for kind in ("json", "text", "javascript", "xml")):
                    text = (await response.text())[:20000]
                self._records.append(NetworkRecord(url=response.url, status=response.status, text=text))
            except Exception:  # noqa: BLE001 - 捕获失败不影响主流程
                pass

        try:
            asyncio.create_task(_store())
        except RuntimeError:
            pass

    async def fetch(self, request: Request) -> Response:
        async with self._lock:
            page = await self._ensure_page()
            started = time.perf_counter()
            try:
                response = await page.goto(request.url, wait_until=self.settings.wait_until)
                if self.settings.wait_selector:
                    await page.wait_for_selector(
                        self.settings.wait_selector, timeout=self.settings.wait_timeout_ms
                    )
                html = await page.content()
                status = response.status if response is not None else 200
            except Exception as exc:  # noqa: BLE001 - 浏览器异常转成会话错误
                raise SessionError(f"浏览器请求失败 {request.url}：{exc}") from exc
            return Response(
                url=page.url,
                status=status,
                headers={"content-type": "text/html; charset=utf-8"},
                content=html.encode("utf-8"),
                request=request,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )

    # -- 网络捕获 -------------------------------------------------
    def search_network(self, pattern: str, limit: int | None = None) -> list[NetworkRecord]:
        matcher = re.compile(pattern)
        hits = [item for item in self._records if matcher.search(item.url) or matcher.search(item.text)]
        return hits[:limit] if limit else hits

    @property
    def network(self) -> "BrowserSession":
        return self

    async def close(self) -> None:
        for closer in (self._context, self._browser):
            if closer is not None:
                try:
                    await closer.close()
                except Exception:  # noqa: BLE001
                    pass
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # noqa: BLE001
                pass
        self._page = self._context = self._browser = self._playwright = None


# ---------------------------------------------------------------- 会话管理
class SessionManager:
    """按 ID 管理多个会话；同 ID 复用，支持混合 HTTP 与浏览器。"""

    def __init__(self, defaults: SessionSettings | None = None):
        self.defaults = defaults or SessionSettings()
        self._sessions: dict[str, Any] = {}
        self._settings: dict[str, SessionSettings] = {}
        self._rotators: dict[str, ProxyRotator] = {}
        self._lock = asyncio.Lock()

    def configure(self, session_id: str, settings: SessionSettings) -> None:
        self._settings[session_id] = settings
        if settings.proxies:
            self._rotators[session_id] = ProxyRotator(settings.proxies, settings.proxy_strategy)

    async def get(self, session_id: str = "default"):
        async with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                return existing
            settings = self._settings.get(session_id, self.defaults)
            rotator = self._rotators.get(session_id)
            session = (
                BrowserSession(settings, rotator)
                if settings.kind == "browser"
                else HttpSession(settings, rotator)
            )
            self._sessions[session_id] = session
            return session

    async def fetch(self, request: Request) -> Response:
        session = await self.get(request.session)
        return await session.fetch(request)

    async def search_network(self, session_id: str, pattern: str, limit: int | None = None):
        session = self._sessions.get(session_id)
        if isinstance(session, BrowserSession):
            return session.search_network(pattern, limit)
        return []

    async def close_all(self) -> None:
        for session in self._sessions.values():
            await session.close()
        self._sessions.clear()


__all__ = [
    "AD_DOMAINS",
    "BrowserSession",
    "DoHResolver",
    "HttpSession",
    "NetworkRecord",
    "ProxyRotator",
    "SessionError",
    "SessionManager",
    "SessionSettings",
    "host_blocked",
]
