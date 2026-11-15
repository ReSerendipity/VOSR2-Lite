#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""权重逐文件 sha256 清单（model/WEIGHTS_SHA256.json）的读写与校验。

回应「权重无逐文件哈希校验」整改项（2026-11-05 评估报告 P1 #4）。
官方未逐文件公布 sha256，故采用「首次下载/首次登记时钉入清单」策略：
清单里有记录的文件做强校验（不一致 = 损坏或被改动，加载侧拒绝加载、
下载侧删文件报错）；清单里没记录的文件只提示未登记，不阻断。

清单结构::

    {
      "schema": 1,
      "updated_at": "YYYY-MM-DD HH:MM:SS",
      "note": "...",
      "files": {"<相对 model/ 的路径>": {"sha256": "...", "size_bytes": 123}}
    }

清单文件随仓库分发（.gitignore 对 model/* 的忽略规则为其开例外），
换机器克隆后校验语义立即生效。
"""

from __future__ import annotations

import hashlib
import json
import os
import time

MANIFEST_FILENAME = "WEIGHTS_SHA256.json"
SCHEMA_VERSION = 1
_CHUNK = 1 << 20

STATUS_OK = "ok"
STATUS_MISMATCH = "mismatch"
STATUS_UNREGISTERED = "unregistered"


def sha256_file(path: str) -> str:
    """流式计算文件 sha256（1MB 分块，几 GB 级权重也不吃内存）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


class WeightsManifest:
    """WEIGHTS_SHA256.json 的内存视图（文件缺失或损坏视作空清单）。"""

    def __init__(self, entries: dict[str, dict] | None = None, updated_at: str = ""):
        self.entries: dict[str, dict] = entries or {}
        self.updated_at = updated_at

    @classmethod
    def load(cls, model_root: str) -> "WeightsManifest":
        path = os.path.join(model_root, MANIFEST_FILENAME)
        if not os.path.isfile(path):
            return cls()
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            # 清单损坏不致崩溃：等同空清单（加载侧只剩「未登记」告警，
            # 下载侧会把再次下载的文件重新钉入）
            return cls()
        files = data.get("files") if isinstance(data, dict) else None
        entries = dict(files) if isinstance(files, dict) else {}
        updated_at = str(data.get("updated_at", "")) if isinstance(data, dict) else ""
        return cls(entries=entries, updated_at=updated_at)

    def save(self, model_root: str) -> None:
        os.makedirs(model_root, exist_ok=True)
        payload = {
            "schema": SCHEMA_VERSION,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "note": "权重逐文件 sha256 清单：下载侧校验/首下钉入；引擎加载时强校验（未登记仅告警）",
            "files": dict(sorted(self.entries.items())),
        }
        path = os.path.join(model_root, MANIFEST_FILENAME)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def get(self, rel: str) -> dict | None:
        entry = self.entries.get(rel)
        return dict(entry) if isinstance(entry, dict) else None

    def record(self, rel: str, abs_path: str) -> dict:
        """登记/更新一个文件的哈希（首次下载钉入或 --record-manifest 补登记）。"""
        entry = {"sha256": sha256_file(abs_path), "size_bytes": os.path.getsize(abs_path)}
        self.entries[rel] = entry
        return entry

    def verify(self, rel: str, abs_path: str) -> str:
        """返回 STATUS_OK / STATUS_MISMATCH / STATUS_UNREGISTERED。

        文件是否存在不在此判定（模型扫描层负责缺文件检查）。
        """
        entry = self.get(rel)
        if entry is None:
            return STATUS_UNREGISTERED
        expected = str(entry.get("sha256", "")).strip().lower()
        if not expected:
            return STATUS_UNREGISTERED
        return STATUS_OK if sha256_file(abs_path) == expected else STATUS_MISMATCH
