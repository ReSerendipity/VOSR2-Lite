#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""图片缩略图工具（预览端点与历史缩略图共用）。

统一按 EXIF 方向校正后等比缩放；有 alpha 存 PNG（保留透明），
否则存渐进式 JPEG（体积小、加载快）。
"""

from __future__ import annotations

import os

from PIL import Image, ImageOps


def has_alpha(img: Image.Image) -> bool:
    return img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info)


def thumbnail_path(cache_dir: str, stem: str, alpha: bool) -> str:
    return os.path.join(cache_dir, f"{stem}.{'png' if alpha else 'jpg'}")


def build_thumbnail(src: str, dest: str, max_side: int) -> None:
    """把 src 等比缩到长边 <= max_side 存为 dest。阻塞调用，请放到线程里跑。"""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with Image.open(src) as raw:
        img = ImageOps.exif_transpose(raw)
        img.thumbnail((max_side, max_side), Image.LANCZOS)
        if has_alpha(img):
            img.convert("RGBA").save(dest, "PNG", optimize=True)
        else:
            img.convert("RGB").save(dest, "JPEG", quality=88, optimize=True,
                                    progressive=True)


def probe_alpha(src: str) -> bool:
    """只读文件头判断是否带 alpha（不解码整图）。"""
    with Image.open(src) as img:
        return has_alpha(img)
