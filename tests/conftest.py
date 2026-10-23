#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""pytest 公共夹具：构建测试用 FastAPI app（临时目录 + 临时数据库）。"""

from __future__ import annotations

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """创建隔离的测试 app（临时数据目录）。"""
    monkeypatch.setenv("VOSR2_ALLOWED_ORIGINS", "http://127.0.0.1:7871")
    from app.integrated_app.app_server import create_app

    config_overrides = {
        "history": {"db_path": str(tmp_path / "test_history.db")},
    }
    app = create_app(config_overrides)
    yield app


@pytest.fixture()
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
