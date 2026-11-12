"""图片扫描测试：img/srcset/懒加载/背景图/og:image + 过滤去重。"""

from __future__ import annotations

from focus_crawler.config.schema import ImageConfig
from focus_crawler.core.image_crawler import scan_page

HTML = """
<html><head>
  <meta property="og:image" content="/img/f.jpg">
</head><body>
  <img src="/img/a.jpg" width="800" height="600">
  <img data-src="/img/b.webp">
  <img srcset="/img/c.jpg 1x, /img/d.jpg 2x">
  <div style="background-image: url('/img/e.png')"></div>
  <img src="/img/logo.png" width="400" height="400">
  <img src="/img/tiny.gif" width="50" height="50">
  <img src="/img/unknown.bmp" width="800" height="600">
  <img src="/img/a.jpg" width="800" height="600">
</body></html>
"""


def test_scan_and_filter():
    cfg = ImageConfig(
        enabled=True,
        min_width=300,
        min_height=300,
        formats=["jpg", "jpeg", "png", "webp"],
        exclude_keywords=["logo"],
    )
    candidates = scan_page(HTML, "https://example.com/page", cfg)
    urls = [item.url for item in candidates]
    assert urls == [
        "https://example.com/img/a.jpg",
        "https://example.com/img/b.webp",
        "https://example.com/img/d.jpg",
        "https://example.com/img/e.png",
        "https://example.com/img/f.jpg",
    ]
    assert candidates[1].source_kind == "img"


def test_scan_respects_max_count():
    cfg = ImageConfig(enabled=True, min_width=0, min_height=0, exclude_keywords=[], max_count=2)
    candidates = scan_page(HTML, "https://example.com/page", cfg)
    assert len(candidates) == 2


GALLERY_HTML = """
<html><body>
  <img src="/img/spacer.gif" data-lazy="/img/g1.jpg" width="800" height="600">
  <a data-mfp-src="/img/g2.jpg" href="javascript:void(0);">
    <img src="/img/thumb.jpg" width="800" height="600">
  </a>
  <a href="/img/g3.webp">原图链接</a>
</body></html>
"""


def test_scan_lazy_gallery_attributes():
    cfg = ImageConfig(
        enabled=True,
        min_width=300,
        min_height=300,
        formats=["jpg", "webp"],
        exclude_keywords=["spacer"],
    )
    candidates = scan_page(GALLERY_HTML, "https://example.com/post", cfg)
    assert [item.url for item in candidates] == [
        "https://example.com/img/g1.jpg",
        "https://example.com/img/thumb.jpg",
        "https://example.com/img/g2.jpg",
        "https://example.com/img/g3.webp",
    ]
    assert candidates[2].source_kind == "a_link"
