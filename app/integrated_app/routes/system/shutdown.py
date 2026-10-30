#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""系统电源路由：桌面壳优雅关闭端点。

供 Tauri v2 桌面壳（``desktop/src-tauri``）在退出/更新换载前调用，替代
``taskkill /F`` 硬杀——硬杀会留下 torch/uvicorn 子进程持有 app/ 目录句柄，
导致增量更新 rename 失败（家族仓 GOTCHAS 同型问题）。本端点只置
``uvicorn.Server.should_exit``，由 uvicorn 正常收尾并触发 lifespan 清理
（停任务队列、关历史库、取消空闲卸载看门狗）。

安全约束：
- 仅允许本机回环调用（127.0.0.1 / ::1 / ::ffff:127.0.0.1），非回环直接 403；
- 非安全方法，受 CSRF double-submit 保护（``runtime.security.csrf.enable=true`` 时
  壳需带 cookie + ``X-CSRF-Token`` 同值；默认关闭时无需 token）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.integrated_app.utils.response import respond_error, respond_success

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["系统"])

# 回环地址白名单（含 IPv4-mapped IPv6：部分 Windows 双栈栈会把 127.0.0.1 报成 ::ffff:127.0.0.1）
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"})


@router.post("/shutdown")
async def shutdown_server(request: Request):
    """优雅关闭本机服务（桌面壳专用）。

    API 端点：POST /api/system/shutdown

    请求参数：无（调用方须为本机回环地址）
    返回格式：``{"success": true, "data": {"status": "shutting_down"}}``

    无 uvicorn Server 句柄时（测试 / 外部 ASGI 托管）仍返回 200，但不会退出进程——
    壳侧据此只做「请求已发出」判定，真正退出由 uvicorn 生命周期完成。
    """
    client_host = (request.client.host if request.client else "") or ""
    if client_host not in LOOPBACK_HOSTS:
        logger.warning(f"拒绝非本机关闭请求，来源: {client_host or '<unknown>'}")
        return respond_error(
            code="FORBIDDEN",
            message="仅允许本机调用",
            status=403,
        )

    from app.integrated_app import app_server

    accepted = app_server.request_shutdown()
    if not accepted:
        # 非守护模式：不做任何破坏性动作，明确告知壳侧「已收到但无句柄」
        return respond_success({"status": "shutting_down", "accepted": False})

    return respond_success({"status": "shutting_down", "accepted": True})
