"""任务配置编辑对话框：表单编辑 + 完整校验。"""

from __future__ import annotations

import copy
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config.schema import BILIBILI_PRIORITIES, ConfigError, TaskConfig, YTDLP_MODES
from ..core import ytdlp_engine
from . import settings


def split_items(text: str) -> list[str]:
    """逗号/分号/顿号/换行分隔的文本转列表。"""
    normalized = (text or "").replace("，", ",").replace("；", ",").replace("、", ",")
    result: list[str] = []
    for chunk in normalized.replace("\n", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            result.append(chunk)
    return result


def join_items(items: list[str]) -> str:
    return ", ".join(items)


def parse_headers_text(text: str) -> dict[str, str]:
    """解析 Key=Value; Key2=Value2 形式的请求头文本。"""
    headers: dict[str, str] = {}
    for chunk in (text or "").replace("\n", ";").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        equal = chunk.find("=")
        colon = chunk.find(":")
        if equal >= 0 and (colon < 0 or equal <= colon):
            key, value = chunk[:equal], chunk[equal + 1 :]
        elif colon >= 0:
            key, value = chunk[:colon], chunk[colon + 1 :]
        else:
            continue
        key, value = key.strip(), value.strip()
        if key and value:
            headers[key] = value
    return headers


def join_headers_text(headers: dict[str, str]) -> str:
    return "; ".join(f"{key}={value}" for key, value in headers.items())


DEFAULT_SUBPAGE_TYPES = ["data", "image", "video", "audio"]
DEFAULT_VIDEO_SNIFF = ["m3u8", "mp4", "flv"]
YTDLP_MODE_LABELS = {"off": "关闭", "auto": "仅补漏（默认）", "force": "积极（未知站点也尝试）"}
BILI_PRIORITY_LABELS = {
    "ytdlp_first": "yt-dlp 优先（推荐）",
    "adapter_first": "内置适配器优先",
    "adapter_only": "仅内置适配器",
}
YTDLP_QUALITY_PRESETS = {
    "highest", "best", "worst", "2160p", "1440p", "1080p", "720p", "480p", "360p", "ba",
}
DEFAULT_AUDIO_SNIFF = ["mp3", "m4a", "aac", "flac", "wav", "ogg", "opus", "hls_audio", "dash_audio"]
DEFAULT_IMAGE_FORMATS = ["jpg", "jpeg", "png", "gif", "webp"]
DEFAULT_IMAGE_EXCLUDE = ["logo", "icon", "avatar", "banner"]
DEFAULT_AUDIO_EXCLUDE = ["notification", "ringtone", "preview"]


def default_task_raw(crawl: dict | None = None) -> dict:
    """新建任务的完整配置骨架。"""
    base_crawl = {
        "max_depth": 0,
        "max_pages": 500,
        "concurrency": 2,
        "delay_ms": [800, 2000],
        "timeout_ms": 20000,
        "retry": 3,
        "respect_robots": False,
    }
    if crawl:
        base_crawl.update(crawl)
    return {
        "task_name": "新任务",
        "start_urls": [],
        "allowed_domains": [],
        "url_rules": {"include": [], "exclude": []},
        "crawl": base_crawl,
        "render": {"mode": "static", "wait_selector": None, "scroll": False},
        "link_discovery": {
            "mode": "auto",
            "selector": "a",
            "href_attribute": "href",
            "include": [],
            "exclude": [],
            "max_links_per_page": 200,
            "follow_next_page": False,
            "deduplicate": True,
        },
        "subpage_collect": {
            "enabled": True,
            "types": list(DEFAULT_SUBPAGE_TYPES),
            "data_as": "detail",
            "media_mode": "discover_only",
            "render_for_media": False,
            "max_subpages": 500,
            "preview_limit": 5,
            "concurrency": 1,
            "auto_download_images": False,
            "auto_download_videos": False,
            "auto_download_audios": False,
            "copyright_confirm": True,
        },
        "parser": {
            "mode": "auto",
            "list_script": "",
            "detail_script": "",
            "script_timeout_ms": 5000,
            "fallback_to_selector": True,
        },
        "list": {"item_selector": "a", "link_selector": "self", "next_page_selector": None},
        "detail": {"fields": {}},
        "image": {
            "enabled": True,
            "min_width": 300,
            "min_height": 300,
            "formats": list(DEFAULT_IMAGE_FORMATS),
            "include_keywords": [],
            "exclude_keywords": list(DEFAULT_IMAGE_EXCLUDE),
            "max_count": 5000,
            "download_dir": "media/images",
            "concurrency": 3,
        },
        "video": {
            "enabled": True,
            "sniff_types": list(DEFAULT_VIDEO_SNIFF),
            "max_count": 20,
            "download_dir": "media/videos",
            "preferred_quality": "highest",
            "use_ffmpeg": True,
            "ffmpeg_path": settings.ffmpeg_path(),
            "headers": {},
            "copyright_confirm": True,
            "adapter": {
                "bilibili": True,
                "bilibili_priority": "ytdlp_first",
                "ytdlp": {
                    "mode": "auto",
                    "quality": "highest",
                    "format": "",
                    "cookies_file": "",
                    "cookies_from_browser": "",
                    "proxy": "",
                },
            },
        },
        "audio": {
            "enabled": True,
            "sniff_types": list(DEFAULT_AUDIO_SNIFF),
            "include_keywords": [],
            "exclude_keywords": list(DEFAULT_AUDIO_EXCLUDE),
            "max_count": 100,
            "download_dir": "media/audio",
            "concurrency": 3,
            "prefer_original_format": True,
            "transcode_to": None,
            "adapter": {
                "bilibili": True,
                "music_163": False,
                "custom_script": "",
                "ytdlp": {
                    "mode": "auto",
                    "quality": "highest",
                    "format": "",
                    "cookies_file": "",
                    "cookies_from_browser": "",
                    "proxy": "",
                },
            },
            "headers": {},
            "copyright_confirm": True,
        },
        "export": {
            "excel": {
                "file": "output.xlsx",
                "sheet": "data",
                "columns": None,
                "include_media_sheets": True,
            }
        },
        "media_fallback": {"enabled": False, "rules": []},
    }


def _spin(minimum: int, maximum: int, value: Any) -> QSpinBox:
    box = QSpinBox()
    box.setRange(minimum, maximum)
    try:
        box.setValue(int(value))
    except (TypeError, ValueError):
        box.setValue(minimum)
    return box


def _combo(options: list[str], value: Any) -> QComboBox:
    combo = QComboBox()
    combo.addItems(options)
    index = combo.findText(str(value))
    if index >= 0:
        combo.setCurrentIndex(index)
    return combo


def _line(value: Any) -> QLineEdit:
    edit = QLineEdit("" if value is None else str(value))
    return edit


def _scrollable(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    return area


def _section(raw: dict, name: str) -> dict:
    value = raw.get(name)
    return value if isinstance(value, dict) else {}


def _choice_combo(values: tuple[str, ...], labels: dict[str, str], current: str = "") -> QComboBox:
    """带中文标签的下拉框：itemData 保存真实配置值。"""
    combo = QComboBox()
    for value in values:
        combo.addItem(labels.get(value, value), value)
    index = combo.findData(current)
    combo.setCurrentIndex(index if index >= 0 else 0)
    return combo


def _set_choice(combo: QComboBox, value: str) -> None:
    index = combo.findData(value)
    combo.setCurrentIndex(index if index >= 0 else 0)


def _fill_ytdlp(
    data: dict,
    mode: QComboBox,
    quality: QLineEdit,
    cookies: QLineEdit,
    browser: QLineEdit,
    proxy: QLineEdit,
) -> None:
    """把任务配置里的 video.adapter.ytdlp / audio.adapter.ytdlp 回填到控件。"""
    mode_value = str(data.get("mode") or "auto")
    index = mode.findData(mode_value)
    mode.setCurrentIndex(index if index >= 0 else mode.findData("auto"))
    quality.setText(str(data.get("format") or data.get("quality") or ""))
    cookies.setText(str(data.get("cookies_file") or ""))
    browser.setText(str(data.get("cookies_from_browser") or ""))
    proxy.setText(str(data.get("proxy") or ""))


def _collect_ytdlp(
    mode: QComboBox,
    quality: QLineEdit,
    cookies: QLineEdit,
    browser: QLineEdit,
    proxy: QLineEdit,
) -> dict[str, Any]:
    """从控件收集 yt-dlp 配置；格式/画质留空时回落到 highest。"""
    text = quality.text().strip()
    # 画质预设（highest/1080p…）写进 quality；自定义格式表达式写进 format
    preset = text if text in YTDLP_QUALITY_PRESETS else "highest"
    custom_format = "" if text in YTDLP_QUALITY_PRESETS else text
    return {
        "mode": str(mode.currentData() or "auto"),
        "quality": preset,
        "format": custom_format,
        "cookies_file": cookies.text().strip(),
        "cookies_from_browser": browser.text().strip(),
        "proxy": proxy.text().strip(),
    }


def detail_fields_raw(raw: dict) -> dict:
    """兼容 detail.fields 与直接映射两种写法。"""
    detail = _section(raw, "detail")
    fields = detail.get("fields")
    if isinstance(fields, dict):
        return fields
    return {key: value for key, value in detail.items() if isinstance(key, str)}


class TaskEditorDialog(QDialog):
    """任务配置表单。保存前用 TaskConfig.from_dict 做完整校验。"""

    FIELD_HEADERS = ["字段名", "选择器/XPath", "类型", "属性", "正则", "转换（逗号分隔）", "自适应"]

    def __init__(
        self,
        raw: dict | None,
        parent: QWidget | None = None,
        crawl_defaults: dict | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("任务配置")
        self.resize(820, 760)
        self._raw = copy.deepcopy(raw) if raw else default_task_raw(crawl_defaults)
        self.raw: dict = {}
        self.config: TaskConfig | None = None
        self._build_ui()
        self._load(self._raw)

    # ---------- UI ----------
    def _build_ui(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_basic_tab(), "基本与抓取")
        self.tabs.addTab(self._build_link_tab(), "链接发现")
        self.tabs.addTab(self._build_subpage_tab(), "子页采集")
        self.tabs.addTab(self._build_parser_tab(), "字段与解析")
        self.tabs.addTab(self._build_media_tab(), "媒体与导出")

        self.error_list = QListWidget()
        self.error_list.setMaximumHeight(90)
        self.error_list.setVisible(False)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(self.error_list)
        layout.addWidget(buttons)

    def _build_basic_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        basic = QGroupBox("任务信息")
        form = QFormLayout(basic)
        self.name_edit = QLineEdit()
        self.start_urls_edit = QPlainTextEdit()
        self.start_urls_edit.setFixedHeight(84)
        self.start_urls_edit.setPlaceholderText("每行一个起始 URL，例如 https://example.com/list.html")
        self.domains_edit = QLineEdit()
        self.domains_edit.setPlaceholderText("逗号分隔，例如 example.com, www.example.com")
        self.url_include_edit = QLineEdit()
        self.url_exclude_edit = QLineEdit()
        form.addRow("任务名称", self.name_edit)
        form.addRow("起始 URL", self.start_urls_edit)
        form.addRow("允许域名", self.domains_edit)
        form.addRow("URL 包含规则", self.url_include_edit)
        form.addRow("URL 排除规则", self.url_exclude_edit)
        outer.addWidget(basic)

        crawl = QGroupBox("抓取参数")
        grid = QGridLayout(crawl)
        self.max_depth = _spin(0, 10, 1)
        self.max_pages = _spin(1, 100000, 500)
        self.concurrency = _spin(1, 10, 2)
        self.delay_min = _spin(0, 600000, 800)
        self.delay_max = _spin(0, 600000, 2000)
        self.timeout_ms = _spin(1000, 120000, 20000)
        self.retry = _spin(0, 10, 3)
        self.respect_robots = QCheckBox("遵守 robots.txt")
        grid.addWidget(QLabel("最大深度"), 0, 0)
        grid.addWidget(self.max_depth, 0, 1)
        grid.addWidget(QLabel("最大页数"), 0, 2)
        grid.addWidget(self.max_pages, 0, 3)
        grid.addWidget(QLabel("并发"), 1, 0)
        grid.addWidget(self.concurrency, 1, 1)
        grid.addWidget(QLabel("重试次数"), 1, 2)
        grid.addWidget(self.retry, 1, 3)
        grid.addWidget(QLabel("延迟下限(ms)"), 2, 0)
        grid.addWidget(self.delay_min, 2, 1)
        grid.addWidget(QLabel("延迟上限(ms)"), 2, 2)
        grid.addWidget(self.delay_max, 2, 3)
        grid.addWidget(QLabel("超时(ms)"), 3, 0)
        grid.addWidget(self.timeout_ms, 3, 1)
        grid.addWidget(self.respect_robots, 3, 2, 1, 2)
        outer.addWidget(crawl)

        list_group = QGroupBox("列表与渲染")
        list_form = QFormLayout(list_group)
        self.item_selector = QLineEdit()
        self.link_selector = QLineEdit()
        self.next_page_selector = QLineEdit()
        self.next_page_selector.setPlaceholderText("可留空")
        self.render_mode = _combo(["static", "playwright"], "static")
        self.wait_selector = QLineEdit()
        self.wait_selector.setPlaceholderText("可留空")
        self.render_scroll = QCheckBox("模拟滚动")
        list_form.addRow("条目选择器", self.item_selector)
        list_form.addRow("链接选择器", self.link_selector)
        list_form.addRow("下一页选择器", self.next_page_selector)
        list_form.addRow("渲染模式", self.render_mode)
        list_form.addRow("等待选择器", self.wait_selector)
        list_form.addRow("", self.render_scroll)
        outer.addWidget(list_group)
        outer.addStretch(1)
        return _scrollable(page)

    def _build_link_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.link_mode = _combo(["selector", "self", "auto"], "auto")
        self.link_selector = QLineEdit()
        self.href_attribute = QLineEdit()
        self.link_include = QLineEdit()
        self.link_exclude = QLineEdit()
        self.max_links = _spin(1, 10000, 200)
        self.follow_next = QCheckBox("跟随列表下一页")
        self.deduplicate = QCheckBox("按 URL 哈希去重")
        form.addRow("发现模式", self.link_mode)
        form.addRow("链接选择器", self.link_selector)
        form.addRow("链接属性", self.href_attribute)
        form.addRow("包含规则", self.link_include)
        form.addRow("排除规则", self.link_exclude)
        form.addRow("单页最大链接数", self.max_links)
        form.addRow("", self.follow_next)
        form.addRow("", self.deduplicate)
        return _scrollable(page)

    def _build_subpage_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        general = QGroupBox("子页采集")
        form = QFormLayout(general)
        self.subpage_enabled = QCheckBox("启用子页多类型采集")
        self.type_checks: dict[str, QCheckBox] = {
            "data": QCheckBox("data（字段解析）"),
            "image": QCheckBox("image（图片）"),
            "video": QCheckBox("video（视频）"),
            "audio": QCheckBox("audio（音频）"),
        }
        types_row = QHBoxLayout()
        for box in self.type_checks.values():
            types_row.addWidget(box)
        types_row.addStretch(1)
        self.data_as = _combo(["detail", "list"], "detail")
        self.media_mode = _combo(["discover_only", "auto_download"], "discover_only")
        self.render_for_media = QCheckBox("媒体嗅探启用 Playwright（动态页面）")
        self.max_subpages = _spin(1, 100000, 500)
        self.preview_limit = _spin(1, 50, 5)
        self.subpage_concurrency = _spin(1, 10, 1)
        self.copyright_confirm = QCheckBox("下载前确认媒体版权")

        form.addRow("", self.subpage_enabled)
        form.addRow("采集类型", types_row)
        form.addRow("数据写入方式", self.data_as)
        form.addRow("媒体模式", self.media_mode)
        form.addRow("", self.render_for_media)
        form.addRow("最大子页数", self.max_subpages)
        form.addRow("预览子页数", self.preview_limit)
        form.addRow("子页并发", self.subpage_concurrency)
        form.addRow("", self.copyright_confirm)
        outer.addWidget(general)

        auto_group = QGroupBox("自动下载（需版权确认）")
        auto_layout = QVBoxLayout(auto_group)
        self.auto_images = QCheckBox("自动下载图片")
        self.auto_videos = QCheckBox("自动下载视频")
        self.auto_audios = QCheckBox("自动下载音频")
        auto_layout.addWidget(self.auto_images)
        auto_layout.addWidget(self.auto_videos)
        auto_layout.addWidget(self.auto_audios)
        hint = QLabel("默认只发现媒体清单，不自动下载；勾选后仍需在运行时确认版权。")
        hint.setWordWrap(True)
        auto_layout.addWidget(hint)
        outer.addWidget(auto_group)

        hotlink_group = QGroupBox("防盗链插件")
        hotlink_form = QFormLayout(hotlink_group)
        self.hotlink_enabled = QCheckBox("启用防盗链请求头")
        self.hotlink_auto_referer = QCheckBox("自动使用来源页作为 Referer")
        self.hotlink_browser_headers = QCheckBox("补充浏览器兼容请求头")
        self.hotlink_auto_origin = QCheckBox("自动生成 Origin 请求头")
        self.hotlink_headers = QLineEdit()
        self.hotlink_headers.setPlaceholderText("Key=Value; Key2=Value2")
        hotlink_hint = QLabel("按域名 / URL 规则配置可编辑任务 JSON 的 hotlink.rules")
        hotlink_hint.setWordWrap(True)
        hotlink_form.addRow("", self.hotlink_enabled)
        hotlink_form.addRow("", self.hotlink_auto_referer)
        hotlink_form.addRow("", self.hotlink_browser_headers)
        hotlink_form.addRow("", self.hotlink_auto_origin)
        hotlink_form.addRow("全局请求头", self.hotlink_headers)
        hotlink_form.addRow("", hotlink_hint)
        outer.addWidget(hotlink_group)
        outer.addStretch(1)
        return _scrollable(page)

    def _build_parser_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        parser_group = QGroupBox("解析模式")
        form = QFormLayout(parser_group)
        self.parser_mode = _combo(["auto", "custom", "hybrid"], "auto")
        self.list_script = QLineEdit()
        self.detail_script = QLineEdit()
        self.script_timeout = _spin(100, 60000, 5000)
        self.fallback_selector = QCheckBox("脚本失败时回退到选择器解析")
        list_script_row = QHBoxLayout()
        list_script_row.addWidget(self.list_script)
        list_script_button = QPushButton("浏览")
        list_script_button.clicked.connect(lambda: self._browse_script(self.list_script))
        list_script_row.addWidget(list_script_button)
        detail_script_row = QHBoxLayout()
        detail_script_row.addWidget(self.detail_script)
        detail_script_button = QPushButton("浏览")
        detail_script_button.clicked.connect(lambda: self._browse_script(self.detail_script))
        detail_script_row.addWidget(detail_script_button)
        form.addRow("解析模式", self.parser_mode)
        form.addRow("列表脚本", list_script_row)
        form.addRow("详情脚本", detail_script_row)
        form.addRow("脚本超时(ms)", self.script_timeout)
        form.addRow("", self.fallback_selector)
        outer.addWidget(parser_group)

        fields_group = QGroupBox("字段定义（CSS / XPath / 正则）")
        fields_layout = QVBoxLayout(fields_group)
        self.fields_table = QTableWidget(0, len(self.FIELD_HEADERS))
        self.fields_table.setHorizontalHeaderLabels(self.FIELD_HEADERS)
        self.fields_table.horizontalHeader().setStretchLastSection(True)
        self.fields_table.setMinimumHeight(220)
        fields_layout.addWidget(self.fields_table)
        buttons_row = QHBoxLayout()
        add_button = QPushButton("添加字段")
        add_button.clicked.connect(lambda: self.fields_table.insertRow(self.fields_table.rowCount()))
        remove_button = QPushButton("删除字段")
        remove_button.clicked.connect(self._remove_field_row)
        buttons_row.addWidget(add_button)
        buttons_row.addWidget(remove_button)
        buttons_row.addStretch(1)
        fields_layout.addLayout(buttons_row)
        hint = QLabel(
            "类型可选 text / html / attr / current_url / json；XPath 以 / 或 // 开头，JSONPath 以 $ 开头。\n"
            "勾选「自适应」后，页面改版导致选择器失效时，会按元素指纹重新定位该字段。"
        )
        hint.setWordWrap(True)
        fields_layout.addWidget(hint)
        outer.addWidget(fields_group, 1)
        return page

    def _build_media_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        fallback_group = QGroupBox("主机回退（域名失效时改用镜像）")
        fallback_form = QFormLayout(fallback_group)
        self.fallback_enabled = QCheckBox("启用主机回退")
        self.fallback_hosts = QLineEdit()
        self.fallback_hosts.setPlaceholderText("失效主机，逗号分隔，如 cdn6.mmdb.cc")
        self.fallback_candidates = QLineEdit()
        self.fallback_candidates.setPlaceholderText("候选镜像主机，按顺序尝试，如 cdn1.mmdb.cc, cdn2.mmdb.cc")
        fallback_hint = QLabel("只替换域名，路径与参数保持不变；更多规则可编辑任务 JSON 的 media_fallback.rules")
        fallback_hint.setWordWrap(True)
        fallback_form.addRow("", self.fallback_enabled)
        fallback_form.addRow("失效主机", self.fallback_hosts)
        fallback_form.addRow("候选主机", self.fallback_candidates)
        fallback_form.addRow("", fallback_hint)
        outer.addWidget(fallback_group)

        image_group = QGroupBox("图片采集")
        image_form = QFormLayout(image_group)
        self.image_enabled = QCheckBox("启用图片采集")
        self.image_min_width = _spin(0, 10000, 300)
        self.image_min_height = _spin(0, 10000, 300)
        self.image_formats = QLineEdit()
        self.image_include = QLineEdit()
        self.image_exclude = QLineEdit()
        self.image_max_count = _spin(1, 100000, 5000)
        self.image_concurrency = _spin(1, 10, 3)
        image_form.addRow("", self.image_enabled)
        image_form.addRow("最小宽度", self.image_min_width)
        image_form.addRow("最小高度", self.image_min_height)
        image_form.addRow("允许格式", self.image_formats)
        image_form.addRow("包含关键词", self.image_include)
        image_form.addRow("排除关键词", self.image_exclude)
        image_form.addRow("最大数量", self.image_max_count)
        image_form.addRow("下载并发", self.image_concurrency)
        outer.addWidget(image_group)

        video_group = QGroupBox("视频嗅探")
        video_form = QFormLayout(video_group)
        self.video_enabled = QCheckBox("启用视频嗅探")
        self.video_sniff_types = QLineEdit()
        self.video_max_count = _spin(1, 1000, 20)
        self.video_download_dir = QLineEdit()
        self.video_quality = QLineEdit()
        self.video_use_ffmpeg = QCheckBox("使用 ffmpeg 合并")
        self.video_ffmpeg_path = QLineEdit()
        self.video_adapter_bilibili = QCheckBox("启用 B 站视频适配器（完整 MP4）")
        self.video_bili_priority = _choice_combo(BILIBILI_PRIORITIES, BILI_PRIORITY_LABELS)
        self.video_bili_priority.setToolTip(
            "yt-dlp 的 B 站提取器跟进改版更快；选「仅内置适配器」可完全避免调用 yt-dlp"
        )
        self.video_ytdlp_mode = _choice_combo(YTDLP_MODES, YTDLP_MODE_LABELS)
        self.video_ytdlp_quality = QLineEdit()
        self.video_ytdlp_quality.setPlaceholderText("highest / 1080p / bv*+ba（留空用 highest）")
        self.video_ytdlp_cookies = QLineEdit()
        self.video_ytdlp_cookies.setPlaceholderText("可留空，Netscape 格式 cookies.txt 路径")
        self.video_ytdlp_browser = QLineEdit()
        self.video_ytdlp_browser.setPlaceholderText("可留空，例如 chrome / edge / firefox")
        self.video_ytdlp_proxy = QLineEdit()
        self.video_ytdlp_proxy.setPlaceholderText("可留空，例如 http://127.0.0.1:7890")
        self.ytdlp_status = QLabel(ytdlp_engine.describe())
        video_form.addRow("", self.video_enabled)
        video_form.addRow("嗅探类型", self.video_sniff_types)
        video_form.addRow("最大数量", self.video_max_count)
        video_form.addRow("下载目录", self.video_download_dir)
        video_form.addRow("清晰度偏好", self.video_quality)
        video_form.addRow("", self.video_adapter_bilibili)
        video_form.addRow("B 站解析优先级", self.video_bili_priority)
        video_form.addRow("yt-dlp 通用解析", self.video_ytdlp_mode)
        video_form.addRow("yt-dlp 画质/格式", self.video_ytdlp_quality)
        video_form.addRow("yt-dlp Cookies 文件", self.video_ytdlp_cookies)
        video_form.addRow("yt-dlp 浏览器 Cookies", self.video_ytdlp_browser)
        video_form.addRow("yt-dlp 代理", self.video_ytdlp_proxy)
        video_form.addRow("yt-dlp 环境", self.ytdlp_status)
        video_form.addRow("", self.video_use_ffmpeg)
        video_form.addRow("ffmpeg 路径", self.video_ffmpeg_path)
        outer.addWidget(video_group)

        audio_group = QGroupBox("音频嗅探")
        audio_form = QFormLayout(audio_group)
        self.audio_enabled = QCheckBox("启用音频嗅探")
        self.audio_sniff_types = QLineEdit()
        self.audio_max_count = _spin(1, 10000, 100)
        self.audio_download_dir = QLineEdit()
        self.audio_concurrency = _spin(1, 10, 3)
        self.audio_transcode = QLineEdit()
        self.audio_transcode.setPlaceholderText("可留空，例如 mp3")
        self.adapter_bilibili = QCheckBox("启用 B 站适配器")
        self.audio_bili_priority = _choice_combo(BILIBILI_PRIORITIES, BILI_PRIORITY_LABELS)
        self.adapter_music_163 = QCheckBox("启用网易云音乐适配器")
        self.adapter_custom_script = QLineEdit()
        self.adapter_custom_script.setPlaceholderText("可留空，例如 custom_adapter.py（需导出 resolve_audio）")
        self.audio_ytdlp_mode = _choice_combo(YTDLP_MODES, YTDLP_MODE_LABELS)
        self.audio_ytdlp_quality = QLineEdit()
        self.audio_ytdlp_quality.setPlaceholderText("highest / ba（留空用 highest）")
        self.audio_ytdlp_cookies = QLineEdit()
        self.audio_ytdlp_cookies.setPlaceholderText("可留空，Netscape 格式 cookies.txt 路径")
        self.audio_ytdlp_browser = QLineEdit()
        self.audio_ytdlp_browser.setPlaceholderText("可留空，例如 chrome / edge / firefox")
        self.audio_ytdlp_proxy = QLineEdit()
        self.audio_ytdlp_proxy.setPlaceholderText("可留空，例如 http://127.0.0.1:7890")
        audio_form.addRow("", self.audio_enabled)
        audio_form.addRow("嗅探类型", self.audio_sniff_types)
        audio_form.addRow("最大数量", self.audio_max_count)
        audio_form.addRow("下载目录", self.audio_download_dir)
        audio_form.addRow("下载并发", self.audio_concurrency)
        audio_form.addRow("转码格式", self.audio_transcode)
        audio_form.addRow("", self.adapter_bilibili)
        audio_form.addRow("B 站解析优先级", self.audio_bili_priority)
        audio_form.addRow("", self.adapter_music_163)
        audio_form.addRow("自定义适配器", self.adapter_custom_script)
        audio_form.addRow("yt-dlp 通用解析", self.audio_ytdlp_mode)
        audio_form.addRow("yt-dlp 音质/格式", self.audio_ytdlp_quality)
        audio_form.addRow("yt-dlp Cookies 文件", self.audio_ytdlp_cookies)
        audio_form.addRow("yt-dlp 浏览器 Cookies", self.audio_ytdlp_browser)
        audio_form.addRow("yt-dlp 代理", self.audio_ytdlp_proxy)
        outer.addWidget(audio_group)

        export_group = QGroupBox("Excel 导出")
        export_form = QFormLayout(export_group)
        self.excel_file = QLineEdit()
        self.excel_sheet = QLineEdit()
        self.excel_columns = QLineEdit()
        self.excel_columns.setPlaceholderText("可留空，逗号分隔字段名")
        self.excel_media_sheets = QCheckBox("包含图片/视频/音频 sheet")
        export_form.addRow("文件名", self.excel_file)
        export_form.addRow("数据 sheet 名", self.excel_sheet)
        export_form.addRow("导出字段", self.excel_columns)
        export_form.addRow("", self.excel_media_sheets)
        outer.addWidget(export_group)
        outer.addStretch(1)
        return _scrollable(page)

    # ---------- 载入 ----------
    def _load(self, raw: dict) -> None:
        crawl = _section(raw, "crawl")
        render = _section(raw, "render")
        link = _section(raw, "link_discovery")
        sub = _section(raw, "subpage_collect")
        parser = _section(raw, "parser")
        list_cfg = _section(raw, "list")
        image = _section(raw, "image")
        video = _section(raw, "video")
        audio = _section(raw, "audio")
        excel = _section(_section(raw, "export"), "excel")

        self.name_edit.setText(str(raw.get("task_name") or ""))
        self.start_urls_edit.setPlainText("\n".join(raw.get("start_urls") or []))
        self.domains_edit.setText(join_items(raw.get("allowed_domains") or []))
        rules = _section(raw, "url_rules")
        self.url_include_edit.setText(join_items(rules.get("include") or []))
        self.url_exclude_edit.setText(join_items(rules.get("exclude") or []))

        self.max_depth.setValue(int(crawl.get("max_depth", 1)))
        self.max_pages.setValue(int(crawl.get("max_pages", 500)))
        self.concurrency.setValue(int(crawl.get("concurrency", 2)))
        delay = crawl.get("delay_ms") or [800, 2000]
        self.delay_min.setValue(int(delay[0]))
        self.delay_max.setValue(int(delay[1]))
        self.timeout_ms.setValue(int(crawl.get("timeout_ms", 20000)))
        self.retry.setValue(int(crawl.get("retry", 3)))
        self.respect_robots.setChecked(bool(crawl.get("respect_robots", True)))

        hotlink = _section(raw, "hotlink")
        self.hotlink_enabled.setChecked(bool(hotlink.get("enabled", True)))
        self.hotlink_auto_referer.setChecked(bool(hotlink.get("auto_referer", True)))
        self.hotlink_browser_headers.setChecked(bool(hotlink.get("browser_headers", False)))
        self.hotlink_auto_origin.setChecked(bool(hotlink.get("auto_origin", False)))
        hotlink_headers = hotlink.get("headers")
        self.hotlink_headers.setText(
            join_headers_text(hotlink_headers) if isinstance(hotlink_headers, dict) else ""
        )

        fallback = _section(raw, "media_fallback")
        self.fallback_enabled.setChecked(bool(fallback.get("enabled", False)))
        rules = fallback.get("rules")
        rule = rules[0] if isinstance(rules, list) and rules and isinstance(rules[0], dict) else {}
        self.fallback_hosts.setText(join_items(rule.get("hosts") or []))
        self.fallback_candidates.setText(join_items(rule.get("candidates") or []))

        self.item_selector.setText(str(list_cfg.get("item_selector", "a") or "a"))
        self.link_selector.setText(str(list_cfg.get("link_selector", "self") or "self"))
        self.next_page_selector.setText(str(list_cfg.get("next_page_selector") or ""))
        self.render_mode.setCurrentIndex(max(0, self.render_mode.findText(str(render.get("mode", "static")))))
        self.wait_selector.setText(str(render.get("wait_selector") or ""))
        self.render_scroll.setChecked(bool(render.get("scroll", False)))

        self.link_mode.setCurrentIndex(max(0, self.link_mode.findText(str(link.get("mode", "auto")))))
        self.link_selector.setText(str(link.get("selector", "a") or "a"))
        self.href_attribute.setText(str(link.get("href_attribute", "href") or "href"))
        self.link_include.setText(join_items(link.get("include") or []))
        self.link_exclude.setText(join_items(link.get("exclude") or []))
        self.max_links.setValue(int(link.get("max_links_per_page", 200)))
        self.follow_next.setChecked(bool(link.get("follow_next_page", False)))
        self.deduplicate.setChecked(bool(link.get("deduplicate", True)))

        self.subpage_enabled.setChecked(bool(sub.get("enabled", False)))
        types = sub.get("types") or DEFAULT_SUBPAGE_TYPES
        for name, box in self.type_checks.items():
            box.setChecked(name in types)
        self.data_as.setCurrentIndex(max(0, self.data_as.findText(str(sub.get("data_as", "detail")))))
        self.media_mode.setCurrentIndex(
            max(0, self.media_mode.findText(str(sub.get("media_mode", "discover_only"))))
        )
        self.render_for_media.setChecked(bool(sub.get("render_for_media", False)))
        self.max_subpages.setValue(int(sub.get("max_subpages", 500)))
        self.preview_limit.setValue(int(sub.get("preview_limit", 5)))
        self.subpage_concurrency.setValue(int(sub.get("concurrency", 1)))
        self.auto_images.setChecked(bool(sub.get("auto_download_images", False)))
        self.auto_videos.setChecked(bool(sub.get("auto_download_videos", False)))
        self.auto_audios.setChecked(bool(sub.get("auto_download_audios", False)))
        self.copyright_confirm.setChecked(bool(sub.get("copyright_confirm", True)))

        self.parser_mode.setCurrentIndex(max(0, self.parser_mode.findText(str(parser.get("mode", "auto")))))
        self.list_script.setText(str(parser.get("list_script") or ""))
        self.detail_script.setText(str(parser.get("detail_script") or ""))
        self.script_timeout.setValue(int(parser.get("script_timeout_ms", 5000)))
        self.fallback_selector.setChecked(bool(parser.get("fallback_to_selector", True)))
        self._load_fields(detail_fields_raw(raw))

        self.image_enabled.setChecked(bool(image.get("enabled", False)))
        self.image_min_width.setValue(int(image.get("min_width", 300)))
        self.image_min_height.setValue(int(image.get("min_height", 300)))
        self.image_formats.setText(join_items(image.get("formats") or DEFAULT_IMAGE_FORMATS))
        self.image_include.setText(join_items(image.get("include_keywords") or []))
        self.image_exclude.setText(join_items(image.get("exclude_keywords") or DEFAULT_IMAGE_EXCLUDE))
        self.image_max_count.setValue(int(image.get("max_count", 5000)))
        self.image_concurrency.setValue(int(image.get("concurrency", 3)))

        self.video_enabled.setChecked(bool(video.get("enabled", False)))
        self.video_sniff_types.setText(join_items(video.get("sniff_types") or DEFAULT_VIDEO_SNIFF))
        self.video_max_count.setValue(int(video.get("max_count", 20)))
        self.video_download_dir.setText(str(video.get("download_dir", "media/videos") or "media/videos"))
        self.video_quality.setText(str(video.get("preferred_quality", "highest") or "highest"))
        self.video_use_ffmpeg.setChecked(bool(video.get("use_ffmpeg", True)))
        self.video_ffmpeg_path.setText(str(video.get("ffmpeg_path") or settings.ffmpeg_path()))
        video_adapter = _section(video, "adapter")
        self.video_adapter_bilibili.setChecked(bool(video_adapter.get("bilibili", True)))
        _set_choice(
            self.video_bili_priority,
            str(video_adapter.get("bilibili_priority") or "ytdlp_first"),
        )
        _fill_ytdlp(
            _section(video_adapter, "ytdlp"),
            self.video_ytdlp_mode,
            self.video_ytdlp_quality,
            self.video_ytdlp_cookies,
            self.video_ytdlp_browser,
            self.video_ytdlp_proxy,
        )

        audio_adapter = _section(audio, "adapter")
        self.audio_enabled.setChecked(bool(audio.get("enabled", False)))
        self.audio_sniff_types.setText(join_items(audio.get("sniff_types") or DEFAULT_AUDIO_SNIFF))
        self.audio_max_count.setValue(int(audio.get("max_count", 100)))
        self.audio_download_dir.setText(str(audio.get("download_dir", "media/audio") or "media/audio"))
        self.audio_concurrency.setValue(int(audio.get("concurrency", 3)))
        self.audio_transcode.setText(str(audio.get("transcode_to") or ""))
        self.adapter_bilibili.setChecked(bool(audio_adapter.get("bilibili", True)))
        _set_choice(
            self.audio_bili_priority,
            str(audio_adapter.get("bilibili_priority") or "ytdlp_first"),
        )
        self.adapter_music_163.setChecked(bool(audio_adapter.get("music_163", False)))
        self.adapter_custom_script.setText(str(audio_adapter.get("custom_script") or ""))
        _fill_ytdlp(
            _section(audio_adapter, "ytdlp"),
            self.audio_ytdlp_mode,
            self.audio_ytdlp_quality,
            self.audio_ytdlp_cookies,
            self.audio_ytdlp_browser,
            self.audio_ytdlp_proxy,
        )

        self.excel_file.setText(str(excel.get("file", "output.xlsx") or "output.xlsx"))
        self.excel_sheet.setText(str(excel.get("sheet", "data") or "data"))
        self.excel_columns.setText(join_items(excel.get("columns") or []))
        self.excel_media_sheets.setChecked(bool(excel.get("include_media_sheets", True)))

    def _load_fields(self, fields: dict) -> None:
        self.fields_table.setRowCount(0)
        for name, spec in fields.items():
            if isinstance(spec, str):
                spec = {"selector": spec}
            if not isinstance(spec, dict):
                continue
            row = self.fields_table.rowCount()
            self.fields_table.insertRow(row)
            values = [
                str(name),
                str(spec.get("selector") or spec.get("xpath") or ""),
                str(spec.get("type") or "text"),
                str(spec.get("attribute") or ""),
                str(spec.get("regex") or ""),
                join_items(spec.get("transform") or []),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self.fields_table.setItem(row, column, item)
            adaptive_item = QTableWidgetItem("")
            adaptive_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsSelectable
            )
            adaptive_item.setCheckState(
                Qt.CheckState.Checked if spec.get("adaptive") else Qt.CheckState.Unchecked
            )
            adaptive_item.setToolTip("页面改版、选择器失效时按元素指纹重新定位")
            self.fields_table.setItem(row, 6, adaptive_item)

    # ---------- 收集 ----------
    def _cell(self, row: int, column: int) -> str:
        item = self.fields_table.item(row, column)
        return item.text().strip() if item is not None else ""

    def _collect_fields(self, existing: dict) -> dict:
        fields: dict[str, Any] = {}
        for row in range(self.fields_table.rowCount()):
            name = self._cell(row, 0)
            if not name:
                continue
            base = existing.get(name)
            if isinstance(base, str):
                spec: dict[str, Any] = {"selector": base}
            elif isinstance(base, dict):
                spec = dict(base)
            else:
                spec = {}
            selector = self._cell(row, 1)
            if selector:
                spec["selector"] = selector
                spec.pop("xpath", None)
            else:
                spec.pop("selector", None)
            spec["type"] = self._cell(row, 2) or "text"
            attribute = self._cell(row, 3)
            if attribute:
                spec["attribute"] = attribute
            else:
                spec.pop("attribute", None)
            regex = self._cell(row, 4)
            if regex:
                spec["regex"] = regex
            else:
                spec.pop("regex", None)
            adaptive_item = self.fields_table.item(row, 6)
            spec["adaptive"] = bool(
                adaptive_item is not None and adaptive_item.checkState() == Qt.CheckState.Checked
            )
            transform = split_items(self._cell(row, 5))
            if transform:
                spec["transform"] = transform
            else:
                spec.pop("transform", None)
            fields[name] = spec
        return fields

    def _collect(self) -> dict:
        raw = copy.deepcopy(self._raw)
        raw["task_name"] = self.name_edit.text().strip()
        raw["start_urls"] = split_items(self.start_urls_edit.toPlainText())
        raw["allowed_domains"] = split_items(self.domains_edit.text())
        rules = raw.setdefault("url_rules", {})
        rules["include"] = split_items(self.url_include_edit.text())
        rules["exclude"] = split_items(self.url_exclude_edit.text())

        crawl = raw.setdefault("crawl", {})
        crawl.update(
            {
                "max_depth": self.max_depth.value(),
                "max_pages": self.max_pages.value(),
                "concurrency": self.concurrency.value(),
                "delay_ms": [self.delay_min.value(), self.delay_max.value()],
                "timeout_ms": self.timeout_ms.value(),
                "retry": self.retry.value(),
                "respect_robots": self.respect_robots.isChecked(),
            }
        )

        hotlink = raw.setdefault("hotlink", {})
        hotlink.update(
            {
                "enabled": self.hotlink_enabled.isChecked(),
                "auto_referer": self.hotlink_auto_referer.isChecked(),
                "browser_headers": self.hotlink_browser_headers.isChecked(),
                "auto_origin": self.hotlink_auto_origin.isChecked(),
                "headers": parse_headers_text(self.hotlink_headers.text()),
            }
        )

        fallback = raw.setdefault("media_fallback", {})
        fallback["enabled"] = self.fallback_enabled.isChecked()
        rules = fallback.get("rules")
        rules = [item for item in rules if isinstance(item, dict)] if isinstance(rules, list) else []
        hosts = split_items(self.fallback_hosts.text())
        candidates = split_items(self.fallback_candidates.text())
        if hosts or candidates:
            if not rules:
                rules.append({})
            rules[0]["hosts"] = hosts
            rules[0]["candidates"] = candidates
        fallback["rules"] = rules

        list_cfg = raw.setdefault("list", {})
        list_cfg.update(
            {
                "item_selector": self.item_selector.text().strip() or "a",
                "link_selector": self.link_selector.text().strip() or "self",
                "next_page_selector": self.next_page_selector.text().strip() or None,
            }
        )

        render = raw.setdefault("render", {})
        render.update(
            {
                "mode": self.render_mode.currentText(),
                "wait_selector": self.wait_selector.text().strip() or None,
                "scroll": self.render_scroll.isChecked(),
            }
        )

        link = raw.setdefault("link_discovery", {})
        link.update(
            {
                "mode": self.link_mode.currentText(),
                "selector": self.link_selector.text().strip() or "a",
                "href_attribute": self.href_attribute.text().strip() or "href",
                "include": split_items(self.link_include.text()),
                "exclude": split_items(self.link_exclude.text()),
                "max_links_per_page": self.max_links.value(),
                "follow_next_page": self.follow_next.isChecked(),
                "deduplicate": self.deduplicate.isChecked(),
            }
        )

        sub = raw.setdefault("subpage_collect", {})
        sub.update(
            {
                "enabled": self.subpage_enabled.isChecked(),
                "types": [name for name, box in self.type_checks.items() if box.isChecked()],
                "data_as": self.data_as.currentText(),
                "media_mode": self.media_mode.currentText(),
                "render_for_media": self.render_for_media.isChecked(),
                "max_subpages": self.max_subpages.value(),
                "preview_limit": self.preview_limit.value(),
                "concurrency": self.subpage_concurrency.value(),
                "auto_download_images": self.auto_images.isChecked(),
                "auto_download_videos": self.auto_videos.isChecked(),
                "auto_download_audios": self.auto_audios.isChecked(),
                "copyright_confirm": self.copyright_confirm.isChecked(),
            }
        )

        parser = raw.setdefault("parser", {})
        parser.update(
            {
                "mode": self.parser_mode.currentText(),
                "list_script": self.list_script.text().strip(),
                "detail_script": self.detail_script.text().strip(),
                "script_timeout_ms": self.script_timeout.value(),
                "fallback_to_selector": self.fallback_selector.isChecked(),
            }
        )

        existing_fields = detail_fields_raw(raw)
        raw["detail"] = {"fields": self._collect_fields(existing_fields)}

        image = raw.setdefault("image", {})
        image.update(
            {
                "enabled": self.image_enabled.isChecked(),
                "min_width": self.image_min_width.value(),
                "min_height": self.image_min_height.value(),
                "formats": split_items(self.image_formats.text()) or list(DEFAULT_IMAGE_FORMATS),
                "include_keywords": split_items(self.image_include.text()),
                "exclude_keywords": split_items(self.image_exclude.text()),
                "max_count": self.image_max_count.value(),
                "concurrency": self.image_concurrency.value(),
            }
        )

        video = raw.setdefault("video", {})
        video_adapter = video.get("adapter") if isinstance(video.get("adapter"), dict) else {}
        video_adapter.update(
            {
                "bilibili": self.video_adapter_bilibili.isChecked(),
                "bilibili_priority": str(self.video_bili_priority.currentData() or "ytdlp_first"),
            }
        )
        video_adapter["ytdlp"] = _collect_ytdlp(
            self.video_ytdlp_mode,
            self.video_ytdlp_quality,
            self.video_ytdlp_cookies,
            self.video_ytdlp_browser,
            self.video_ytdlp_proxy,
        )
        video.update(
            {
                "enabled": self.video_enabled.isChecked(),
                "sniff_types": split_items(self.video_sniff_types.text()) or list(DEFAULT_VIDEO_SNIFF),
                "max_count": self.video_max_count.value(),
                "download_dir": self.video_download_dir.text().strip() or "media/videos",
                "preferred_quality": self.video_quality.text().strip() or "highest",
                "use_ffmpeg": self.video_use_ffmpeg.isChecked(),
                "ffmpeg_path": self.video_ffmpeg_path.text().strip() or "ffmpeg",
                "adapter": video_adapter,
            }
        )

        audio = raw.setdefault("audio", {})
        adapter = audio.get("adapter") if isinstance(audio.get("adapter"), dict) else {}
        adapter.update(
            {
                "bilibili": self.adapter_bilibili.isChecked(),
                "bilibili_priority": str(self.audio_bili_priority.currentData() or "ytdlp_first"),
                "music_163": self.adapter_music_163.isChecked(),
                "custom_script": self.adapter_custom_script.text().strip(),
                "ytdlp": _collect_ytdlp(
                    self.audio_ytdlp_mode,
                    self.audio_ytdlp_quality,
                    self.audio_ytdlp_cookies,
                    self.audio_ytdlp_browser,
                    self.audio_ytdlp_proxy,
                ),
            }
        )
        audio.update(
            {
                "enabled": self.audio_enabled.isChecked(),
                "sniff_types": split_items(self.audio_sniff_types.text()) or list(DEFAULT_AUDIO_SNIFF),
                "max_count": self.audio_max_count.value(),
                "download_dir": self.audio_download_dir.text().strip() or "media/audio",
                "concurrency": self.audio_concurrency.value(),
                "transcode_to": self.audio_transcode.text().strip() or None,
                "adapter": adapter,
            }
        )

        export = raw.setdefault("export", {})
        excel = export.setdefault("excel", {})
        excel.update(
            {
                "file": self.excel_file.text().strip() or "output.xlsx",
                "sheet": self.excel_sheet.text().strip() or "data",
                "columns": split_items(self.excel_columns.text()) or None,
                "include_media_sheets": self.excel_media_sheets.isChecked(),
            }
        )
        return raw

    # ---------- 动作 ----------
    def _browse_script(self, target: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择解析脚本", str(settings.tasks_dir()), "Python 脚本 (*.py)"
        )
        if path:
            target.setText(path)

    def _remove_field_row(self) -> None:
        row = self.fields_table.currentRow()
        if row >= 0:
            self.fields_table.removeRow(row)

    def _show_errors(self, errors: list[str]) -> None:
        self.error_list.clear()
        for message in errors:
            self.error_list.addItem(f"· {message}")
        self.error_list.setVisible(True)

    def accept(self) -> None:
        try:
            raw = self._collect()
            config = TaskConfig.from_dict(raw)
        except ConfigError as exc:
            self._show_errors(exc.errors)
            return
        except Exception as exc:  # noqa: BLE001 - 表单内容异常统一提示
            self._show_errors([f"配置无法保存：{exc}"])
            return
        self.raw = raw
        self.config = config
        self.error_list.setVisible(False)
        super().accept()
