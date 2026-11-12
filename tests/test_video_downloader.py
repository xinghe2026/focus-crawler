"""视频下载测试：m3u8 解析、AES-128 分片、DASH、ffmpeg 缺失提示。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from focus_crawler.config.schema import VideoConfig
from focus_crawler.core.fetcher import Fetcher
from focus_crawler.core.media_downloader import MediaDownloader
from focus_crawler.core.dash import parse_mpd
from focus_crawler.core.hls import parse_playlist, pick_variant
from focus_crawler.core.media_common import MediaDownloadError, guess_format
from focus_crawler.core.video_downloader import download_video
from focus_crawler.models.media import MediaRecord
from focus_crawler.utils.aes import cbc_encrypt
from focus_crawler.utils.hash import media_hash

BASE = "http://fixture.test"
KEY = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
IV = bytes.fromhex("101112131415161718191a1b1c1d1e1f")

PLAIN = [b"A" * 32, b"B" * 32]
HIGH = [b"H" * 32, b"I" * 32]
ENC_PLAIN = b"E" * 32

PLAIN_M3U8 = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:1
#EXTINF:1.0,
seg0.ts
#EXTINF:1.0,
seg1.ts
#EXT-X-ENDLIST
"""

MASTER_M3U8 = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=200000,RESOLUTION=640x360
low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=1280x720
high.m3u8
"""

LOW_M3U8 = """#EXTM3U
#EXTINF:1.0,
low0.ts
#EXTINF:1.0,
low1.ts
#EXT-X-ENDLIST
"""

HIGH_M3U8 = """#EXTM3U
#EXTINF:1.0,
high0.ts
#EXTINF:1.0,
high1.ts
#EXT-X-ENDLIST
"""

ENC_M3U8 = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-KEY:METHOD=AES-128,URI="key.bin",IV=0x101112131415161718191a1b1c1d1e1f
#EXTINF:1.0,
enc0.ts
#EXT-X-ENDLIST
"""

MPD = """<?xml version="1.0" encoding="utf-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" mediaPresentationDuration="PT2S">
  <Period duration="PT2S">
    <AdaptationSet contentType="video" mimeType="video/mp4">
      <SegmentTemplate initialization="init.mp4" media="seg-$Number$.m4s"
                      startNumber="1" duration="1" timescale="1"/>
      <Representation id="v1" bandwidth="500000"/>
    </AdaptationSet>
  </Period>
</MPD>
"""

PROTECTED_MPD = """<?xml version="1.0" encoding="utf-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011">
  <Period>
    <AdaptationSet contentType="video" mimeType="video/mp4">
      <ContentProtection schemeIdUri="urn:mpeg:dash:mp4protection:2011"/>
      <Representation id="v1"><BaseURL>video.mp4</BaseURL></Representation>
    </AdaptationSet>
  </Period>
</MPD>
"""

FILES = {
    "/media/v/plain.m3u8": (PLAIN_M3U8, "application/vnd.apple.mpegurl"),
    "/media/v/master.m3u8": (MASTER_M3U8, "application/vnd.apple.mpegurl"),
    "/media/v/low.m3u8": (LOW_M3U8, "application/vnd.apple.mpegurl"),
    "/media/v/high.m3u8": (HIGH_M3U8, "application/vnd.apple.mpegurl"),
    "/media/v/seg0.ts": (PLAIN[0], "video/mp2t"),
    "/media/v/seg1.ts": (PLAIN[1], "video/mp2t"),
    "/media/v/low0.ts": (PLAIN[0], "video/mp2t"),
    "/media/v/low1.ts": (PLAIN[1], "video/mp2t"),
    "/media/v/high0.ts": (HIGH[0], "video/mp2t"),
    "/media/v/high1.ts": (HIGH[1], "video/mp2t"),
    "/media/enc/index.m3u8": (ENC_M3U8, "application/vnd.apple.mpegurl"),
    "/media/enc/key.bin": (KEY, "application/octet-stream"),
    "/media/enc/enc0.ts": (cbc_encrypt(ENC_PLAIN, KEY, IV), "video/mp2t"),
    "/media/dash/manifest.mpd": (MPD, "application/dash+xml"),
    "/media/dash/protected.mpd": (PROTECTED_MPD, "application/dash+xml"),
    "/media/dash/init.mp4": (b"INIT", "video/mp4"),
    "/media/dash/seg-1.m4s": (b"S1", "video/iso.segment"),
    "/media/dash/seg-2.m4s": (b"S2", "video/iso.segment"),
}


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path in FILES:
        payload, content_type = FILES[path]
        content = payload if isinstance(payload, bytes) else payload.encode("utf-8")
        return httpx.Response(200, headers={"content-type": content_type}, content=content)
    return httpx.Response(404, text="not found")


@pytest.fixture
def video_fetcher():
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    fetcher = Fetcher(
        timeout_ms=5000,
        retry=0,
        delay_ms=(0, 0),
        respect_robots=False,
        client=client,
        sleep=lambda _seconds: None,
        random_uniform=lambda low, high: low,
    )
    yield fetcher
    fetcher.close()


def fake_merge(paths: list[Path], output: Path, ffmpeg_path: str) -> None:
    output.write_bytes(b"".join(path.read_bytes() for path in paths))


def test_parse_playlist_with_aes_key():
    playlist = parse_playlist(ENC_M3U8, BASE + "/media/enc/index.m3u8")
    assert len(playlist.segments) == 1
    key = playlist.segments[0].key
    assert key is not None
    assert key.method == "AES-128"
    assert key.uri == BASE + "/media/enc/key.bin"
    assert key.iv == IV


def test_pick_variant_orders_by_bandwidth():
    playlist = parse_playlist(MASTER_M3U8, BASE + "/media/v/master.m3u8")
    assert playlist.is_master
    assert pick_variant(playlist, "highest").url.endswith("high.m3u8")
    assert pick_variant(playlist, "lowest").url.endswith("low.m3u8")


def test_hls_download_and_merge(video_fetcher, tmp_path):
    output, size = download_video(
        video_fetcher,
        BASE + "/media/v/plain.m3u8",
        tmp_path / "plain.m3u8",
        merge_fn=fake_merge,
    )
    assert output == tmp_path / "plain.mp4"
    assert output.read_bytes() == PLAIN[0] + PLAIN[1]
    assert size == 64


def test_hls_master_picks_highest(video_fetcher, tmp_path):
    config = VideoConfig(enabled=True, preferred_quality="highest")
    output, _ = download_video(
        video_fetcher,
        BASE + "/media/v/master.m3u8",
        tmp_path / "master.m3u8",
        config=config,
        merge_fn=fake_merge,
    )
    assert output.read_bytes() == HIGH[0] + HIGH[1]


def test_hls_aes128_segment_decrypted(video_fetcher, tmp_path):
    output, _ = download_video(
        video_fetcher,
        BASE + "/media/enc/index.m3u8",
        tmp_path / "enc.m3u8",
        merge_fn=fake_merge,
    )
    assert output.read_bytes() == ENC_PLAIN


def test_missing_ffmpeg_reports_friendly_error(video_fetcher, tmp_path):
    with pytest.raises(MediaDownloadError) as info:
        download_video(
            video_fetcher,
            BASE + "/media/v/plain.m3u8",
            tmp_path / "plain.m3u8",
            ffmpeg_path="ffmpeg-does-not-exist",
        )
    assert "ffmpeg" in str(info.value)


def test_dash_template_download(video_fetcher, tmp_path):
    output, size = download_video(video_fetcher, BASE + "/media/dash/manifest.mpd", tmp_path / "dash.mpd")
    assert output == tmp_path / "dash.mp4"
    assert output.read_bytes() == b"INIT" + b"S1" + b"S2"
    assert size == 8


def test_dash_content_protection_rejected(video_fetcher, tmp_path):
    with pytest.raises(MediaDownloadError) as info:
        download_video(
            video_fetcher,
            BASE + "/media/dash/protected.mpd",
            tmp_path / "protected.mpd",
        )
    assert "DRM" in str(info.value)


def test_parse_mpd_protection_is_rejected():
    with pytest.raises(MediaDownloadError) as info:
        parse_mpd(PROTECTED_MPD, BASE + "/media/dash/protected.mpd")
    assert "DRM" in str(info.value)


def test_video_download_via_media_downloader(video_fetcher, store, task_id, tmp_path):
    url = BASE + "/media/v/plain.m3u8"
    record = MediaRecord(
        task_id=task_id,
        source_url=BASE + "/sub/1.html",
        parent_url=BASE + "/entry.html",
        media_url=url,
        media_hash=media_hash(url),
        media_type="video",
        format=guess_format(url),
    )
    assert store.add_media(record)
    media_id = int(store.get_media(task_id, record.media_hash)["id"])

    def video_fn(fetcher, row, target):
        return download_video(
            fetcher,
            row["media_url"],
            target,
            store=store,
            media_id=int(row["id"]),
            merge_fn=fake_merge,
        )

    downloader = MediaDownloader(
        store=store,
        task_id=task_id,
        task_name="测试任务",
        fetcher=video_fetcher,
        media_type="video",
        media_root=tmp_path,
        download_fn=video_fn,
    )
    summary = downloader.run()

    assert summary.downloaded == 1
    row = store.list_media(task_id, "video")[0]
    assert row["status"] == "done"
    assert Path(row["local_path"]).suffix == ".mp4"
    segments = store.list_media_segments(media_id)
    assert [segment["status"] for segment in segments] == ["done", "done"]
