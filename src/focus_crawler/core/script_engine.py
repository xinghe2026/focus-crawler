"""自定义解析脚本引擎：加载、超时、异常捕获、返回值校验。"""

from __future__ import annotations

import importlib.util
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models.parse_context import ParseContext, XhrResponse


@dataclass
class ScriptResult:
    ok: bool
    data: Any = None
    error_type: str | None = None
    message: str | None = None
    duration_ms: int = 0
    traceback_text: str | None = None
    script_path: str = ""


class ScriptEngine:
    """按路径加载本地脚本；不执行远程代码。"""

    def __init__(self, timeout_ms: int = 5000, logger: Any = None):
        self.timeout_ms = timeout_ms
        self.logger = logger
        self._modules: dict[tuple[str, float], Any] = {}

    def load(self, script_path: str | Path):
        path = Path(script_path)
        if not path.exists():
            raise FileNotFoundError(f"脚本不存在: {path}")
        key = (str(path.resolve()), path.stat().st_mtime)
        if key in self._modules:
            return self._modules[key]
        module_name = f"focus_crawler_script_{abs(hash(str(path.resolve())))}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载脚本: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self._modules[key] = module
        return module

    def run(
        self,
        script_path: str | Path,
        func_name: str,
        html: str,
        url: str,
        ctx: ParseContext | None = None,
        page_type: str = "detail",
        config: Any = None,
        responses: list[XhrResponse] | None = None,
    ) -> ScriptResult:
        path = str(script_path)
        started = time.perf_counter()

        def finish(**kwargs: Any) -> ScriptResult:
            return ScriptResult(
                duration_ms=int((time.perf_counter() - started) * 1000), script_path=path, **kwargs
            )

        try:
            module = self.load(path)
        except FileNotFoundError as exc:
            return finish(ok=False, error_type="custom_parser_missing", message=str(exc))
        except Exception as exc:  # 导入期任意错误
            return finish(
                ok=False,
                error_type="custom_parser_import_error",
                message=str(exc),
                traceback_text=traceback.format_exc(),
            )

        func = getattr(module, func_name, None)
        if not callable(func):
            return finish(
                ok=False,
                error_type="custom_parser_missing",
                message=f"脚本缺少函数 {func_name}",
            )

        context = ctx or ParseContext(
            html=html,
            url=url,
            page_type=page_type,
            config=config,
            logger=self.logger,
            responses=responses or [],
        )

        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="parser-script")
        future = executor.submit(func, html, url, context)
        try:
            data = future.result(timeout=self.timeout_ms / 1000)
        except FutureTimeout:
            executor.shutdown(wait=False, cancel_futures=True)
            return finish(
                ok=False,
                error_type="custom_parser_timeout",
                message=f"脚本执行超过 {self.timeout_ms}ms",
            )
        except Exception as exc:
            executor.shutdown(wait=False)
            return finish(
                ok=False,
                error_type="custom_parser_runtime_error",
                message=f"{type(exc).__name__}: {exc}",
                traceback_text=traceback.format_exc(),
            )
        executor.shutdown(wait=False)

        if not isinstance(data, dict):
            return finish(
                ok=False,
                error_type="custom_parser_invalid_return",
                message=f"脚本返回值必须是 dict，实际为 {type(data).__name__}",
            )

        return finish(ok=True, data=data)
