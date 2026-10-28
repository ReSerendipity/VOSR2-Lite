#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""SQLite 历史记录与任务状态持久化（aiosqlite，WAL 模式）。

两张表：
- history: 修复任务历史（输入/输出、参数、状态、耗时）
- tasks:   运行中任务的实时状态快照（SSE 进度查询读这里）

家族仓（SeedVR2-Lite）同款模块为 1100 行（FTS5 / 回收站 / pinned / 迁移链），
本项目按"取其精华去其糟粕"原则只保留核心：WAL、白名单列更新、记录数上限修剪。
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

import aiosqlite
from loguru import logger

SCHEMA_VERSION = 1

# tasks.status 合法取值
TASK_STATUSES = ("pending", "processing", "completed", "failed", "cancelled")

# tasks 表允许 UPDATE 的列（白名单，防 SQL 注入）
_TASK_UPDATABLE_COLUMNS = {
    "status",
    "progress",
    "message",
    "error",
    "output_path",
    "output_size",
    "processing_time",
    "current_stage",
    "finished_at",
}


@dataclass
class HistoryRecord:
    input_file: str
    output_file: str = ""
    task_type: str = "image"  # image | video（预留）
    status: str = "pending"
    checkpoint: str = ""
    params_json: str = "{}"
    error: str = ""
    processing_time: float = 0.0
    output_size: int = 0
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    record_id: int | None = None


class HistoryDB:
    """异步 SQLite 访问层（async with 或 initialize() 使用）。"""

    def __init__(self, db_path: str = "data/history.db", max_records: int = 10000):
        self.db_path = db_path
        self.max_records = max_records
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = sqlite3.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                input_file TEXT NOT NULL,
                output_file TEXT DEFAULT '',
                task_type TEXT DEFAULT 'image',
                status TEXT DEFAULT 'pending',
                checkpoint TEXT DEFAULT '',
                params_json TEXT DEFAULT '{}',
                error TEXT DEFAULT '',
                processing_time REAL DEFAULT 0,
                output_size INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                record_id INTEGER,
                status TEXT DEFAULT 'pending',
                progress REAL DEFAULT 0,
                message TEXT DEFAULT '',
                error TEXT DEFAULT '',
                output_path TEXT DEFAULT '',
                output_size INTEGER DEFAULT 0,
                processing_time REAL DEFAULT 0,
                current_stage TEXT DEFAULT '',
                task_type TEXT DEFAULT 'image',
                params_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                finished_at TEXT DEFAULT NULL
            )
            """)
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        await self._db.commit()
        logger.info(f"历史数据库已初始化: {self.db_path}")

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> "HistoryDB":
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("HistoryDB 未初始化，请先调用 initialize()")
        return self._db

    # ── history 表 ─────────────────────────────────────────────────────────
    async def add_record(self, record: HistoryRecord) -> int:
        cursor = await self.db.execute(
            """
            INSERT INTO history (input_file, output_file, task_type, status,
                                 checkpoint, params_json, error,
                                 processing_time, output_size, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.input_file,
                record.output_file,
                record.task_type,
                record.status,
                record.checkpoint,
                record.params_json,
                record.error,
                record.processing_time,
                record.output_size,
                record.created_at,
            ),
        )
        await self.db.commit()
        await self.prune_old_records()
        assert cursor.lastrowid is not None, "INSERT 未返回 lastrowid"
        return int(cursor.lastrowid)

    async def update_record(self, record_id: int, **kwargs: Any) -> bool:
        if not kwargs:
            return False
        allowed = {
            "output_file",
            "status",
            "error",
            "processing_time",
            "output_size",
            "checkpoint",
            "params_json",
        }
        cols = {k: v for k, v in kwargs.items() if k in allowed}
        if not cols:
            return False
        assignments = ", ".join(f"{k} = ?" for k in cols)
        await self.db.execute(
            f"UPDATE history SET {assignments} WHERE id = ?", (*cols.values(), record_id)
        )
        await self.db.commit()
        return True

    # 记录 -> 最近一次任务（前端「打开对比」需要 task_id 才能取原图/结果）
    _RECORD_SELECT = """
        SELECT h.*, (
            SELECT t.task_id FROM tasks t
            WHERE t.record_id = h.id ORDER BY t.created_at DESC LIMIT 1
        ) AS task_id
        FROM history h
    """

    async def get_record(self, record_id: int) -> dict | None:
        cursor = await self.db.execute(self._RECORD_SELECT + " WHERE h.id = ?", (record_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_records(
        self, page: int = 1, page_size: int = 20, status: str | None = None
    ) -> tuple[list[dict], int]:
        where, params = "", []
        if status:
            where, params = "WHERE h.status = ?", [status]
        cursor = await self.db.execute(f"SELECT COUNT(*) FROM history h {where}", params)
        count_row = await cursor.fetchone()
        total = int(count_row[0]) if count_row is not None else 0
        cursor = await self.db.execute(
            f"{self._RECORD_SELECT} {where} ORDER BY h.id DESC LIMIT ? OFFSET ?",
            (*params, page_size, (page - 1) * page_size),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows], total

    async def delete_record(self, record_id: int) -> bool:
        cursor = await self.db.execute("DELETE FROM history WHERE id = ?", (record_id,))
        await self.db.commit()
        return cursor.rowcount > 0

    async def count_records(self) -> int:
        cursor = await self.db.execute("SELECT COUNT(*) FROM history")
        count_row = await cursor.fetchone()
        return int(count_row[0]) if count_row is not None else 0

    async def prune_old_records(self, max_records: int | None = None) -> int:
        limit = max_records or self.max_records
        if limit <= 0:
            return 0
        cursor = await self.db.execute(
            """
            DELETE FROM history WHERE id NOT IN (
                SELECT id FROM history ORDER BY id DESC LIMIT ?
            )
            """,
            (limit,),
        )
        await self.db.commit()
        return cursor.rowcount or 0

    # ── tasks 表 ───────────────────────────────────────────────────────────
    async def create_task(
        self, task_id: str, record_id: int | None, params_json: str = "{}", task_type: str = "image"
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO tasks (task_id, record_id, status, params_json,
                               task_type, created_at)
            VALUES (?, ?, 'pending', ?, ?, ?)
            """,
            (task_id, record_id, params_json, task_type, time.strftime("%Y-%m-%d %H:%M:%S")),
        )
        await self.db.commit()

    async def update_task(self, task_id: str, **kwargs: Any) -> bool:
        cols = {k: v for k, v in kwargs.items() if k in _TASK_UPDATABLE_COLUMNS}
        if not cols:
            return False
        assignments = ", ".join(f"{k} = ?" for k in cols)
        await self.db.execute(
            f"UPDATE tasks SET {assignments} WHERE task_id = ?", (*cols.values(), task_id)
        )
        await self.db.commit()
        return True

    async def get_task(self, task_id: str) -> dict | None:
        cursor = await self.db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
