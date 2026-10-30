#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""下载管理：运行时与模型的就绪检测、组件清单。

为什么单独一个模块：瘦包（约 36 MB）不含 Python 运行时与模型权重，用户装完
必须先在程序内的下载管理页里安装它们，才能开始推理。本模块是该页面的数据后端。

硬约束（改动前务必读完）：
    **禁止在本模块顶层 import torch。**

首次启动时后端跑在**引导解释器**上（只有 Web 依赖、没有 torch）。若本模块因缺
torch 而导入失败，下载管理页就打不开，用户会陷入「装了软件却没法装环境」的死局
——而这条链路恰恰是瘦包唯一的自救通道。因此：

    - torch 版本 / CUDA 版本一律从 `runtime/RUNTIME.json` 元信息文件读，
      不启动子进程、不 import torch（启动一次 torch 要数秒，不适合状态轮询）；
    - 确实需要探测的 torch 信息，放进函数内 try/except 且不在本模块使用。

元信息文件由环境包构建流程写入（scripts/build_desktop_bundle.ps1 的
runtime 模式），字段见 RUNTIME_META_SCHEMA。
"""

from __future__ import annotations

import json
import logging
import os
import sys

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.integrated_app.config import PROJECT_ROOT
from app.integrated_app.dependencies import get_config
from app.integrated_app.download_service import (
    _runtime_dir,
    catalog_view,
    get_download_manager,
    uninstall_model,
    uninstall_runtime,
)
from app.integrated_app.utils.response import respond_error, respond_success
from app.integrated_app.version import get_app_version

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/downloads", tags=["下载管理"])

# 与 system/shutdown.py 同一套回环白名单：下载/删除是重操作，只认本机
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"})


def _deny_remote(request: Request):
    client_host = (request.client.host if request.client else "") or ""
    if client_host not in LOOPBACK_HOSTS:
        logger.warning(f"拒绝非本机下载管理请求，来源: {client_host or '<unknown>'}")
        return respond_error(code="FORBIDDEN", message="仅允许本机调用", status=403)
    return None


class StartRequest(BaseModel):
    kind: str = Field(description="runtime | model")
    target: str = Field(default="", description="kind=model 时的 checkpoint 名")


class ComponentRequest(BaseModel):
    kind: str = Field(description="runtime | model")
    target: str = Field(default="", description="kind=model 时的 checkpoint 名")


RUNTIME_DIRNAME = "runtime"
BOOTSTRAP_DIRNAME = "bootstrap"
RUNTIME_META_NAME = "RUNTIME.json"

# 环境包元信息字段（构建期写入；缺字段按 None 处理，不因个别字段缺失判为不可用）
RUNTIME_META_SCHEMA = (
    "runtime_version",  # 环境包版本（形如 1）；应用侧用它判兼容
    "python",  # Python 版本，如 3.12.10
    "torch",  # torch 版本，如 2.10.0+cu128
    "cuda",  # CUDA 标签，如 cu128
    "built_at",  # 构建时间（UTC，ISO8601）
    "min_app_version",  # 该环境包支持的最低应用版本
)


def _exe_name() -> str:
    return "python.exe" if os.name == "nt" else "python"


def _dir_stats(path: str) -> tuple[int, int]:
    """返回（文件数，总字节）。目录不存在或不可读时返回 (0, 0)。"""
    files = 0
    total = 0
    if not os.path.isdir(path):
        return 0, 0
    for root, _dirs, names in os.walk(path):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(root, name))
                files += 1
            except OSError:
                continue
    return files, total


def _read_runtime_meta() -> dict | None:
    meta_path = os.path.join(_runtime_dir(), RUNTIME_META_NAME)
    if not os.path.isfile(meta_path):
        return None
    try:
        with open(meta_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _current_mode() -> str:
    """当前后端跑在哪个解释器上。

    - `full`：打包态完整运行时 `app/runtime/python.exe`
    - `bootstrap`：瘦包内置引导解释器 `app/bootstrap/python.exe`（无 torch）
    - `system`：系统 Python（开发态或兜底）
    """
    exe = os.path.abspath(sys.executable or "")
    if not exe:
        return "system"
    runtime_dir = os.path.abspath(os.path.join(PROJECT_ROOT, RUNTIME_DIRNAME))
    bootstrap_dir = os.path.abspath(os.path.join(PROJECT_ROOT, BOOTSTRAP_DIRNAME))
    if exe.startswith(runtime_dir + os.sep) or exe == runtime_dir:
        return "full"
    if exe.startswith(bootstrap_dir + os.sep) or exe == bootstrap_dir:
        return "bootstrap"
    return "system"


def _version_ge(actual: str | None, required: str | None) -> bool:
    """比较 x.y.z 版本：actual >= required 返回 True。缺值一律视为兼容（不阻断）。"""
    if not actual or not required:
        return True

    def parts(text: str) -> tuple[int, ...]:
        out: list[int] = []
        for chunk in str(text).split(".")[:3]:
            digits = "".join(ch for ch in chunk if ch.isdigit())
            out.append(int(digits) if digits else 0)
        return tuple(out)

    return parts(actual) >= parts(required)


def build_runtime_status() -> dict:
    runtime_dir = _runtime_dir()
    exe = os.path.join(runtime_dir, _exe_name())
    meta = _read_runtime_meta() or {}
    present = os.path.isfile(exe)
    files, size = _dir_stats(runtime_dir) if os.path.isdir(runtime_dir) else (0, 0)
    app_version = get_app_version()
    min_app = meta.get("min_app_version")
    return {
        "present": present,
        "path": runtime_dir,
        "executable": exe if present else None,
        "python_version": meta.get("python"),
        "torch_version": meta.get("torch"),
        "cuda": meta.get("cuda"),
        "runtime_version": meta.get("runtime_version"),
        "built_at": meta.get("built_at"),
        "file_count": files,
        "size_bytes": size,
        # 兼容判定：环境包声明的 min_app_version 必须 <= 当前应用版本
        "compatible": _version_ge(app_version, min_app) if present else True,
        "min_app_version": min_app,
    }


def scan_models(config: dict) -> list[dict]:
    """扫描权重目录，返回各 checkpoint 的就位情况（不加载权重、不 import torch）。"""
    model_cfg = config.get("model", {}) or {}
    rel = model_cfg.get("pretrained_dir", "model")
    root = rel if os.path.isabs(rel) else os.path.join(PROJECT_ROOT, rel)
    checkpoints = model_cfg.get("checkpoints", {}) or {}
    default = model_cfg.get("default_checkpoint", "")
    out: list[dict] = []
    for key, spec in checkpoints.items():
        if not isinstance(spec, dict):
            spec = {}
        path = os.path.join(root, str(key))
        files, size = _dir_stats(path)
        out.append(
            {
                "name": str(key),
                "display_name": spec.get("name", str(key)),
                "type": spec.get("type", ""),
                "min_vram_gb": spec.get("min_vram_gb", 0),
                "present": files > 0,
                "path": path,
                "file_count": files,
                "size_bytes": size,
                "is_default": str(key) == default,
            }
        )
    out.sort(key=lambda item: (not item["is_default"], item["name"]))
    return out


@router.get("/status")
async def status(config: dict = Depends(get_config)):
    """下载管理页的整体状态：当前运行模式、运行时就位情况、各模型就位情况。"""
    runtime = build_runtime_status()
    models = scan_models(config)
    app_version = get_app_version()
    ready = bool(runtime["present"] and runtime["compatible"] and any(m["present"] for m in models))
    return respond_success(
        {
            "app_version": app_version,
            "mode": _current_mode(),
            "ready": ready,
            "runtime": runtime,
            "models": models,
            "model_root": os.path.join(
                PROJECT_ROOT, str((config.get("model", {}) or {}).get("pretrained_dir", "model"))
            ),
        }
    )


@router.get("/catalog")
async def catalog(config: dict = Depends(get_config)):
    """可下载组件清单：环境包（含分卷与校验和）+ 各 checkpoint 的就位情况。"""
    return respond_success(catalog_view(config))


@router.post("/start")
async def start(request: Request, payload: StartRequest):
    """启动一个下载任务。重复调用同一组件时返回既有任务（不会并行下两遍）。"""
    denied = _deny_remote(request)
    if denied:
        return denied
    kind = payload.kind.strip()
    if kind not in ("runtime", "model"):
        return respond_error(code="BAD_REQUEST", message="kind 必须是 runtime 或 model", status=400)
    if kind == "model" and not payload.target.strip():
        return respond_error(code="BAD_REQUEST", message="kind=model 必须指定 target", status=400)
    if kind == "runtime":
        from app.integrated_app.download_service import runtime_catalog

        if not runtime_catalog():
            return respond_error(
                code="NO_CATALOG",
                message="未找到环境包清单 downloads_catalog.json，无法确定下载地址",
                status=503,
            )
    task = get_download_manager().start(kind, payload.target.strip())
    return respond_success(task.to_dict())


@router.get("/progress")
async def progress():
    """全部下载任务的进度快照（前端轮询）。"""
    return respond_success({"tasks": get_download_manager().progress()})


@router.post("/cancel")
async def cancel(request: Request, payload: ComponentRequest):
    """请求取消：置取消标志，下载循环在下一次读块时退出。"""
    denied = _deny_remote(request)
    if denied:
        return denied
    component = "runtime" if payload.kind == "runtime" else f"model:{payload.target.strip()}"
    return respond_success({"cancelled": get_download_manager().cancel(component)})


@router.post("/uninstall")
async def uninstall(
    request: Request, payload: ComponentRequest, config: dict = Depends(get_config)
):
    """卸载组件：删除 app/runtime/ 或某个 checkpoint 的权重目录。

    共享资产（VAE / torch_cache）会被其他 checkpoint 引用，故只删目标目录本身。
    """
    denied = _deny_remote(request)
    if denied:
        return denied
    kind = payload.kind.strip()
    if kind == "runtime":
        uninstall_runtime()
        return respond_success({"uninstalled": "runtime"})
    if kind == "model" and payload.target.strip():
        uninstall_model(payload.target.strip(), config)
        return respond_success({"uninstalled": f"model:{payload.target.strip()}"})
    return respond_error(
        code="BAD_REQUEST", message="kind 必须是 runtime 或 model+target", status=400
    )
