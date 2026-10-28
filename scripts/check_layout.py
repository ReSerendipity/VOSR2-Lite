#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""仓库布局守卫：防止目录/模块被误移、误删，以及家族禁入文件混进版本库。

CI 每次 push/PR 都会跑（.github/workflows/structure-guard.yml），本地也可直接
``python scripts/check_layout.py``。

检查三类：
1. **必需路径存在**：顶层入口、应用骨架、拆分后的引擎子模块；
2. **禁入文件**：AGENTS.md 家族文件、data/ 下的密钥与库、模型权重，一律不得入库；
3. **.gitignore 覆盖**：model/ data/ outputs/ logs/ 必须被忽略，否则大文件与
   本地状态会被误提交（历史上最容易犯的错）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_PATHS = [
    # 顶层入口
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "config.yaml",
    "requirements.txt",
    "pyproject.toml",
    ".gitignore",
    # 应用骨架
    "app/integrated_app/app_server.py",
    "app/integrated_app/config.py",
    "app/integrated_app/task_queue.py",
    "app/integrated_app/history_db.py",
    "app/integrated_app/middleware",
    "app/integrated_app/routes",
    "app/integrated_app/security",
    "app/integrated_app/templates",
    "app/integrated_app/static",
    "app/integrated_app/locales",
    # 引擎（2026-10-28 拆分后的模块布局）
    "app/integrated_app/engines/vosr2_engine.py",
    "app/integrated_app/engines/_core.py",
    "app/integrated_app/engines/_weights.py",
    "app/integrated_app/engines/_loaders.py",
    "app/integrated_app/engines/_tiling.py",
    "app/integrated_app/engines/_sampling.py",
    "app/integrated_app/engines/_color.py",
    # vendored 上游 + 脚本 + 测试
    "vosr/UPSTREAM.md",
    "scripts/download_model.py",
    "scripts/smoke_infer.py",
    "scripts/ci_import_smoke.py",
    "tests/conftest.py",
    # 文档与语言包
    "docs/用户手册.md",
    "app/integrated_app/locales/zh.json",
    "app/integrated_app/locales/zh-TW.json",
    "app/integrated_app/locales/en.json",
]

# 家族治理约定：AGENTS.md 的完整本地版不入库；但本仓根 AGENTS.md 是**公开精简版**
# （文件头带 `governance:agent-contract` 标记，只写公开安全的最小契约），允许入库。
# 因此这里按「有无公开标记」判定，而不是一刀切禁止。
AGENTS_PUBLIC_MARKER = "governance:agent-contract"
FORBIDDEN_NAMES = {"LOCAL_RULES.md", "FIX_LOG.md"}
FORBIDDEN_PATTERNS = [
    "data/.vosr2_secret",
    "data/history.db",
]
FORBIDDEN_SUFFIXES = (".safetensors", ".pth", ".ckpt", ".bin")

# 必须被 .gitignore 忽略的本地产物（用真实路径探测，比匹配模式字符串可靠）
GITIGNORED_PROBES = [
    "model/VOSR2/checkpoints/ema_model.safetensors",
    "data/history.db",
    "data/uploads/probe.png",
    "data/cache/thumbs/probe.jpg",
    "outputs/probe.png",
    "logs/app.log",
]


def _fail(msg: str) -> None:
    print(f"[FAIL] {msg}")


def _ok(msg: str) -> None:
    print(f"[OK] {msg}")


def _git(*args: str) -> list[str] | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return [line for line in out.stdout.splitlines() if line.strip()]


def check_required(failures: list[str]) -> None:
    missing = [p for p in REQUIRED_PATHS if not (ROOT / p).exists()]
    if missing:
        failures.append(f"缺失必需路径 {len(missing)} 项")
        for item in missing:
            _fail(f"缺失: {item}")
    else:
        _ok(f"必需路径齐全（{len(REQUIRED_PATHS)} 项）")


def check_gitignore(failures: list[str]) -> None:
    """用 git check-ignore 探测真实产物路径是否被忽略（权威判定，不靠模式字符串猜）。"""
    if _git("rev-parse", "--is-inside-work-tree") is None:
        print("[SKIP] 非 git 工作区，跳过 .gitignore 探测")
        return

    leaked = []
    for probe in GITIGNORED_PROBES:
        result = subprocess.run(
            ["git", "check-ignore", "-q", probe],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            leaked.append(probe)

    if leaked:
        failures.append(f"{len(leaked)} 个本地产物未被忽略")
        for item in leaked:
            _fail(f"未被 .gitignore 忽略: {item}")
    else:
        _ok(f"本地产物已被忽略（{len(GITIGNORED_PROBES)} 条探测路径）")


def check_forbidden(failures: list[str]) -> None:
    tracked = _git("ls-files")
    if tracked is None:
        print("[SKIP] 非 git 工作区，跳过「禁入文件入库」检查")
        return

    hits = []
    for path in tracked:
        name = Path(path).name
        if name == "AGENTS.md":
            head = ""
            try:
                head = "\n".join((ROOT / path).read_text(encoding="utf-8").splitlines()[:6])
            except OSError:
                pass
            if AGENTS_PUBLIC_MARKER not in head:
                hits.append((path, "缺少公开标记的 AGENTS.md 不得入库（完整版仅本地）"))
        elif name in FORBIDDEN_NAMES:
            hits.append((path, "家族约定：本地治理文件不入库"))
        elif path in FORBIDDEN_PATTERNS:
            hits.append((path, "本地状态/密钥不得入库"))
        elif path.endswith(FORBIDDEN_SUFFIXES):
            hits.append((path, "模型权重不得入库"))

    if hits:
        failures.append(f"禁入文件入库 {len(hits)} 项")
        for path, reason in hits:
            _fail(f"{path} — {reason}")
    else:
        _ok("无禁入文件入库（密钥 / 权重 / 本地治理文件）")


def main() -> int:
    failures: list[str] = []
    check_required(failures)
    check_gitignore(failures)
    check_forbidden(failures)

    if failures:
        print(f"\n布局守卫失败：{len(failures)} 类问题")
        return 1
    print("\n布局守卫通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
