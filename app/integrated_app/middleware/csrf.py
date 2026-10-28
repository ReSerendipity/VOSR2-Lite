#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""CSRF 保护中间件（Double Submit Cookie，移植自家族仓 SeedVR2-Lite，默认关闭）。

默认 ``runtime.security.csrf.enable=false``——本地回环单用户场景下 CSRF 收益
很低，开关打开后才会生效。启用场景：把服务暴露到局域网/公网、或有多标签页
与第三方页面共存的部署。

工作方式：
1. 安全方法（GET/HEAD/OPTIONS）响应时补种 ``csrf_token`` cookie（HMAC 签名）；
2. 前端读 cookie，在非安全请求里通过 ``X-CSRF-Token`` 头回传（app.js 已实现）；
3. 服务端校验「cookie 存在且签名合法」+「cookie == header」双条件。

关键设计：
- token = ``nonce.HMAC-SHA256(server_secret, nonce)``，密钥持久化在
  ``data/.vosr2_secret``（见 security/secret_key.py），攻击者无法伪造；
- cookie 缺失或签名失效时，即使是 GET 也补种新 token —— 浏览器里残留的坏
  token 会被自动替换，避免「一次写坏永久 403」的自锁；
- ``SameSite=Strict`` + HTTPS 下 ``Secure``，双保险；
- 白名单：文档路径、静态资源，以及 ``POST /api/system/locale``（只改显示语言，
  无副作用，前端首屏可能在没有 token 时调用）。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double Submit Cookie 形式的 CSRF 防护。"""

    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
    CSRF_COOKIE_NAME = "csrf_token"
    CSRF_HEADER_NAME = "X-CSRF-Token"
    SKIP_PATHS = ("/docs", "/openapi.json", "/redoc", "/static/")
    # 低风险的非安全方法豁免（仅改显示语言，无持久副作用）
    EXEMPT_POST_PATTERNS = (re.compile(r"^/api/system/locale$"),)

    @staticmethod
    def _is_secure_request(request: Request) -> bool:
        """是否 HTTPS（含反向代理的 X-Forwarded-Proto）。"""
        if request.url.scheme == "https":
            return True
        return "https" in request.headers.get("x-forwarded-proto", "").lower()

    @staticmethod
    def _generate_signed_token() -> str:
        """生成 ``nonce.HMAC-SHA256(secret, nonce)`` 形式的签名 token。"""
        from app.integrated_app.security.secret_key import get_secret_key

        nonce = secrets.token_hex(32)
        signature = hmac.new(get_secret_key(), nonce.encode(), hashlib.sha256).hexdigest()
        return f"{nonce}.{signature}"

    @staticmethod
    def _verify_signed_token(token: str) -> bool:
        """校验 token 签名（常量时间比较）。"""
        from app.integrated_app.security.secret_key import get_secret_key

        if not token or "." not in token:
            return False
        nonce, signature = token.rsplit(".", 1)
        expected = hmac.new(get_secret_key(), nonce.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)

    def _has_valid_cookie(self, request: Request) -> bool:
        token = request.cookies.get(self.CSRF_COOKIE_NAME)
        return token is not None and self._verify_signed_token(token)

    def _set_csrf_cookie(self, response: Response, request: Request) -> None:
        response.set_cookie(
            self.CSRF_COOKIE_NAME,
            self._generate_signed_token(),
            httponly=False,  # 前端 JS 必须能读到它才能回传 header
            samesite="strict",
            secure=self._is_secure_request(request),
            path="/",
        )

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in self.SAFE_METHODS:
            response: Response = await call_next(request)
            if not self._has_valid_cookie(request):
                self._set_csrf_cookie(response, request)
            return response

        if any(request.url.path.startswith(p) for p in self.SKIP_PATHS):
            return await call_next(request)

        if any(p.match(request.url.path) for p in self.EXEMPT_POST_PATTERNS):
            return await call_next(request)

        cookie_token = request.cookies.get(self.CSRF_COOKIE_NAME)
        header_token = request.headers.get(self.CSRF_HEADER_NAME)

        if (
            self._has_valid_cookie(request)
            and cookie_token is not None
            and header_token is not None
            and secrets.compare_digest(cookie_token, header_token)
        ):
            return await call_next(request)

        logger.warning("CSRF 验证失败: %s %s", request.method, request.url.path)
        response = JSONResponse(
            status_code=403,
            content={
                "success": False,
                "error": {
                    "code": "CSRF_INVALID",
                    "message": "CSRF token 验证失败",
                    "detail": {},
                },
            },
        )
        # 失败也补种：浏览器里的失效 cookie 下一次请求即可自愈
        self._set_csrf_cookie(response, request)
        return response


def should_enable_csrf(config: dict) -> bool:
    """按 ``runtime.security.csrf.enable`` 判断是否启用（默认关闭）。"""
    security = config.get("runtime", {}).get("security", {}) or {}
    csrf_cfg = security.get("csrf", {}) or {}
    return bool(csrf_cfg.get("enable", False))
