#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""CLI 直调服务层：复用 Web 后端的 Service 对象，不重复实现引擎逻辑。

与散装脚本（scripts/smoke_infer.py / run_engine_eval.py）的关键差异：
- 统一走 ``ModelManager.get_engine``，继承权重 sha256 校验 / 设备配置 /
  空闲卸载等治理能力；散装脚本直接实例化 VOSR2Engine 绕过了这些保护。
- 批量推理一次加载引擎，逐张失败不影响后续（与 run_engine_eval 一致）。
- 不 import FastAPI，纯 CLI 环境可直接调用。
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from app.integrated_app.config import AppConfig, load_config
from app.integrated_app.model_manager import ModelManager

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

ProgressCb = Callable[[str, float], None]


def _make_model_manager(config_path: str | None = None) -> tuple[AppConfig, ModelManager]:
    data = load_config(config_path)
    config = AppConfig(data)
    return config, ModelManager(config)


def _build_params(args: Any):
    from app.integrated_app.engines.vosr2_engine import RestoreParams

    return RestoreParams(
        upscale=int(getattr(args, "upscale", 4)),
        steps=int(getattr(args, "steps", 1)),
        align_method=str(getattr(args, "align_method", "wavelet")),
        seed=int(getattr(args, "seed", 42)),
        tile_size=int(getattr(args, "tile_size", 0)),
        vae_tile_size=int(getattr(args, "vae_tile_size", 0)),
    )


def _restore_one(engine, source: str, target_dir: str, params, suffix: str = "") -> dict[str, Any]:
    with Image.open(source) as img:
        image = img.convert("RGB")
    started = time.time()
    result = engine.restore(image, params)
    stem = Path(source).stem
    out_path = os.path.join(target_dir, f"{stem}{suffix}.png")
    result.save(out_path)
    return {
        "input": source,
        "output": out_path,
        "size": list(result.size),
        "elapsed": round(time.time() - started, 2),
    }


def restore(
    inputs: list[str],
    output_dir: str,
    *,
    checkpoint: str | None = None,
    config_path: str | None = None,
    args: Any,
    progress_cb: ProgressCb | None = None,
) -> dict[str, Any]:
    """单图/批量修复（一次加载引擎）。返回 success/failed/results。"""
    for path in inputs:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"输入文件不存在: {path}")
    os.makedirs(output_dir, exist_ok=True)

    config, manager = _make_model_manager(config_path)
    engine = manager.get_engine(checkpoint, progress_cb=progress_cb)
    params = _build_params(args)

    results: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for index, source in enumerate(inputs, start=1):
        if progress_cb:
            progress_cb("batch", index / len(inputs))
        try:
            results.append(_restore_one(engine, source, output_dir, params))
        except Exception as exc:
            failed.append({"input": source, "error": str(exc)})
    return {
        "checkpoint": checkpoint or config.get("model.default_checkpoint", "VOSR2"),
        "succeeded": len(results),
        "failed": len(failed),
        "results": results,
        "failures": failed,
        "output_dir": output_dir,
    }


def models_status(*, config_path: str | None = None) -> dict[str, Any]:
    """盘查各 checkpoint 的权重完整性（走 ModelManager.scan）。"""
    config, manager = _make_model_manager(config_path)
    infos = manager.scan()
    engine_name = manager.engine_name or None
    items = []
    for info in infos:
        items.append(
            {
                "name": info.name,
                "display_name": info.display_name,
                "type": info.ckpt_type,
                "present": info.present,
                "missing": info.missing,
                "min_vram_gb": info.min_vram_gb,
                "default_steps": info.default_steps,
            }
        )
    return {
        "model_root": manager.model_root,
        "default_checkpoint": config.get("model.default_checkpoint", "VOSR2"),
        "loaded": engine_name,
        "checkpoints": items,
    }


def system_status(*, config_path: str | None = None) -> dict[str, Any]:
    """GPU / 权重 / 关键配置摘要，供自动化巡检。不 import torch 时也能给出配置部分。"""
    config, manager = _make_model_manager(config_path)
    infos = manager.scan()
    payload: dict[str, Any] = {
        "model_root": manager.model_root,
        "device": config.get("model.device", "auto"),
        "checkpoints_present": sum(1 for i in infos if i.present),
        "checkpoints_total": len(infos),
        "max_output_side": config.get("inference.max_output_side"),
        "server_host": config.get("server.host"),
        "server_port": config.get("server.port"),
    }
    try:
        import torch

        payload["torch_version"] = torch.__version__
        payload["cuda_available"] = bool(torch.cuda.is_available())
        if torch.cuda.is_available():
            payload["gpu_name"] = torch.cuda.get_device_name(0)
            payload["vram_gb"] = round(
                torch.cuda.get_device_properties(0).total_memory / 1024**3, 1
            )
    except ImportError:
        payload["torch_version"] = None
        payload["cuda_available"] = False
        payload["note"] = "torch 未安装：环境未就绪，请到下载管理页安装"
    return payload


def history_list(
    *,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    """分页查询历史记录（复用 HistoryDB）。"""
    config = AppConfig(load_config(config_path))
    db_path = config.abspath(config.get("history.db_path", "data/history.db"))
    if not os.path.isfile(db_path):
        return {"records": [], "total": 0, "page": page, "page_size": page_size, "db_path": db_path}

    from app.integrated_app.history_db import HistoryDB

    async def _query() -> tuple[list[dict], int]:
        db = HistoryDB(db_path)
        await db.initialize()
        try:
            return await db.get_records(page=page, page_size=page_size, status=status)
        finally:
            await db.close()

    records, total = asyncio.run(_query())
    return {
        "records": records,
        "total": total,
        "page": page,
        "page_size": page_size,
        "db_path": db_path,
    }


def history_show(record_id: int, *, config_path: str | None = None) -> dict[str, Any]:
    """查询单条历史记录详情。"""
    config = AppConfig(load_config(config_path))
    db_path = config.abspath(config.get("history.db_path", "data/history.db"))
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"历史库不存在: {db_path}")

    from app.integrated_app.history_db import HistoryDB

    async def _query() -> dict | None:
        db = HistoryDB(db_path)
        await db.initialize()
        try:
            return await db.get_record(record_id)
        finally:
            await db.close()

    record = asyncio.run(_query())
    if record is None:
        raise ValueError(f"历史记录不存在: id={record_id}")
    return {"record": record}


__all__ = [
    "restore",
    "models_status",
    "system_status",
    "history_list",
    "history_show",
]
