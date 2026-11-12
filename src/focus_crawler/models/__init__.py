"""数据模型包。"""

from .item import ItemRecord
from .media import MEDIA_STATUSES, MEDIA_TYPES, MediaRecord
from .parse_context import ParseContext, XhrResponse
from .task import TaskRecord
from .url_record import PAGE_TYPES, URL_STATUSES, UrlRecord

__all__ = [
    "TaskRecord",
    "UrlRecord",
    "ItemRecord",
    "MediaRecord",
    "ParseContext",
    "XhrResponse",
    "MEDIA_TYPES",
    "MEDIA_STATUSES",
    "URL_STATUSES",
    "PAGE_TYPES",
]
