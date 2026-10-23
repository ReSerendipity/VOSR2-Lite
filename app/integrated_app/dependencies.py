#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""FastAPI 依赖注入模块（家族模式）。

路由通过 Depends(get_xxx) 从 request.app.state 获取核心组件，
避免直接访问 app.state 内部结构。
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.integrated_app.history_db import HistoryDB
from app.integrated_app.i18n import I18n
from app.integrated_app.model_manager import ModelManager
from app.integrated_app.task_queue import TaskQueue


def get_history_db(request: Request) -> HistoryDB:
    return request.app.state.history_db


def get_task_queue(request: Request) -> TaskQueue:
    return request.app.state.task_queue


def get_model_manager(request: Request) -> ModelManager:
    return request.app.state.model_manager


def get_config(request: Request) -> dict:
    return request.app.state.config


def get_i18n(request: Request) -> I18n:
    return request.app.state.i18n


def get_event_bus(request: Request) -> Any:
    return getattr(request.app.state, "event_bus", None)
