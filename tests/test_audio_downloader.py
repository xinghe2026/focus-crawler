"""音频下载测试：HLS 独立音频轨、AES-128、DASH 音频、直链与分片记录。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from focus_crawler.config.schema import AudioConfig
from focus_crawler.core.audio_downloader import download_audio
from focus_crawler.core.fetcher import Fetcher
from focus_crawler.core.media_common import guess_format
from focus_crawler.core.media_downloader import MediaDownloader
from focus_crawler.models.media import MediaRecord
from focus_crawler.utils.aes import cbc_encrypt
from focus_crawler.utils.hash import media_hash

BASE = "http://fixture.test"
KEY = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
IV = bytes.fromhex("101112131415161718191a1b1c1d1e1f")

SEG_A = b"A" * 32
SEG_B = b"B" * 32

MASTER_M3U8 = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="中文",DEFAULT=YES,AUTOSELECT=YES,URI="audio.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=500000,AUDIO="aud"
video.m3u8
"""

AUDIO_M3U8 = """#EXTM3U
#EXT-X-TARGETDURATION:1
#EXTINF:1.0,
seg0.aac
#EXTINF:1.0,
seg1.aac
#EXT-X-ENDLIST
"""

ENC_M3U8 = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-KEY:METHOD=AES-128,URI="key.bin",IV=0x101112131415161718191a1b1c1d1e1f
#EXTINF:1.0,
enc0.aac
#EXT-X-ENDLIST
"""

DASH_AUDIO_MPD = """<?xml version="1.0" encoding="utf-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" mediaPresentationDuration="PT2S">
  <Period duration="PT2S">
    <AdaptationSet contentType="audio" mimeType="audio/mp4">
      <SegmentTemplate initialization="init-audio.mp4" media="audio-$Number$.m4s"
                      startNumber="1" duration="1" timescale="1"/>
      <Representation id="a1" bandwidth="96000"/>
    </AdaptationSet>
  </Period>
</MPD>
"""

FILES = {
    "/audio/master.m3u8": (MASTER_M3U8, "application/vnd.apple.mpegurl"),
    "/audio/audio.m3u8": (AUDIO_M3U8, "application/vnd.apple.mpegurl"),
    "/audio/plain.m3u8": (AUDIO_M3U8, "application/vnd.apple.mpegurl"),
    "/audio/seg0.aac": (SEG_A, "audio/aac"),
    "/audio/seg1.aac": (SEG_B, "audio/aac"),
    "/audio/enc/index.m3u8": (ENC_M3U8, "application/vnd.apple.mpegurl"),
    "/audio/enc/key.bin": (KEY, "application/octet-stream"),
    "/audio/enc/enc0.aac": (cbc_encrypt(SEG_A, KEY, IV), "audio/aac"),
    "/audio/dash/manifest.mpd": (DASH_AUDIO_MPD, "application/dash+xml"),
    "/audio/dash/init-audio.mp4": (b"INIT", "audio/mp4"),
    "/audio/dash/audio-1.m4s": (b"S1", "audio/iso.segment"),
    "/audio/dash/audio-2.m4s": (b"S2", "audio/iso.segment"),
    "/audio/track.mp3": (b"MP3-DATA", "audio/mpeg"),
    "/audio/track.m4s": (b"M4S-DATA", "audio/mp4"),
}


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path in FILES:
        payload, content_type = FILES[path]
        content = payload if isinstance(payload, bytes) else payload.encode("utf-8")
        return httpx.Response(200, headers={"content-type": content_type}, content=content)
    return httpx.Response(404, text="not found")


@pytest.fixture
def audio_fetcher():
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


def test_hls_audio_master_uses_media_track(audio_fetcher, tmp_path):
    output, size = download_audio(
        audio_fetcher,
        BASE + "/audio/master.m3u8",
        tmp_path / "master.m3u8",
        merge_fn=fake_merge,
    )
    assert output == tmp_path / "master.m4a"
    assert output.read_bytes() == SEG_A + SEG_B
    assert size == 64


def test_hls_audio_plain_playlist(audio_fetcher, tmp_path):
    output, _ = download_audio(
        audio_fetcher,
        BASE + "/audio/plain.m3u8",
        tmp_path / "plain.m3u8",
        config=AudioConfig(enabled=True),
        merge_fn=fake_merge,
    )
    assert output.read_bytes() == SEG_A + SEG_B


def test_hls_audio_aes128_decrypted(audio_fetcher, tmp_path):
    output, _ = download_audio(
        audio_fetcher,
        BASE + "/audio/enc/index.m3u8",
        tmp_path / "enc.m3u8",
        merge_fn=fake_merge,
    )
    assert output.read_bytes() == SEG_A


def test_dash_audio_download(audio_fetcher, tmp_path):
    output, size = download_audio(
        audio_fetcher,
        BASE + "/audio/dash/manifest.mpd",
        tmp_path / "dash.mpd",
    )
    assert output == tmp_path / "dash.m4a"
    assert output.read_bytes() == b"INIT" + b"S1" + b"S2"
    assert size == 8


def test_direct_audio_download(audio_fetcher, tmp_path):
    output, size = download_audio(
        audio_fetcher,
        BASE + "/audio/track.mp3",
        tmp_path / "track.mp3",
    )
    assert output == tmp_path / "track.mp3"
    assert output.read_bytes() == b"MP3-DATA"
    assert size == 8


def test_direct_audio_m4s_renamed_to_m4a(audio_fetcher, tmp_path):
    output, _ = download_audio(
        audio_fetcher,
        BASE + "/audio/track.m4s",
        tmp_path / "track.m4s",
    )
    assert output.suffix == ".m4a"
    assert output.read_bytes() == b"M4S-DATA"


def test_audio_downloader_records_segments(audio_fetcher, store, task_id, tmp_path):
    url = BASE + "/audio/plain.m3u8"
    record = MediaRecord(
        task_id=task_id,
        source_url=BASE + "/sub/1.html",
        parent_url=BASE + "/entry.html",
        media_url=url,
        media_hash=media_hash(url),
        media_type="audio",
        format=guess_format(url),
    )
    assert store.add_media(record)
    media_id = int(store.get_media(task_id, record.media_hash)["id"])

    def audio_fn(fetcher, row, target):
        return download_audio(
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
        fetcher=audio_fetcher,
        media_type="audio",
        media_root=tmp_path,
        download_fn=audio_fn,
    )
    summary = downloader.run()

    assert summary.downloaded == 1
    row = store.list_media(task_id, "audio")[0]
    assert row["status"] == "done"
    assert Path(row["local_path"]).suffix == ".m4a"

    segments = store.list_media_segments(media_id)
    assert [segment["status"] for segment in segments] == ["done", "done"]
    assert [segment["segment_index"] for segment in segments] == [0, 1]
