#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""路径白名单守卫：仅允许访问配置内的基础目录，防路径遍历。"""

from __future__ import annotations

from pathlib import Path


class PathGuard:
    def __init__(self, project_root: str, allowed_base_dirs: list[str]):
        self._roots: list[Path] = []
        for base in allowed_base_dirs:
            p = Path(base)
            if not p.is_absolute():
                p = Path(project_root) / p
            self._roots.append(p.resolve())

    def is_safe_path(self, target: str) -> bool:
        """target 是否落在任一白名单根目录内（解析符号链接后判定）。"""
        try:
            resolved = Path(target).resolve()
        except OSError:
            return False
        return any(resolved == root or root in resolved.parents for root in self._roots)


def build_default_path_guard(project_root: str, allowed_base_dirs: list[str]) -> PathGuard:
    return PathGuard(project_root, allowed_base_dirs)
