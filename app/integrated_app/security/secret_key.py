#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""服务端密钥：CSRF token 的 HMAC 签名密钥（持久化，跨重启有效）。

取值优先级：
1. 环境变量 ``VOSR2_SECRET_KEY``（hex 或任意字符串，容器化/多实例部署用）；
2. 密钥文件 ``data/.vosr2_secret``（首次启动生成并落盘）；
3. 生成 32 字节随机密钥并写入该文件。

文件权限尽力收紧到 0o600（POSIX）；Windows 上 chmod 只影响只读位，属已知限制
——密钥文件在 data/ 下，与历史库同级，已由 .gitignore 排除。
"""

from __future__ import annotations

import logging
import os
import secrets
import stat
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_KEY_BYTES = 32
_ENV_VAR = "VOSR2_SECRET_KEY"
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_KEY_FILE = _PROJECT_ROOT / "data" / ".vosr2_secret"

_lock = threading.Lock()
# 按路径分键缓存：不同 key_file 互不串用（历史上单例缓存踩过这个坑）
_cache: dict[Path, bytes] = {}


def default_key_file() -> Path:
    """默认密钥文件路径（``<repo>/data/.vosr2_secret``）。"""
    return _DEFAULT_KEY_FILE


def get_secret_key(key_file: str | os.PathLike | None = None) -> bytes:
    """取服务端密钥（环境变量 > 密钥文件 > 生成并落盘）。"""
    env_key = os.environ.get(_ENV_VAR, "").strip()
    if env_key:
        # 环境变量不进缓存：同一进程内改环境变量要立刻生效
        try:
            return bytes.fromhex(env_key)
        except ValueError:
            return env_key.encode("utf-8")

    resolved = Path(key_file).resolve() if key_file is not None else default_key_file().resolve()
    with _lock:
        cached = _cache.get(resolved)
        if cached is not None:
            return cached

        if resolved.exists():
            try:
                key = bytes.fromhex(resolved.read_text(encoding="utf-8").strip())
                if len(key) == _KEY_BYTES:
                    _harden(resolved)
                    _cache[resolved] = key
                    return key
                logger.warning("密钥文件长度异常，重新生成: %s", resolved)
            except (OSError, ValueError) as exc:
                logger.warning("读取密钥文件失败，重新生成: %s", exc)

        key = secrets.token_bytes(_KEY_BYTES)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(key.hex(), encoding="utf-8")
        _harden(resolved)
        _cache[resolved] = key
        logger.info("已生成并持久化服务端密钥: %s", resolved)
        return key


def _harden(path: Path) -> None:
    """权限收紧：POSIX 0o600；Windows 仅去掉组/其他用户写权限（尽力而为）。"""
    try:
        if os.name == "nt":
            os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
        else:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        logger.debug("密钥文件权限收紧失败（可忽略）: %s", exc)


def reset_cached_key() -> None:
    """清空密钥缓存（仅测试用）。"""
    with _lock:
        _cache.clear()
