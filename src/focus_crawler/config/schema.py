"""任务配置数据模型与校验。"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

LINK_MODES = ("selector", "self", "auto")
MEDIA_MODES = ("discover_only", "auto_download")
SUBPAGE_TYPES = ("data", "image", "video", "audio")
PARSER_MODES = ("auto", "custom", "hybrid")
RENDER_MODES = ("static", "playwright")
FIELD_TYPES = ("text", "html", "attr", "current_url", "json")
FIELD_TRANSFORMS = ("strip", "date", "lower", "upper", "int", "float", "absolute_url")
YTDLP_MODES = ("off", "auto", "force")


class ConfigError(ValueError):
    """配置校验失败，携带全部错误信息。"""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("；".join(errors))


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _read_int(raw: Any, name: str, errors: list[str], default: int, minimum: int | None = None) -> int:
    if raw is None:
        return default
    if not _is_int(raw):
        errors.append(f"{name} 必须为整数，当前为 {type(raw).__name__}")
        return default
    if minimum is not None and raw < minimum:
        errors.append(f"{name} 不能小于 {minimum}，当前为 {raw}")
        return default
    return raw


def _read_bool(raw: Any, name: str, errors: list[str], default: bool) -> bool:
    if raw is None:
        return default
    if not isinstance(raw, bool):
        errors.append(f"{name} 必须为 true/false，当前为 {raw!r}")
        return default
    return raw


def _read_str(raw: Any, name: str, errors: list[str], default: str = "") -> str:
    if raw is None:
        return default
    if not isinstance(raw, str):
        errors.append(f"{name} 必须为字符串，当前为 {type(raw).__name__}")
        return default
    return raw


def _read_str_list(raw: Any, name: str, errors: list[str]) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        errors.append(f"{name} 必须为字符串数组")
        return []
    return list(raw)


def _read_choice(raw: Any, name: str, errors: list[str], choices: tuple[str, ...], default: str) -> str:
    value = _read_str(raw, name, errors, default)
    if value not in choices:
        errors.append(f"{name} 取值必须是 {list(choices)} 之一，当前为 {value!r}")
        return default
    return value


def _read_mapping(raw: Any, name: str, errors: list[str]) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        errors.append(f"{name} 必须为对象")
        return {}
    return raw


@dataclass
class UrlRules:
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: Any, errors: list[str], prefix: str = "url_rules") -> "UrlRules":
        data = _read_mapping(raw, prefix, errors)
        return cls(
            include=_read_str_list(data.get("include"), f"{prefix}.include", errors),
            exclude=_read_str_list(data.get("exclude"), f"{prefix}.exclude", errors),
        )


@dataclass
class CrawlConfig:
    max_depth: int = 1
    max_pages: int = 500
    concurrency: int = 2
    delay_ms: tuple[int, int] = (800, 2000)
    timeout_ms: int = 20000
    retry: int = 3
    respect_robots: bool = True

    @classmethod
    def from_dict(cls, raw: Any, errors: list[str], prefix: str = "crawl") -> "CrawlConfig":
        data = _read_mapping(raw, prefix, errors)
        delay_raw = data.get("delay_ms")
        delay: tuple[int, int] = (800, 2000)
        if delay_raw is not None:
            if (
                isinstance(delay_raw, (list, tuple))
                and len(delay_raw) == 2
                and all(_is_int(item) and item >= 0 for item in delay_raw)
            ):
                delay = (delay_raw[0], delay_raw[1])
            else:
                errors.append(f"{prefix}.delay_ms 必须是两个非负整数组成的数组")
        return cls(
            max_depth=_read_int(data.get("max_depth"), f"{prefix}.max_depth", errors, 1, 0),
            max_pages=_read_int(data.get("max_pages"), f"{prefix}.max_pages", errors, 500, 1),
            concurrency=_read_int(data.get("concurrency"), f"{prefix}.concurrency", errors, 2, 1),
            delay_ms=delay,
            timeout_ms=_read_int(data.get("timeout_ms"), f"{prefix}.timeout_ms", errors, 20000, 1000),
            retry=_read_int(data.get("retry"), f"{prefix}.retry", errors, 3, 0),
            respect_robots=_read_bool(data.get("respect_robots"), f"{prefix}.respect_robots", errors, True),
        )


@dataclass
class RenderConfig:
    mode: str = "static"
    wait_selector: str | None = None
    scroll: bool = False

    @classmethod
    def from_dict(cls, raw: Any, errors: list[str], prefix: str = "render") -> "RenderConfig":
        data = _read_mapping(raw, prefix, errors)
        wait = data.get("wait_selector")
        if wait is not None and not isinstance(wait, str):
            errors.append(f"{prefix}.wait_selector 必须为字符串或 null")
            wait = None
        return cls(
            mode=_read_choice(data.get("mode"), f"{prefix}.mode", errors, RENDER_MODES, "static"),
            wait_selector=wait,
            scroll=_read_bool(data.get("scroll"), f"{prefix}.scroll", errors, False),
        )


@dataclass
class LinkDiscoveryConfig:
    mode: str = "auto"
    selector: str = "a"
    href_attribute: str = "href"
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    max_links_per_page: int = 200
    follow_next_page: bool = False
    deduplicate: bool = True

    @classmethod
    def from_dict(cls, raw: Any, errors: list[str], prefix: str = "link_discovery") -> "LinkDiscoveryConfig":
        data = _read_mapping(raw, prefix, errors)
        return cls(
            mode=_read_choice(data.get("mode"), f"{prefix}.mode", errors, LINK_MODES, "auto"),
            selector=_read_str(data.get("selector"), f"{prefix}.selector", errors, "a") or "a",
            href_attribute=_read_str(data.get("href_attribute"), f"{prefix}.href_attribute", errors, "href") or "href",
            include=_read_str_list(data.get("include"), f"{prefix}.include", errors),
            exclude=_read_str_list(data.get("exclude"), f"{prefix}.exclude", errors),
            max_links_per_page=_read_int(
                data.get("max_links_per_page"), f"{prefix}.max_links_per_page", errors, 200, 1
            ),
            follow_next_page=_read_bool(
                data.get("follow_next_page"), f"{prefix}.follow_next_page", errors, False
            ),
            deduplicate=_read_bool(data.get("deduplicate"), f"{prefix}.deduplicate", errors, True),
        )


@dataclass
class SubpageCollectConfig:
    enabled: bool = False
    types: list[str] = field(default_factory=lambda: ["data", "image"])
    data_as: str = "detail"
    media_mode: str = "discover_only"
    render_for_media: bool = False
    max_subpages: int = 500
    preview_limit: int = 5
    concurrency: int = 1
    auto_download_images: bool = False
    auto_download_videos: bool = False
    auto_download_audios: bool = False
    copyright_confirm: bool = True

    @classmethod
    def from_dict(
        cls, raw: Any, errors: list[str], prefix: str = "subpage_collect"
    ) -> "SubpageCollectConfig":
        data = _read_mapping(raw, prefix, errors)
        types = _read_str_list(data.get("types"), f"{prefix}.types", errors) or ["data", "image"]
        invalid = [item for item in types if item not in SUBPAGE_TYPES]
        if invalid:
            errors.append(f"{prefix}.types 含非法类型 {invalid}，可选 {list(SUBPAGE_TYPES)}")
        return cls(
            enabled=_read_bool(data.get("enabled"), f"{prefix}.enabled", errors, False),
            types=types,
            data_as=_read_choice(
                data.get("data_as"), f"{prefix}.data_as", errors, ("detail", "list"), "detail"
            ),
            media_mode=_read_choice(
                data.get("media_mode"), f"{prefix}.media_mode", errors, MEDIA_MODES, "discover_only"
            ),
            render_for_media=_read_bool(
                data.get("render_for_media"), f"{prefix}.render_for_media", errors, False
            ),
            max_subpages=_read_int(data.get("max_subpages"), f"{prefix}.max_subpages", errors, 500, 1),
            preview_limit=_read_int(data.get("preview_limit"), f"{prefix}.preview_limit", errors, 5, 1),
            concurrency=_read_int(data.get("concurrency"), f"{prefix}.concurrency", errors, 1, 1),
            auto_download_images=_read_bool(
                data.get("auto_download_images"), f"{prefix}.auto_download_images", errors, False
            ),
            auto_download_videos=_read_bool(
                data.get("auto_download_videos"), f"{prefix}.auto_download_videos", errors, False
            ),
            auto_download_audios=_read_bool(
                data.get("auto_download_audios"), f"{prefix}.auto_download_audios", errors, False
            ),
            copyright_confirm=_read_bool(
                data.get("copyright_confirm"), f"{prefix}.copyright_confirm", errors, True
            ),
        )


@dataclass
class ParserConfig:
    mode: str = "auto"
    list_script: str = ""
    detail_script: str = ""
    script_timeout_ms: int = 5000
    fallback_to_selector: bool = True

    @classmethod
    def from_dict(cls, raw: Any, errors: list[str], prefix: str = "parser") -> "ParserConfig":
        data = _read_mapping(raw, prefix, errors)
        mode = _read_choice(data.get("mode"), f"{prefix}.mode", errors, PARSER_MODES, "auto")
        return cls(
            mode=mode,
            list_script=_read_str(data.get("list_script"), f"{prefix}.list_script", errors),
            detail_script=_read_str(data.get("detail_script"), f"{prefix}.detail_script", errors),
            script_timeout_ms=_read_int(
                data.get("script_timeout_ms"), f"{prefix}.script_timeout_ms", errors, 5000, 100
            ),
            fallback_to_selector=_read_bool(
                data.get("fallback_to_selector"), f"{prefix}.fallback_to_selector", errors, True
            ),
        )


@dataclass
class ListConfig:
    item_selector: str = "a"
    link_selector: str = "self"
    next_page_selector: str | None = None

    @classmethod
    def from_dict(cls, raw: Any, errors: list[str], prefix: str = "list") -> "ListConfig":
        data = _read_mapping(raw, prefix, errors)
        next_selector = data.get("next_page_selector")
        if next_selector is not None and not isinstance(next_selector, str):
            errors.append(f"{prefix}.next_page_selector 必须为字符串或 null")
            next_selector = None
        return cls(
            item_selector=_read_str(data.get("item_selector"), f"{prefix}.item_selector", errors, "a") or "a",
            link_selector=_read_str(data.get("link_selector"), f"{prefix}.link_selector", errors, "self") or "self",
            next_page_selector=next_selector,
        )


@dataclass
class FieldSpec:
    selector: str | None = None
    type: str = "text"
    attribute: str | None = None
    regex: str | None = None
    transform: list[str] = field(default_factory=list)
    default: Any = ""
    source: str = "html"
    response_index: int = 0
    jsonpath: str | None = None

    @classmethod
    def from_dict(cls, name: str, raw: Any, errors: list[str], prefix: str) -> "FieldSpec":
        if isinstance(raw, str):
            raw = {"selector": raw}
        data = _read_mapping(raw, f"{prefix}.{name}", errors)
        transform = _read_str_list(data.get("transform"), f"{prefix}.{name}.transform", errors)
        invalid = [item for item in transform if item not in FIELD_TRANSFORMS]
        if invalid:
            errors.append(
                f"{prefix}.{name}.transform 含不支持的处理 {invalid}，可选 {list(FIELD_TRANSFORMS)}"
            )
        selector = data.get("selector") or data.get("xpath")
        if selector is not None and not isinstance(selector, str):
            errors.append(f"{prefix}.{name}.selector 必须为字符串或 null")
            selector = None
        regex = data.get("regex")
        if regex is not None:
            if not isinstance(regex, str):
                errors.append(f"{prefix}.{name}.regex 必须为字符串")
                regex = None
            else:
                try:
                    import re

                    re.compile(regex)
                except re.error as exc:
                    errors.append(f"{prefix}.{name}.regex 非法: {exc}")
                    regex = None
        attribute = data.get("attribute")
        if attribute is not None and not isinstance(attribute, str):
            errors.append(f"{prefix}.{name}.attribute 必须为字符串")
            attribute = None
        source = _read_choice(
            data.get("source"), f"{prefix}.{name}.source", errors, ("html", "response", "url"), "html"
        )
        field_type = _read_choice(data.get("type"), f"{prefix}.{name}.type", errors, FIELD_TYPES, "text")
        if field_type == "attr" and not attribute:
            attribute = "src"
        jsonpath = data.get("jsonpath")
        if jsonpath is not None and not isinstance(jsonpath, str):
            errors.append(f"{prefix}.{name}.jsonpath 必须为字符串")
            jsonpath = None
        return cls(
            selector=selector,
            type=field_type,
            attribute=attribute,
            regex=regex,
            transform=transform,
            default=data.get("default", ""),
            source=source,
            response_index=_read_int(
                data.get("response_index"), f"{prefix}.{name}.response_index", errors, 0, 0
            ),
            jsonpath=jsonpath,
        )


@dataclass
class DetailConfig:
    fields: dict[str, FieldSpec] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Any, errors: list[str] | None = None, prefix: str = "detail") -> "DetailConfig":
        errors = [] if errors is None else errors
        data = _read_mapping(raw, prefix, errors)
        if isinstance(data.get("fields"), dict):
            data = data["fields"]
        specs: dict[str, FieldSpec] = {}
        for name, raw_spec in data.items():
            if not isinstance(name, str):
                errors.append(f"{prefix} 字段名必须为字符串")
                continue
            specs[name] = FieldSpec.from_dict(name, raw_spec, errors, prefix)
        return cls(fields=specs)


@dataclass
class ImageConfig:
    enabled: bool = False
    min_width: int = 300
    min_height: int = 300
    formats: list[str] = field(default_factory=lambda: ["jpg", "jpeg", "png", "gif", "webp"])
    include_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=lambda: ["logo", "icon", "avatar", "banner"])
    max_count: int = 5000
    download_dir: str = "media/images"
    concurrency: int = 3

    @classmethod
    def from_dict(cls, raw: Any, errors: list[str], prefix: str = "image") -> "ImageConfig":
        data = _read_mapping(raw, prefix, errors)
        formats = _read_str_list(data.get("formats"), f"{prefix}.formats", errors) or [
            "jpg",
            "jpeg",
            "png",
            "gif",
            "webp",
        ]
        return cls(
            enabled=_read_bool(data.get("enabled"), f"{prefix}.enabled", errors, False),
            min_width=_read_int(data.get("min_width"), f"{prefix}.min_width", errors, 300, 0),
            min_height=_read_int(data.get("min_height"), f"{prefix}.min_height", errors, 300, 0),
            formats=[item.lower().lstrip(".") for item in formats],
            include_keywords=_read_str_list(data.get("include_keywords"), f"{prefix}.include_keywords", errors),
            exclude_keywords=_read_str_list(data.get("exclude_keywords"), f"{prefix}.exclude_keywords", errors),
            max_count=_read_int(data.get("max_count"), f"{prefix}.max_count", errors, 5000, 1),
            download_dir=_read_str(data.get("download_dir"), f"{prefix}.download_dir", errors, "media/images"),
            concurrency=_read_int(data.get("concurrency"), f"{prefix}.concurrency", errors, 3, 1),
        )


@dataclass
class YtdlpConfig:
    """yt-dlp 通用解析/下载开关（可选依赖）。

    mode:
      * ``off``   —— 不解析
      * ``auto``  —— 仅当本页没有嗅到任何媒体，且站点被 yt-dlp 支持时才解析（默认）
      * ``force`` —— 本页没有候选就对任意站点尝试（含通用提取器，会多一次请求）
    """

    mode: str = "auto"
    quality: str = "highest"
    format: str = ""
    cookies_file: str = ""
    cookies_from_browser: str = ""
    proxy: str = ""
    user_agent: str = ""
    socket_timeout: int = 30
    retries: int = 3
    playlist: bool = False
    max_entries: int = 20

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    @classmethod
    def from_dict(cls, raw: Any, errors: list[str], prefix: str = "ytdlp") -> "YtdlpConfig":
        data = _read_mapping(raw, prefix, errors)
        return cls(
            mode=_read_choice(data.get("mode"), f"{prefix}.mode", errors, YTDLP_MODES, "auto"),
            quality=_read_str(data.get("quality"), f"{prefix}.quality", errors, "highest") or "highest",
            format=_read_str(data.get("format"), f"{prefix}.format", errors),
            cookies_file=_read_str(data.get("cookies_file"), f"{prefix}.cookies_file", errors),
            cookies_from_browser=_read_str(
                data.get("cookies_from_browser"), f"{prefix}.cookies_from_browser", errors
            ),
            proxy=_read_str(data.get("proxy"), f"{prefix}.proxy", errors),
            user_agent=_read_str(data.get("user_agent"), f"{prefix}.user_agent", errors),
            socket_timeout=_read_int(data.get("socket_timeout"), f"{prefix}.socket_timeout", errors, 30, 1),
            retries=_read_int(data.get("retries"), f"{prefix}.retries", errors, 3, 0),
            playlist=_read_bool(data.get("playlist"), f"{prefix}.playlist", errors, False),
            max_entries=_read_int(data.get("max_entries"), f"{prefix}.max_entries", errors, 20, 1),
        )


@dataclass
class VideoAdapterConfig:
    bilibili: bool = True
    ytdlp: YtdlpConfig = field(default_factory=YtdlpConfig)

    @classmethod
    def from_dict(cls, raw: Any, errors: list[str], prefix: str = "video.adapter") -> "VideoAdapterConfig":
        data = _read_mapping(raw, prefix, errors)
        return cls(
            bilibili=_read_bool(data.get("bilibili"), f"{prefix}.bilibili", errors, True),
            ytdlp=YtdlpConfig.from_dict(data.get("ytdlp"), errors, f"{prefix}.ytdlp"),
        )


@dataclass
class VideoConfig:
    enabled: bool = False
    sniff_types: list[str] = field(default_factory=lambda: ["m3u8", "mp4", "flv"])
    max_count: int = 20
    download_dir: str = "media/videos"
    preferred_quality: str = "highest"
    use_ffmpeg: bool = True
    ffmpeg_path: str = "ffmpeg"
    headers: dict[str, str] = field(default_factory=dict)
    copyright_confirm: bool = True
    adapter: VideoAdapterConfig = field(default_factory=VideoAdapterConfig)

    @classmethod
    def from_dict(cls, raw: Any, errors: list[str], prefix: str = "video") -> "VideoConfig":
        data = _read_mapping(raw, prefix, errors)
        headers = _read_mapping(data.get("headers"), f"{prefix}.headers", errors)
        headers = {str(k): str(v) for k, v in headers.items()}
        return cls(
            enabled=_read_bool(data.get("enabled"), f"{prefix}.enabled", errors, False),
            sniff_types=_read_str_list(data.get("sniff_types"), f"{prefix}.sniff_types", errors)
            or ["m3u8", "mp4", "flv"],
            max_count=_read_int(data.get("max_count"), f"{prefix}.max_count", errors, 20, 1),
            download_dir=_read_str(data.get("download_dir"), f"{prefix}.download_dir", errors, "media/videos"),
            preferred_quality=_read_str(
                data.get("preferred_quality"), f"{prefix}.preferred_quality", errors, "highest"
            ),
            use_ffmpeg=_read_bool(data.get("use_ffmpeg"), f"{prefix}.use_ffmpeg", errors, True),
            ffmpeg_path=_read_str(data.get("ffmpeg_path"), f"{prefix}.ffmpeg_path", errors, "ffmpeg"),
            headers=headers,
            copyright_confirm=_read_bool(
                data.get("copyright_confirm"), f"{prefix}.copyright_confirm", errors, True
            ),
            adapter=VideoAdapterConfig.from_dict(data.get("adapter"), errors, f"{prefix}.adapter"),
        )


@dataclass
class AudioAdapterConfig:
    bilibili: bool = True
    music_163: bool = False
    custom_script: str = ""
    ytdlp: YtdlpConfig = field(default_factory=YtdlpConfig)

    @classmethod
    def from_dict(cls, raw: Any, errors: list[str], prefix: str = "audio.adapter") -> "AudioAdapterConfig":
        data = _read_mapping(raw, prefix, errors)
        return cls(
            bilibili=_read_bool(data.get("bilibili"), f"{prefix}.bilibili", errors, True),
            music_163=_read_bool(data.get("music_163"), f"{prefix}.music_163", errors, False),
            custom_script=_read_str(data.get("custom_script"), f"{prefix}.custom_script", errors),
            ytdlp=YtdlpConfig.from_dict(data.get("ytdlp"), errors, f"{prefix}.ytdlp"),
        )


@dataclass
class AudioConfig:
    enabled: bool = False
    sniff_types: list[str] = field(
        default_factory=lambda: ["mp3", "m4a", "aac", "flac", "wav", "ogg", "opus", "hls_audio", "dash_audio"]
    )
    include_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=lambda: ["notification", "ringtone", "preview"])
    max_count: int = 100
    download_dir: str = "media/audio"
    concurrency: int = 3
    prefer_original_format: bool = True
    transcode_to: str | None = None
    adapter: AudioAdapterConfig = field(default_factory=AudioAdapterConfig)
    headers: dict[str, str] = field(default_factory=dict)
    copyright_confirm: bool = True

    @classmethod
    def from_dict(cls, raw: Any, errors: list[str], prefix: str = "audio") -> "AudioConfig":
        data = _read_mapping(raw, prefix, errors)
        transcode = data.get("transcode_to")
        if transcode is not None and not isinstance(transcode, str):
            errors.append(f"{prefix}.transcode_to 必须为字符串或 null")
            transcode = None
        headers = _read_mapping(data.get("headers"), f"{prefix}.headers", errors)
        headers = {str(k): str(v) for k, v in headers.items()}
        default_types = ["mp3", "m4a", "aac", "flac", "wav", "ogg", "opus", "hls_audio", "dash_audio"]
        return cls(
            enabled=_read_bool(data.get("enabled"), f"{prefix}.enabled", errors, False),
            sniff_types=_read_str_list(data.get("sniff_types"), f"{prefix}.sniff_types", errors) or default_types,
            include_keywords=_read_str_list(data.get("include_keywords"), f"{prefix}.include_keywords", errors),
            exclude_keywords=_read_str_list(data.get("exclude_keywords"), f"{prefix}.exclude_keywords", errors),
            max_count=_read_int(data.get("max_count"), f"{prefix}.max_count", errors, 100, 1),
            download_dir=_read_str(data.get("download_dir"), f"{prefix}.download_dir", errors, "media/audio"),
            concurrency=_read_int(data.get("concurrency"), f"{prefix}.concurrency", errors, 3, 1),
            prefer_original_format=_read_bool(
                data.get("prefer_original_format"), f"{prefix}.prefer_original_format", errors, True
            ),
            transcode_to=transcode or None,
            adapter=AudioAdapterConfig.from_dict(data.get("adapter"), errors, f"{prefix}.adapter"),
            headers=headers,
            copyright_confirm=_read_bool(
                data.get("copyright_confirm"), f"{prefix}.copyright_confirm", errors, True
            ),
        )


@dataclass
class ExcelConfig:
    file: str = "output.xlsx"
    sheet: str = "data"
    columns: list[str] | None = None
    include_media_sheets: bool = True

    @classmethod
    def from_dict(cls, raw: Any, errors: list[str], prefix: str = "export.excel") -> "ExcelConfig":
        data = _read_mapping(raw, prefix, errors)
        columns = data.get("columns")
        if columns is not None:
            columns = _read_str_list(columns, f"{prefix}.columns", errors)
        return cls(
            file=_read_str(data.get("file"), f"{prefix}.file", errors, "output.xlsx") or "output.xlsx",
            sheet=_read_str(data.get("sheet"), f"{prefix}.sheet", errors, "data") or "data",
            columns=columns,
            include_media_sheets=_read_bool(
                data.get("include_media_sheets"), f"{prefix}.include_media_sheets", errors, True
            ),
        )


@dataclass
class ExportConfig:
    excel: ExcelConfig = field(default_factory=ExcelConfig)

    @classmethod
    def from_dict(cls, raw: Any, errors: list[str], prefix: str = "export") -> "ExportConfig":
        data = _read_mapping(raw, prefix, errors)
        return cls(excel=ExcelConfig.from_dict(data.get("excel"), errors, f"{prefix}.excel"))


@dataclass
class HotlinkRule:
    hosts: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    referer: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Any, errors: list[str], prefix: str) -> "HotlinkRule":
        data = _read_mapping(raw, prefix, errors)
        hosts = _read_str_list(data.get("hosts"), f"{prefix}.hosts", errors)
        patterns = _read_str_list(data.get("patterns"), f"{prefix}.patterns", errors)
        if not hosts and not patterns:
            errors.append(f"{prefix} 至少需要 hosts 或 patterns")
        headers = _read_mapping(data.get("headers"), f"{prefix}.headers", errors)
        return cls(
            hosts=[host.lower() for host in hosts],
            patterns=list(patterns),
            referer=_read_str(data.get("referer"), f"{prefix}.referer", errors),
            headers={str(k): str(v) for k, v in headers.items()},
        )


@dataclass
class HotlinkConfig:
    enabled: bool = True
    auto_referer: bool = True
    browser_headers: bool = False
    auto_origin: bool = False
    headers: dict[str, str] = field(default_factory=dict)
    rules: list[HotlinkRule] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: Any, errors: list[str], prefix: str = "hotlink") -> "HotlinkConfig":
        data = _read_mapping(raw, prefix, errors)
        headers = _read_mapping(data.get("headers"), f"{prefix}.headers", errors)
        rules_raw = data.get("rules")
        rules: list[HotlinkRule] = []
        if rules_raw is not None:
            if not isinstance(rules_raw, list):
                errors.append(f"{prefix}.rules 必须是数组")
            else:
                for index, item in enumerate(rules_raw):
                    rules.append(HotlinkRule.from_dict(item, errors, f"{prefix}.rules[{index}]"))
        return cls(
            enabled=_read_bool(data.get("enabled"), f"{prefix}.enabled", errors, True),
            auto_referer=_read_bool(data.get("auto_referer"), f"{prefix}.auto_referer", errors, True),
            browser_headers=_read_bool(
                data.get("browser_headers"), f"{prefix}.browser_headers", errors, False
            ),
            auto_origin=_read_bool(data.get("auto_origin"), f"{prefix}.auto_origin", errors, False),
            headers={str(k): str(v) for k, v in headers.items()},
            rules=rules,
        )


@dataclass
class FallbackRule:
    """失效主机回退规则：匹配的域名解析失败时改用 candidates 中的镜像主机。"""

    hosts: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: Any, errors: list[str], prefix: str) -> "FallbackRule":
        data = _read_mapping(raw, prefix, errors)
        hosts = _read_str_list(data.get("hosts"), f"{prefix}.hosts", errors)
        patterns = _read_str_list(data.get("patterns"), f"{prefix}.patterns", errors)
        candidates = _read_str_list(data.get("candidates"), f"{prefix}.candidates", errors)
        if not hosts and not patterns:
            errors.append(f"{prefix} 至少需要 hosts 或 patterns")
        cleaned = [item.strip().lower() for item in candidates if item.strip()]
        if not cleaned:
            errors.append(f"{prefix}.candidates 至少需要一个候选主机")
        return cls(
            hosts=[host.lower() for host in hosts],
            patterns=list(patterns),
            candidates=cleaned,
        )


@dataclass
class MediaFallbackConfig:
    """媒体主机回退：域名解析失败时按规则切换到同路径的镜像主机。"""

    enabled: bool = False
    rules: list[FallbackRule] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: Any, errors: list[str], prefix: str = "media_fallback") -> "MediaFallbackConfig":
        data = _read_mapping(raw, prefix, errors)
        rules_raw = data.get("rules")
        rules: list[FallbackRule] = []
        if rules_raw is not None:
            if not isinstance(rules_raw, list):
                errors.append(f"{prefix}.rules 必须是数组")
            else:
                for index, item in enumerate(rules_raw):
                    rules.append(FallbackRule.from_dict(item, errors, f"{prefix}.rules[{index}]"))
        return cls(
            enabled=_read_bool(data.get("enabled"), f"{prefix}.enabled", errors, False),
            rules=rules,
        )


@dataclass
class TaskConfig:
    task_name: str
    start_urls: list[str]
    allowed_domains: list[str]
    url_rules: UrlRules = field(default_factory=UrlRules)
    hotlink: HotlinkConfig = field(default_factory=HotlinkConfig)
    media_fallback: MediaFallbackConfig = field(default_factory=MediaFallbackConfig)
    crawl: CrawlConfig = field(default_factory=CrawlConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    link_discovery: LinkDiscoveryConfig = field(default_factory=LinkDiscoveryConfig)
    subpage_collect: SubpageCollectConfig = field(default_factory=SubpageCollectConfig)
    parser: ParserConfig = field(default_factory=ParserConfig)
    list: ListConfig = field(default_factory=ListConfig)
    detail: DetailConfig = field(default_factory=DetailConfig)
    image: ImageConfig = field(default_factory=ImageConfig)
    video: VideoConfig = field(default_factory=VideoConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    export: ExportConfig = field(default_factory=ExportConfig)

    @classmethod
    def from_dict(cls, raw: Any) -> "TaskConfig":
        if not isinstance(raw, dict):
            raise ConfigError(["配置文件根节点必须是 JSON 对象"])
        errors: list[str] = []
        task_name = _read_str(raw.get("task_name"), "task_name", errors).strip()
        if not task_name:
            errors.append("task_name 不能为空")

        start_urls = _read_str_list(raw.get("start_urls"), "start_urls", errors)
        if not start_urls:
            errors.append("start_urls 至少需要一个起始 URL")
        for url in start_urls:
            if not url.lower().startswith(("http://", "https://")):
                errors.append(f"start_urls 中的 {url!r} 必须以 http:// 或 https:// 开头")

        allowed_domains = _read_str_list(raw.get("allowed_domains"), "allowed_domains", errors)
        if not allowed_domains:
            errors.append("allowed_domains 至少需要一个域名（白名单）")

        config = cls(
            task_name=task_name or "task",
            start_urls=start_urls,
            allowed_domains=[domain.lower() for domain in allowed_domains],
            url_rules=UrlRules.from_dict(raw.get("url_rules"), errors),
            hotlink=HotlinkConfig.from_dict(raw.get("hotlink"), errors),
            media_fallback=MediaFallbackConfig.from_dict(raw.get("media_fallback"), errors),
            crawl=CrawlConfig.from_dict(raw.get("crawl"), errors),
            render=RenderConfig.from_dict(raw.get("render"), errors),
            link_discovery=LinkDiscoveryConfig.from_dict(raw.get("link_discovery"), errors),
            subpage_collect=SubpageCollectConfig.from_dict(raw.get("subpage_collect"), errors),
            parser=ParserConfig.from_dict(raw.get("parser"), errors),
            list=ListConfig.from_dict(raw.get("list"), errors),
            detail=DetailConfig.from_dict(raw.get("detail"), errors),
            image=ImageConfig.from_dict(raw.get("image"), errors),
            video=VideoConfig.from_dict(raw.get("video"), errors),
            audio=AudioConfig.from_dict(raw.get("audio"), errors),
            export=ExportConfig.from_dict(raw.get("export"), errors),
        )
        if errors:
            raise ConfigError(errors)
        return config

    def to_dict(self) -> dict:
        return asdict(self)

    def validate(self) -> None:
        """对已构造的配置做二次校验（GUI 直接构造时使用）。"""
        TaskConfig.from_dict(self.to_dict())
