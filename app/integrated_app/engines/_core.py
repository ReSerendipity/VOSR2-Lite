#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""引擎共享基础件：类型别名、参数/状态数据类、张量形状辅助与硬约束常量。

被 ``vosr2_engine`` 与其拆分模块共同引用，本身不依赖任何同级模块（避免环）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import torch

from vosr import tiled_vae

# 8 倍数补齐（VAE 对齐）；与上游 tiled_vae._pad_to_multiple 同一实现
_pad_to_multiple = tiled_vae._pad_to_multiple

# 多步模型（*_ms）训练分辨率为 512，输出过小时（实测 ≤256px）25 步轨迹会发散；
# 384px 起稳定（2026-10-24 实测：256→糊成色块，384/512/1548 正常）
MULTISTEP_MIN_SIDE = 384

ProgressCb = Callable[[str, float], None]  # (stage, ratio 0..1)

# 输出边长硬上限（像素）。4x 放大 + 大图很容易产生数小时且必然 OOM 的任务，
# 超过上限直接报错并建议降低倍率（可用 config inference.max_output_side 调整）。
MAX_OUTPUT_SIDE_DEFAULT = 6144


@dataclass
class RestoreParams:
    """单次修复的推理参数（全部有默认值，UI 只传覆盖项）。"""

    upscale: int = 4
    steps: int = 1
    align_method: str = "wavelet"
    seed: int = 42
    tile_size: int = 0
    vae_tile_size: int = 0
    tile_overlap: int = 32
    output_format: str = "png"


@dataclass
class EngineStatus:
    loaded: bool = False
    checkpoint: str = ""
    device: str = ""
    dtype: str = ""
    error: str = ""
    vram_gb: float = 0.0
    load_seconds: float = 0.0


def _empty_cache() -> None:
    """阶段间释放未使用的显存（编码激活 / 解码中间量）。"""
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _pad_to_square(x: torch.Tensor) -> tuple[torch.Tensor, tuple[int, int]]:
    """reflect 填充到正方形（上游 forward_flexible 的 H==W 断言要求）。

    Returns: (padded, (orig_h, orig_w))
    """
    _, _, h, w = x.shape
    side = max(h, w)
    if h == side and w == side:
        return x, (h, w)
    padded = torch.nn.functional.pad(x, (0, side - w, 0, side - h), mode="reflect")
    return padded, (h, w)
