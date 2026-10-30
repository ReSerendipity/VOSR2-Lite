#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""桌面壳关闭契约测试：POST /api/system/shutdown。

覆盖三条底线：
1. 非回环来源必须 403（防止误把服务暴露到局域网后被任意关闭）；
2. 回环来源返回 200，且在非守护模式（测试客户端无 uvicorn Server 句柄）下
   明确回 ``accepted=false``，不产生任何破坏性动作；
3. ``app_server.request_shutdown()`` 无句柄时安全返回 False。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_shutdown_rejects_non_loopback(app):
    """默认 TestClient 来源是 testclient:50000（非回环）→ 必须 403。"""
    with TestClient(app) as c:
        resp = c.post("/api/system/shutdown")

    assert resp.status_code == 403
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "FORBIDDEN"


def test_shutdown_accepts_loopback_without_server_handle(app):
    """回环来源：200 + accepted=false（无 uvicorn Server 句柄时不假装已关闭）。"""
    with TestClient(app, client=("127.0.0.1", 50001)) as c:
        resp = c.post("/api/system/shutdown")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["status"] == "shutting_down"
    assert body["data"]["accepted"] is False


def test_request_shutdown_without_handle_returns_false():
    from app.integrated_app import app_server

    assert app_server._SERVER is None
    assert app_server.request_shutdown() is False


def test_request_shutdown_sets_should_exit_flag(monkeypatch):
    """有句柄时只置 should_exit，不自行 os._exit（交由 uvicorn 走 lifespan 清理）。"""
    from app.integrated_app import app_server

    class _FakeServer:
        should_exit = False

    fake = _FakeServer()
    monkeypatch.setattr(app_server, "_SERVER", fake, raising=False)

    assert app_server.request_shutdown() is True
    assert fake.should_exit is True


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"])
def test_loopback_whitelist_contents(host):
    from app.integrated_app.routes.system.shutdown import LOOPBACK_HOSTS

    assert host in LOOPBACK_HOSTS
