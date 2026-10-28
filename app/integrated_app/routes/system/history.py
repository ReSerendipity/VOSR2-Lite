#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""历史记录路由：分页列表 / 缩略图 / 删除。"""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from loguru import logger

from app.integrated_app.config import PROJECT_ROOT
from app.integrated_app.dependencies import get_config, get_history_db
from app.integrated_app.history_db import HistoryDB
from app.integrated_app.security.path_guard import build_default_path_guard
from app.integrated_app.utils.imaging import build_thumbnail, probe_alpha, thumbnail_path
from app.integrated_app.utils.response import respond_success

router = APIRouter(prefix="/api/system", tags=["系统"])

_THUMB_DIR = os.path.join(PROJECT_ROOT, "data", "cache", "thumbs")


@router.get("/history")
async def list_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
    history_db: HistoryDB = Depends(get_history_db),
):
    records, total = await history_db.get_records(page=page, page_size=page_size, status=status)
    return respond_success(
        {
            "records": records,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@router.get("/history/{record_id}/thumb")
async def history_thumbnail(
    record_id: int,
    max_side: int = Query(320, ge=64, le=1024, alias="max"),
    history_db: HistoryDB = Depends(get_history_db),
    config: dict = Depends(get_config),
):
    """记录缩略图：优先结果图，没有结果时回退原图（失败记录也能看到预览）。"""
    record = await history_db.get_record(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")

    src = record.get("output_file") or record.get("input_file") or ""
    allowed = (
        config.get("runtime", {})
        .get("security", {})
        .get("allowed_base_dirs", ["outputs/", "data/uploads/"])
    )
    guard = build_default_path_guard(PROJECT_ROOT, allowed)
    if not src or not os.path.isfile(src) or not guard.is_safe_path(src):
        raise HTTPException(status_code=404, detail="缩略图源文件不存在")

    os.makedirs(_THUMB_DIR, exist_ok=True)
    stem = f"rec{record_id}_{max_side}"
    try:
        alpha = await asyncio.to_thread(probe_alpha, src)
    except Exception as exc:
        logger.warning(f"缩略图生成失败 {src}: {exc}")
        raise HTTPException(status_code=422, detail="图片无法解析") from exc

    dest = thumbnail_path(_THUMB_DIR, stem, alpha)
    if not os.path.isfile(dest):
        await asyncio.to_thread(build_thumbnail, src, dest, max_side)

    return FileResponse(
        dest,
        media_type="image/png" if alpha else "image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.delete("/history/{record_id}")
async def delete_history(record_id: int, history_db: HistoryDB = Depends(get_history_db)):
    ok = await history_db.delete_record(record_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    return respond_success({"deleted": record_id})
