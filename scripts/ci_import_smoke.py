#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""CI 导入冒烟：不加载权重、不碰 GPU，验证依赖树与端点可用性。

用途：``.github/workflows/gpu-smoke.yml`` 的 cpu-import-smoke 步骤，也可本地跑
（``python scripts/ci_import_smoke.py``）。

与 pytest 的分工：pytest 依赖测试夹具与临时库，本脚本只做「装完依赖后还能不能
import / 建 app / 每个端点是否还在线」的体检——pip 解析出问题、上游 vendored 包
被误改、路由注册顺序（FastAPI 0.141 懒加载）踩坑时最先暴露的一层。

为什么探测 HTTP 而不是检查 app.routes：FastAPI 0.141 的 include_router 是懒加载
（路由树里留 `_IncludedRouter` 占位，首次请求时内部展开但不回填 app.routes），
按 app.routes 断言会把「全部 API 缺失」误报成故障（2026-10-28 实测）。
"""

from __future__ import annotations

import os
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# task_id 必须是 16 位小写十六进制（media.py 的路径注入防线），故未知任务用
# 合法格式的假 id；非法格式另设一条断言。
_UNKNOWN = "deadbeefdeadbeef"

# (method, path, 允许的状态码集合, 说明)
PROBES: list[tuple[str, str, set[int], str]] = [
    ("GET", "/", {200}, "首页"),
    ("GET", "/restore", {200}, "修复页"),
    ("GET", "/history", {200}, "历史页"),
    ("GET", "/system-status", {200}, "系统状态页"),
    ("GET", "/settings", {200}, "设置页"),
    ("GET", "/api/system/health", {200}, "健康检查"),
    ("GET", "/api/system/history", {200}, "历史列表"),
    ("POST", "/api/system/locale", {200}, "语言切换"),
    ("POST", "/api/restore/", {422}, "上传（缺文件应 422）"),
    ("GET", f"/api/restore/{_UNKNOWN}/result", {404}, "结果查询"),
    ("POST", f"/api/restore/{_UNKNOWN}/cancel", {404}, "任务取消"),
    ("GET", f"/api/restore/{_UNKNOWN}/progress", {404}, "SSE 进度（未知任务）"),
    ("GET", f"/api/restore/{_UNKNOWN}/input", {404}, "原图端点"),
    ("GET", f"/api/restore/{_UNKNOWN}/preview", {400, 404}, "预览端点"),
    ("GET", f"/api/restore/{_UNKNOWN}/download", {404}, "下载端点"),
    ("GET", "/api/restore/not-a-hex-id/input", {400}, "非法 task_id 应 400"),
    ("GET", "/api/definitely-not-a-route", {404}, "未知 API 返回 404 信封"),
]


def main() -> int:
    failures: list[str] = []

    # 1) 依赖树：torch / torchvision / cv2 能 import
    try:
        import cv2  # noqa: F401
        import torch
        import torchvision  # noqa: F401
    except Exception as exc:  # pragma: no cover - 环境损坏时的兜底
        print(f"[FAIL] 核心依赖导入失败: {exc}")
        return 1
    print(f"[OK] torch {torch.__version__} / cuda={torch.cuda.is_available()}")

    # 2) vendored 上游包与引擎模块
    try:
        from app.integrated_app.engines.vosr2_engine import (  # noqa: F401
            MULTISTEP_MIN_SIDE,
            RestoreParams,
            VOSR2Engine,
        )
        from vosr import tiled_vae  # noqa: F401
        from vosr.vosr import VOSR  # noqa: F401
    except Exception as exc:
        print(f"[FAIL] 引擎/vendored 包导入失败: {exc}")
        return 1
    print(f"[OK] vosr + 引擎导入成功（MULTISTEP_MIN_SIDE={MULTISTEP_MIN_SIDE}）")

    # 3) 端点可用性（真实 HTTP 探测，触发懒加载路由展开）
    try:
        from fastapi.testclient import TestClient

        from app.integrated_app.app_server import create_app

        with tempfile.TemporaryDirectory() as tmp:
            app = create_app({"history": {"db_path": os.path.join(tmp, "smoke.db")}})
            with TestClient(app) as client:
                for method, path, allowed, label in PROBES:
                    kwargs = {}
                    if path == "/api/system/locale":
                        kwargs["json"] = {"locale": "zh"}
                    resp = client.request(method, path, **kwargs)
                    ok = resp.status_code in allowed
                    mark = "OK" if ok else "FAIL"
                    print(f"[{mark}] {method} {path} -> {resp.status_code}（{label}）")
                    if not ok:
                        failures.append(f"{method} {path} 期望 {allowed} 实得 {resp.status_code}")
    except Exception as exc:
        print(f"[FAIL] create_app / 端点探测失败: {exc}")
        return 1

    if failures:
        print(f"\n导入冒烟失败：{len(failures)} 项")
        return 1
    print("\n导入冒烟通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
