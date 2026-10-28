#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""安全中间件测试：Basic Auth / CSRF / 服务端密钥。

覆盖两件事：
1. **默认关闭时行为完全不变**（这是移植的硬约束——现有 200+ 用例零修改通过）；
2. **开启后每条拒绝路径都成立**（401/403/429 的状态码、错误信封、响应头）。
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

FIXED_SECRET = "a" * 64  # 64 位 hex = 32 字节


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """清掉可能从外部环境漏进来的认证/密钥变量，并固定 CSRF 密钥。"""
    for name in ("VOSR2_AUTH_USERNAME", "VOSR2_AUTH_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOSR2_SECRET_KEY", FIXED_SECRET)
    from app.integrated_app.security.secret_key import reset_cached_key

    reset_cached_key()
    yield
    reset_cached_key()


@pytest.fixture()
def make_client(tmp_path):
    """按需构造带指定 runtime.security 配置的 app，返回 TestClient。"""
    from app.integrated_app.app_server import create_app

    clients: list[TestClient] = []
    counter = {"n": 0}

    def _make(**security) -> TestClient:
        counter["n"] += 1
        cfg = {
            "history": {"db_path": str(tmp_path / f"h{counter['n']}.db")},
            "runtime": {"security": security},
        }
        client = TestClient(create_app(cfg))
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.__exit__(None, None, None)


def _basic(user: str, password: str) -> dict[str, str]:
    raw = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


# ── 默认关闭：行为与未挂载中间件一致 ──────────────────────────────────────
def test_auth_disabled_by_default(make_client):
    client = make_client()
    assert client.get("/api/system/health").status_code == 200
    assert client.get("/").status_code == 200


def test_csrf_disabled_by_default(make_client):
    """默认关闭时 POST 不需要 token（缺文件仍是 422，说明已进入路由）。"""
    client = make_client()
    assert client.post("/api/restore/").status_code == 422


def test_auth_not_enabled_without_credentials(make_client):
    """enable=true 但凭据为空：跳过启用而不是锁死自己。"""
    client = make_client(auth={"enable": True, "username": "", "password": ""})
    assert client.get("/api/system/health").status_code == 200


# ── Basic Auth 开启后 ────────────────────────────────────────────────────
AUTH_ON = {"enable": True, "username": "admin", "password": "s3cret"}


def test_auth_requires_credentials(make_client):
    client = make_client(auth=AUTH_ON)
    resp = client.get("/api/system/health")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"
    assert resp.headers["www-authenticate"] == 'Basic realm="VOSR2"'


def test_auth_rejects_wrong_password(make_client):
    client = make_client(auth=AUTH_ON)
    assert client.get("/api/system/health", headers=_basic("admin", "wrong")).status_code == 401


def test_auth_accepts_correct_credentials(make_client):
    client = make_client(auth=AUTH_ON)
    resp = client.get("/api/system/health", headers=_basic("admin", "s3cret"))
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_auth_protects_pages_and_static(make_client):
    """页面与静态资源同样受保护（否则未授权者可看到完整 UI）。"""
    client = make_client(auth=AUTH_ON)
    assert client.get("/").status_code == 401
    assert client.get("/static/js/app.js").status_code == 401


def test_auth_env_password_overrides_config(make_client, monkeypatch):
    """环境变量密码优先于 config.yaml（避免明文入库）。"""
    monkeypatch.setenv("VOSR2_AUTH_PASSWORD", "from-env")
    client = make_client(auth=AUTH_ON)
    assert client.get("/api/system/health", headers=_basic("admin", "s3cret")).status_code == 401
    assert client.get("/api/system/health", headers=_basic("admin", "from-env")).status_code == 200


def test_auth_env_pair_enables_without_config(monkeypatch):
    """同时注入用户名+密码环境变量即视为启用（容器部署快捷通道）。"""
    from app.integrated_app.middleware.basic_auth import (
        resolve_auth_settings,
        should_enable_auth,
    )

    monkeypatch.setenv("VOSR2_AUTH_USERNAME", "u")
    monkeypatch.setenv("VOSR2_AUTH_PASSWORD", "p")
    cfg = {"runtime": {"security": {"auth": {"enable": False}}}}
    assert should_enable_auth(cfg) is True
    assert resolve_auth_settings(cfg)["username"] == "u"


# ── 防爆破：失败计数与临时封禁 ───────────────────────────────────────────
def test_auth_bans_after_repeated_failures(make_client):
    client = make_client(auth={**AUTH_ON, "max_auth_failures": 2, "auth_ban_seconds": 60})
    assert client.get("/api/system/health", headers=_basic("admin", "bad")).status_code == 401
    assert client.get("/api/system/health", headers=_basic("admin", "bad")).status_code == 401
    # 封禁期内即使凭据正确也拒绝（先判封禁，防绕过）
    resp = client.get("/api/system/health", headers=_basic("admin", "s3cret"))
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "AUTH_BANNED"
    assert int(resp.headers["retry-after"]) >= 1


def test_tracker_window_expiry(monkeypatch):
    from app.integrated_app.middleware import basic_auth

    clock = {"t": 1000.0}
    monkeypatch.setattr(basic_auth, "_monotonic", lambda: clock["t"])
    tracker = basic_auth.AuthFailureTracker(max_failures=3, window_seconds=10, ban_seconds=60)
    tracker.record_failure("1.1.1.1")
    tracker.record_failure("1.1.1.1")
    clock["t"] += 11  # 前两次已滑出窗口
    tracker.record_failure("1.1.1.1")
    assert tracker.is_banned("1.1.1.1") is False


def test_tracker_success_resets_counter():
    from app.integrated_app.middleware.basic_auth import AuthFailureTracker

    tracker = AuthFailureTracker(max_failures=2, window_seconds=300, ban_seconds=60)
    tracker.record_failure("1.1.1.1")
    tracker.record_success("1.1.1.1")
    tracker.record_failure("1.1.1.1")
    assert tracker.is_banned("1.1.1.1") is False


def test_tracker_ban_expires(monkeypatch):
    from app.integrated_app.middleware import basic_auth

    clock = {"t": 2000.0}
    monkeypatch.setattr(basic_auth, "_monotonic", lambda: clock["t"])
    tracker = basic_auth.AuthFailureTracker(max_failures=1, window_seconds=300, ban_seconds=30)
    tracker.record_failure("2.2.2.2")
    assert tracker.is_banned("2.2.2.2") is True
    clock["t"] += 31
    assert tracker.is_banned("2.2.2.2") is False


def test_tracker_retry_after_counts_down(monkeypatch):
    from app.integrated_app.middleware import basic_auth

    clock = {"t": 3000.0}
    monkeypatch.setattr(basic_auth, "_monotonic", lambda: clock["t"])
    tracker = basic_auth.AuthFailureTracker(max_failures=1, window_seconds=300, ban_seconds=60)
    tracker.record_failure("3.3.3.3")
    assert tracker.retry_after_seconds("3.3.3.3") == 60
    clock["t"] += 45
    assert tracker.retry_after_seconds("3.3.3.3") == 15


def test_tracker_disabled_when_max_failures_zero():
    from app.integrated_app.middleware.basic_auth import AuthFailureTracker

    tracker = AuthFailureTracker(max_failures=0)
    for _ in range(10):
        tracker.record_failure("4.4.4.4")
    assert tracker.is_banned("4.4.4.4") is False


# ── CSRF 开启后 ──────────────────────────────────────────────────────────
CSRF_ON = {"csrf": {"enable": True}}


def test_csrf_sets_signed_cookie_on_get(make_client):
    client = make_client(**CSRF_ON)
    resp = client.get("/")
    token = resp.cookies.get("csrf_token")
    assert token and "." in token
    assert len(token.split(".")[0]) == 64  # 32 字节 nonce 的 hex


def test_csrf_blocks_post_without_token(make_client):
    client = make_client(**CSRF_ON)
    resp = client.post("/api/restore/")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "CSRF_INVALID"


def test_csrf_allows_post_with_matching_token(make_client):
    client = make_client(**CSRF_ON)
    token = client.get("/").cookies["csrf_token"]
    resp = client.post("/api/restore/", headers={"X-CSRF-Token": token})
    assert resp.status_code == 422  # 已进入路由：缺 file 字段


def test_csrf_rejects_mismatched_header(make_client):
    client = make_client(**CSRF_ON)
    token = client.get("/").cookies["csrf_token"]
    resp = client.post("/api/restore/", headers={"X-CSRF-Token": token + "0"})
    assert resp.status_code == 403


def test_csrf_rejects_forged_cookie_and_reseeds(make_client):
    """cookie 与 header 一致但签名是伪造的：必须拒绝，并补种有效 token。"""
    client = make_client(**CSRF_ON)
    client.cookies.set("csrf_token", "deadbeef.notasignature")
    resp = client.post("/api/restore/", headers={"X-CSRF-Token": "deadbeef.notasignature"})
    assert resp.status_code == 403
    assert resp.cookies.get("csrf_token") not in (None, "deadbeef.notasignature")


def test_csrf_exempts_locale_endpoint(make_client):
    """语言切换是唯一豁免的非安全方法（只改显示偏好，无副作用）。"""
    client = make_client(**CSRF_ON)
    assert client.post("/api/system/locale", json={"locale": "zh"}).status_code == 200


def test_csrf_does_not_block_safe_methods(make_client):
    client = make_client(**CSRF_ON)
    assert client.get("/api/system/health").status_code == 200
    assert client.get("/docs").status_code == 200


def test_csrf_reseeds_broken_cookie_on_get(make_client):
    """GET 携带坏 token 时自动换新，避免浏览器永久 403 自锁。"""
    client = make_client(**CSRF_ON)
    client.cookies.set("csrf_token", "broken")
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.cookies.get("csrf_token") not in (None, "broken")


# ── 服务端密钥 ───────────────────────────────────────────────────────────
def test_secret_key_reads_env():
    from app.integrated_app.security.secret_key import get_secret_key

    assert get_secret_key() == bytes.fromhex(FIXED_SECRET)


def test_secret_key_persists_to_file(tmp_path, monkeypatch):
    from app.integrated_app.security.secret_key import get_secret_key, reset_cached_key

    monkeypatch.delenv("VOSR2_SECRET_KEY", raising=False)
    reset_cached_key()
    key_file = tmp_path / ".vosr2_secret"
    first = get_secret_key(key_file)
    assert len(first) == 32
    assert key_file.is_file()
    reset_cached_key()
    assert get_secret_key(key_file) == first


def test_secret_key_regenerates_on_corrupt_file(tmp_path, monkeypatch):
    from app.integrated_app.security.secret_key import get_secret_key, reset_cached_key

    monkeypatch.delenv("VOSR2_SECRET_KEY", raising=False)
    reset_cached_key()
    key_file = tmp_path / ".vosr2_secret"
    key_file.write_text("not-hex-at-all", encoding="utf-8")
    key = get_secret_key(key_file)
    assert len(key) == 32
    assert key_file.read_text(encoding="utf-8") == key.hex()


def test_secret_key_lives_under_data_dir():
    """密钥文件落在 data/ 下（该目录已由 .gitignore 排除，不会进版本库）。"""
    from app.integrated_app.security.secret_key import default_key_file

    path = default_key_file()
    assert path.parent.name == "data"
    assert path.name.startswith(".")
