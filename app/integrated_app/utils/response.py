#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""统一响应包装工具（家族模式）。

成功: {"success": true, "data": ..., **extra}
失败: {"success": false, "error": {"code", "message", "detail"}}
"""

from typing import Any

from fastapi.responses import JSONResponse


def respond_success(data: Any = None, status: int = 200, **extra: Any) -> JSONResponse:
    body: dict[str, Any] = {"success": True}
    if data is not None:
        body["data"] = data
    body.update(extra)
    return JSONResponse(status_code=status, content=body)


def respond_error(
    code: str,
    message: str,
    status: int = 400,
    detail: dict[str, Any] | list[Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "success": False,
        "error": {"code": code, "message": message, "detail": detail or {}},
    }
    return JSONResponse(status_code=status, content=body)
