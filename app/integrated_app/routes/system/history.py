#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""历史记录路由：分页列表 / 删除。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.integrated_app.dependencies import get_history_db
from app.integrated_app.history_db import HistoryDB
from app.integrated_app.utils.response import respond_success

router = APIRouter(prefix="/api/system", tags=["系统"])


@router.get("/history")
async def list_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
    history_db: HistoryDB = Depends(get_history_db),
):
    records, total = await history_db.get_records(
        page=page, page_size=page_size, status=status)
    return respond_success({
        "records": records,
        "total": total,
        "page": page,
        "page_size": page_size,
    })


@router.delete("/history/{record_id}")
async def delete_history(record_id: int,
                         history_db: HistoryDB = Depends(get_history_db)):
    ok = await history_db.delete_record(record_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    return respond_success({"deleted": record_id})
