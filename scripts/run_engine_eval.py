#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""批量引擎评测：加载一次引擎，跑完整个 LR 目录，输出与参考同名的 PNG。

回应「模型质量零评测」整改项（2026-11-05 评估报告 P1 #1）。
与 scripts/smoke_infer.py 的区别：smoke 是单图冒烟（每图重载引擎），
本脚本为评测集批量推理（引擎只加载一次），跑完可用
scripts/eval_quality.py 对照 evalset/hr 算 PSNR/SSIM。

用法:
    python scripts/run_engine_eval.py --input-dir evalset/lr --output-dir outputs_eval
    python scripts/run_engine_eval.py --input-dir evalset/lr --output-dir outputs_eval \
        --checkpoint VOSR2 --upscale 4 --steps 1 --seed 42
"""

from __future__ import annotations

import argparse
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from loguru import logger  # noqa: E402
from PIL import Image  # noqa: E402

from app.integrated_app.config import AppConfig  # noqa: E402
from app.integrated_app.engines.vosr2_engine import RestoreParams, VOSR2Engine  # noqa: E402

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def main() -> None:
    parser = argparse.ArgumentParser(description="评测集批量推理（引擎单次加载）")
    parser.add_argument("--input-dir", required=True, help="LR 输入图目录（如 evalset/lr）")
    parser.add_argument("--output-dir", required=True, help="输出目录（文件名与输入主干一致）")
    parser.add_argument("--checkpoint", default="VOSR2", help="检查点目录名（默认 VOSR2）")
    parser.add_argument("--upscale", type=int, default=4, choices=[1, 2, 3, 4], help="放大倍率")
    parser.add_argument("--steps", type=int, default=1, help="采样步数（默认 1）")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（默认 42）")
    parser.add_argument(
        "--align-method", default="wavelet", choices=["wavelet", "adain", "nofix"], help="颜色对齐"
    )
    parser.add_argument("--tile-size", type=int, default=0, help="DiT 分块像素尺寸（0=自动）")
    parser.add_argument("--vae-tile-size", type=int, default=0, help="VAE 分块像素尺寸（0=自动）")
    args = parser.parse_args()

    names = sorted(
        name
        for name in os.listdir(args.input_dir)
        if os.path.splitext(name)[1].lower() in IMAGE_EXTENSIONS
    )
    if not names:
        raise SystemExit(f"输入目录没有图片: {args.input_dir}")
    os.makedirs(args.output_dir, exist_ok=True)

    config = AppConfig.load()
    model_root = config.abspath(config.get("model.pretrained_dir", "model"))
    engine = VOSR2Engine(args.checkpoint, model_root)
    logger.info(f"加载引擎: {args.checkpoint} (model_root={model_root})")
    t0 = time.time()
    engine.load()
    logger.info(f"引擎就绪，耗时 {time.time() - t0:.1f}s")

    params = RestoreParams(
        upscale=args.upscale,
        steps=args.steps,
        align_method=args.align_method,
        seed=args.seed,
        tile_size=args.tile_size,
        vae_tile_size=args.vae_tile_size,
    )

    failed: list[str] = []
    timings: list[float] = []
    for index, name in enumerate(names, start=1):
        stem = os.path.splitext(name)[0]
        source = os.path.join(args.input_dir, name)
        target = os.path.join(args.output_dir, f"{stem}.png")
        try:
            with Image.open(source) as img:
                image = img.convert("RGB")
            t1 = time.time()
            result = engine.restore(image, params)
            elapsed = time.time() - t1
            result.save(target)
            timings.append(elapsed)
            logger.info(
                f"[{index}/{len(names)}] {stem}: {image.size[0]}x{image.size[1]} -> "
                f"{result.size[0]}x{result.size[1]}，{elapsed:.1f}s"
            )
        except Exception as exc:
            failed.append(stem)
            logger.exception(f"[{index}/{len(names)}] {stem} 推理失败: {exc}")

    logger.info(
        f"批量推理完成: 成功 {len(timings)} / 失败 {len(failed)}"
        + (f"，单图中位耗时 {sorted(timings)[len(timings) // 2]:.1f}s" if timings else "")
    )
    if failed:
        print(f"失败图片: {failed}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
