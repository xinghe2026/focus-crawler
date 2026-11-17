"""B 站解析优先级：yt-dlp 优先 / 适配器优先 / 仅适配器，避免重复解析。"""

from __future__ import annotations

import pytest

from focus_crawler.config.schema import AudioConfig, TaskConfig, VideoConfig
from focus_crawler.core import audio_adapter, video_adapter
from focus_crawler.core.audio_adapter import AdapterContext
from focus_crawler.core.media_common import MediaCandidate

BILI = "https://www.bilibili.com/video/BV1xx411c7mD"


def _video_cfg(priority: str) -> VideoConfig:
    task = TaskConfig.from_dict(
        {
            "task_name": "t",
            "start_urls": [BILI],
            "allowed_domains": ["bilibili.com"],
            "video": {"enabled": True, "adapter": {"bilibili": True, "bilibili_priority": priority}},
            "audio": {"enabled": True, "adapter": {"bilibili": True, "bilibili_priority": priority}},
        }
    )
    return task.video


def _audio_cfg(priority: str) -> AudioConfig:
    task = TaskConfig.from_dict(
        {
            "task_name": "t",
            "start_urls": [BILI],
            "allowed_domains": ["bilibili.com"],
            "video": {"enabled": True, "adapter": {"bilibili": True, "bilibili_priority": priority}},
            "audio": {"enabled": True, "adapter": {"bilibili": True, "bilibili_priority": priority}},
        }
    )
    return task.audio


def _candidate(label: str) -> MediaCandidate:
    return MediaCandidate(url=f"https://cdn.test/{label}.mp4", kind="video", format="mp4", source_kind=label)


@pytest.fixture()
def patch_backends(monkeypatch):
    calls = {"ytdlp": 0, "adapter": 0}
    ytdlp_result: list[MediaCandidate] = []

    def fake_ytdlp(page_url, cfg, **kwargs):
        # 模拟真实语义：已有候选时 maybe_resolve 直接返回空、不发请求
        if kwargs.get("existing_count"):
            return []
        calls["ytdlp"] += 1
        return list(ytdlp_result)

    def fake_platform(page_url, cfg, ctx):
        calls["adapter"] += 1
        return "bilibili", [_candidate("adapter")]

    monkeypatch.setattr(video_adapter, "ytdlp_resolve", fake_ytdlp)
    monkeypatch.setattr(video_adapter, "_run_platform", fake_platform)
    return calls, ytdlp_result


def test_ytdlp_first_prefers_ytdlp_and_skips_adapter(patch_backends):
    calls, ytdlp_result = patch_backends
    ytdlp_result.append(_candidate("ytdlp"))
    result = video_adapter.resolve_video(BILI, _video_cfg("ytdlp_first"), AdapterContext(fetcher=None))
    assert [item.source_kind for item in result] == ["ytdlp"]
    assert calls == {"ytdlp": 1, "adapter": 0}


def test_ytdlp_first_falls_back_to_adapter_when_empty(patch_backends):
    calls, ytdlp_result = patch_backends
    ytdlp_result.clear()
    result = video_adapter.resolve_video(BILI, _video_cfg("ytdlp_first"), AdapterContext(fetcher=None))
    assert [item.source_kind for item in result] == ["adapter"]
    assert calls == {"ytdlp": 1, "adapter": 1}


def test_adapter_only_never_calls_ytdlp(patch_backends):
    calls, _ = patch_backends
    result = video_adapter.resolve_video(BILI, _video_cfg("adapter_only"), AdapterContext(fetcher=None))
    assert [item.source_kind for item in result] == ["adapter"]
    assert calls == {"ytdlp": 0, "adapter": 1}


def test_adapter_first_keeps_old_behaviour(patch_backends):
    calls, _ = patch_backends
    result = video_adapter.resolve_video(BILI, _video_cfg("adapter_first"), AdapterContext(fetcher=None))
    assert [item.source_kind for item in result] == ["adapter"]
    assert calls == {"ytdlp": 0, "adapter": 1}


def test_audio_priority_ytdlp_first(monkeypatch):
    calls = {"ytdlp": 0, "adapter": 0}

    def fake_ytdlp(page_url, cfg, **kwargs):
        calls["ytdlp"] += 1
        return [MediaCandidate(url="https://cdn.test/a.m4a", kind="audio", format="m4a", source_kind="ytdlp")]

    def fake_run_adapter(module, page_url, ctx, name):
        calls["adapter"] += 1
        return [MediaCandidate(url="https://cdn.test/b.m4a", kind="audio", format="m4a", source_kind="adapter")]

    monkeypatch.setattr(audio_adapter, "ytdlp_resolve", fake_ytdlp)
    monkeypatch.setattr(audio_adapter, "_run_adapter", fake_run_adapter)
    monkeypatch.setattr(audio_adapter, "_load_adapter", lambda name: object())
    result = audio_adapter.resolve_audio(BILI, _audio_cfg("ytdlp_first"), AdapterContext(fetcher=None))
    assert [item.source_kind for item in result] == ["ytdlp"]
    assert calls == {"ytdlp": 1, "adapter": 0}


def test_audio_adapter_only_skips_ytdlp(monkeypatch):
    calls = {"ytdlp": 0}

    def fake_ytdlp(page_url, cfg, **kwargs):
        calls["ytdlp"] += 1
        return []

    monkeypatch.setattr(audio_adapter, "ytdlp_resolve", fake_ytdlp)
    monkeypatch.setattr(
        audio_adapter,
        "_run_adapter",
        lambda module, page_url, ctx, name: [
            MediaCandidate(url="https://cdn.test/b.m4a", kind="audio", format="m4a", source_kind="adapter")
        ],
    )
    monkeypatch.setattr(audio_adapter, "_load_adapter", lambda name: object())
    result = audio_adapter.resolve_audio(BILI, _audio_cfg("adapter_only"), AdapterContext(fetcher=None))
    assert [item.source_kind for item in result] == ["adapter"]
    assert calls["ytdlp"] == 0


def test_non_bilibili_site_still_uses_ytdlp(monkeypatch):
    calls = {"ytdlp": 0}

    def fake_ytdlp(page_url, cfg, **kwargs):
        calls["ytdlp"] += 1
        return [MediaCandidate(url="https://cdn.test/v.mp4", kind="video", format="mp4", source_kind="ytdlp")]

    monkeypatch.setattr(video_adapter, "ytdlp_resolve", fake_ytdlp)
    result = video_adapter.resolve_video(
        "https://vimeo.com/123", _video_cfg("adapter_only"), AdapterContext(fetcher=None)
    )
    assert [item.source_kind for item in result] == ["ytdlp"]
    assert calls["ytdlp"] == 1
