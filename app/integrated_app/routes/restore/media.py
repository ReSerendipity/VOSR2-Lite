#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""任务媒体路由：原图 / 预览缩略图 / 结果下载（含格式转换）。

- GET /api/restore/{task_id}/input     原图（前后对比左侧）
- GET /api/restore/{task_id}/preview   缩放预览（kind=input|output，磁盘缓存）
- GET /api/restore/{task_id}/download  下载结果（format=png|jpeg|webp 即时转换）

预览的意义：6144² 输出 PNG 可达 45 MB，直接塞进浏览器做对比会卡顿数秒；
统一先缩到 max 边长再传输（默认 1600，约 300 KB），需要看细节时前端再走
原图 + 缩放查看器。缓存落在 data/cache/previews/，按 (kind, task, max) 命名。
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from loguru import logger
from PIL import Image, ImageOps

from app.integrated_app.config import PROJECT_ROOT
from app.integrated_app.dependencies import get_config, get_history_db
from app.integrated_app.history_db import HistoryDB
from app.integrated_app.security.path_guard import build_default_path_guard
from app.integrated_app.utils.imaging import build_thumbnail, probe_alpha, thumbnail_path

router = APIRouter(prefix="/api/restore", tags=["修复"])

# task_id 由服务端生成（uuid4().hex[:16]），严格校验可杜绝路径注入
_TASK_ID_RE = re.compile(r"^[0-9a-f]{16}$")

_PREVIEW_DIR = os.path.join(PROJECT_ROOT, "data", "cache", "previews")
_EXPORT_DIR = os.path.join(PROJECT_ROOT, "data", "cache", "exports")

# 格式 -> (媒体类型, Pillow 保存格式)
_FORMATS: dict[str, tuple[str, str]] = {
    "png": ("image/png", "PNG"),
    "jpeg": ("image/jpeg", "JPEG"),
    "webp": ("image/webp", "WEBP"),
}

_IMMUTABLE_CACHE = {"Cache-Control": "private, max-age=86400"}


def _require_task_id(task_id: str) -> str:
    if not _TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="task_id 非法")
    return task_id


async def _load_task(task_id: str, history_db: HistoryDB) -> dict:
    task = await history_db.get_task(_require_task_id(task_id))
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


def _guard(config: dict):
    allowed = config.get("runtime", {}).get("security", {}).get(
        "allowed_base_dirs", ["outputs/", "data/uploads/"])
    return build_default_path_guard(PROJECT_ROOT, allowed)


def _checked_file(path: str, guard, missing_msg: str) -> str:
    if not path:
        raise HTTPException(status_code=404, detail=missing_msg)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=missing_msg)
    if not guard.is_safe_path(path):
        logger.warning(f"路径不在允许范围: {path}")
        raise HTTPException(status_code=403, detail="不允许访问该路径")
    return path


async def _input_path(task: dict, history_db: HistoryDB) -> str:
    """任务对应的原图路径（tasks 表不存输入，经 record_id 关联 history）。"""
    record_id = task.get("record_id")
    if not record_id:
        raise HTTPException(status_code=404, detail="该任务没有关联的原图记录")
    record = await history_db.get_record(int(record_id))
    if not record:
        raise HTTPException(status_code=404, detail="原图记录已被删除")
    return record.get("input_file") or ""


def _convert(src: str, fmt: str, quality: int) -> str:
    """按目标格式转换并写入临时文件，返回路径（调用方负责删除）。"""
    os.makedirs(_EXPORT_DIR, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=_EXPORT_DIR, suffix=f".{fmt}")
    os.close(fd)
    with Image.open(src) as raw:
        img = ImageOps.exif_transpose(raw)
        if fmt == "jpeg":
            # JPEG 无 alpha：铺白底，避免透明区域变黑
            if img.mode in ("RGBA", "LA", "P"):
                rgba = img.convert("RGBA")
                canvas = Image.new("RGB", rgba.size, (255, 255, 255))
                canvas.paste(rgba, mask=rgba.split()[-1])
                img = canvas
            else:
                img = img.convert("RGB")
            img.save(tmp_path, "JPEG", quality=quality, optimize=True,
                     progressive=True, subsampling=0)
        elif fmt == "webp":
            img.save(tmp_path, "WEBP", quality=quality, method=4)
        else:
            img.save(tmp_path, "PNG", optimize=True)
    return tmp_path


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


@router.get("/{task_id}/input")
async def get_input_image(
    task_id: str,
    history_db: HistoryDB = Depends(get_history_db),
    config: dict = Depends(get_config),
):
    """原图（对比查看器左侧；浏览器按 EXIF 方向渲染）。"""
    task = await _load_task(task_id, history_db)
    src = _checked_file(await _input_path(task, history_db), _guard(config),
                        "原图文件不存在")
    return FileResponse(src, media_type=None, headers=_IMMUTABLE_CACHE,
                        filename=os.path.basename(src))


@router.get("/{task_id}/preview")
async def get_preview(
    task_id: str,
    kind: str = Query("output", pattern="^(input|output)$"),
    max_side: int = Query(1600, ge=128, le=4096, alias="max"),
    history_db: HistoryDB = Depends(get_history_db),
    config: dict = Depends(get_config),
):
    """缩放预览（带磁盘缓存）：kind=input 原图，kind=output 结果。"""
    task = await _load_task(task_id, history_db)
    guard = _guard(config)

    if kind == "input":
        src = _checked_file(await _input_path(task, history_db), guard,
                            "原图文件不存在")
    else:
        if task.get("status") != "completed":
            raise HTTPException(status_code=400, detail="任务尚未完成")
        src = _checked_file(task.get("output_path") or "", guard,
                            "结果文件不存在")

    os.makedirs(_PREVIEW_DIR, exist_ok=True)
    stem = f"{kind}_{task_id}_{max_side}"
    try:
        alpha = await asyncio.to_thread(probe_alpha, src)
    except Exception as exc:  # 损坏 / 非图片
        logger.warning(f"预览失败，无法读取 {src}: {exc}")
        raise HTTPException(status_code=422, detail="图片无法解析") from exc

    dest = thumbnail_path(_PREVIEW_DIR, stem, alpha)
    if not os.path.isfile(dest):
        # Pillow 解码 6000² 级图片要几百毫秒，放线程里避免阻塞事件循环
        await asyncio.to_thread(build_thumbnail, src, dest, max_side)

    return FileResponse(dest,
                        media_type="image/png" if alpha else "image/jpeg",
                        headers=_IMMUTABLE_CACHE)


@router.get("/{task_id}/download")
async def download_result(
    task_id: str,
    background: BackgroundTasks,
    fmt: str = Query("", alias="format",
                     pattern="^(|png|jpeg|webp)$"),
    quality: int = Query(92, ge=40, le=100),
    history_db: HistoryDB = Depends(get_history_db),
    config: dict = Depends(get_config),
):
    """下载结果；format 为空按原样（PNG），指定则即时转换（JPEG/WebP 体积小）。"""
    task = await _load_task(task_id, history_db)
    if task.get("status") != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成")
    src = _checked_file(task.get("output_path") or "", _guard(config),
                        "结果文件不存在")

    base = os.path.splitext(os.path.basename(src))[0]
    if not fmt:
        return FileResponse(src, media_type="image/png",
                            filename=f"{base}.png")

    media_type, _ = _FORMATS[fmt]
    tmp_path = await asyncio.to_thread(_convert, src, fmt, quality)
    background.add_task(_remove_quietly, tmp_path)
    return FileResponse(tmp_path, media_type=media_type,
                        filename=f"{base}.{fmt}",
                        background=background)
