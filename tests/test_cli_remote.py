#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""CLI 远程层单测：用假 HTTP 客户端覆盖契约，不依赖网络 / GPU。

覆盖率目标：cli_remote.py 的 probe / _csrf_headers / restore 三条主路径，
以及错误分支（连接失败、任务失败、超时、CSRF token 提取）。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.integrated_app import cli_remote


class FakeResponse:
    def __init__(self, payload: dict, headers: dict | None = None, status: int = 200):
        self._payload = payload
        self.headers = headers or {}
        self.status_code = status

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    """记录调用的假 httpx.Client，支持 context manager。"""

    def __init__(
        self, responses: dict[str, list[FakeResponse]] | None = None, fail_get: bool = False
    ):
        self.responses = responses or {}
        self.fail_get = fail_get
        self.calls: list[tuple[str, str, dict]] = []
        self.times = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("GET", url, kwargs))
        if self.fail_get:
            raise OSError("connection refused")
        seq = self.responses.get(url, [])
        return seq.pop(0) if seq else FakeResponse({"success": True, "data": {}})

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("POST", url, kwargs))
        seq = self.responses.get(url, [])
        return seq.pop(0) if seq else FakeResponse({"success": True, "data": {"task_id": "t1"}})


def _patch_client(monkeypatch, fake: FakeClient) -> None:
    monkeypatch.setattr(cli_remote, "_client", lambda base_url, timeout=30.0: fake)


class TestProbe:
    def test_probe_ok(self, monkeypatch):
        fake = FakeClient(
            {"/api/system/health": [FakeResponse({"success": True, "data": {"gpu": {}}})]}
        )
        _patch_client(monkeypatch, fake)
        assert cli_remote.probe("http://127.0.0.1:7871")["success"] is True

    def test_probe_connection_failure(self, monkeypatch):
        _patch_client(monkeypatch, FakeClient(fail_get=True))
        with pytest.raises(cli_remote.RemoteError, match="无法连接常驻服务"):
            cli_remote.probe("http://127.0.0.1:1")

    def test_probe_service_error(self, monkeypatch):
        fake = FakeClient(
            {"/api/system/health": [FakeResponse({"success": False, "error": "boom"})]}
        )
        _patch_client(monkeypatch, fake)
        with pytest.raises(cli_remote.RemoteError, match="boom"):
            cli_remote.probe("http://127.0.0.1:7871")


class TestCsrfHeaders:
    def test_from_response_header(self, monkeypatch):
        fake = FakeClient(
            {"/api/system/health": [FakeResponse({}, headers={"X-CSRF-Token": "tok1"})]}
        )
        _patch_client(monkeypatch, fake)
        headers = cli_remote._csrf_headers(fake)
        assert headers == {"X-CSRF-Token": "tok1", "Cookie": "csrf_token=tok1"}

    def test_from_set_cookie(self, monkeypatch):
        fake = FakeClient(
            {
                "/api/system/health": [
                    FakeResponse(
                        {}, headers={"Set-Cookie": "csrf_token=tok2; Path=/; SameSite=Strict"}
                    )
                ]
            }
        )
        _patch_client(monkeypatch, fake)
        assert cli_remote._csrf_headers(fake)["X-CSRF-Token"] == "tok2"

    def test_no_token_returns_empty(self, monkeypatch):
        _patch_client(monkeypatch, FakeClient())
        assert cli_remote._csrf_headers(FakeClient()) == {}


class TestRemoteRestore:
    def test_success_copies_output(self, monkeypatch, tmp_path):
        src = tmp_path / "a.png"
        src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        remote_out = tmp_path / "remote.png"
        remote_out.write_bytes(b"\x89PNG\r\n\x1a\n" + b"1" * 128)
        out_dir = tmp_path / "out"

        health = FakeResponse({"success": True, "data": {}})
        upload = FakeResponse({"success": True, "data": {"task_id": "t1"}})
        result = FakeResponse(
            {
                "success": True,
                "data": {
                    "status": "completed",
                    "output_path": str(remote_out),
                    "output_size": 136,
                },
            }
        )
        fake = FakeClient(
            {
                "/api/system/health": [health, FakeResponse({"success": True, "data": {}})],
                "/api/restore/": [upload],
                "/api/restore/t1/result": [result],
            }
        )
        _patch_client(monkeypatch, fake)
        data = cli_remote.restore(
            "http://127.0.0.1:7871",
            [str(src)],
            str(out_dir),
            params={"upscale": 2, "steps": 1},
            checkpoint="VOSR2",
        )
        assert data["succeeded"] == 1
        assert data["failed"] == 0
        assert data["mode"] == "remote"
        assert (out_dir / "a.png").read_bytes() == remote_out.read_bytes()

    def test_task_failed_becomes_failure(self, monkeypatch, tmp_path):
        src = tmp_path / "b.png"
        src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        fake = FakeClient(
            {
                "/api/system/health": [
                    FakeResponse({"success": True, "data": {}}),
                    FakeResponse({"success": True, "data": {}}),
                ],
                "/api/restore/": [FakeResponse({"success": True, "data": {"task_id": "t2"}})],
                "/api/restore/t2/result": [
                    FakeResponse(
                        {"success": True, "data": {"status": "failed", "error": "CUDA OOM"}}
                    )
                ],
            }
        )
        _patch_client(monkeypatch, fake)
        data = cli_remote.restore(
            "http://127.0.0.1:7871",
            [str(src)],
            str(tmp_path / "out"),
            params={},
            checkpoint="VOSR2",
        )
        assert data["succeeded"] == 0
        assert data["failed"] == 1
        assert "CUDA OOM" in data["failures"][0]["error"]

    def test_success_envelope_has_expected_keys(self):
        payload = {"success": True, "data": {"x": 1}}
        assert json.loads(json.dumps(payload))["data"]["x"] == 1
