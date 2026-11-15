#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""修复路由公共工具：参数解析与任务状态读写。"""

from __future__ import annotations

import json
import os
import time
import uuid

from fastapi import Form, HTTPException

from app.integrated_app.history_db import HistoryDB
from app.integrated_app.security.magic_check import ALLOWED_IMAGE_EXTENSIONS

# 预留位（TD-006）：视频修复**尚未实现**，全仓无任何读取点——`parse_restore_params`
# 只认图片参数，上传侧校验走 `security.magic_check.ALLOWED_IMAGE_EXTENSIONS`。
# 保留本常量唯一的作用：在动手前把「什么算视频」的扩展名口径钉住，免得实现时各写一份。
# 真正启用视频（即可被路由）需三条同时满足，缺一条就会出现「界面能选、后端静默失败」：
#   1) `security/magic_check.py` 增加对应容器的魔数校验（按内容判，不只看后缀）；
#   2) `history_db.HistoryRecord.task_type` 的 `"video"` 分支真正落库并在读路径还原；
#   3) 引擎侧支持逐帧寻址 + 音频轨回灌（当前 restore 管线按单帧图像建任务）。
# 三条在 2027-06-30 前仍未满足的，按 TD-006 处置方向直接删除本常量。
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}


def new_task_id() -> str:
    return uuid.uuid4().hex[:16]


def parse_restore_params(
    upscale: int = Form(4),
    steps: int = Form(1),
    align_method: str = Form("wavelet"),
    seed: int = Form(42),
    tile_size: int = Form(0),
    vae_tile_size: int = Form(0),
    checkpoint: str = Form(""),
) -> dict:
    """解析并校验修复表单参数（返回 dict，缺省值由 config 兜底）。"""
    if upscale not in (1, 2, 3, 4):
        raise HTTPException(status_code=400, detail="upscale 仅支持 1/2/3/4")
    if align_method not in ("wavelet", "adain", "nofix"):
        raise HTTPException(status_code=400, detail="align_method 非法")
    if steps < 1 or steps > 50:
        raise HTTPException(status_code=400, detail="steps 须在 1-50 之间")
    if seed < -1:
        raise HTTPException(status_code=400, detail="seed 须 >= -1（-1 为随机）")
    if tile_size < 0 or vae_tile_size < 0:
        raise HTTPException(status_code=400, detail="分块尺寸不能为负")
    return {
        "upscale": upscale,
        "steps": steps,
        "align_method": align_method,
        "seed": seed,
        "tile_size": tile_size,
        "vae_tile_size": vae_tile_size,
        "checkpoint": checkpoint.strip(),
    }


async def get_task_state(task_id: str, history_db: HistoryDB) -> dict | None:
    return await history_db.get_task(task_id)


async def update_task_state(task_id: str, history_db: HistoryDB, **kwargs) -> None:
    await history_db.update_task(task_id, **kwargs)


def is_image(filename: str) -> bool:
    return os.path.splitext(filename)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def validate_image_extension(filename: str) -> None:
    if not is_image(filename):
        raise HTTPException(
            status_code=400,
            detail=f"不支持的图片格式（支持: {', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS))}）",
        )


def params_to_json(params: dict) -> str:
    return json.dumps(params, ensure_ascii=False)


def now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
