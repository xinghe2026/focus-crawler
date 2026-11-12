"""DASH MPD 解析：选取视频 / 音频轨道并生成分片 URL。"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urljoin

from .media_common import MediaDownloadError

PLACEHOLDER = re.compile(r"\$([A-Za-z]+)(?:%0(\d+)d)?\$")
ISO_DURATION = re.compile(
    r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?"
)


@dataclass
class DashSegments:
    init_url: str | None = None
    segment_urls: list[str] = field(default_factory=list)
    mime_type: str | None = None


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def find_all(parent: ET.Element, name: str) -> list[ET.Element]:
    return [node for node in parent.iter() if local_name(node.tag) == name]


def find_child(parent: ET.Element, name: str) -> ET.Element | None:
    for node in list(parent):
        if local_name(node.tag) == name:
            return node
    return None


def child_text(parent: ET.Element, name: str) -> str | None:
    node = find_child(parent, name)
    if node is None or node.text is None:
        return None
    value = node.text.strip()
    return value or None


def parse_iso_duration(value: str | None) -> float | None:
    if not value:
        return None
    match = ISO_DURATION.fullmatch(value.strip())
    if not match:
        return None
    years, months, days, hours, minutes, seconds = match.groups()
    total = (
        (int(years or 0) * 365 + int(months or 0) * 30 + int(days or 0)) * 86400
        + int(hours or 0) * 3600
        + int(minutes or 0) * 60
        + float(seconds or 0)
    )
    return total


def _expand(template: str, base_url: str, representation_id: str, bandwidth: str, number: int, time: int) -> str:
    def replace(match: re.Match) -> str:
        name = match.group(1)
        width = match.group(2)
        mapping = {
            "RepresentationID": representation_id,
            "Bandwidth": bandwidth,
            "Number": number,
            "Time": time,
        }
        value = str(mapping.get(name, ""))
        return value.zfill(int(width)) if width else value

    return urljoin(base_url, PLACEHOLDER.sub(replace, template))


def parse_mpd(text: str, mpd_url: str, content_type: str = "video") -> DashSegments:
    try:
        root = ET.fromstring(text or "")
    except ET.ParseError as exc:
        raise MediaDownloadError(f"MPD 解析失败：{exc}") from exc

    if any(local_name(node.tag) == "ContentProtection" for node in root.iter()):
        raise MediaDownloadError("MPD 含 ContentProtection（DRM），按合规要求不下载", "media_download_error")

    base = mpd_url
    mpd_base = child_text(root, "BaseURL")
    if mpd_base:
        base = urljoin(base, mpd_base)

    periods = find_all(root, "Period") or [root]
    period = periods[0]
    period_base = child_text(period, "BaseURL")
    if period_base:
        base = urljoin(base, period_base)
    period_duration = parse_iso_duration(period.attrib.get("duration"))

    adaptations = [node for node in list(period) if local_name(node.tag) == "AdaptationSet"]
    if not adaptations:
        raise MediaDownloadError("MPD 未找到 AdaptationSet")

    selected: ET.Element | None = None
    for adaptation in adaptations:
        kind = (adaptation.attrib.get("contentType") or "").lower()
        mime = (adaptation.attrib.get("mimeType") or "").lower()
        if content_type == "video":
            match = kind == "video" or mime.startswith("video/")
        else:
            match = kind == "audio" or mime.startswith("audio/")
        if match:
            selected = adaptation
            break
    if selected is None:
        raise MediaDownloadError(f"MPD 未找到 {content_type} 轨道")

    adaptation_base = child_text(selected, "BaseURL")
    if adaptation_base:
        base = urljoin(base, adaptation_base)

    representations = [node for node in list(selected) if local_name(node.tag) == "Representation"]
    if not representations:
        raise MediaDownloadError(f"MPD 的 {content_type} AdaptationSet 没有 Representation")

    best = max(representations, key=lambda node: int(node.attrib.get("bandwidth") or 0))
    representation_base = child_text(best, "BaseURL")
    if representation_base:
        base = urljoin(base, representation_base)

    representation_id = best.attrib.get("id", "")
    bandwidth = best.attrib.get("bandwidth", "")
    mime_type = best.attrib.get("mimeType") or selected.attrib.get("mimeType")

    template = find_child(best, "SegmentTemplate") or find_child(selected, "SegmentTemplate")
    segment_list = find_child(best, "SegmentList") or find_child(selected, "SegmentList")

    if template is not None:
        return _template_segments(template, base, representation_id, bandwidth, period_duration, mime_type)
    if segment_list is not None:
        segments: list[str] = []
        initialization = find_child(segment_list, "Initialization")
        init_url = None
        if initialization is not None and initialization.attrib.get("sourceURL"):
            init_url = urljoin(base, initialization.attrib["sourceURL"])
        for node in list(segment_list):
            if local_name(node.tag) == "SegmentURL" and node.attrib.get("media"):
                segments.append(urljoin(base, node.attrib["media"]))
        if not segments:
            raise MediaDownloadError("MPD SegmentList 没有分片")
        return DashSegments(init_url=init_url, segment_urls=segments, mime_type=mime_type)

    if base == mpd_url:
        raise MediaDownloadError("MPD 缺少可下载的媒体地址")
    return DashSegments(init_url=None, segment_urls=[base], mime_type=mime_type)


def _template_segments(
    template: ET.Element,
    base_url: str,
    representation_id: str,
    bandwidth: str,
    period_duration: float | None,
    mime_type: str | None,
) -> DashSegments:
    media = template.attrib.get("media")
    if not media:
        raise MediaDownloadError("MPD SegmentTemplate 缺少 media 模板")
    start_number = int(template.attrib.get("startNumber") or 1)
    timescale = int(template.attrib.get("timescale") or 1)
    duration = int(template.attrib.get("duration") or 0)

    init_url = None
    initialization = template.attrib.get("initialization")
    if initialization:
        init_url = _expand(initialization, base_url, representation_id, bandwidth, start_number, 0)

    segments: list[str] = []
    timeline = find_child(template, "SegmentTimeline")
    if timeline is not None:
        time = 0
        for node in list(timeline):
            if local_name(node.tag) != "S":
                continue
            if node.attrib.get("t"):
                time = int(node.attrib["t"])
            step = int(node.attrib.get("d") or 0)
            repeat = int(node.attrib.get("r") or 0)
            if repeat < 0:
                if period_duration and step > 0:
                    repeat = max(0, int(math.ceil(period_duration * timescale / step)) - 1)
                else:
                    repeat = 0
            for _ in range(repeat + 1):
                segments.append(_expand(media, base_url, representation_id, bandwidth, start_number, time))
                time += step
    elif duration > 0 and period_duration:
        count = max(1, int(math.ceil(period_duration * timescale / duration)))
        for number in range(start_number, start_number + count):
            segments.append(
                _expand(media, base_url, representation_id, bandwidth, number, 0)
            )
    else:
        raise MediaDownloadError("MPD SegmentTemplate 无法确定分片数量（缺少 SegmentTimeline 或 duration）")

    if not segments:
        raise MediaDownloadError("MPD 未生成任何分片")
    return DashSegments(init_url=init_url, segment_urls=segments, mime_type=mime_type)
