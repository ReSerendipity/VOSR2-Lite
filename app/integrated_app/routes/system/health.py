#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""系统健康状态路由：GPU / 模型 / 权重完整性。"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends

from app.integrated_app.dependencies import get_config, get_model_manager
from app.integrated_app.model_manager import ModelManager
from app.integrated_app.utils.response import respond_success

router = APIRouter(prefix="/api/system", tags=["系统"])

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))


def _gpu_info() -> dict:
    try:
        import torch

        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            return {
                "is_gpu_available": True,
                "device_name": torch.cuda.get_device_name(0),
                "vram_total_gb": round(total / 1024**3, 1),
                "vram_free_gb": round(free / 1024**3, 1),
            }
    except Exception:
        pass
    return {"is_gpu_available": False, "device_name": "",
            "vram_total_gb": 0, "vram_free_gb": 0}


@router.get("/health")
async def health(model_manager: ModelManager = Depends(get_model_manager),
                 config: dict = Depends(get_config)):
    """综合健康状态（首页 Hero 与系统状态页使用）。"""
    gpu = await __import__("asyncio").to_thread(_gpu_info)
    engine = model_manager.engine
    checkpoints = model_manager.scan()
    return respond_success({
        "gpu": gpu,
        "model": {
            "model_loaded": bool(engine and engine.status.loaded),
            "current_checkpoint": model_manager.engine_name or None,
            "device": engine.status.device if engine else "",
            "load_seconds": engine.status.load_seconds if engine else 0,
            "vram_gb": engine.status.vram_gb if engine else 0,
        },
        "checkpoints": [
            {"name": c.name, "display_name": c.display_name,
             "present": c.present, "missing": c.missing,
             "min_vram_gb": c.min_vram_gb, "type": c.ckpt_type}
            for c in checkpoints
        ],
        "default_checkpoint": config.get("model", {}).get(
            "default_checkpoint", "VOSR2"),
    })
