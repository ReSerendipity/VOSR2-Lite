#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""颜色对齐：wavelet（低频取参考）/ adain（统计量对齐）/ 原样返回。

wavelet 是默认方法：低频取自参考图（还原真实色调），高频保留模型生成细节——
两者分工是它与 adain 的本质区别（adain 会把生成细节一起拉向参考图统计量）。
"""

from __future__ import annotations

import numpy as np
import torch
from PIL import Image


def align_images(sr_img: Image.Image, input_img: Image.Image, method: str) -> Image.Image:
    """按 method 对齐颜色；未知方法原样返回（配置写错不应崩，也不应静默改色）。"""
    if method == "adain":
        return _adain_color_fix(sr_img, input_img)
    if method == "wavelet":
        return _wavelet_color_fix(sr_img, input_img)
    return sr_img


def _adain_color_fix(target: Image.Image, source: Image.Image) -> Image.Image:
    from torchvision.transforms.functional import to_pil_image, to_tensor

    target_tensor = to_tensor(target).unsqueeze(0)
    source_tensor = to_tensor(source).unsqueeze(0)
    eps = 1e-5
    target_mean = torch.mean(target_tensor, dim=[2, 3], keepdim=True)
    target_std = torch.std(target_tensor, dim=[2, 3], keepdim=True) + eps
    source_mean = torch.mean(source_tensor, dim=[2, 3], keepdim=True)
    source_std = torch.std(source_tensor, dim=[2, 3], keepdim=True) + eps
    target_tensor = (target_tensor - target_mean) / target_std * source_std + source_mean
    return to_pil_image(torch.clamp(target_tensor[0], 0, 1))


def _wavelet_color_fix(target: Image.Image, source: Image.Image) -> Image.Image:
    import cv2

    target_np = np.array(target).astype(np.float32) / 255.0
    source_np = (
        np.array(source.resize(target.size, Image.Resampling.LANCZOS)).astype(np.float32) / 255.0
    )
    sigma = 5
    source_low = cv2.GaussianBlur(source_np, (0, 0), sigma)
    target_low = cv2.GaussianBlur(target_np, (0, 0), sigma)
    target_high = target_np - target_low
    result = np.clip(source_low + target_high, 0, 1) * 255.0
    return Image.fromarray(result.astype(np.uint8))
