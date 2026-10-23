#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""上传文件魔数校验：防伪造扩展名上传恶意内容。"""

from __future__ import annotations

# 支持的文件格式 -> 魔数签名（取前缀即可）
_MAGIC_SIGNATURES: dict[str, list[bytes]] = {
    "png": [b"\x89PNG\r\n\x1a\n"],
    "jpg": [b"\xff\xd8\xff"],
    "gif": [b"GIF87a", b"GIF89a"],
    "bmp": [b"BM"],
    "webp": [b"RIFF"],  # WEBP  additionally bytes 8-12 == 'WEBP'
    "tiff": [b"II*\x00", b"MM\x00*"],
}

_EXT_TO_KIND = {
    ".png": "png", ".jpg": "jpg", ".jpeg": "jpg", ".gif": "gif",
    ".bmp": "bmp", ".webp": "webp", ".tif": "tiff", ".tiff": "tiff",
}

ALLOWED_IMAGE_EXTENSIONS = set(_EXT_TO_KIND)


def validate_upload_magic(filename: str, head: bytes) -> bool:
    """按扩展名对应的魔数校验文件头（head 为前 16 字节）。"""
    import os

    ext = os.path.splitext(filename)[1].lower()
    kind = _EXT_TO_KIND.get(ext)
    if kind is None:
        return False
    signatures = _MAGIC_SIGNATURES[kind]
    if not any(head.startswith(sig) for sig in signatures):
        return False
    if kind == "webp":
        return head[8:12] == b"WEBP"
    return True
