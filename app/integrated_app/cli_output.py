#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""CLI 统一输出层：结构化结果信封 + 人类/JSON 双渲染器。

面向 AI agent 与自动化脚本：
- stdout 只承载结果数据，日志/警告一律走 stderr（引擎与 loguru 默认写 stderr）；
- ``--json`` 输出稳定 schema 的 JSON，便于 ``json.loads``；
- 语义化退出码，agent 可据此分支处理。

本模块不依赖 FastAPI / torch，可在纯 CLI 环境直接 import。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any

# 语义化退出码
EXIT_OK = 0
EXIT_BUSINESS_ERROR = 1  # 业务失败（推理失败、模型加载失败等）
EXIT_USAGE_ERROR = 2  # 参数错误（argparse 亦用 2）
EXIT_NOT_READY = 3  # 环境未就绪（模型/依赖/GPU 缺失）

_ERROR_CODE_EXIT: dict[str, int] = {
    "NOT_READY": EXIT_NOT_READY,
    "USAGE_ERROR": EXIT_USAGE_ERROR,
}


@dataclass
class Result:
    """命令执行结果的统一信封。"""

    ok: bool = True
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    error_code: str = "BUSINESS_ERROR"
    warnings: list[str] = field(default_factory=list)
    command: str = ""

    @classmethod
    def success(cls, data: dict[str, Any] | None = None, **kwargs: Any) -> "Result":
        return cls(ok=True, data=dict(data or {}), **kwargs)

    @classmethod
    def failure(cls, error: str, error_code: str = "BUSINESS_ERROR", **kwargs: Any) -> "Result":
        return cls(ok=False, error=error, error_code=error_code, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        """转换为键顺序稳定的 JSON 可序列化字典。"""
        payload: dict[str, Any] = {"ok": self.ok, "command": self.command}
        if self.ok:
            payload["data"] = self.data
        else:
            payload["error"] = self.error
            payload["error_code"] = self.error_code
        if self.warnings:
            payload["warnings"] = self.warnings
        return payload

    def exit_code(self) -> int:
        if self.ok:
            return EXIT_OK
        return _ERROR_CODE_EXIT.get(self.error_code, EXIT_BUSINESS_ERROR)


def begin_json_mode() -> None:
    """隔离 import 期的 stdout 污染：把 stdout 重定向到 stderr。

    须在导入有 print 副作用的依赖之前调用。本项目的 loguru 默认写 stderr，
    但第三方（如 vendored vosr 的上游脚本）可能直接 print，故前置隔离。
    """
    sys.stdout = sys.stderr


def emit(result: Result, as_json: bool, human_formatter=None) -> int:
    """渲染结果并返回进程退出码。"""
    if as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=False))
    elif human_formatter is not None:
        human_formatter(result)
    elif result.ok:
        for key, value in result.data.items():
            print(f"{key}: {value}")
    else:
        print(f"错误: {result.error}", file=sys.stderr)
        if result.error_code:
            print(f"错误码: {result.error_code}", file=sys.stderr)
    return result.exit_code()


def emit_warning(message: str) -> None:
    print(f"警告: {message}", file=sys.stderr)


__all__ = [
    "EXIT_OK",
    "EXIT_BUSINESS_ERROR",
    "EXIT_USAGE_ERROR",
    "EXIT_NOT_READY",
    "Result",
    "begin_json_mode",
    "emit",
    "emit_warning",
]
