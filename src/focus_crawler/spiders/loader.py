"""从文件 / 模块加载用户 Spider 类（CLI 与 GUI 共用）。"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
from pathlib import Path
from types import ModuleType


class SpiderLoadError(ValueError):
    """Spider 目标无法加载。"""


def load_module(module_ref: str) -> ModuleType:
    path = Path(module_ref)
    if path.is_file():
        spec = importlib.util.spec_from_file_location("focus_crawler_user_spider", path)
        if spec is None or spec.loader is None:
            raise SpiderLoadError(f"无法加载 Spider 文件：{path}")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 - 用户脚本异常要原样报出
            raise SpiderLoadError(f"执行 {path} 失败：{exc}") from exc
        return module
    try:
        return importlib.import_module(module_ref)
    except Exception as exc:  # noqa: BLE001
        raise SpiderLoadError(f"导入模块 {module_ref} 失败：{exc}") from exc


def load_spider_class(target: str):
    """``path/to/module.py:Class`` 或 ``package.module:Class``。"""
    if ":" not in target:
        raise SpiderLoadError("Spider 目标格式应为 文件路径.py:类名 或 模块:类名")
    module_ref, class_name = target.rsplit(":", 1)
    module = load_module(module_ref)
    spider_class = getattr(module, class_name, None)
    if spider_class is None:
        raise SpiderLoadError(f"{module_ref} 中没有找到类 {class_name}")
    return spider_class


def discover_spiders(path: str | Path) -> list[tuple[str, type]]:
    """列出文件里所有 Spider 子类（GUI 下拉用）。"""
    from .spider import Spider

    module = load_module(str(path))
    found: list[tuple[str, type]] = []
    for name, obj in vars(module).items():
        if inspect.isclass(obj) and issubclass(obj, Spider) and obj is not Spider:
            if obj.__module__ == module.__name__:
                found.append((name, obj))
    return found


__all__ = ["SpiderLoadError", "discover_spiders", "load_module", "load_spider_class"]
