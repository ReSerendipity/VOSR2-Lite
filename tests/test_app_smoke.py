#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""应用冒烟测试：页面、API、SSE、上传校验、i18n、历史库。

不加载模型、不需要 GPU；引擎相关路径以"权重缺失"降级断言。
"""

from __future__ import annotations


def test_index_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "VOSR2" in resp.text


def test_pages(client):
    for path in ("/restore", "/history", "/system-status", "/settings"):
        resp = client.get(path)
        assert resp.status_code == 200, path


def test_health_api(client):
    resp = client.get("/api/system/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "gpu" in body["data"]
    assert "checkpoints" in body["data"]


def test_history_api_empty(client):
    resp = client.get("/api/system/history?page=1&page_size=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["records"] == []
    assert body["data"]["total"] == 0


def test_locale_switch(client):
    resp = client.post("/api/system/locale", json={"locale": "en"})
    assert resp.status_code == 200
    assert resp.json()["data"]["locale"] == "en"
    resp = client.post("/api/system/locale", json={"locale": "fr"})
    assert resp.status_code == 400


def test_restore_requires_file(client):
    resp = client.post("/api/restore/")
    assert resp.status_code == 422  # 缺少 file 字段


def test_restore_rejects_bad_extension(client):
    resp = client.post(
        "/api/restore/",
        files={"file": ("evil.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400
    assert "不支持" in resp.json()["error"]["message"]


def test_restore_rejects_fake_magic(client):
    # 扩展名是 png，内容是文本 —— 魔数校验应拦截
    resp = client.post(
        "/api/restore/",
        files={"file": ("fake.png", b"not a real png", "image/png")},
    )
    assert resp.status_code == 400
    assert "伪造" in resp.json()["error"]["message"]


def test_task_not_found(client):
    resp = client.get("/api/restore/nonexistent/result")
    assert resp.status_code == 404


def test_sse_endpoint_connects(client):
    # SSE 流式响应：只验证可建立连接且 Content-Type 正确
    with client.stream("GET", "/api/sse/events") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")


def test_api_404_envelope(client):
    resp = client.get("/api/nonexistent")
    assert resp.status_code == 404
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_FOUND"


def test_page_404_redirects(client):
    resp = client.get("/nonexistent-page", follow_redirects=False)
    assert resp.status_code == 302
