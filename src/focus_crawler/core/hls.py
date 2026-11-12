"""HLS 播放列表解析：主列表 / 子列表 / 分片 / AES-128 密钥信息。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

ATTRIBUTE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]+)')
HEX_IV = re.compile(r"0x([0-9a-fA-F]+)")


@dataclass
class HlsKey:
    method: str = "NONE"
    uri: str | None = None
    iv: bytes | None = None


@dataclass
class HlsSegment:
    url: str
    duration: float | None = None
    sequence: int = 0
    key: HlsKey | None = None


@dataclass
class HlsVariant:
    url: str
    bandwidth: int | None = None
    resolution: str | None = None
    codecs: str | None = None


@dataclass
class HlsMedia:
    uri: str
    group_id: str | None = None
    name: str | None = None
    default: bool = False
    autoselect: bool = False


@dataclass
class HlsPlaylist:
    is_master: bool = False
    variants: list[HlsVariant] = field(default_factory=list)
    media: list[HlsMedia] = field(default_factory=list)
    segments: list[HlsSegment] = field(default_factory=list)
    maps: list[str] = field(default_factory=list)
    duration: float = 0.0
    media_sequence: int = 0


def _parse_attributes(line: str) -> dict[str, str]:
    attributes: dict[str, str] = {}
    for match in ATTRIBUTE.finditer(line):
        value = match.group(2).strip().strip('"')
        attributes[match.group(1)] = value
    return attributes


def _parse_iv(value: str | None) -> bytes | None:
    if not value:
        return None
    match = HEX_IV.search(value)
    if not match:
        return None
    raw = bytes.fromhex(match.group(1))
    if len(raw) > 16:
        raw = raw[-16:]
    return raw.rjust(16, b"\x00")


def parse_playlist(text: str, base_url: str) -> HlsPlaylist:
    playlist = HlsPlaylist()
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    current_key: HlsKey | None = None
    pending_duration: float | None = None
    sequence = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("#EXT-X-STREAM-INF"):
            attributes = _parse_attributes(line)
            if index + 1 < len(lines) and not lines[index + 1].startswith("#"):
                playlist.variants.append(
                    HlsVariant(
                        url=urljoin(base_url, lines[index + 1]),
                        bandwidth=int(attributes["BANDWIDTH"]) if attributes.get("BANDWIDTH") else None,
                        resolution=attributes.get("RESOLUTION"),
                        codecs=attributes.get("CODECS"),
                    )
                )
                index += 2
                continue
            index += 1
            continue
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            playlist.media_sequence = int(line.split(":", 1)[1].strip() or 0)
            sequence = playlist.media_sequence
            index += 1
            continue
        if line.startswith("#EXT-X-KEY:"):
            attributes = _parse_attributes(line.split(":", 1)[1])
            method = (attributes.get("METHOD") or "NONE").upper()
            if method == "NONE":
                current_key = None
            else:
                current_key = HlsKey(
                    method=method,
                    uri=urljoin(base_url, attributes["URI"]) if attributes.get("URI") else None,
                    iv=_parse_iv(attributes.get("IV")),
                )
            index += 1
            continue
        if line.startswith("#EXT-X-MEDIA:"):
            attributes = _parse_attributes(line.split(":", 1)[1])
            if (attributes.get("TYPE") or "").upper() == "AUDIO" and attributes.get("URI"):
                playlist.media.append(
                    HlsMedia(
                        uri=urljoin(base_url, attributes["URI"]),
                        group_id=attributes.get("GROUP-ID"),
                        name=attributes.get("NAME"),
                        default=(attributes.get("DEFAULT") or "NO").upper() == "YES",
                        autoselect=(attributes.get("AUTOSELECT") or "NO").upper() == "YES",
                    )
                )
            index += 1
            continue
        if line.startswith("#EXT-X-MAP:"):
            attributes = _parse_attributes(line.split(":", 1)[1])
            if attributes.get("URI"):
                playlist.maps.append(urljoin(base_url, attributes["URI"]))
            index += 1
            continue
        if line.startswith("#EXTINF:"):
            value = line.split(":", 1)[1].split(",", 1)[0].strip()
            try:
                pending_duration = float(value)
            except ValueError:
                pending_duration = None
            index += 1
            continue
        if line.startswith("#"):
            index += 1
            continue
        playlist.segments.append(
            HlsSegment(
                url=urljoin(base_url, line),
                duration=pending_duration,
                sequence=sequence,
                key=current_key,
            )
        )
        if pending_duration:
            playlist.duration += pending_duration
        sequence += 1
        pending_duration = None
        index += 1
    playlist.is_master = bool(playlist.variants)
    return playlist


def pick_variant(playlist: HlsPlaylist, preferred: str = "highest") -> HlsVariant | None:
    if not playlist.variants:
        return None

    def score(variant: HlsVariant) -> tuple[int, int]:
        height = 0
        if variant.resolution and "x" in variant.resolution:
            try:
                height = int(variant.resolution.lower().split("x", 1)[1])
            except ValueError:
                height = 0
        return variant.bandwidth or 0, height

    ordered = sorted(playlist.variants, key=score)
    return ordered[0] if preferred == "lowest" else ordered[-1]


def pick_audio(playlist: HlsPlaylist) -> HlsMedia | None:
    if not playlist.media:
        return None
    for media in playlist.media:
        if media.default:
            return media
    for media in playlist.media:
        if media.autoselect:
            return media
    return playlist.media[0]


def segment_iv(segment: HlsSegment) -> bytes:
    if segment.key and segment.key.iv:
        return segment.key.iv
    return segment.sequence.to_bytes(16, "big")
