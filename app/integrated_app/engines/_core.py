#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""引擎共享基础件：类型别名、参数/状态数据类、张量形状辅助与硬约束常量。

被 ``vosr2_engine`` 与其拆分模块共同引用，本身不依赖任何同级模块（避免环）。
"""

from __future__ import annotations

import inspect
import time
from dataclasses import dataclass
from typing import Callable

import torch

from app.integrated_app.exceptions import InferenceCancelled
from vosr import tiled_vae

# 8 倍数补齐（VAE 对齐）；与上游 tiled_vae._pad_to_multiple 同一实现
_pad_to_multiple = tiled_vae._pad_to_multiple

# 多步模型（*_ms）训练分辨率为 512，输出过小时（实测 ≤256px）25 步轨迹会发散；
# 384px 起稳定（2026-10-24 实测：256→糊成色块，384/512/1548 正常）
MULTISTEP_MIN_SIDE = 384

# 进度回调：(stage, ratio 0..1[, detail])。detail 是可选文案（如「第 3 块 / 共 45 块」），
# 历史调用方多为双参数实现，故用 ... 放宽签名，由 _accepts_detail 运行时判定。
ProgressCb = Callable[..., None]

# 瓦片级进度的最小上报间隔（秒）。大图单块可能 0.5s 以上，若每块都写库会把
# DB 与 SSE 快照打满；按时间节流既让进度持续变化，又不拖慢推理。
TILE_PROGRESS_INTERVAL = 0.2


def _accepts_detail(cb: ProgressCb | None) -> bool:
    """判断进度回调是否接受第三个 ``detail`` 参数（兼容历史双参数回调）。"""
    if cb is None:
        return False
    try:
        sig = inspect.signature(cb)
    except (TypeError, ValueError):  # 内建或不可解析的可调用对象
        return False
    positional = 0
    for param in sig.parameters.values():
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            return True
        if param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            positional += 1
    return positional >= 3


def make_tile_reporter(
    cb: ProgressCb | None,
    total: int,
    *,
    stage: str,
    base: float,
    span: float,
    interval: float = TILE_PROGRESS_INTERVAL,
    clock: Callable[[], float] | None = None,
) -> Callable[[int], None]:
    """构造瓦片阶段的进度上报器（按块计数 + 时间节流）。

    返回 ``report(done)``：``done`` 为已完成瓦片数（1..total），
    ratio = ``base`` + ``span`` × done / total。首块与末块强制上报，
    中间块仅在距上次上报 ≥ ``interval`` 秒时上报。

    ``clock`` 可注入，单测用假时钟验证节流，不依赖真实时间。
    """
    if cb is None or total <= 0:
        return lambda _done: None

    detail_capable = _accepts_detail(cb)
    now_fn = clock or time.monotonic
    last_emit = float("-inf")

    def report(done: int) -> None:
        nonlocal last_emit
        done = max(1, min(int(done), total))
        now = now_fn()
        # 首块立刻给反馈，末块必报（保证 0.4→0.85 的终点准确），中间按时间节流
        if not (done <= 1 or done >= total or now - last_emit >= interval):
            return
        last_emit = now
        ratio = base + span * (done / total)
        detail = f"第 {done} 块 / 共 {total} 块"
        try:
            if detail_capable:
                cb(stage, ratio, detail)
            else:
                cb(stage, ratio)
        except InferenceCancelled:
            raise  # 取消必须透传：它是协作式中断的信号，不能被吞
        except Exception:
            pass  # 进度上报失败不影响推理

    return report


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
