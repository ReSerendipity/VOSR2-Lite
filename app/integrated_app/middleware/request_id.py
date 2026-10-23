#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""请求 ID 中间件：为每个请求注入 X-Request-ID 并挂到日志上下文。"""

from __future__ import annotations

import contextvars
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

REQUEST_ID_HEADER = "X-Request-ID"

# 请求 ID 上下文变量：中间件写入，日志过滤器读取
current_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-")


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        token = current_request_id.set(request_id)
        try:
            response = await call_next(request)
        finally:
            current_request_id.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class RequestIDLogFilter:
    """日志过滤器：从 contextvars 读取 request_id 注入日志记录。"""

    def filter(self, record) -> bool:
        record.request_id = current_request_id.get("-")
        return True
