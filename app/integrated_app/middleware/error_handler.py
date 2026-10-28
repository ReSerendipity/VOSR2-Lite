#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""全局错误处理中间件：统一捕获未处理异常，按信封返回 JSON。"""

from __future__ import annotations

import logging

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception:
            logger.exception(f"未处理异常: {request.method} {request.url.path}")
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error": {
                        "code": "INTERNAL_ERROR",
                        "message": "服务器内部错误，请查看日志",
                        "detail": {},
                    },
                },
            )


def register_error_handlers(app) -> None:
    """注册 HTTPException 与请求校验错误的统一信封处理器。"""
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc):
        detail = getattr(exc, "detail", None)
        if isinstance(detail, dict):
            message = str(detail.get("message", "请求失败"))
            extra = detail
        else:
            message = str(detail) if detail else "请求失败"
            extra = {}
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error": {"code": f"HTTP_{exc.status_code}", "message": message, "detail": extra},
            },
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc):
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "请求参数不合法",
                    "detail": exc.errors(),
                },
            },
        )
