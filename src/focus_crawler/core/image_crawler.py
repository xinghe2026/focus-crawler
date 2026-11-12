"""图片扫描：img / srcset / data-* / picture / 背景图 / og:image。"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from parsel import Selector

from ..config.schema import ImageConfig
from ..utils.hash import media_hash
from .media_common import IMAGE_EXTS, MediaCandidate, absolute_url, guess_format

_BACKGROUND_URL = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", re.I)
_SRCSET_ITEM = re.compile(r"(\S+)\s*(\d+(?:\.\d+)?[wx])?")

_IMG_ATTRIBUTES = ("src", "data-src", "data-original", "data-lazy-src", "data-lazy")
_A_LINK_ATTRIBUTES = ("href", "data-mfp-src", "data-src", "data-original", "data-lazy", "data-lightbox", "data-img")


def _parse_srcset(srcset: str) -> str:
    best_url = ""
    best_score = -1.0
    for item in srcset.split(","):
        match = _SRCSET_ITEM.match(item.strip())
        if not match:
            continue
        url, descriptor = match.group(1), match.group(2) or "1x"
        score = float(descriptor[:-1]) if descriptor[-1] in "wx" else 1.0
        if score > best_score:
            best_url, best_score = url, score
    return best_url


def _int_attr(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else None


def scan_page(html: str, page_url: str, cfg: ImageConfig) -> list[MediaCandidate]:
    root = Selector(text=html or "")
    raw: list[MediaCandidate] = []

    for node in root.css("img"):
        hrefs: list[str] = []
        for attribute in _IMG_ATTRIBUTES:
            value = node.attrib.get(attribute)
            if value:
                hrefs.append(value)
        srcset = node.attrib.get("srcset")
        if srcset:
            hrefs.append(_parse_srcset(srcset))
        for href in hrefs:
            absolute = absolute_url(page_url, href)
            if absolute:
                raw.append(
                    MediaCandidate(
                        url=absolute,
                        kind="image",
                        width=_int_attr(node.attrib.get("width")),
                        height=_int_attr(node.attrib.get("height")),
                        source_kind="img",
                    )
                )

    for node in root.css("a"):
        for attribute in _A_LINK_ATTRIBUTES:
            href = node.attrib.get(attribute)
            if not href:
                continue
            absolute = absolute_url(page_url, href)
            if absolute:
                raw.append(MediaCandidate(url=absolute, kind="image", source_kind="a_link"))

    for node in root.css("source[srcset]"):
        href = _parse_srcset(node.attrib.get("srcset", ""))
        absolute = absolute_url(page_url, href)
        if absolute:
            raw.append(MediaCandidate(url=absolute, kind="image", source_kind="picture_source"))

    for node in root.css("[style]"):
        for href in _BACKGROUND_URL.findall(node.attrib.get("style", "")):
            absolute = absolute_url(page_url, href)
            if absolute:
                raw.append(MediaCandidate(url=absolute, kind="image", source_kind="background"))

    for node in root.css('meta[property="og:image"], meta[name="og:image"]'):
        absolute = absolute_url(page_url, node.attrib.get("content", ""))
        if absolute:
            raw.append(MediaCandidate(url=absolute, kind="image", source_kind="og_image"))

    return _filter(raw, cfg)


def _filter(candidates: list[MediaCandidate], cfg: ImageConfig) -> list[MediaCandidate]:
    formats = {item.lower().lstrip(".") for item in cfg.formats}
    result: list[MediaCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        fmt = guess_format(candidate.url)
        if not fmt or fmt not in IMAGE_EXTS:
            continue
        if formats and fmt not in formats:
            continue
        if fmt != "svg" and candidate.width is not None and candidate.width < cfg.min_width:
            continue
        if fmt != "svg" and candidate.height is not None and candidate.height < cfg.min_height:
            continue
        lowered = candidate.url.lower()
        if cfg.include_keywords and not any(word.lower() in lowered for word in cfg.include_keywords if word):
            continue
        if cfg.exclude_keywords and any(word.lower() in lowered for word in cfg.exclude_keywords if word):
            continue
        key = media_hash(candidate.url)
        if key in seen:
            continue
        seen.add(key)
        candidate.format = fmt
        result.append(candidate)
        if len(result) >= cfg.max_count:
            break
    return result


def filename_for_image(url: str) -> str:
    base = urlsplit(url).path.rsplit("/", 1)[-1]
    return base or f"{media_hash(url)[:12]}.jpg"
