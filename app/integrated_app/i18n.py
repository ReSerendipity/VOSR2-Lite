#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""VOSR2-Lite i18n 模块（家族模式精简版）。

- 语言包为嵌套 JSON（locales/*.json），查找时按点号键扁平化；
- 查找顺序：当前语言 -> 默认语言 -> key 本身；
- ``t(key, **kwargs)`` 供模板使用；``get_flat_translations()`` 供前端
  ``window.__I18N__`` 整表注入（替代手写白名单，避免漏键静默退化）。
"""

from __future__ import annotations

import json
import os
from typing import Any

from loguru import logger

LOCALES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")

LOCALE_NAMES = {"zh": "简体中文", "zh-TW": "繁體中文", "en": "English"}


def _flatten(data: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, val in data.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(val, dict):
            out.update(_flatten(val, full))
        else:
            out[full] = str(val)
    return out


def _load_translations(lang: str) -> dict[str, str] | None:
    path = os.path.join(LOCALES_DIR, f"{lang}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return _flatten(json.load(f))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error(f"语言包加载失败 {lang}: {exc}")
        return None


class I18n:
    """进程级 i18n 单例（与家族仓接口保持一致）。"""

    def __init__(self, locales_dir: str | None = None, default_locale: str = "zh"):
        global LOCALES_DIR
        if locales_dir:
            LOCALES_DIR = locales_dir
        self._catalogs: dict[str, dict[str, str]] = {}
        self.default_locale = default_locale
        self.current_locale = default_locale
        self._load()

    def _load(self) -> None:
        if not os.path.isdir(LOCALES_DIR):
            logger.warning(f"locales 目录不存在: {LOCALES_DIR}")
            return
        for fname in sorted(os.listdir(LOCALES_DIR)):
            if not fname.endswith(".json"):
                continue
            catalog = _load_translations(fname[:-5])
            if catalog is not None:
                self._catalogs[fname[:-5]] = catalog
        self.available = sorted(self._catalogs.keys())
        if self.default_locale not in self._catalogs and self.available:
            self.default_locale = self.available[0]
            self.current_locale = self.available[0]

    # ── 查询 ────────────────────────────────────────────────────────────────
    @property
    def available_locales(self) -> list[str]:
        return list(self.available)

    def get_locale_name(self, locale: str) -> str:
        return LOCALE_NAMES.get(locale, locale)

    def set_locale(self, locale: str) -> str:
        """切换当前语言（非法值回退默认），返回生效语言。"""
        if locale in self._catalogs:
            self.current_locale = locale
        else:
            self.current_locale = self.default_locale
        return self.current_locale

    def t(self, key: str, locale: str | None = None, **kwargs: Any) -> str:
        catalog = self._catalogs.get(locale or self.current_locale, {})
        fallback = self._catalogs.get(self.default_locale, {})
        text = catalog.get(key) or fallback.get(key) or key
        if kwargs:
            try:
                return text.format(**kwargs)
            except (KeyError, IndexError):
                return text
        return text


def get_flat_translations(locale: str | None = None) -> dict[str, str]:
    """当前（或指定）语言的压平词表，供前端整表注入。"""
    i18n = _get_i18n()
    if i18n is None:
        return {}
    return dict(i18n._catalogs.get(locale or i18n.current_locale, {}))


_I18N: I18n | None = None


def init_i18n(locales_dir: str | None = None, default_locale: str = "zh") -> I18n:
    global _I18N
    _I18N = I18n(locales_dir=locales_dir, default_locale=default_locale)
    return _I18N


def _get_i18n() -> I18n | None:
    return _I18N
