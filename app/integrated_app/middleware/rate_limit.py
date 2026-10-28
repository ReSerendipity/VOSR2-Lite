#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""请求速率限制中间件（滑动窗口，按客户端 IP 计数）。

默认 30 次/分钟（config.yaml runtime.security.rate_limit_per_minute）；
GET 请求限额放宽（默认 10 倍，封顶 600/min）——历史页一屏就是 20 张缩略图，
加上预览图与静态资源，按 POST 的配额算会误伤正常浏览。

静态资源（/static/*、favicon）不计入配额：它们由浏览器缓存、
不触碰业务逻辑，计入只会让"多开几个页面"就触发限流。
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

_GET_LIMIT_FACTOR = 10
_GET_LIMIT_CAP = 600
_SKIP_PREFIXES = ("/static/", "/favicon.ico")


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self, app, rate_limit_per_minute: int = 30, get_rate_limit_per_minute: int | None = None
    ):
        super().__init__(app)
        self._limit = max(1, rate_limit_per_minute)
        self._get_limit = get_rate_limit_per_minute or min(
            self._limit * _GET_LIMIT_FACTOR, _GET_LIMIT_CAP
        )
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._window = 60.0

    def _client_key(self, request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(_SKIP_PREFIXES):
            return await call_next(request)

        # 读写分开计数：浏览页面产生的大量 GET 不能挤占上传/推理的写配额
        bucket = "get" if request.method in ("GET", "HEAD") else "write"
        limit = self._get_limit if bucket == "get" else self._limit
        key = (self._client_key(request), bucket)
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self._window:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = int(self._window - (now - hits[0])) + 1
            return JSONResponse(
                status_code=429,
                content={
                    "success": False,
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "请求过于频繁，请稍后再试",
                        "detail": {},
                    },
                },
                headers={"Retry-After": str(retry_after)},
            )
        hits.append(now)
        return await call_next(request)
