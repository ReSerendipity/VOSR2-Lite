#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""CLI 契约测试：Result 信封 / JSON 输出 / 语义退出码 / 命令分派。

只测不依赖 GPU 与权重的路径（--help、models/system/history 查询、错误分支），
保证 CI（CPU torch、无权重）也能跑。
"""

from __future__ import annotations

import json

import pytest

from app.integrated_app.cli import build_parser, main
from app.integrated_app.cli_output import (
    EXIT_NOT_READY,
    EXIT_OK,
    EXIT_USAGE_ERROR,
    Result,
    emit,
)


def _run_json(*args: str) -> tuple[int, dict]:
    """在进程内跑 main，捕获 stdout 的 JSON（避免子进程开销与路径漂移）。"""
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main([*args, "--json"])
    return rc, json.loads(buf.getvalue())


class TestResultContract:
    def test_success_envelope(self):
        r = Result.success({"a": 1}, command="t")
        d = r.to_dict()
        assert d["ok"] is True
        assert d["data"] == {"a": 1}
        assert d["command"] == "t"
        assert r.exit_code() == EXIT_OK

    def test_failure_envelope(self):
        r = Result.failure("boom", "NOT_READY", command="t")
        d = r.to_dict()
        assert d["ok"] is False
        assert d["error"] == "boom"
        assert d["error_code"] == "NOT_READY"
        assert r.exit_code() == EXIT_NOT_READY

    def test_emit_json_only_stdout(self, capsys):
        rc = emit(Result.success({"x": 2}, command="t"), as_json=True)
        out = capsys.readouterr().out
        assert rc == EXIT_OK
        assert json.loads(out)["data"]["x"] == 2


class TestParser:
    def test_commands_registered(self):
        p = build_parser()
        for cmd in ("restore", "models", "system", "history"):
            with pytest.raises(SystemExit) as exc:
                p.parse_args([cmd, "--help"])
            assert exc.value.code == 0

    def test_restore_requires_input(self):
        p = build_parser()
        with pytest.raises(SystemExit) as exc:
            p.parse_args(["restore", "-o", "outputs"])
        assert exc.value.code == EXIT_USAGE_ERROR


class TestCommands:
    def test_models_status_json(self):
        rc, d = _run_json("models", "status")
        assert rc == EXIT_OK
        assert d["ok"] is True
        assert d["command"] == "models status"
        assert isinstance(d["data"]["checkpoints"], list)
        assert "model_root" in d["data"]

    def test_system_status_json(self):
        rc, d = _run_json("system", "status")
        assert rc == EXIT_OK
        assert d["data"]["server_host"] == "127.0.0.1"
        assert "cuda_available" in d["data"]

    def test_restore_missing_input_is_not_ready(self):
        rc, d = _run_json("restore", "-i", "__definitely_missing__.png", "-o", "outputs")
        assert rc == EXIT_NOT_READY
        assert d["ok"] is False
        assert d["error_code"] == "NOT_READY"

    def test_batch_with_file_is_usage_error(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("not an image", encoding="utf-8")
        rc, d = _run_json("restore", "-i", str(f), "-o", str(tmp_path), "--batch")
        assert rc == EXIT_USAGE_ERROR
        assert d["error_code"] == "USAGE_ERROR"

    def test_json_flag_anywhere(self):
        rc = main(["--json", "models", "status"])
        assert rc == EXIT_OK
