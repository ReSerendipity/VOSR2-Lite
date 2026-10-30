#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""壳版本口径门禁：一处定义、三处镜像。

校验项（任一不符即退出码 1，供 CI / pre-commit 调用）：

1. 壳版本三处一致：`desktop/src-tauri/Cargo.toml` = `desktop/package.json` = `tauri.conf.json`；
2. 应用版本两处一致：仓库根 `version.json` = `pyproject.toml`；
3. `version.json` 的 `minimum_shell_version` 存在且是合法 semver；
4. `minimum_shell_version` ≤ 当前壳版本（否则这版安装包自相矛盾：要求比自己的壳还新）。

为什么需要它：家族仓 Image-DuoModel 曾出现 Cargo 1.0.0 / tauri.conf 1.0.2 的漂移，
壳自报版本与安装包版本错位，`minimum_shell_version` 判定整体偏移一个版本。

用法:
    python scripts/check_shell_version.py            # 校验
    python scripts/check_shell_version.py --verbose  # 打印每个版本位
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def cargo_version(path: Path) -> str:
    """从 Cargo.toml 的 [package] 段取 version（只认第一处，避免匹配依赖版本）。"""
    in_package = False
    for line in _read_text(path).splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_package = stripped == "[package]"
            continue
        if in_package:
            m = re.match(r'^version\s*=\s*"([^"]+)"', stripped)
            if m:
                return m.group(1)
    raise SystemExit(f"[FAIL] 未在 {path} 的 [package] 段找到 version")


def pyproject_version(path: Path) -> str:
    in_project = False
    for line in _read_text(path).splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_project = stripped == "[project]"
            continue
        if in_project:
            m = re.match(r'^version\s*=\s*"([^"]+)"', stripped)
            if m:
                return m.group(1)
    raise SystemExit(f"[FAIL] 未在 {path} 的 [project] 段找到 version")


def compare(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    return (left > right) - (left < right)


def numeric(version: str) -> tuple[int, ...]:
    core = version.split("-")[0].split("+")[0]
    return tuple(int(p) for p in core.split("."))


def main() -> int:
    ap = argparse.ArgumentParser(description="壳版本口径校验")
    ap.add_argument("--verbose", action="store_true", help="打印各版本位")
    args = ap.parse_args()

    errors: list[str] = []

    cargo = cargo_version(ROOT / "desktop/src-tauri/Cargo.toml")
    pkg = json.loads(_read_text(ROOT / "desktop/package.json"))["version"]
    conf = json.loads(_read_text(ROOT / "desktop/src-tauri/tauri.conf.json"))["version"]

    app_version_json = json.loads(_read_text(ROOT / "version.json"))
    app = str(app_version_json.get("version", ""))
    pyproject = pyproject_version(ROOT / "pyproject.toml")
    min_shell = str(app_version_json.get("minimum_shell_version", ""))

    if args.verbose:
        print(f"壳版本         Cargo={cargo}  package.json={pkg}  tauri.conf={conf}")
        print(f"应用版本       version.json={app}  pyproject={pyproject}")
        print(f"最低壳版本     {min_shell}")

    if len({cargo, pkg, conf}) != 1:
        errors.append(
            f"壳版本三处不一致：Cargo.toml={cargo} / package.json={pkg} / tauri.conf.json={conf}"
        )
    if app != pyproject:
        errors.append(f"应用版本不一致：version.json={app} / pyproject.toml={pyproject}")
    if not SEMVER_RE.match(app or ""):
        errors.append(f"version.json 的 version 不是合法 semver: {app!r}")
    if not SEMVER_RE.match(min_shell or ""):
        errors.append(f"version.json 的 minimum_shell_version 缺失或非法: {min_shell!r}")
    if SEMVER_RE.match(min_shell or "") and SEMVER_RE.match(cargo):
        if compare(numeric(min_shell), numeric(cargo)) > 0:
            errors.append(
                f"minimum_shell_version({min_shell}) 高于当前壳版本({cargo})："
                "这版安装包会要求比自己更新的壳，属自相矛盾配置"
            )

    if errors:
        print("[FAIL] 壳版本口径校验未通过：")
        for e in errors:
            print(f"  - {e}")
        print(
            "\n规则：壳版本独立于应用版本，但 Cargo.toml / package.json / "
            "tauri.conf.json 必须逐字相等。"
        )
        return 1

    print(f"[PASS] 壳版本 {cargo}（三处一致），应用版本 {app}，最低壳版本 {min_shell}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
