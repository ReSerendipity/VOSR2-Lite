#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""孤儿任务回收与续跑（崩溃恢复）。

服务崩溃 / 强杀后，tasks 表里 status 仍停在 pending / processing 的记录，
其执行现场（内存队列 / 推理线程）已随进程消失——此前这些任务会永远停在
「处理中」。本模块在 lifespan 启动早期做一次对账：

- ``processing``（进程死亡时正在推理）：引擎无中间态检查点，现场不可恢复，
  统一标记为 failed 并注明「进程中断」——孤儿回收；
- ``pending``（已入队但从未执行）：输入文件仍在则按原 task_id 重新入队
  （续跑；SSE 进度与历史记录经同一 task_id 保持连贯），输入文件已丢失则
  标记 failed。

必须在 ``task_queue.start()`` 之前调用：重新入队的任务随 worker 启动即刻
执行，且不会被启动窗口期内的新上传任务插队（单 worker 串行）。
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import threading
from typing import Any

from loguru import logger

from app.integrated_app.history_db import HistoryDB
from app.integrated_app.task_queue import TaskQueue

ERROR_INTERRUPTED = "上次进程异常退出，任务中断（启动时孤儿回收自动标记）"
ERROR_INPUT_LOST = "输入文件已丢失，无法续跑（启动时孤儿回收自动标记）"


async def recover_orphans(
    history_db: HistoryDB, task_queue: TaskQueue, model_manager: Any
) -> dict[str, int]:
    """对账 tasks 表遗留的 pending/processing 状态，返回 {"requeued", "marked_failed"}。"""
    from app.integrated_app.routes.restore import common
    from app.integrated_app.routes.restore import upload as upload_mod

    orphans = await history_db.get_tasks_by_status(("pending", "processing"))
    if not orphans:
        return {"requeued": 0, "marked_failed": 0}

    # 重新入队的任务在 worker 协程里执行，进度回调需要事件循环句柄（与上传路径一致）
    upload_mod._LOOP_HOLDER["loop"] = asyncio.get_running_loop()
    requeued = 0
    marked_failed = 0
    for row in orphans:
        task_id = str(row["task_id"])
        record_id = row.get("record_id")
        record: dict | None = None
        if record_id is not None:
            record = await history_db.get_record(int(record_id))

        if row["status"] == "processing":
            # 引擎无中间态检查点：推理现场不可恢复，回收为 failed
            await history_db.update_task(
                task_id,
                status="failed",
                error=ERROR_INTERRUPTED,
                message="任务中断",
                finished_at=common.now_str(),
            )
            if record_id is not None:
                await history_db.update_record(
                    int(record_id), status="failed", error=ERROR_INTERRUPTED
                )
            marked_failed += 1
            logger.warning(f"孤儿任务已回收（原 processing）: {task_id}")
            continue

        # pending：从未执行，尝试续跑
        input_path = str((record or {}).get("input_file") or "")
        checkpoint = str((record or {}).get("checkpoint") or "")
        try:
            params: dict = json.loads(str(row.get("params_json") or "{}"))
        except json.JSONDecodeError:
            params = {}
        if not checkpoint:
            checkpoint = str(params.get("checkpoint") or "")

        if not input_path or not os.path.isfile(input_path):
            await history_db.update_task(
                task_id,
                status="failed",
                error=ERROR_INPUT_LOST,
                message="无法续跑",
                finished_at=common.now_str(),
            )
            if record_id is not None:
                await history_db.update_record(
                    int(record_id), status="failed", error=ERROR_INPUT_LOST
                )
            marked_failed += 1
            logger.warning(f"孤儿任务无法续跑（输入丢失）: {task_id}")
            continue

        cancel_event = threading.Event()
        factory = functools.partial(
            upload_mod._run_restore_task,
            task_id,
            input_path,
            params,
            history_db,
            model_manager,
            checkpoint,
            cancel_event,
        )
        await task_queue.submit(task_id, factory, on_cancel=cancel_event.set)
        requeued += 1
        logger.info(f"孤儿任务已重新入队（续跑）: {task_id} <- {input_path}")

    logger.info(
        f"孤儿任务对账完成: 重新入队 {requeued} 个，标记失败 {marked_failed} 个"
        f"（遗留 {len(orphans)} 个）"
    )
    return {"requeued": requeued, "marked_failed": marked_failed}
