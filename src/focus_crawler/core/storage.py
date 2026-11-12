"""SQLite 存储层（DAO）。

表结构遵循规格文档第 7 节。所有写入使用参数化 SQL。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from ..models.media import MediaRecord
from ..utils.hash import url_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  config_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS urls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL,
  url TEXT NOT NULL,
  url_hash TEXT NOT NULL,
  parent_url TEXT,
  depth INTEGER NOT NULL DEFAULT 0,
  page_type TEXT NOT NULL DEFAULT 'seed',
  status TEXT NOT NULL DEFAULT 'pending',
  http_status INTEGER,
  retry_count INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  fetched_at TEXT,
  content_hash TEXT,
  UNIQUE(task_id, url_hash)
);

CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL,
  url TEXT NOT NULL,
  url_hash TEXT NOT NULL,
  data_json TEXT NOT NULL,
  content_hash TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(task_id, url_hash)
);

CREATE TABLE IF NOT EXISTS failures (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL,
  url TEXT NOT NULL,
  error TEXT NOT NULL,
  failure_type TEXT NOT NULL,
  retry_count INTEGER NOT NULL DEFAULT 0,
  last_try_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER,
  level TEXT NOT NULL,
  message TEXT NOT NULL,
  url TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL,
  source_url TEXT NOT NULL,
  parent_url TEXT,
  media_url TEXT NOT NULL,
  media_hash TEXT NOT NULL,
  media_type TEXT NOT NULL,
  format TEXT,
  width INTEGER,
  height INTEGER,
  duration REAL,
  bitrate INTEGER,
  sample_rate INTEGER,
  quality TEXT,
  file_size INTEGER,
  local_path TEXT,
  headers TEXT,
  source_kind TEXT,
  origin_url TEXT,
  status TEXT NOT NULL DEFAULT 'discovered',
  error TEXT,
  discovered_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(task_id, media_hash)
);

CREATE TABLE IF NOT EXISTS media_segments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  media_id INTEGER NOT NULL,
  segment_index INTEGER NOT NULL,
  segment_url TEXT NOT NULL,
  local_path TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  error TEXT
);

CREATE TABLE IF NOT EXISTS parser_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL,
  url TEXT NOT NULL,
  script_path TEXT NOT NULL,
  status TEXT NOT NULL,
  error_type TEXT,
  error_message TEXT,
  duration_ms INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media_adapters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL,
  adapter_name TEXT NOT NULL,
  source_url TEXT NOT NULL,
  status TEXT NOT NULL,
  error TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_urls_task_status ON urls(task_id, status, depth);
CREATE INDEX IF NOT EXISTS idx_media_task_type ON media(task_id, media_type, status);
CREATE INDEX IF NOT EXISTS idx_items_task ON items(task_id);
CREATE INDEX IF NOT EXISTS idx_failures_task ON failures(task_id);
CREATE INDEX IF NOT EXISTS idx_logs_task ON logs(task_id, created_at);
"""


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


class Storage:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        self._ensure_media_headers()
        self.conn.commit()

    def _ensure_media_headers(self) -> None:
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(media)")}
        if "headers" not in columns:
            self.conn.execute("ALTER TABLE media ADD COLUMN headers TEXT")
        if "source_kind" not in columns:
            # 老库升级：记录媒体来源（静态嗅探 / 平台适配器 / yt-dlp 等）
            self.conn.execute("ALTER TABLE media ADD COLUMN source_kind TEXT")
        if "origin_url" not in columns:
            # 老库升级：yt-dlp 记录重新解析用的页面地址
            self.conn.execute("ALTER TABLE media ADD COLUMN origin_url TEXT")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ---------- tasks ----------
    def upsert_task(self, name: str, config_json: str) -> int:
        ts = now_iso()
        row = self.conn.execute(
            "SELECT id FROM tasks WHERE name = ? ORDER BY id DESC LIMIT 1", (name,)
        ).fetchone()
        if row:
            self.conn.execute(
                "UPDATE tasks SET config_json = ?, updated_at = ? WHERE id = ?",
                (config_json, ts, row["id"]),
            )
            task_id = int(row["id"])
        else:
            cur = self.conn.execute(
                "INSERT INTO tasks(name, config_json, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (name, config_json, ts, ts),
            )
            task_id = int(cur.lastrowid)
        self.conn.commit()
        return task_id

    def update_task(self, task_id: int, name: str, config_json: str) -> None:
        """按 id 更新任务记录，改名时不会覆盖其他同名任务。"""
        self.conn.execute(
            "UPDATE tasks SET name = ?, config_json = ?, updated_at = ? WHERE id = ?",
            (name, config_json, now_iso(), task_id),
        )
        self.conn.commit()

    def get_task(self, task_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()

    def list_tasks(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall())

    def delete_task(self, task_id: int) -> None:
        """删除任务及其关联记录；已下载到磁盘的媒体文件保留。"""
        self.conn.execute(
            "DELETE FROM media_segments WHERE media_id IN (SELECT id FROM media WHERE task_id = ?)",
            (task_id,),
        )
        for table in ("media", "urls", "items", "failures", "parser_runs", "media_adapters", "logs"):
            self.conn.execute(f"DELETE FROM {table} WHERE task_id = ?", (task_id,))
        self.conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self.conn.commit()

    # ---------- urls ----------
    def add_url(
        self,
        task_id: int,
        url: str,
        parent_url: str | None,
        depth: int,
        page_type: str = "discovered",
        status: str = "pending",
    ) -> bool:
        """返回是否实际插入（已存在则忽略）。"""
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO urls
               (task_id, url, url_hash, parent_url, depth, page_type, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (task_id, url, url_hash(url), parent_url, depth, page_type, status),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_url(self, task_id: int, url: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM urls WHERE task_id = ? AND url_hash = ?", (task_id, url_hash(url))
        ).fetchone()

    def next_pending_urls(self, task_id: int, limit: int, max_depth: int | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM urls WHERE task_id = ? AND status = 'pending'"
        params: list[Any] = [task_id]
        if max_depth is not None:
            sql += " AND depth <= ?"
            params.append(max_depth)
        sql += " ORDER BY depth ASC, id ASC LIMIT ?"
        params.append(limit)
        return list(self.conn.execute(sql, params).fetchall())

    def update_url(
        self,
        url_id: int,
        status: str,
        http_status: int | None = None,
        error: str | None = None,
        content_hash: str | None = None,
        retry_count: int | None = None,
    ) -> None:
        self.conn.execute(
            """UPDATE urls SET status = ?, http_status = ?, error = ?,
               content_hash = COALESCE(?, content_hash),
               retry_count = COALESCE(?, retry_count),
               fetched_at = CASE WHEN ? = 'done' THEN ? ELSE fetched_at END
               WHERE id = ?""",
            (
                status,
                http_status,
                error,
                content_hash,
                retry_count,
                status,
                now_iso(),
                url_id,
            ),
        )
        self.conn.commit()

    def retry_url(self, url_id: int) -> None:
        self.conn.execute(
            "UPDATE urls SET status = 'pending', retry_count = retry_count + 1 WHERE id = ?",
            (url_id,),
        )
        self.conn.commit()

    def reset_failed_urls(self, task_id: int) -> int:
        cur = self.conn.execute(
            "UPDATE urls SET status = 'pending' WHERE task_id = ? AND status = 'failed'", (task_id,)
        )
        self.conn.commit()
        return cur.rowcount

    def count_urls(self, task_id: int, status: str | None = None, page_type: str | None = None) -> int:
        sql = "SELECT COUNT(*) AS n FROM urls WHERE task_id = ?"
        params: list[Any] = [task_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        if page_type:
            sql += " AND page_type = ?"
            params.append(page_type)
        return int(self.conn.execute(sql, params).fetchone()["n"])

    # ---------- items ----------
    def add_item(
        self, task_id: int, url: str, data: dict[str, Any], content_hash: str | None = None
    ) -> bool:
        ts = now_iso()
        payload = json.dumps(data, ensure_ascii=False)
        self.conn.execute(
            """INSERT INTO items(task_id, url, url_hash, data_json, content_hash, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(task_id, url_hash) DO UPDATE SET
                 data_json = excluded.data_json,
                 content_hash = COALESCE(excluded.content_hash, items.content_hash),
                 updated_at = excluded.updated_at""",
            (task_id, url, url_hash(url), payload, content_hash, ts, ts),
        )
        self.conn.commit()
        return True

    def list_items(self, task_id: int, limit: int | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM items WHERE task_id = ? ORDER BY id"
        params: list[Any] = [task_id]
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return list(self.conn.execute(sql, params).fetchall())

    def count_items(self, task_id: int) -> int:
        return int(
            self.conn.execute("SELECT COUNT(*) AS n FROM items WHERE task_id = ?", (task_id,)).fetchone()["n"]
        )

    # ---------- media ----------
    def add_media(self, record: MediaRecord) -> bool:
        ts = now_iso()
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO media
               (task_id, source_url, parent_url, media_url, media_hash, media_type, format,
                width, height, duration, bitrate, sample_rate, quality, file_size, local_path,
                headers, source_kind, origin_url, status, error, discovered_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.task_id,
                record.source_url,
                record.parent_url,
                record.media_url,
                record.media_hash,
                record.media_type,
                record.format,
                record.width,
                record.height,
                record.duration,
                record.bitrate,
                record.sample_rate,
                record.quality,
                record.file_size,
                record.local_path,
                record.headers,
                record.source_kind,
                record.origin_url,
                record.status,
                record.error,
                record.discovered_at or ts,
                ts,
                ts,
            ),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_media(self, task_id: int, media_hash_value: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM media WHERE task_id = ? AND media_hash = ?",
            (task_id, media_hash_value),
        ).fetchone()

    def update_media(
        self,
        media_id: int,
        status: str | None = None,
        local_path: str | None = None,
        file_size: int | None = None,
        error: str | None = None,
    ) -> None:
        self.conn.execute(
            """UPDATE media SET status = COALESCE(?, status),
               local_path = COALESCE(?, local_path),
               file_size = COALESCE(?, file_size),
               error = ?,
               updated_at = ?
               WHERE id = ?""",
            (status, local_path, file_size, error, now_iso(), media_id),
        )
        self.conn.commit()

    def update_media_source(self, media_id: int, media_url: str) -> None:
        """更新媒体的远程地址（用于平台签名地址过期后重新解析）。"""
        self.conn.execute(
            "UPDATE media SET media_url = ?, updated_at = ? WHERE id = ?",
            (media_url, now_iso(), media_id),
        )
        self.conn.commit()

    def list_media(
        self, task_id: int, media_type: str | None = None, status: str | None = None
    ) -> list[sqlite3.Row]:
        sql = "SELECT * FROM media WHERE task_id = ?"
        params: list[Any] = [task_id]
        if media_type:
            sql += " AND media_type = ?"
            params.append(media_type)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY id"
        return list(self.conn.execute(sql, params).fetchall())

    def count_media(self, task_id: int, media_type: str | None = None) -> int:
        sql = "SELECT COUNT(*) AS n FROM media WHERE task_id = ?"
        params: list[Any] = [task_id]
        if media_type:
            sql += " AND media_type = ?"
            params.append(media_type)
        return int(self.conn.execute(sql, params).fetchone()["n"])

    def get_media_by_id(self, media_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()

    def clear_media_file(self, media_id: int) -> None:
        """删除磁盘文件后重置记录：清空本地路径/大小，回到 discovered。"""
        self.conn.execute("DELETE FROM media_segments WHERE media_id = ?", (media_id,))
        self.conn.execute(
            """UPDATE media SET status = 'discovered', local_path = NULL, file_size = NULL,
               error = NULL, updated_at = ? WHERE id = ?""",
            (now_iso(), media_id),
        )
        self.conn.commit()

    def list_media_sources(self, task_id: int, media_type: str) -> list[tuple[str, int]]:
        """按来源页聚合媒体数量，供界面筛选。"""
        rows = self.conn.execute(
            """SELECT source_url, COUNT(*) AS n FROM media
               WHERE task_id = ? AND media_type = ?
               GROUP BY source_url ORDER BY n DESC, source_url ASC""",
            (task_id, media_type),
        ).fetchall()
        return [(str(row["source_url"]), int(row["n"])) for row in rows]

    # ---------- media segments ----------
    def upsert_media_segment(
        self,
        media_id: int,
        segment_index: int,
        segment_url: str,
        local_path: str | None = None,
        status: str = "pending",
        error: str | None = None,
    ) -> None:
        row = self.conn.execute(
            "SELECT id FROM media_segments WHERE media_id = ? AND segment_index = ?",
            (media_id, segment_index),
        ).fetchone()
        if row:
            self.conn.execute(
                """UPDATE media_segments SET segment_url = ?, local_path = ?, status = ?, error = ?
                   WHERE id = ?""",
                (segment_url, local_path, status, error, row["id"]),
            )
        else:
            self.conn.execute(
                """INSERT INTO media_segments(media_id, segment_index, segment_url, local_path, status, error)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (media_id, segment_index, segment_url, local_path, status, error),
            )
        self.conn.commit()

    def list_media_segments(self, media_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM media_segments WHERE media_id = ? ORDER BY segment_index",
                (media_id,),
            ).fetchall()
        )

    # ---------- failures ----------
    def add_failure(self, task_id: int, url: str, error: str, failure_type: str, retry_count: int = 0) -> None:
        self.conn.execute(
            """INSERT INTO failures(task_id, url, error, failure_type, retry_count, last_try_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (task_id, url, error, failure_type, retry_count, now_iso()),
        )
        self.conn.commit()

    def list_failures(self, task_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute("SELECT * FROM failures WHERE task_id = ? ORDER BY id", (task_id,)).fetchall()
        )

    def failure_stats(self, task_id: int) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT failure_type, COUNT(*) AS n FROM failures WHERE task_id = ? GROUP BY failure_type",
            (task_id,),
        ).fetchall()
        return {row["failure_type"]: int(row["n"]) for row in rows}

    # ---------- logs ----------
    def add_log(self, task_id: int | None, level: str, message: str, url: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO logs(task_id, level, message, url, created_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, level, message, url, now_iso()),
        )
        self.conn.commit()

    def list_logs(self, task_id: int, limit: int = 200) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM logs WHERE task_id = ? ORDER BY id DESC LIMIT ?", (task_id, limit)
            ).fetchall()
        )

    # ---------- parser runs ----------
    def add_parser_run(
        self,
        task_id: int,
        url: str,
        script_path: str,
        status: str,
        error_type: str | None,
        error_message: str | None,
        duration_ms: int,
    ) -> None:
        self.conn.execute(
            """INSERT INTO parser_runs(task_id, url, script_path, status, error_type, error_message,
               duration_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, url, script_path, status, error_type, error_message, duration_ms, now_iso()),
        )
        self.conn.commit()

    def list_parser_runs(self, task_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute("SELECT * FROM parser_runs WHERE task_id = ? ORDER BY id", (task_id,)).fetchall()
        )

    # ---------- stats ----------
    def stats(self, task_id: int) -> dict[str, Any]:
        media_counts = {
            row["media_type"]: int(row["n"])
            for row in self.conn.execute(
                "SELECT media_type, COUNT(*) AS n FROM media WHERE task_id = ? GROUP BY media_type",
                (task_id,),
            ).fetchall()
        }
        return {
            "urls_total": self.count_urls(task_id),
            "urls_done": self.count_urls(task_id, status="done"),
            "urls_failed": self.count_urls(task_id, status="failed"),
            "urls_pending": self.count_urls(task_id, status="pending"),
            "items": self.count_items(task_id),
            "media": media_counts,
            "failures": self.failure_stats(task_id),
        }

    def iter_media(self, task_id: int, media_type: str) -> Iterable[sqlite3.Row]:
        yield from self.list_media(task_id, media_type=media_type)
