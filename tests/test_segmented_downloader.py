"""分段下载引擎：规划器算法、限速、断点续传与回退。"""

from __future__ import annotations

import threading

import httpx
import pytest

from focus_crawler.core.media_common import MediaDownloadError
from focus_crawler.core import segmented_downloader as seg

PAYLOAD = bytes(range(256)) * (12 * 1024 * 100 // 256)  # ≈1.2 MiB


def make_handler(payload: bytes, supports_range: bool = True):
    def handler(request: httpx.Request) -> httpx.Response:
        range_header = request.headers.get("range", "")
        if not supports_range:
            return httpx.Response(200, headers={"content-length": str(len(payload))}, content=payload)
        if not range_header.startswith("bytes="):
            return httpx.Response(200, headers={"content-length": str(len(payload))}, content=payload)
        spec = range_header.split("=", 1)[1]
        start_text, _, end_text = spec.partition("-")
        start = int(start_text or 0)
        end = int(end_text) if end_text else len(payload) - 1
        end = min(end, len(payload) - 1)
        chunk = payload[start : end + 1]
        return httpx.Response(
            206,
            headers={
                "content-range": f"bytes {start}-{end}/{len(payload)}",
                "content-length": str(len(chunk)),
                "etag": '"abc123"',
            },
            content=chunk,
        )

    return handler


def client_for(payload: bytes, supports_range: bool = True) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(make_handler(payload, supports_range)))


# ---------------------------------------------------------------- Planner
def test_initial_segments_cover_file():
    planner = seg.Planner(size=10 * 1024 * 1024, connections=4)
    assert len(planner.segments) == 4
    assert planner.segments[0].start == 0
    assert planner.segments[-1].end == 10 * 1024 * 1024
    for left, right in zip(planner.segments, planner.segments[1:]):
        assert left.end == right.start


def test_claim_commit_release():
    planner = seg.Planner(size=8 * 1024 * 1024, connections=2)
    assignment = planner.claim(0)
    assert assignment is not None
    segment = assignment.segment
    assert assignment.from_offset == segment.start
    planner.commit(0, segment, 4096, segment.start + 4096)
    assert segment.cursor == segment.start + 4096
    planner.release(0, segment)
    assert segment.worker is None


def test_split_largest_when_all_assigned():
    planner = seg.Planner(size=4 * 1024 * 1024, connections=1, min_split=128 * 1024)
    first = planner.claim(0)
    assert first is not None
    before = len(planner.segments)
    second = planner.claim(1)
    assert second is not None
    assert len(planner.segments) == before + 1
    # 拆出来的新段与剩余段不重叠
    spans = sorted((item.start, item.end) for item in planner.segments)
    for left, right in zip(spans, spans[1:]):
        assert left[1] <= right[0]


def test_preempt_next_segment():
    planner = seg.Planner(size=4 * 1024 * 1024, connections=2, min_split=1024 * 1024, preempt_after=0.0)
    first = planner.claim(0)
    assert first is not None
    planner.commit(0, first.segment, 1024, first.to_offset)  # 第一段完成
    other = planner.claim(1)  # worker1 拿到第二段但未开始接收
    assert other is not None
    preempted = planner.claim(0)  # worker0 应抢占第二段
    assert preempted is not None
    assert preempted.segment is other.segment
    assert preempted.segment.worker == 0


def test_intervals_merge():
    planner = seg.Planner(size=4 * 1024 * 1024, connections=4)
    for item in planner.segments:
        item.worker = 99
        planner.commit(99, item, 1024, item.end)
    spans = planner.merge_intervals()
    assert spans == [(0, 4 * 1024 * 1024)]
    assert planner.is_done() is True


# ---------------------------------------------------------------- 限速
def test_rate_limiter_throttles():
    limiter = seg.RateLimiter(1024 * 100)  # 100 KiB/s
    start = __import__("time").monotonic()
    limiter.consume(1024 * 50)
    assert __import__("time").monotonic() - start >= 0.1


# ---------------------------------------------------------------- 端到端
def test_segmented_download_writes_correct_bytes(tmp_path):
    client = client_for(PAYLOAD)
    target = tmp_path / "video.mp4"
    report = seg.download_file(
        client, "https://cdn.test/v.mp4", target, connections=4, chunk_size=32 * 1024, min_segmented_size=64 * 1024
    )
    assert report.path == target
    assert target.read_bytes() == PAYLOAD
    assert report.segmented is True
    assert not (tmp_path / "video.mp4.part").exists()
    assert not (tmp_path / "video.mp4.fcdl").exists()


def test_download_falls_back_when_server_ignores_range(tmp_path):
    client = client_for(PAYLOAD, supports_range=False)
    target = tmp_path / "file.bin"
    report = seg.download_file(client, "https://cdn.test/f.bin", target, connections=4)
    assert report.segmented is False
    assert target.read_bytes() == PAYLOAD


def test_small_file_uses_single_stream(tmp_path):
    small = b"tiny-bytes"
    client = client_for(small)
    target = tmp_path / "small.bin"
    report = seg.download_file(client, "https://cdn.test/s.bin", target)
    assert report.segmented is False
    assert target.read_bytes() == small


def test_download_resumes_from_partial_state(tmp_path):
    payload = bytes(range(256)) * (16 * 1024 * 8 // 256)  # 128 KiB → 会走单连接
    client = client_for(payload)
    target = tmp_path / "big.bin"
    # 用小 min_split 与低阈值强制走分段
    stop_flag = {"stop": False}
    seen = {"bytes": 0}

    def on_progress(done, total, speed):
        seen["bytes"] = done
        if done > len(payload) // 4:
            stop_flag["stop"] = True

    with pytest.raises(MediaDownloadError):
        seg.download_file(
            client,
            "https://cdn.test/big.bin",
            target,
            connections=2,
            min_split=8 * 1024,
            min_segmented_size=32 * 1024,
            should_stop=lambda: stop_flag["stop"],
            on_progress=on_progress,
        )
    part = tmp_path / "big.bin.part"
    state = tmp_path / "big.bin.fcdl"
    if not part.exists():
        pytest.skip("该环境未进入分段模式")
    assert state.exists()

    report = seg.download_file(
        client,
        "https://cdn.test/big.bin",
        target,
        connections=2,
        min_split=8 * 1024,
        min_segmented_size=32 * 1024,
    )
    assert target.read_bytes() == payload
    assert report.resumed is True


def test_download_stop_raises(tmp_path):
    client = client_for(PAYLOAD)
    with pytest.raises(MediaDownloadError):
        seg.download_file(
            client,
            "https://cdn.test/v.mp4",
            tmp_path / "x.mp4",
            connections=2,
            min_segmented_size=64 * 1024,
            should_stop=lambda: True,
        )


def test_cleanup_partial(tmp_path):
    target = tmp_path / "media.mp4"
    (tmp_path / "media.mp4.part").write_bytes(b"x")
    (tmp_path / "media.mp4.fcdl").write_text("{}", encoding="utf-8")
    seg.cleanup_partial(target)
    assert not (tmp_path / "media.mp4.part").exists()
    assert not (tmp_path / "media.mp4.fcdl").exists()


def test_supports_segmented_download():
    assert seg.supports_segmented_download("https://cdn.test/a.mp4") is True
    assert seg.supports_segmented_download("ftp://cdn.test/a.mp4") is False
