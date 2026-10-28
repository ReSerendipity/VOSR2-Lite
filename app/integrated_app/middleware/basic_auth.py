#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""HTTP Basic Auth 中间件（移植自家族仓 SeedVR2-Lite，默认关闭）。

用途：服务绑到非回环地址（局域网 / 公网 / 容器端口映射）时阻止未授权访问
（CWE-306）。默认 ``runtime.security.auth.enable=false``，行为与未挂载本中间件
完全一致——所以移植后现有测试与本地开发体验零变化。

启用方式（config.yaml）::

    runtime:
      security:
        auth:
          enable: true
          username: admin
          password: "强密码"   # 建议改用环境变量注入，避免明文入库
          realm: VOSR2

环境变量（优先级高于 config.yaml；两者同时给出即视为启用，容器部署快捷通道）：
``VOSR2_AUTH_USERNAME`` / ``VOSR2_AUTH_PASSWORD``。

安全特性：
- ``hmac.compare_digest`` 常量时间比较，防时序攻击；
- 失败滑动窗口 + 临时封禁（CWE-307 防爆破）；
- 401 带 ``WWW-Authenticate``，浏览器自动弹凭据框；
- Basic Auth 凭据是 Base64 明文，公网部署必须叠加 HTTPS。
"""

from __future__ import annotations

import base64
import hmac
import logging
import math
import os
import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.integrated_app.utils.response import respond_error

logger = logging.getLogger(__name__)

_ENV_USERNAME = "VOSR2_AUTH_USERNAME"
_ENV_PASSWORD = "VOSR2_AUTH_PASSWORD"

# 单测可替换的单调时钟
_monotonic = time.monotonic


class AuthFailureTracker:
    """认证失败滑动窗口计数 + 临时 IP 封禁。

    窗口期内失败达到上限后，该 IP 被临时封禁 ``ban_seconds`` 秒；封禁期内直接
    拒绝（不再比对凭据，避免边爆破边消耗 CPU）。认证成功即清零。
    内存滑动窗口，单事件循环下无锁；条目超限时清理过期记录防无界增长。
    """

    _MAX_TRACKED_IPS = 1024

    def __init__(
        self, max_failures: int = 5, window_seconds: float = 300.0, ban_seconds: float = 600.0
    ):
        self.max_failures = int(max_failures)
        self.window_seconds = float(window_seconds)
        self.ban_seconds = float(ban_seconds)
        self._failures: dict[str, deque[float]] = {}
        self._banned: dict[str, float] = {}

    def _sweep(self, now: float) -> None:
        """内存护栏：条目超阈值时清理过期失败记录与已解封条目。"""
        if len(self._failures) + len(self._banned) <= self._MAX_TRACKED_IPS:
            return
        for ip in [
            i
            for i, hits in self._failures.items()
            if not hits or hits[-1] <= now - self.window_seconds
        ]:
            del self._failures[ip]
        for ip in [i for i, t in self._banned.items() if now - t >= self.ban_seconds]:
            del self._banned[ip]

    def is_banned(self, client_ip: str) -> bool:
        if self.max_failures <= 0:
            return False
        now = _monotonic()
        ban_start = self._banned.get(client_ip)
        if ban_start is not None:
            if now - ban_start < self.ban_seconds:
                return True
            del self._banned[client_ip]
            self._failures.pop(client_ip, None)
        self._sweep(now)
        return False

    def record_failure(self, client_ip: str) -> None:
        if self.max_failures <= 0:
            return
        now = _monotonic()
        hits = self._failures.setdefault(client_ip, deque())
        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()
        hits.append(now)
        if len(hits) >= self.max_failures and client_ip not in self._banned:
            self._banned[client_ip] = now
            self._failures.pop(client_ip, None)
            logger.error(
                "[SECURITY] 认证失败达 %s 次，IP %s 封禁 %ss",
                self.max_failures,
                client_ip,
                int(self.ban_seconds),
            )
        self._sweep(now)

    def record_success(self, client_ip: str) -> None:
        self._failures.pop(client_ip, None)
        self._banned.pop(client_ip, None)

    def retry_after_seconds(self, client_ip: str) -> int:
        ban_start = self._banned.get(client_ip)
        if ban_start is None:
            return 1
        return max(1, int(math.ceil(self.ban_seconds - (_monotonic() - ban_start))))


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """所有请求需携带 ``Authorization: Basic <base64(user:pass)>``。"""

    def __init__(
        self,
        app,
        username: str,
        password: str,
        realm: str = "VOSR2",
        max_auth_failures: int = 5,
        auth_failure_window_seconds: float = 300.0,
        auth_ban_seconds: float = 600.0,
    ):
        super().__init__(app)
        self._username = username
        # 环境变量优先于配置文件（避免密码明文入库）
        self._password = os.environ.get(_ENV_PASSWORD, password)
        self._realm = realm
        expected = f"{username}:{self._password}"
        self._expected_b64 = base64.b64encode(expected.encode("utf-8")).decode("ascii")
        self._failure_tracker = AuthFailureTracker(
            max_failures=max_auth_failures,
            window_seconds=auth_failure_window_seconds,
            ban_seconds=auth_ban_seconds,
        )
        logger.info("Basic Auth 已启用 (realm=%s, user=%s)", realm, username)

    @staticmethod
    def _client_ip(request: Request) -> str:
        """客户端 IP（与 rate_limit 同策略：默认不信 X-Forwarded-For）。"""
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next):
        client_ip = self._client_ip(request)

        # 先判封禁：封禁期内连凭据都不比对
        if self._failure_tracker.is_banned(client_ip):
            retry_after = self._failure_tracker.retry_after_seconds(client_ip)
            logger.warning(
                "认证封禁生效: %s %s from %s（剩余 %ss）",
                request.method,
                request.url.path,
                client_ip,
                retry_after,
            )
            response = respond_error(
                code="AUTH_BANNED",
                message="认证失败次数过多，已被临时封禁",
                status=429,
                detail={"retry_after_seconds": retry_after},
            )
            response.headers["Retry-After"] = str(retry_after)
            response.headers["WWW-Authenticate"] = f'Basic realm="{self._realm}"'
            return response

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Basic "):
            provided = auth_header[6:]
            if hmac.compare_digest(provided, self._expected_b64):
                self._failure_tracker.record_success(client_ip)
                return await call_next(request)

        self._failure_tracker.record_failure(client_ip)
        logger.warning("未授权访问: %s %s from %s", request.method, request.url.path, client_ip)
        response = respond_error(
            code="UNAUTHORIZED",
            message="VOSR2-Lite requires authentication.",
            status=401,
        )
        response.headers["WWW-Authenticate"] = f'Basic realm="{self._realm}"'
        return response


def resolve_auth_settings(config: dict) -> dict:
    """汇总生效的 Basic Auth 配置（环境变量优先于 config.yaml）。"""
    security = config.get("runtime", {}).get("security", {}) or {}
    auth_cfg = security.get("auth", {}) or {}
    env_username = os.environ.get(_ENV_USERNAME, "").strip()
    env_password = os.environ.get(_ENV_PASSWORD, "").strip()

    enable = bool(auth_cfg.get("enable", False))
    if env_username and env_password:
        enable = True  # 容器部署快捷通道：两个环境变量同时注入即视为启用

    return {
        "enable": enable,
        "username": env_username or str(auth_cfg.get("username", "")),
        "password": env_password or str(auth_cfg.get("password", "")),
        "realm": str(auth_cfg.get("realm", "VOSR2")),
        "max_auth_failures": int(auth_cfg.get("max_auth_failures", 5)),
        "auth_failure_window_seconds": float(auth_cfg.get("auth_failure_window_seconds", 300)),
        "auth_ban_seconds": float(auth_cfg.get("auth_ban_seconds", 600)),
    }


def should_enable_auth(config: dict) -> bool:
    """enable=true 且用户名密码均非空才启用；配了开关却没填凭据时告警并跳过。"""
    settings = resolve_auth_settings(config)
    if not settings["enable"]:
        return False
    if not settings["username"] or not settings["password"]:
        logger.warning("Basic Auth 已配置 enable=true 但用户名或密码为空，跳过启用")
        return False
    return True
