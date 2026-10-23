#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""VOSR2-Lite 配置加载模块。

对齐家族仓（SeedVR2-Lite）的口径：
- ``load_config()`` 返回 dict（路由层通过 dependencies.get_config 使用）；
- 缺失字段以 DEFAULTS 深合并兜底，配置文件不存在不阻塞启动；
- ``.env`` 支持（纯标准库，环境变量优先级高于 .env）；
- 同时提供 ``AppConfig`` 薄封装（点号取值 + 相对路径解析），
  供 model_manager / engine 等非路由组件使用。
"""

from __future__ import annotations

import copy
import os
from typing import Any

import yaml
from loguru import logger

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

DEFAULTS: dict[str, Any] = {
    "server": {
        "host": "127.0.0.1",
        "port": 7871,
        "auto_open_browser": True,
        "debug": False,
        "allowed_origins": ["http://127.0.0.1:7871", "http://localhost:7871"],
    },
    "model": {
        "pretrained_dir": "model",
        "default_checkpoint": "VOSR2",
        "auto_load": False,
        "idle_unload_minutes": 15,
        "device": "auto",
        "checkpoints": {},
    },
    "inference": {
        "default_upscale": 4,
        "default_align_method": "wavelet",
        "default_seed": 42,
        "default_steps": 1,
        "default_tile_size": 512,
        "tile_overlap": 32,
        "default_vae_tile_size": 0,
        "align_methods": ["wavelet", "adain", "nofix"],
    },
    "history": {"db_path": "data/history.db", "max_records": 10000},
    "i18n": {"available_locales": ["zh", "en"], "default_locale": "zh"},
    "logging": {"file": "logs/app.log", "level": "INFO",
                "max_size_mb": 50, "backup_count": 3},
    "runtime": {
        "security": {
            "rate_limit_per_minute": 30,
            "max_upload_image_mb": 50,
            "allowed_base_dirs": ["outputs/", "data/uploads/",
                                  "data/checkpoints/", "model/"],
            "headers": {"enabled": True, "csp": ""},
        },
        "task": {"queue_maxsize": 100, "max_timeout_seconds": 86400,
                 "progress_stall_timeout_minutes": 30},
        "sse": {"heartbeat_interval_seconds": 30, "max_duration_seconds": 3600},
    },
}


def _load_dotenv() -> None:
    """加载项目根目录 .env（无第三方依赖；显式环境变量优先）。"""
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                if key:
                    os.environ.setdefault(key, value)
    except OSError as exc:
        logger.warning(f".env 加载失败: {exc}")


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, val in (override or {}).items():
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def load_config(config_path: str | None = None) -> dict:
    """加载 config.yaml 并与默认值深合并，返回 dict。"""
    _load_dotenv()
    path = config_path or os.path.join(PROJECT_ROOT, "config.yaml")
    data: dict[str, Any] = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except yaml.YAMLError as exc:
            logger.error(f"config.yaml 解析失败，使用默认值: {exc}")
            data = {}
    else:
        logger.warning(f"配置文件不存在，使用内置默认值: {path}")
    return _deep_merge(DEFAULTS, data)


class AppConfig:
    """dict 配置的薄封装：点号取值 + 相对路径解析。"""

    def __init__(self, data: dict[str, Any], project_root: str = PROJECT_ROOT):
        self._data = data
        self.project_root = project_root

    @classmethod
    def load(cls, path: str | None = None) -> "AppConfig":
        return cls(load_config(path))

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def section(self, name: str) -> dict[str, Any]:
        val = self.get(name, {})
        return val if isinstance(val, dict) else {}

    def abspath(self, relative: str) -> str:
        if os.path.isabs(relative):
            return relative
        return os.path.normpath(os.path.join(self.project_root, relative))

    @property
    def data(self) -> dict[str, Any]:
        return self._data


def get_app_config() -> AppConfig:
    """进程级单例配置（非路由组件使用）。"""
    global _APP_CONFIG
    if _APP_CONFIG is None:
        _APP_CONFIG = AppConfig.load()
    return _APP_CONFIG


_APP_CONFIG: AppConfig | None = None
