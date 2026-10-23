#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""请求速率限制中间件（滑动窗口，按客户端 IP 计数）。

默认 30 次/分钟（config.yaml runtime.security.rate_limit_per_minute）；
GET 请求限额放宽为 4 倍（封顶 240/min），避免目录枚举挤占上传配额。
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

_GET_LIMIT_FACTOR = 4
_GET_LIMIT_CAP = 240


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, rate_limit_per_minute: int = 30,
                 get_rate_limit_per_minute: int | None = None):
        super().__init__(app)
        self._limit = max(1, rate_limit_per_minute)
        self._get_limit = get_rate_limit_per_minute or min(
            self._limit * _GET_LIMIT_FACTOR, _GET_LIMIT_CAP)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._window = 60.0

    def _client_key(self, request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next):
        key = self._client_key(request)
        limit = self._get_limit if request.method == "GET" else self._limit
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self._window:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = int(self._window - (now - hits[0])) + 1
            return JSONResponse(
                status_code=429,
                content={"success": False,
                         "error": {"code": "RATE_LIMITED",
                                   "message": "请求过于频繁，请稍后再试",
                                   "detail": {}}},
                headers={"Retry-After": str(retry_after)},
            )
        hits.append(now)
        return await call_next(request)
