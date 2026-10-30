#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""单文件上传与修复路由。

POST /api/restore/ —— 上传图片 -> 校验（扩展名 + 魔数 + 大小）-> 落盘
-> 建历史记录与任务 -> 入队（单 worker 串行推理）-> 返回 task_id。
进度经 SSE（GET /api/restore/{task_id}/progress）推送。
"""

from __future__ import annotations

import asyncio
import os
import threading
import time

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from loguru import logger

from app.integrated_app.config import PROJECT_ROOT
from app.integrated_app.dependencies import (
    get_config,
    get_history_db,
    get_model_manager,
    get_task_queue,
)
from app.integrated_app.engines.vosr2_engine import RestoreParams
from app.integrated_app.exceptions import InferenceCancelled, TaskQueueFullError
from app.integrated_app.history_db import HistoryDB, HistoryRecord
from app.integrated_app.model_manager import ModelManager
from app.integrated_app.routes.restore import common
from app.integrated_app.security.magic_check import validate_upload_magic
from app.integrated_app.task_queue import TaskQueue
from app.integrated_app.utils.response import respond_success

router = APIRouter(prefix="/api/restore", tags=["修复"])

# 推理线程回调进度时需要把协程抛回事件循环；submit 时注入
_LOOP_HOLDER: dict[str, asyncio.AbstractEventLoop] = {}

_STAGE_LABELS = {
    "load_vae": "加载 VAE",
    "load_venc": "加载 DINOv2",
    "load_dit": "加载 DiT",
    "init": "初始化",
    "encode": "VAE 编码",
    "venc": "特征提取",
    "sample": "扩散采样",
    "decode": "VAE 解码",
    "align": "颜色对齐",
    "done": "完成",
}


def get_uploads_dir() -> str:
    path = os.path.join(PROJECT_ROOT, "data", "uploads")
    os.makedirs(path, exist_ok=True)
    return path


def get_outputs_dir() -> str:
    path = os.path.join(PROJECT_ROOT, "outputs")
    os.makedirs(path, exist_ok=True)
    return path


def _make_progress_cb(
    task_id: str, history_db: HistoryDB, cancel_event: threading.Event | None = None
):
    """引擎进度回调（推理线程调用）-> 线程安全地写 tasks 表。

    取消事件已置位时抛 InferenceCancelled，引擎的进度上报会把该异常原样透传，
    从而在下一个采样步之前中止推理。
    """

    def _cb(stage: str, ratio: float) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise InferenceCancelled(task_id)
        loop = _LOOP_HOLDER.get("loop")
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(
                common.update_task_state(
                    task_id,
                    history_db,
                    progress=round(min(max(ratio, 0.0), 1.0), 4),
                    message=_STAGE_LABELS.get(stage, stage),
                    current_stage=stage,
                ),
                loop,
            )
        except Exception:
            pass  # 进度回调失败不影响推理

    return _cb


async def _run_restore_task(
    task_id: str,
    input_path: str,
    params: dict,
    history_db: HistoryDB,
    model_manager: ModelManager,
    checkpoint: str,
    cancel_event: threading.Event | None = None,
) -> None:
    """worker 中执行的实际推理协程（同步引擎调用放线程池）。"""
    from PIL import Image

    if cancel_event is not None and cancel_event.is_set():
        logger.info(f"任务在执行前已被取消，跳过: {task_id}")
        return

    task_row = await history_db.get_task(task_id)
    record_id = task_row.get("record_id") if task_row else None
    started = time.monotonic()
    out_path: str | None = None

    try:
        if cancel_event is not None and cancel_event.is_set():
            raise InferenceCancelled(task_id)
        await common.update_task_state(
            task_id,
            history_db,
            status="processing",
            progress=0.01,
            message="正在加载模型",
            current_stage="load",
        )

        engine = model_manager.get_engine(checkpoint or None)
        progress_cb = _make_progress_cb(task_id, history_db, cancel_event)

        def _run() -> str:
            with Image.open(input_path) as img:
                image = img.convert("RGB")
            restore_params = RestoreParams(
                upscale=int(params.get("upscale", 4)),
                steps=int(params.get("steps", 1)),
                align_method=params.get("align_method", "wavelet"),
                seed=int(params.get("seed", 42)),
                tile_size=int(params.get("tile_size", 0)),
                vae_tile_size=int(params.get("vae_tile_size", 0)),
            )
            result = engine.restore(image, restore_params, progress_cb=progress_cb)
            stem = os.path.splitext(os.path.basename(input_path))[0]
            # 输入名可能带 task_id 前缀，剥掉让输出名干净
            if stem.startswith(f"{task_id}_"):
                stem = stem[len(task_id) + 1 :]
            out_path = os.path.join(get_outputs_dir(), f"{stem}_vosr2_{task_id[:8]}.png")
            result.save(out_path)
            return out_path

        loop = asyncio.get_running_loop()
        out_path = await loop.run_in_executor(None, _run)
        if cancel_event is not None and cancel_event.is_set():
            raise InferenceCancelled(task_id)
        size = os.path.getsize(out_path)

        elapsed = round(time.monotonic() - started, 2)
        await common.update_task_state(
            task_id,
            history_db,
            status="completed",
            progress=1.0,
            message="修复完成",
            current_stage="done",
            output_path=out_path,
            output_size=size,
            processing_time=elapsed,
            finished_at=common.now_str(),
        )
        if record_id:
            await history_db.update_record(
                record_id,
                status="completed",
                output_file=out_path,
                output_size=size,
                processing_time=elapsed,
            )
        logger.info(f"任务完成: {task_id} -> {out_path}")

    except InferenceCancelled:
        logger.info(f"任务已取消: {task_id}")
        if out_path and os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass
        await common.update_task_state(
            task_id,
            history_db,
            status="cancelled",
            error="用户取消",
            message="已取消",
            finished_at=common.now_str(),
        )
        if record_id:
            await history_db.update_record(record_id, status="cancelled", error="用户取消")

    except Exception as exc:
        logger.exception(f"任务失败: {task_id}")
        # OOM 等 CUDA 错误后上下文可能中毒：卸载引擎释放显存，
        # 给后续任务一次干净的重载机会（模型会再次懒加载）。
        if "out of memory" in str(exc).lower() or "cuda" in str(exc).lower():
            try:
                model_manager.unload_engine()
                logger.warning(
                    "检测到 CUDA 错误，已卸载引擎释放显存；" "如下个任务仍失败请重启服务"
                )
            except Exception:
                pass
        await common.update_task_state(
            task_id,
            history_db,
            status="failed",
            error=str(exc),
            message="修复失败",
            processing_time=round(time.monotonic() - started, 2),
            finished_at=common.now_str(),
        )
        if record_id:
            await history_db.update_record(
                record_id,
                status="failed",
                error=str(exc),
                processing_time=round(time.monotonic() - started, 2),
            )


@router.post("/")
async def upload_and_restore(
    file: UploadFile = File(...),
    params: dict = Depends(common.parse_restore_params),
    history_db: HistoryDB = Depends(get_history_db),
    task_queue: TaskQueue = Depends(get_task_queue),
    model_manager: ModelManager = Depends(get_model_manager),
    config: dict = Depends(get_config),
):
    """上传图片并创建修复任务。"""
    common.validate_image_extension(file.filename or "")

    max_mb = int(config.get("runtime", {}).get("security", {}).get("max_upload_image_mb", 50))
    head = await file.read(16)
    await file.seek(0)
    if not validate_upload_magic(file.filename or "", head):
        raise HTTPException(status_code=400, detail="文件内容与扩展名不符，疑似伪造格式")

    task_id = common.new_task_id()
    safe_name = os.path.basename(file.filename or "upload.png")
    input_path = os.path.join(get_uploads_dir(), f"{task_id}_{safe_name}")
    written = 0
    with open(input_path, "wb") as f:
        while True:
            chunk = await file.read(8192)
            if not chunk:
                break
            written += len(chunk)
            if written > max_mb * 1024 * 1024:
                f.close()
                os.remove(input_path)
                raise HTTPException(status_code=413, detail=f"文件超过大小上限（{max_mb} MB）")
            f.write(chunk)

    checkpoint = params.get("checkpoint") or config.get("model", {}).get(
        "default_checkpoint", "VOSR2"
    )
    record_id = await history_db.add_record(
        HistoryRecord(
            input_file=input_path,
            task_type="image",
            status="pending",
            checkpoint=checkpoint,
            params_json=common.params_to_json(params),
        )
    )
    await history_db.create_task(task_id, record_id, params_json=common.params_to_json(params))

    _LOOP_HOLDER["loop"] = asyncio.get_running_loop()
    cancel_event = threading.Event()
    try:
        await task_queue.submit(
            task_id,
            lambda: _run_restore_task(
                task_id,
                input_path,
                params,
                history_db,
                model_manager,
                checkpoint,
                cancel_event,
            ),
            on_cancel=cancel_event.set,
        )
    except TaskQueueFullError as exc:
        await history_db.update_record(record_id, status="failed", error=str(exc))
        await history_db.update_task(task_id, status="failed", error=str(exc))
        raise HTTPException(status_code=503, detail=str(exc))

    return respond_success({"task_id": task_id, "status": "pending"})
