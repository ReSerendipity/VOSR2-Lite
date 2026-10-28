#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""语言切换路由：设置当前语言（Cookie 持久化，非法值回退默认）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.integrated_app.dependencies import get_config, get_i18n
from app.integrated_app.i18n import I18n

router = APIRouter(prefix="/api/system", tags=["系统"])

LOCALE_COOKIE = "vosr2_locale"


@router.post("/locale")
async def set_locale(
    request: Request, i18n: I18n = Depends(get_i18n), config: dict = Depends(get_config)
):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    requested = str(body.get("locale", "")).strip()
    # 以实际加载到的语言包为准：config 里列了但没有对应 json 的语言无法切换，
    # config 缺失时也不能退化成硬编码的两语言（新增语言包后要自动可用）。
    configured = config.get("i18n", {}).get("available_locales") or []
    available = [loc for loc in configured if loc in i18n.available_locales] or (
        i18n.available_locales
    )
    if requested not in available:
        from app.integrated_app.utils.response import respond_error

        return respond_error(
            code="INVALID_LOCALE", message=f"不支持的语言: {requested}", status=400
        )
    i18n.set_locale(requested)
    response = JSONResponse(content={"success": True, "data": {"locale": requested}})
    response.set_cookie(
        LOCALE_COOKIE, requested, max_age=365 * 86400, path="/", httponly=False, samesite="lax"
    )
    return response
