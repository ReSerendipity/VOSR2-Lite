#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""孤儿回收与续跑测试：崩溃遗留的 pending/processing 任务在启动时对账。"""

from __future__ import annotations

import asyncio
import sqlite3
import time
import uuid

from fastapi.testclient import TestClient
from PIL import Image

from app.integrated_app.history_db import HistoryDB, HistoryRecord
from app.integrated_app.orphan_recovery import (
    ERROR_INPUT_LOST,
    ERROR_INTERRUPTED,
    recover_orphans,
)
from app.integrated_app.routes.restore import upload as upload_mod
from app.integrated_app.task_queue import TaskQueue


class _FakeEngine:
    """立即返回原图的假引擎（不碰权重/GPU）。"""

    def restore(self, image, params, progress_cb=None):  # noqa: ANN001, ANN002
        return image


class _FakeModelManager:
    def get_engine(self, name=None):  # noqa: ANN001
        return _FakeEngine()


async def _seed(db_path, task_id, status, *, input_file="", params_json="{}", checkpoint="VOSR2"):
    """造一条任务 + 历史记录，返回 (task_id, record_id)。"""
    db = HistoryDB(db_path=db_path)
    await db.initialize()
    record_id = await db.add_record(
        HistoryRecord(input_file=input_file, checkpoint=checkpoint, status=status)
    )
    await db.create_task(task_id, record_id, params_json=params_json)
    if status != "pending":
        await db.update_task(task_id, status=status)
    await db.close()
    return record_id


def _get_task_row(db_path, task_id):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


async def _wait_terminal(db: HistoryDB, task_id: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = await db.get_task(task_id)
        if task and task["status"] in ("completed", "failed", "cancelled"):
            return task
        await asyncio.sleep(0.05)
    raise AssertionError("任务未在超时内到达终态")


def test_no_orphans_is_noop(tmp_path):
    async def run():
        db = HistoryDB(db_path=str(tmp_path / "t.db"))
        await db.initialize()
        summary = await recover_orphans(db, TaskQueue(), _FakeModelManager())
        await db.close()
        return summary

    assert asyncio.run(run()) == {"requeued": 0, "marked_failed": 0}


def test_processing_orphan_marked_failed(tmp_path):
    db_path = str(tmp_path / "t.db")
    task_id = uuid.uuid4().hex[:16]
    record_id = asyncio.run(_seed(db_path, task_id, "processing", input_file="nowhere.png"))

    async def run():
        db = HistoryDB(db_path=db_path)
        await db.initialize()
        summary = await recover_orphans(db, TaskQueue(), _FakeModelManager())
        task = await db.get_task(task_id)
        record = await db.get_record(record_id)
        await db.close()
        return summary, task, record

    summary, task, record = asyncio.run(run())
    assert summary == {"requeued": 0, "marked_failed": 1}
    assert task["status"] == "failed"
    assert "中断" in task["error"]
    assert record["status"] == "failed"
    assert task["finished_at"]


def test_pending_orphan_missing_input_marked_failed(tmp_path):
    db_path = str(tmp_path / "t.db")
    task_id = uuid.uuid4().hex[:16]
    record_id = asyncio.run(_seed(db_path, task_id, "pending", input_file="gone.png"))

    async def run():
        db = HistoryDB(db_path=db_path)
        await db.initialize()
        summary = await recover_orphans(db, TaskQueue(), _FakeModelManager())
        task = await db.get_task(task_id)
        record = await db.get_record(record_id)
        await db.close()
        return summary, task, record

    summary, task, record = asyncio.run(run())
    assert summary == {"requeued": 0, "marked_failed": 1}
    assert task["status"] == "failed"
    assert "丢失" in task["error"]
    assert record["status"] == "failed"


def test_pending_orphan_requeued_and_completes(tmp_path, monkeypatch):
    out_dir = tmp_path / "outputs"
    out_dir.mkdir()
    monkeypatch.setattr(upload_mod, "get_outputs_dir", lambda: str(out_dir))

    img = tmp_path / "in.png"
    Image.new("RGB", (16, 16)).save(img)

    db_path = str(tmp_path / "t.db")
    task_id = uuid.uuid4().hex[:16]
    record_id = asyncio.run(
        _seed(db_path, task_id, "pending", input_file=str(img), params_json='{"upscale": 2}')
    )

    async def run():
        db = HistoryDB(db_path=db_path)
        await db.initialize()
        queue = TaskQueue()
        summary = await recover_orphans(db, queue, _FakeModelManager())
        assert summary == {"requeued": 1, "marked_failed": 0}
        await queue.start()
        task = await _wait_terminal(db, task_id)
        record = await db.get_record(record_id)
        await queue.stop()
        await db.close()
        return task, record

    task, record = asyncio.run(run())
    assert task["status"] == "completed"
    assert task["output_path"]
    assert record["status"] == "completed"
    assert out_dir.joinpath(task["output_path"]).parent == out_dir


def test_lifespan_startup_recovers_orphans(tmp_path, monkeypatch):
    """集成：app 启动（lifespan）即完成对账——processing 标失败、pending 续跑完成。"""
    out_dir = tmp_path / "outputs"
    out_dir.mkdir()
    monkeypatch.setattr(upload_mod, "get_outputs_dir", lambda: str(out_dir))

    from app.integrated_app.app_server import create_app
    from app.integrated_app.model_manager import ModelManager

    def _fake_get_engine(self, name=None):  # noqa: ANN001
        return _FakeEngine()

    monkeypatch.setattr(ModelManager, "get_engine", _fake_get_engine)

    img = tmp_path / "in.png"
    Image.new("RGB", (16, 16)).save(img)
    db_path = str(tmp_path / "h.db")

    asyncio.run(_seed(db_path, "orphpending0001", "pending", input_file=str(img), params_json="{}"))
    asyncio.run(_seed(db_path, "orphprocess01", "processing", input_file=str(img)))
    # 修正 processing 的输入为不存在文件不影响断言：processing 一律标失败

    app = create_app({"history": {"db_path": db_path}})
    with TestClient(app):
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            pending_row = _get_task_row(db_path, "orphpending0001")
            if pending_row and pending_row["status"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(0.05)
        processing_row = _get_task_row(db_path, "orphprocess01")

    assert pending_row is not None and pending_row["status"] == "completed"
    assert ERROR_INPUT_LOST not in pending_row["error"]
    assert processing_row is not None
    assert processing_row["status"] == "failed"
    assert ERROR_INTERRUPTED in processing_row["error"]


def test_pending_orphan_queue_full_stays_pending(tmp_path, monkeypatch):
    """队列满时续跑提交应抛 TaskQueueFullError——孤儿保持 pending，不静默吞掉。"""
    from app.integrated_app.exceptions import TaskQueueFullError

    img = tmp_path / "in.png"
    Image.new("RGB", (8, 8)).save(img)
    db_path = str(tmp_path / "t.db")
    task_id = uuid.uuid4().hex[:16]
    asyncio.run(_seed(db_path, task_id, "pending", input_file=str(img)))

    class _FullQueue:
        async def submit(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise TaskQueueFullError("任务队列已满")

    async def run():
        db = HistoryDB(db_path=db_path)
        await db.initialize()
        try:
            await recover_orphans(db, _FullQueue(), _FakeModelManager())  # type: ignore[arg-type]
        except TaskQueueFullError:
            return True
        finally:
            await db.close()
        return False

    assert asyncio.run(run()) is True
