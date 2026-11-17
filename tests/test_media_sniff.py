"""视频/音频静态嗅探与 m3u8 解析测试。"""

from __future__ import annotations

from focus_crawler.config.schema import AudioConfig, VideoConfig
from focus_crawler.core.audio_sniffer import sniff_static as sniff_audio
from focus_crawler.core.hls import parse_playlist, pick_variant
from focus_crawler.core.video_sniffer import sniff_static as sniff_video

VIDEO_HTML = """
<html><head><meta property="og:video" content="/media/og.mp4"></head>
<body>
  <video src="/media/v1.mp4"></video>
  <video><source src="/media/v2.m3u8" type="application/vnd.apple.mpegurl"></video>
  <script>var url = "https://cdn.example.com/clip.mp4?token=1";</script>
</body></html>
"""

AUDIO_HTML = """
<html><head><meta property="og:audio" content="/media/og.mp3"></head>
<body>
  <audio src="/media/a1.mp3"></audio>
  <audio><source src="/media/a2.m4a"></audio>
  <a href="/media/preview.mp3">试听</a>
</body></html>
"""

MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
360/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1600000,RESOLUTION=1280x720
720/index.m3u8
"""

MEDIA = """#EXTM3U
#EXTINF:2.0,
seg0.ts
#EXTINF:2.0,
seg1.ts
"""


def test_video_static_candidates():
    cfg = VideoConfig(enabled=True, sniff_types=["mp4", "m3u8"])
    candidates = sniff_video(VIDEO_HTML, "https://example.com/page", cfg)
    urls = [item.url for item in candidates]
    assert "https://example.com/media/v1.mp4" in urls
    assert "https://example.com/media/v2.m3u8" in urls
    assert "https://example.com/media/og.mp4" in urls
    assert "https://cdn.example.com/clip.mp4?token=1" in urls
    assert all(item.format in ("mp4", "m3u8") for item in candidates)


def test_video_disabled_returns_empty():
    cfg = VideoConfig(enabled=False)
    assert sniff_video(VIDEO_HTML, "https://example.com/page", cfg) == []


def test_audio_static_candidates_and_exclude():
    cfg = AudioConfig(enabled=True, exclude_keywords=["preview"])
    candidates = sniff_audio(AUDIO_HTML, "https://example.com/page", cfg)
    urls = [item.url for item in candidates]
    assert "https://example.com/media/preview.mp3" not in urls
    assert "https://example.com/media/a1.mp3" in urls
    assert "https://example.com/media/a2.m4a" in urls
    assert "https://example.com/media/og.mp3" in urls


def test_parse_master_m3u8():
    playlist = parse_playlist(MASTER, "https://cdn.example.com/hls/master.m3u8")
    assert playlist.is_master is True
    assert len(playlist.variants) == 2
    assert playlist.variants[0].bandwidth == 800000
    assert playlist.segments == []
    best = pick_variant(playlist, "highest")
    assert best.url == "https://cdn.example.com/hls/720/index.m3u8"


def test_parse_media_m3u8():
    playlist = parse_playlist(MEDIA, "https://cdn.example.com/hls/index.m3u8")
    assert playlist.is_master is False
    assert [segment.url for segment in playlist.segments] == [
        "https://cdn.example.com/hls/seg0.ts",
        "https://cdn.example.com/hls/seg1.ts",
    ]
