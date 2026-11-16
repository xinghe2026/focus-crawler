"""分段并发下载引擎（借鉴 Mister Download Manager 的动态分段规划思想）。

算法来源说明：分段规划规则参考 IDM 公开文档与 MDM 的 planner 实现思路
（GPL-3.0 项目，仅借鉴算法，代码为本项目独立实现）：
空闲连接优先领取未分配的段；否则抢占"刚完成段的下一个、尚未开始接收"的段；
再否则把最大的剩余段对半拆开，取其中一半。

相对单连接直链下载的收益：
* 多连接并行，充分利用带宽；
* 慢连接会被动态拆分，避免"一条慢连接拖垮整包"；
* 断点续传（``.fcdl`` 元数据 + ``.part`` 数据），中断后从断点继续；
* 全局限速令牌桶、进度回调、暂停 / 停止。

不支持 Range 的服务器会自动回退成单连接流式下载，行为与旧实现一致。
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from ..utils.logging import get_logger
from .errors import MediaDownloadError
from .failure_classifier import classify_exception

logger = get_logger("segmented_download")

ALIGN = 4096                      # 分段点按 4 KiB 对齐
DEFAULT_CONNECTIONS = 4
DEFAULT_MIN_SPLIT = 512 * 1024    # 小于 1 MiB 的剩余段不再拆
DEFAULT_CHUNK = 64 * 1024
DEFAULT_PREEMPT_AFTER = 1.5       # 秒：抢占"已分配但尚未开始接收"的段
MIN_SEGMENTED_SIZE = 2 * 1024 * 1024  # 小于 2 MiB 的文件不值得分段


# ---------------------------------------------------------------- 规划器（纯逻辑）
@dataclass
class Segment:
    """文件的一段：``[start, cursor)`` 已落盘，``[cursor, reserved)`` 正被写入。"""

    start: int
    end: int
    cursor: int = 0
    reserved: int = 0
    worker: int | None = None
    receiving: bool = False
    given_at: float | None = None

    def __post_init__(self) -> None:
        self.cursor = max(self.start, self.cursor)
        self.reserved = max(self.cursor, self.reserved)

    @property
    def size(self) -> int:
        return self.end - self.start

    @property
    def unclaimed(self) -> int:
        return self.end - self.reserved

    @property
    def done(self) -> bool:
        return self.cursor >= self.end


@dataclass
class Assignment:
    """分给某个 worker 的字节区间。"""

    from_offset: int
    to_offset: int
    segment: Segment


class Planner:
    """动态分段规划器：线程安全，只做记账，不碰 IO。"""

    def __init__(
        self,
        size: int,
        segments: list[Segment] | None = None,
        connections: int = DEFAULT_CONNECTIONS,
        min_split: int = DEFAULT_MIN_SPLIT,
        align: int = ALIGN,
        preempt_after: float = DEFAULT_PREEMPT_AFTER,
    ):
        if size < 0:
            raise ValueError("size 不能为负")
        self.size = size
        self.min_split = max(align, min_split)
        self.align = align
        self.preempt_after = preempt_after
        self._lock = threading.Lock()
        self._last_segment: dict[int, Segment] = {}
        self.segments = segments[:] if segments else self._initial_segments(size, connections)

    # -- 初始化 -------------------------------------------------------
    def _initial_segments(self, size: int, connections: int) -> list[Segment]:
        if size <= 0:
            return []
        count = max(1, min(connections, size // max(self.min_split, 1) or 1))
        step = self._align_up(size // count)
        segments: list[Segment] = []
        start = 0
        while start < size:
            end = min(start + step, size)
            segments.append(Segment(start=start, end=end))
            start = end
        return segments

    def _align_up(self, value: int) -> int:
        if self.align <= 1:
            return value
        return ((value + self.align - 1) // self.align) * self.align

    # -- 记账 ---------------------------------------------------------
    def claim(self, worker_id: int) -> Assignment | None:
        """给 worker 分配一段可下载的字节区间；没有可分配的就返回 None。"""
        with self._lock:
            now = time.monotonic()
            target = self._take_unassigned() or self._preempt_next(worker_id, now) or self._split_largest()
            if target is None:
                return None
            target.worker = worker_id
            target.receiving = False
            target.given_at = now
            self._last_segment[worker_id] = target
            return Assignment(from_offset=target.reserved, to_offset=target.end, segment=target)

    def _take_unassigned(self) -> Segment | None:
        for segment in self.segments:
            if segment.worker is None and segment.cursor < segment.end:
                return segment
        return None

    def _preempt_next(self, worker_id: int, now: float) -> Segment | None:
        last = self._last_segment.get(worker_id)
        if last is None:
            return None
        try:
            index = self.segments.index(last)
        except ValueError:
            return None
        if index + 1 >= len(self.segments):
            return None
        nxt = self.segments[index + 1]
        if nxt.worker is None or nxt.receiving or nxt.done:
            return None
        if nxt.given_at is not None and now - nxt.given_at < self.preempt_after:
            return None
        # 抢占：原 worker 会在下一次写盘前发现 owner 已变
        logger.debug("抢占分段 %s-%s（原 worker %s）", nxt.start, nxt.end, nxt.worker)
        return nxt

    def _split_largest(self) -> Segment | None:
        candidates = [segment for segment in self.segments if segment.unclaimed >= self.min_split * 2]
        if not candidates:
            return None
        target = max(candidates, key=lambda item: item.unclaimed)
        split_at = self._align_down(target.reserved + target.unclaimed // 2)
        if split_at <= target.reserved or split_at >= target.end:
            return None
        upper = Segment(start=split_at, end=target.end)
        target.end = split_at
        self.segments.insert(self.segments.index(target) + 1, upper)
        return upper

    def _align_down(self, value: int) -> int:
        if self.align <= 1:
            return value
        return (value // self.align) * self.align

    def owns(self, worker_id: int, segment: Segment) -> bool:
        return segment.worker == worker_id

    def commit(self, worker_id: int, segment: Segment, written: int, end_offset: int) -> None:
        """worker 写盘后确认进度。"""
        with self._lock:
            if segment.worker != worker_id:
                return
            segment.cursor = min(segment.end, end_offset)
            segment.reserved = max(segment.reserved, min(segment.end, end_offset))
            segment.receiving = True

    def start_receiving(self, worker_id: int, segment: Segment) -> None:
        with self._lock:
            if segment.worker == worker_id:
                segment.receiving = True

    def release(self, worker_id: int, segment: Segment) -> None:
        """worker 结束（正常完成或异常）后释放段。"""
        with self._lock:
            if segment.worker == worker_id:
                segment.worker = None
                segment.receiving = False

    def remaining(self) -> int:
        with self._lock:
            return sum(segment.end - segment.cursor for segment in self.segments)

    def downloaded(self) -> int:
        with self._lock:
            return sum(segment.cursor - segment.start for segment in self.segments)

    def is_done(self) -> bool:
        with self._lock:
            return all(segment.done for segment in self.segments)

    def snapshot(self) -> list[dict[str, int]]:
        with self._lock:
            return [
                {"start": item.start, "cursor": item.cursor, "end": item.end}
                for item in self.segments
            ]

    def merge_intervals(self) -> list[tuple[int, int]]:
        """把已完成区间合并，供断点续传时判断哪些字节还缺。"""
        with self._lock:
            spans = sorted((item.start, item.cursor) for item in self.segments if item.cursor > item.start)
        merged: list[tuple[int, int]] = []
        for start, end in spans:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged


# ---------------------------------------------------------------- 限速令牌桶
class RateLimiter:
    """全局限速：bps <= 0 表示不限速。"""

    def __init__(self, bytes_per_second: int = 0):
        self.rate = max(0, int(bytes_per_second))
        self._lock = threading.Lock()
        self._allowance = 0.0
        self._last = time.monotonic()

    def consume(self, amount: int, should_stop: Callable[[], bool] | None = None) -> None:
        if self.rate <= 0 or amount <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last
                self._last = now
                self._allowance = min(self.rate, self._allowance + elapsed * self.rate)
                if self._allowance >= amount:
                    self._allowance -= amount
                    return
                wait = (amount - self._allowance) / self.rate
            if should_stop and should_stop():
                return
            time.sleep(min(wait, 0.25))


# ---------------------------------------------------------------- 断点元数据
@dataclass
class ResumeState:
    url: str
    size: int
    etag: str = ""
    last_modified: str = ""
    segments: list[dict[str, int]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "ResumeState | None":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        try:
            return cls(
                url=str(payload.get("url") or ""),
                size=int(payload.get("size") or 0),
                etag=str(payload.get("etag") or ""),
                last_modified=str(payload.get("last_modified") or ""),
                segments=[dict(item) for item in payload.get("segments") or []],
            )
        except (TypeError, ValueError):
            return None

    def save(self, path: Path) -> None:
        try:
            path.write_text(json.dumps(asdict(self), ensure_ascii=False), encoding="utf-8")
        except OSError as exc:  # noqa: BLE001 - 元数据写失败不应让下载失败
            logger.debug("写入断点元数据失败 %s：%s", path, exc)


def _resume_segments(state: ResumeState, connections: int, size: int) -> list[Segment] | None:
    if state.size != size or not state.segments:
        return None
    segments: list[Segment] = []
    for item in state.segments:
        start = int(item.get("start", 0))
        end = int(item.get("end", 0))
        cursor = int(item.get("cursor", start))
        if not (0 <= start <= cursor <= end <= size):
            return None
        if end <= start:
            continue
        segments.append(Segment(start=start, end=end, cursor=cursor, reserved=cursor))
    return segments or None


# ---------------------------------------------------------------- 下载执行
@dataclass
class DownloadReport:
    path: Path
    size: int
    connections: int = 1
    resumed: bool = False
    segmented: bool = False
    elapsed_ms: int = 0


ProgressFn = Callable[[int, int | None, float], None]


def _segment_paths(target: Path) -> tuple[Path, Path]:
    return target.with_suffix(target.suffix + ".part"), target.with_suffix(target.suffix + ".fcdl")


def _probe(client: Any, url: str, headers: dict[str, str]) -> dict[str, Any]:
    """用 Range: bytes=0-0 探测是否支持分段与文件大小。"""
    probe_headers = dict(headers)
    probe_headers["Range"] = "bytes=0-0"
    try:
        response = client.get(url, headers=probe_headers)
    except Exception as exc:  # noqa: BLE001 - 探测失败按不支持处理
        logger.debug("分段探测失败 %s：%s", url, exc)
        return {"supported": False}
    try:
        content_range = response.headers.get("content-range", "")
        total = 0
        if content_range.startswith("bytes 0-0/"):
            try:
                total = int(content_range.split("/", 1)[1])
            except ValueError:
                total = 0
        if response.status_code != 206 or total <= 0:
            length = response.headers.get("content-length", "")
            size = int(length) if length.isdigit() else 0
            return {"supported": False, "size": size, "content_type": response.headers.get("content-type", "")}
        return {
            "supported": True,
            "size": total,
            "etag": response.headers.get("etag", ""),
            "last_modified": response.headers.get("last-modified", ""),
            "content_type": response.headers.get("content-type", ""),
        }
    finally:
        response.close()


class _Writer:
    """每个 worker 独立文件句柄，按偏移写入自己的分段，互不加锁。"""

    def __init__(self, path: Path):
        self._handle = open(path, "r+b", buffering=0)  # noqa: SIM115 - 生命周期由 close 管理

    def write_at(self, offset: int, data: bytes) -> None:
        self._handle.seek(offset)
        self._handle.write(data)

    def close(self) -> None:
        try:
            self._handle.close()
        except OSError:
            pass


def download_file(
    client: Any,
    url: str,
    target: str | Path,
    headers: dict[str, str] | None = None,
    *,
    connections: int = DEFAULT_CONNECTIONS,
    min_split: int = DEFAULT_MIN_SPLIT,
    min_segmented_size: int = MIN_SEGMENTED_SIZE,
    chunk_size: int = DEFAULT_CHUNK,
    rate_limit: int = 0,
    resume: bool = True,
    timeout: float = 30.0,
    should_stop: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
    on_progress: ProgressFn | None = None,
    pause_interval: float = 0.2,
) -> DownloadReport:
    """下载 ``url`` 到 ``target``；支持分段并发、动态拆分、断点续传与限速。"""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    merged_headers = {str(k): str(v) for k, v in (headers or {}).items()}
    started = time.perf_counter()
    info = _probe(client, url, merged_headers)
    size = int(info.get("size") or 0)
    use_segments = bool(info.get("supported")) and size >= max(1, min_segmented_size) and connections > 1

    if not use_segments:
        report = _stream_download(
            client,
            url,
            target,
            merged_headers,
            rate_limit=rate_limit,
            timeout=timeout,
            should_stop=should_stop,
            should_pause=should_pause,
            on_progress=on_progress,
            pause_interval=pause_interval,
        )
        report.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return report

    return _segmented_download(
        client,
        url,
        target,
        merged_headers,
        size=size,
        info=info,
        connections=connections,
        min_split=min_split,
        chunk_size=chunk_size,
        rate_limit=rate_limit,
        resume=resume,
        timeout=timeout,
        should_stop=should_stop,
        should_pause=should_pause,
        on_progress=on_progress,
        pause_interval=pause_interval,
        started=started,
    )


def _stream_download(
    client: Any,
    url: str,
    target: Path,
    headers: dict[str, str],
    *,
    rate_limit: int,
    timeout: float,
    should_stop: Callable[[], bool] | None,
    should_pause: Callable[[], bool] | None,
    on_progress: ProgressFn | None,
    pause_interval: float,
) -> DownloadReport:
    limiter = RateLimiter(rate_limit)
    tmp = target.with_suffix(target.suffix + ".part")
    downloaded = 0
    total: int | None = None
    last_emit = 0.0
    try:
        with client.stream("GET", url, headers=headers, timeout=timeout) as response:
            if response.status_code >= 400:
                from .failure_classifier import classify_http_status

                raise MediaDownloadError(
                    f"HTTP {response.status_code}",
                    classify_http_status(response.status_code) or "media_download_error",
                )
            length = response.headers.get("content-length", "")
            total = int(length) if length.isdigit() else None
            with tmp.open("wb") as handle:
                for chunk in response.iter_bytes(64 * 1024):
                    if should_stop and should_stop():
                        raise MediaDownloadError("下载已停止", "media_download_error")
                    while should_pause and should_pause():
                        if should_stop and should_stop():
                            raise MediaDownloadError("下载已停止", "media_download_error")
                        time.sleep(pause_interval)
                    limiter.consume(len(chunk), should_stop)
                    handle.write(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()
                    if on_progress and now - last_emit >= 0.25:
                        last_emit = now
                        on_progress(downloaded, total, 0.0)
    except MediaDownloadError:
        raise
    except Exception as exc:  # noqa: BLE001 - 网络异常统一分类
        raise MediaDownloadError(f"下载失败：{exc}", classify_exception(exc)) from exc
    tmp.replace(target)
    if on_progress:
        on_progress(downloaded, total or downloaded, 0.0)
    return DownloadReport(path=target, size=target.stat().st_size)


def _segmented_download(
    client: Any,
    url: str,
    target: Path,
    headers: dict[str, str],
    *,
    size: int,
    info: dict[str, Any],
    connections: int,
    min_split: int,
    chunk_size: int,
    rate_limit: int,
    resume: bool,
    timeout: float,
    should_stop: Callable[[], bool] | None,
    should_pause: Callable[[], bool] | None,
    on_progress: ProgressFn | None,
    pause_interval: float,
    started: float,
) -> DownloadReport:
    part_path, state_path = _segment_paths(target)
    resumed = False
    segments: list[Segment] | None = None
    if resume and part_path.exists():
        state = ResumeState.load(state_path)
        if state and state.url == url:
            segments = _resume_segments(state, connections, size)
            resumed = segments is not None
            if not resumed:
                part_path.unlink(missing_ok=True)
                state_path.unlink(missing_ok=True)
    if not part_path.exists() or not resumed:
        with part_path.open("wb") as handle:
            handle.truncate(size)
        resumed = False

    planner = Planner(
        size=size,
        segments=segments,
        connections=connections,
        min_split=min_split,
    )
    limiter = RateLimiter(rate_limit)
    stop_event = threading.Event()
    errors: list[BaseException] = []
    lock = threading.Lock()
    last_emit = [0.0]
    start_ts = time.monotonic()

    def emit(force: bool = False) -> None:
        if on_progress is None:
            return
        now = time.monotonic()
        if not force and now - last_emit[0] < 0.25:
            return
        last_emit[0] = now
        done = planner.downloaded()
        speed = done / max(now - start_ts, 1e-6)
        on_progress(done, size, speed)

    def worker(index: int) -> None:
        writer = _Writer(part_path)
        try:
            while not stop_event.is_set():
                if should_stop and should_stop():
                    stop_event.set()
                    break
                while should_pause and should_pause():
                    if should_stop and should_stop():
                        stop_event.set()
                        break
                    time.sleep(pause_interval)
                if stop_event.is_set():
                    break
                assignment = planner.claim(index)
                if assignment is None:
                    if planner.is_done():
                        break
                    # 没有可领的段：可能别的 worker 正在收尾
                    if planner.remaining() <= 0:
                        break
                    time.sleep(0.05)
                    continue
                segment = assignment.segment
                _fetch_segment(
                    client,
                    url,
                    headers,
                    assignment,
                    planner,
                    writer,
                    index,
                    limiter,
                    chunk_size,
                    timeout,
                    stop_event,
                    should_stop,
                    emit,
                )
        except BaseException as exc:  # noqa: BLE001 - 线程内异常收集后统一抛出
            with lock:
                errors.append(exc)
            stop_event.set()
        finally:
            writer.close()

    threads = [
        threading.Thread(target=worker, args=(index,), name=f"seg-{index}", daemon=True)
        for index in range(max(1, connections))
    ]
    for thread in threads:
        thread.start()
    while any(thread.is_alive() for thread in threads):
        for thread in threads:
            thread.join(timeout=0.2)
        emit()
        _save_state(state_path, url, size, info, planner)

    emit(force=True)
    _save_state(state_path, url, size, info, planner)

    if errors:
        raise MediaDownloadError(f"分片下载失败：{errors[0]}", "media_download_error")
    if should_stop and should_stop():
        raise MediaDownloadError("下载已停止", "media_download_error")
    if not planner.is_done():
        raise MediaDownloadError("分片未全部完成", "media_download_error")

    with part_path.open("r+b") as handle:
        handle.truncate(size)
    part_path.replace(target)
    state_path.unlink(missing_ok=True)
    logger.info("分段下载完成 %s（%d 段并发）", target.name, connections)
    return DownloadReport(
        path=target,
        size=target.stat().st_size,
        connections=connections,
        resumed=resumed,
        segmented=True,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )


def _fetch_segment(
    client: Any,
    url: str,
    headers: dict[str, str],
    assignment: Assignment,
    planner: Planner,
    writer: _Writer,
    worker_id: int,
    limiter: RateLimiter,
    chunk_size: int,
    timeout: float,
    stop_event: threading.Event,
    should_stop: Callable[[], bool] | None,
    emit: Callable[[bool], None],
) -> None:
    segment = assignment.segment
    position = assignment.from_offset
    request_headers = dict(headers)
    while position < segment.end and not stop_event.is_set():
        if not planner.owns(worker_id, segment):
            return  # 被别的连接抢占
        if should_stop and should_stop():
            stop_event.set()
            return
        request_headers["Range"] = f"bytes={position}-{segment.end - 1}"
        try:
            with client.stream("GET", url, headers=request_headers, timeout=timeout) as response:
                if response.status_code not in (200, 206):
                    raise MediaDownloadError(
                        f"HTTP {response.status_code}", "media_download_error"
                    )
                planner.start_receiving(worker_id, segment)
                for chunk in response.iter_bytes(chunk_size):
                    if stop_event.is_set() or (should_stop and should_stop()):
                        stop_event.set()
                        return
                    if not planner.owns(worker_id, segment):
                        return
                    limiter.consume(len(chunk), should_stop)
                    room = segment.end - position
                    data = chunk[:room]
                    writer.write_at(position, data)
                    position += len(data)
                    planner.commit(worker_id, segment, len(data), position)
                    emit(False)
                    if position >= segment.end:
                        break
                # 服务器给了完整 200 而不是 206：说明 Range 无效，交给上层回退
                if response.status_code == 200:
                    raise MediaDownloadError("服务器忽略了 Range 请求", "media_download_error")
        except MediaDownloadError:
            raise
        except Exception as exc:  # noqa: BLE001 - 网络抖动重试当前段
            logger.debug("分段 %s-%s 中断：%s，重试", segment.start, segment.end, exc)
            time.sleep(0.5)
    planner.release(worker_id, segment)


def _save_state(state_path: Path, url: str, size: int, info: dict[str, Any], planner: Planner) -> None:
    ResumeState(
        url=url,
        size=size,
        etag=str(info.get("etag") or ""),
        last_modified=str(info.get("last_modified") or ""),
        segments=planner.snapshot(),
    ).save(state_path)


def supports_segmented_download(url: str) -> bool:
    """粗判：只有 http(s) 直链才考虑分段。"""
    scheme = urlsplit(url or "").scheme.lower()
    return scheme in ("http", "https")


def cleanup_partial(target: str | Path) -> None:
    """删除某个目标文件的断点残留。"""
    part, state = _segment_paths(Path(target))
    for path in (part, state):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


__all__ = [
    "Assignment",
    "DownloadReport",
    "Planner",
    "RateLimiter",
    "ResumeState",
    "Segment",
    "cleanup_partial",
    "download_file",
    "supports_segmented_download",
]
