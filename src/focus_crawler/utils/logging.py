"""日志工具：控制台 + 文件，避免重复注册 Handler。"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .paths import log_path

LOGGER_NAME = "focus_crawler"
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO, log_file: Path | None = None) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    if logger.handlers:
        return logger

    formatter = logging.Formatter(_FORMAT)

    # PyInstaller --windowed 下 sys.stderr 为 None，只保留文件日志
    if sys.stderr is not None:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        logger.addHandler(stream)

    try:
        target = log_file or log_path()
        file_handler = RotatingFileHandler(target, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        logger.warning("日志文件不可写，仅输出到控制台")

    logger.propagate = False
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)
