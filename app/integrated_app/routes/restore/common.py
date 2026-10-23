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


async def update_task_state(task_id: str, history_db: HistoryDB,
                            **kwargs) -> None:
    await history_db.update_task(task_id, **kwargs)


def is_image(filename: str) -> bool:
    return os.path.splitext(filename)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def validate_image_extension(filename: str) -> None:
    if not is_image(filename):
        raise HTTPException(
            status_code=400,
            detail=f"不支持的图片格式（支持: {', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS))}）")


def params_to_json(params: dict) -> str:
    return json.dumps(params, ensure_ascii=False)


def now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
