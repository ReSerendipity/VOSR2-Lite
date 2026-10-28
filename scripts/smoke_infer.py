#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""CLI 推理冒烟：不经 Web 服务，直接对单张图片跑一遍引擎。

用于验证模型权重 / 引擎加载 / 推理链路是否正常，也是改推理代码后的
最快回归手段（先于 pytest）。

用法:
    python scripts/smoke_infer.py -i input.png -o outputs/ -u 2
    python scripts/smoke_infer.py -i input.png -o outputs/ -u 4 --steps 1 --seed 42
    python scripts/smoke_infer.py -i input.png -o outputs/ --tile-size 512 --vae-tile-size 1024
"""

from __future__ import annotations

import argparse
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from loguru import logger  # noqa: E402

from app.integrated_app.config import AppConfig  # noqa: E402
from app.integrated_app.engines.vosr2_engine import (  # noqa: E402
    RestoreParams,
    VOSR2Engine,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="VOSR2-Lite CLI 推理冒烟")
    parser.add_argument("--input", "-i", required=True, help="输入图片路径")
    parser.add_argument("--output-dir", "-o", default="outputs", help="输出目录（默认 outputs/）")
    parser.add_argument(
        "--upscale", "-u", type=int, default=2, choices=[1, 2, 3, 4], help="放大倍率（默认 2）"
    )
    parser.add_argument("--steps", type=int, default=1, help="采样步数（默认 1）")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（默认 42）")
    parser.add_argument(
        "--align-method",
        default="wavelet",
        choices=["wavelet", "adain", "nofix"],
        help="颜色对齐（默认 wavelet）",
    )
    parser.add_argument("--tile-size", type=int, default=0, help="DiT 分块像素尺寸（0=关闭）")
    parser.add_argument("--vae-tile-size", type=int, default=0, help="VAE 分块像素尺寸（0=整图）")
    parser.add_argument("--checkpoint", "-c", default="VOSR2", help="检查点目录名（默认 VOSR2）")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        raise SystemExit(f"输入文件不存在: {args.input}")
    os.makedirs(args.output_dir, exist_ok=True)

    from PIL import Image

    config = AppConfig.load()
    model_root = config.abspath(config.get("model.pretrained_dir", "model"))
    engine = VOSR2Engine(args.checkpoint, model_root)

    logger.info(f"加载引擎: {args.checkpoint} (model_root={model_root})")
    t0 = time.time()
    engine.load()
    logger.info(f"引擎就绪，耗时 {time.time() - t0:.1f}s")

    with Image.open(args.input) as img:
        image = img.convert("RGB")
    logger.info(f"输入: {args.input} {image.size[0]}x{image.size[1]}")

    params = RestoreParams(
        upscale=args.upscale,
        steps=args.steps,
        align_method=args.align_method,
        seed=args.seed,
        tile_size=args.tile_size,
        vae_tile_size=args.vae_tile_size,
    )

    def _progress(stage: str, ratio: float) -> None:
        logger.info(f"进度: {stage} ({ratio * 100:.0f}%)")

    t0 = time.time()
    result = engine.restore(image, params, progress_cb=_progress)
    elapsed = time.time() - t0

    stem = os.path.splitext(os.path.basename(args.input))[0]
    out_path = os.path.join(args.output_dir, f"{stem}_x{args.upscale}.png")
    result.save(out_path)

    logger.info(
        f"完成: {out_path} ({result.size[0]}x{result.size[1]}, " f"推理耗时 {elapsed:.1f}s)"
    )
    print(f"\n输出: {out_path}")


if __name__ == "__main__":
    main()
