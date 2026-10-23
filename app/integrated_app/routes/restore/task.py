#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""任务状态路由：SSE 进度 / 取消 / 结果 / 下载。

- GET  /api/restore/{task_id}/progress  SSE 实时进度（读 tasks 表快照）
- POST /api/restore/{task_id}/cancel    取消排队中/运行中的任务
- GET  /api/restore/{task_id}/result    任务结果信息
- GET  /api/restore/{task_id}/download  下载结果（PathGuard 白名单）
"""

from __future__ import annotations

import asyncio
import json
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from loguru import logger

from app.integrated_app.config import PROJECT_ROOT
from app.integrated_app.dependencies import get_config, get_history_db, get_task_queue
from app.integrated_app.history_db import HistoryDB
from app.integrated_app.routes.restore import common
from app.integrated_app.security.path_guard import build_default_path_guard
from app.integrated_app.task_queue import TaskQueue
from app.integrated_app.utils.response import respond_success

router = APIRouter(prefix="/api/restore", tags=["修复"])


@router.get("/{task_id}/progress")
async def get_progress(
    task_id: str,
    request: Request,
    history_db: HistoryDB = Depends(get_history_db),
    config: dict = Depends(get_config),
):
    """SSE 实时进度推送（快照式：每次事件后读 tasks 表最新状态）。"""
    task = await common.get_task_state(task_id, history_db)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    sse_cfg = config.get("runtime", {}).get("sse", {})
    max_duration = int(sse_cfg.get("max_duration_seconds", 3600))
    heartbeat_interval = int(sse_cfg.get("heartbeat_interval_seconds", 30))

    async def event_generator():
        start = asyncio.get_event_loop().time()
        last_heartbeat = start
        last_payload: str | None = None
        try:
            while True:
                now = asyncio.get_event_loop().time()
                if now - start > max_duration:
                    yield f"data: {json.dumps({'task_id': task_id, 'status': 'timeout'})}\n\n"
                    break

                task = await common.get_task_state(task_id, history_db)
                if not task:
                    yield f"data: {json.dumps({'error': '任务不存在'})}\n\n"
                    break

                data = {
                    "task_id": task_id,
                    "status": task["status"],
                    "progress": task.get("progress", 0),
                    "message": task.get("message", ""),
                    "current_stage": task.get("current_stage", ""),
                    "error": task.get("error") or "",
                    "output_path": task.get("output_path") or "",
                    "output_size": task.get("output_size", 0),
                }
                payload = json.dumps(data, ensure_ascii=False)
                if payload != last_payload:
                    last_payload = payload
                    yield f"data: {payload}\n\n"

                if task["status"] in ("completed", "failed", "cancelled"):
                    break

                if now - last_heartbeat >= heartbeat_interval:
                    yield ": heartbeat\n\n"
                    last_heartbeat = now
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    history_db: HistoryDB = Depends(get_history_db),
    task_queue: TaskQueue = Depends(get_task_queue),
):
    task = await common.get_task_state(task_id, history_db)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["status"] not in ("pending", "processing"):
        raise HTTPException(status_code=400,
                            detail=f"任务状态为 {task['status']}，无法取消")
    task_queue.request_cancel(task_id)
    await common.update_task_state(task_id, history_db, status="cancelled",
                                   error="用户取消", finished_at=common.now_str())
    record_id = task.get("record_id")
    if record_id:
        await history_db.update_record(record_id, status="cancelled",
                                       error="用户取消")
    return respond_success({"task_id": task_id, "status": "cancelled"})


@router.get("/{task_id}/result")
async def get_result(task_id: str,
                     history_db: HistoryDB = Depends(get_history_db)):
    task = await common.get_task_state(task_id, history_db)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    status = task["status"]
    if status in ("pending", "processing"):
        return respond_success({"task_id": task_id, "status": status,
                                "progress": task.get("progress", 0)})
    if status in ("failed", "cancelled"):
        return respond_success({"task_id": task_id, "status": status,
                                "error": task.get("error")})
    output_path = task.get("output_path") or ""
    exists = await asyncio.to_thread(os.path.exists, output_path) if output_path else False
    payload = {"task_id": task_id, "status": "completed",
               "output_path": output_path,
               "output_size": task.get("output_size", 0)}
    if not exists:
        payload["warning"] = "输出文件不存在"
    return respond_success(payload)


@router.get("/{task_id}/download")
async def download_result(
    task_id: str,
    history_db: HistoryDB = Depends(get_history_db),
    config: dict = Depends(get_config),
):
    task = await common.get_task_state(task_id, history_db)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["status"] != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成")
    output_path = task.get("output_path") or ""
    if not output_path or not await asyncio.to_thread(os.path.exists, output_path):
        raise HTTPException(status_code=404, detail="输出文件不存在")

    allowed = config.get("runtime", {}).get("security", {}).get(
        "allowed_base_dirs", ["outputs/", "data/uploads/"])
    guard = build_default_path_guard(PROJECT_ROOT, allowed)
    if not guard.is_safe_path(output_path):
        logger.warning(f"下载路径不在允许范围: {output_path}")
        raise HTTPException(status_code=403, detail="不允许下载该路径")

    return FileResponse(path=output_path,
                        filename=os.path.basename(output_path),
                        media_type="image/png")
