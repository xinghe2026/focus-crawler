"""会话层：HTTP 会话、拦截、代理轮换、DoH、按 ID 路由。"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from focus_crawler.spiders.request import Request
from focus_crawler.spiders.sessions import (
    AD_DOMAINS,
    DoHResolver,
    HttpSession,
    ProxyRotator,
    SessionManager,
    SessionSettings,
    host_blocked,
)


def run(coro):
    return asyncio.run(coro)


def test_http_session_fetches_and_records_elapsed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<h1>hi</h1>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    session = HttpSession(SessionSettings(), client=client)
    response = run(session.fetch(Request(url="https://site.test/")))
    assert response.status == 200
    assert "hi" in response.text
    assert response.elapsed_ms >= 0
    run(session.close())


def test_http_session_sends_headers_and_body():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        seen["body"] = request.content
        return httpx.Response(200, text="ok")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = SessionSettings(headers={"X-Test": "1"})
    session = HttpSession(settings, client=client)
    run(session.fetch(Request(url="https://site.test/", method="POST", body=b"payload")))

    assert seen["headers"]["x-test"] == "1"
    assert seen["body"] == b"payload"


def test_block_ads_and_custom_domains():
    settings = SessionSettings(block_ads=True, block_domains=["evil.test"])
    session = HttpSession(settings)
    blocked = run(session.fetch(Request(url="https://doubleclick.net/ad.js")))
    assert blocked.status == 403
    assert blocked.headers.get("x-focus-blocked") == "1"
    evil = run(session.fetch(Request(url="https://sub.evil.test/x")))
    assert evil.status == 403
    assert host_blocked("https://ads.doubleclick.net/x", AD_DOMAINS)


def test_proxy_rotator_round_robin_and_random():
    rotator = ProxyRotator(["http://p1", "http://p2"])
    assert rotator.next() == "http://p1"
    assert rotator.next() == "http://p2"
    assert rotator.next() == "http://p1"

    random_rotator = ProxyRotator(["http://p1", "http://p2"], strategy="random")
    assert random_rotator.next() in {"http://p1", "http://p2"}
    assert ProxyRotator([]).next() is None


def test_doh_resolver_parses_answers(monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None, headers=None):
            class R:
                def json(self):
                    return {"Answer": [{"type": 1, "data": "1.2.3.4"}, {"type": 1, "data": "5.6.7.8"}]}

            return R()

    monkeypatch.setattr("focus_crawler.spiders.sessions.httpx.AsyncClient", FakeClient)
    resolver = DoHResolver()
    assert run(resolver.resolve("site.test")) == ["1.2.3.4", "5.6.7.8"]
    # 缓存生效
    assert run(resolver.resolve("site.test")) == ["1.2.3.4", "5.6.7.8"]


def test_session_manager_routes_by_id_and_reuses():
    manager = SessionManager(SessionSettings())

    async def scenario():
        first = await manager.get("default")
        again = await manager.get("default")
        chrome = await manager.get("chrome")
        assert first is again
        assert chrome is not first
        await manager.close_all()

    run(scenario())


def test_session_manager_configures_browser_sessions():
    manager = SessionManager(SessionSettings())
    manager.configure("chrome", SessionSettings.from_dict({"kind": "browser", "headless": True}))

    async def scenario():
        session = await manager.get("chrome")
        assert session.__class__.__name__ == "BrowserSession"
        await manager.close_all()

    run(scenario())


def test_session_settings_from_dict_ignores_unknown_keys():
    settings = SessionSettings.from_dict({"kind": "browser", "nope": 1, "timeout": 5})
    assert settings.kind == "browser"
    assert settings.timeout == 5
