#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""安全响应头中间件（CSP / X-Content-Type-Options / X-Frame-Options 等）。

置于中间件栈最外层（add_middleware 后添加 = 最先执行），
确保所有响应（含 401/403/429）均携带安全头。
默认 CSP 保留 'unsafe-inline' 以兼容模板内联脚本（与 base.html 的
meta CSP 保持一致：浏览器对两份策略取交集）。
"""

from __future__ import annotations

from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

_DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob:; "
    "connect-src 'self'; "
    "font-src 'self' data:; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

_BASE_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, config: Any = None) -> None:
        super().__init__(app)
        self._config = config

    def _headers_config(self) -> dict[str, Any]:
        if not isinstance(self._config, dict):
            return {}
        return ((self._config.get("runtime") or {}).get("security") or {}
                .get("headers") or {})

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        headers_cfg = self._headers_config()
        enabled = True if not headers_cfg else bool(headers_cfg.get("enabled", True))
        if not enabled:
            return response
        for key, value in _BASE_HEADERS.items():
            response.headers.setdefault(key, value)
        csp = headers_cfg.get("csp", "") if headers_cfg else ""
        response.headers.setdefault("Content-Security-Policy", csp or _DEFAULT_CSP)
        return response


__all__ = ["SecurityHeadersMiddleware", "_DEFAULT_CSP"]
