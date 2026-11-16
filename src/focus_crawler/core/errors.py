"""核心错误类型：放在独立模块里，避免 media_common 与下载器互相导入。"""

from __future__ import annotations


class MediaDownloadError(RuntimeError):
    """媒体下载失败，``failure_type`` 用于失败表分类。"""

    def __init__(self, message: str, failure_type: str = "media_download_error"):
        super().__init__(message)
        self.failure_type = failure_type
