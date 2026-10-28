#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""语言包一致性测试：三套语言（zh / zh-TW / en）必须键集合完全对齐。

为什么单独测：漏译在运行时表现为「中文界面里突然蹦出一句英文」，肉眼很难在
review 阶段发现；键集合比对能在 CI 阶段直接拦住。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

LOCALES = os.path.join(PROJECT_ROOT, "app", "integrated_app", "locales")


def _flatten(node, prefix=""):
    out = {}
    for key, val in node.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(val, dict):
            out.update(_flatten(val, full))
        else:
            out[full] = val
    return out


def _catalog(locale: str) -> dict[str, str]:
    with open(os.path.join(LOCALES, f"{locale}.json"), encoding="utf-8") as f:
        return _flatten(json.load(f))


@pytest.mark.parametrize("locale", ["zh-TW", "en"])
def test_catalog_keys_match_zh(locale):
    """每套语言包的键集合必须与简体中文基线完全一致（无漏译、无多余键）。"""
    base = set(_catalog("zh"))
    other = set(_catalog(locale))
    assert other - base == set(), f"{locale} 有多余键: {sorted(other - base)}"
    assert base - other == set(), f"{locale} 漏译: {sorted(base - other)}"


@pytest.mark.parametrize("locale", ["zh", "zh-TW", "en"])
def test_no_empty_translation(locale):
    empty = [k for k, v in _catalog(locale).items() if not str(v).strip()]
    assert empty == [], f"{locale} 存在空翻译: {empty}"


def test_zh_tw_differs_from_zh_but_keeps_placeholders():
    """繁体包应确实做了转换（不是简体复制），且占位符 {name} 一个不少。"""
    zh, tw = _catalog("zh"), _catalog("zh-TW")
    changed = sum(1 for k in zh if zh[k] != tw[k])
    assert changed > len(zh) * 0.4, f"仅 {changed}/{len(zh)} 条发生转换，疑似未真正本地化"
    for key, text in zh.items():
        if "{" in text:
            assert text.count("{") == tw[key].count("{"), f"占位符数量不一致: {key}"


def test_locale_name_mapping():
    from app.integrated_app.i18n import I18n

    i18n = I18n()
    assert i18n.get_locale_name("zh-TW") == "繁體中文"
    assert i18n.get_locale_name("en") == "English"
    assert i18n.get_locale_name("xx") == "xx"  # 未知语言回退代码本身


def test_zh_tw_switch_and_lookup():
    from app.integrated_app.i18n import I18n

    i18n = I18n()
    assert "zh-TW" in i18n.available_locales
    assert i18n.set_locale("zh-TW") == "zh-TW"
    assert i18n.t("nav.home") == "首頁"
    assert i18n.t("nav.home", locale="zh") == "首页"
    assert i18n.t("nav.home", locale="en") == "Home"


def test_api_accepts_zh_tw(client):
    """后端 locale 接口要放行 zh-TW（白名单来自 config.i18n.available_locales）。"""
    resp = client.post("/api/system/locale", json={"locale": "zh-TW"})
    assert resp.status_code == 200
    assert resp.json()["data"]["locale"] == "zh-TW"


def test_page_renders_zh_tw(client):
    """切到 zh-TW 后页面应出现繁体文案，且语言选择器列出三种语言。"""
    client.post("/api/system/locale", json={"locale": "zh-TW"})
    html = client.get("/").text
    assert "繁體中文" in html
    assert "首页" not in html
