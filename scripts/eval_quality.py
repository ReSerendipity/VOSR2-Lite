#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""图像质量评测指标：PSNR / SSIM（纯 numpy 实现，无新增第三方依赖）。

回应「模型质量零评测」整改项（2026-11-05 评估报告 P1 #1）：给出可复算的
客观画质指标，超分输出对照参考图逐对计算并汇总。

指标口径（固定，改动即破坏基线可比性）：
- PSNR：在 RGB 三通道（float32，0-255）上算 MSE，``10*log10(255^2 / MSE)``；
  两图逐像素完全一致时按惯例记 99.99 dB（避免 JSON 里出现 inf）。
- SSIM：Wang et al. 2004 单尺度 SSIM，11×11 高斯窗（σ=1.5）、K1=0.01、
  K2=0.03、数据范围 L=255，在 BT.601 亮度通道上计算，取图均值。
  （超分评测惯例：亮度结构相似度为主，避免色度细微偏移主导分数。）

用法:
    # 单对图片
    python scripts/eval_quality.py --ref a.png --out b.png

    # 目录对目录（按文件名主干一一对应），汇总打印并写 JSON
    python scripts/eval_quality.py --ref-dir evalset/hr --out-dir outputs_eval \
        --json outputs_eval/metrics.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

DATA_RANGE = 255.0
MAX_PSNR_DB = 99.99  # 完全一致时的 PSNR 记账值（真实值 +inf，JSON 不支持）
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

_SSIM_WINDOW_SIZE = 11
_SSIM_SIGMA = 1.5
_SSIM_K1 = 0.01
_SSIM_K2 = 0.03


def psnr(ref: np.ndarray, out: np.ndarray, data_range: float = DATA_RANGE) -> float:
    """PSNR（RGB 或任意同形数组）；MSE=0 时返回 MAX_PSNR_DB 记账值。"""
    if ref.shape != out.shape:
        raise ValueError(f"尺寸不一致: {ref.shape} vs {out.shape}")
    mse = float(np.mean((np.asarray(ref, np.float64) - np.asarray(out, np.float64)) ** 2))
    if mse == 0.0:
        return MAX_PSNR_DB
    return float(10.0 * math.log10((data_range**2) / mse))


def _gaussian_kernel_1d(size: int = _SSIM_WINDOW_SIZE, sigma: float = _SSIM_SIGMA) -> np.ndarray:
    offsets = np.arange(size, dtype=np.float64) - (size - 1) / 2.0
    kernel = np.exp(-(offsets**2) / (2.0 * sigma**2))
    return kernel / kernel.sum()


def _separable_filter(img: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """一维核的水平和垂直相关（'valid' 输出），reflect 边界。确定性实现。"""
    size = kernel.size
    radius = size // 2
    height, width = img.shape
    padded = np.pad(img, ((0, 0), (radius, radius)), mode="reflect")
    tmp = np.zeros((height, width), dtype=np.float64)
    for i in range(size):
        tmp += kernel[i] * padded[:, i : i + width]
    padded = np.pad(tmp, ((radius, radius), (0, 0)), mode="reflect")
    result = np.zeros((height, width), dtype=np.float64)
    for i in range(size):
        result += kernel[i] * padded[i : i + height, :]
    return result


def ssim(ref: np.ndarray, out: np.ndarray, data_range: float = DATA_RANGE) -> float:
    """单尺度 SSIM（亮度通道）；输入为 0-255 float 数组，返回全图均值。"""
    if ref.shape != out.shape:
        raise ValueError(f"尺寸不一致: {ref.shape} vs {out.shape}")
    x = to_luma_array(ref)
    y = to_luma_array(out)
    kernel = _gaussian_kernel_1d()
    c1 = (_SSIM_K1 * data_range) ** 2
    c2 = (_SSIM_K2 * data_range) ** 2

    mu_x = _separable_filter(x, kernel)
    mu_y = _separable_filter(y, kernel)
    mu_x_sq = mu_x * mu_x
    mu_y_sq = mu_y * mu_y
    mu_xy = mu_x * mu_y
    sigma_x_sq = _separable_filter(x * x, kernel) - mu_x_sq
    sigma_y_sq = _separable_filter(y * y, kernel) - mu_y_sq
    sigma_xy = _separable_filter(x * y, kernel) - mu_xy

    numerator = (2.0 * mu_xy + c1) * (2.0 * sigma_xy + c2)
    denominator = (mu_x_sq + mu_y_sq + c1) * (sigma_x_sq + sigma_y_sq + c2)
    return float(np.mean(numerator / denominator))


def to_luma_array(arr: np.ndarray) -> np.ndarray:
    """RGB 数组 -> 亮度；若已是二维则原样返回。"""
    arr = np.asarray(arr, dtype=np.float64)
    if arr.ndim == 2:
        return arr
    if arr.ndim == 3 and arr.shape[2] == 3:
        return 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    raise ValueError(f"不支持的数组形状: {arr.shape}")


def compare_pair(ref_path: str, out_path: str) -> dict:
    """对比一对图片，返回 {"file", "psnr_db", "ssim"}。"""
    with Image.open(ref_path) as ref_img, Image.open(out_path) as out_img:
        ref = np.asarray(ref_img.convert("RGB"), dtype=np.float64)
        out = np.asarray(out_img.convert("RGB"), dtype=np.float64)
    if ref.shape != out.shape:
        raise ValueError(
            f"参考与输出尺寸不一致: {os.path.basename(ref_path)} {ref.shape[1]}x{ref.shape[0]} "
            f"vs {os.path.basename(out_path)} {out.shape[1]}x{out.shape[0]}"
        )
    return {
        "file": os.path.basename(ref_path),
        "psnr_db": psnr(ref, out),
        "ssim": ssim(ref, out),
    }


def _index_by_stem(directory: str) -> dict[str, str]:
    index: dict[str, str] = {}
    for name in sorted(os.listdir(directory)):
        stem, ext = os.path.splitext(name)
        if ext.lower() in IMAGE_EXTENSIONS:
            index[stem] = os.path.join(directory, name)
    return index


def compare_dirs(ref_dir: str, out_dir: str) -> dict:
    """目录对目录对比（按文件名主干匹配），返回汇总 JSON 结构。"""
    refs = _index_by_stem(ref_dir)
    outs = _index_by_stem(out_dir)
    if not refs:
        raise SystemExit(f"参考目录没有可比图片: {ref_dir}")
    missing = sorted(set(refs) - set(outs))
    extra = sorted(set(outs) - set(refs))
    per_image = [compare_pair(refs[stem], outs[stem]) for stem in sorted(set(refs) & set(outs))]
    if not per_image:
        raise SystemExit("参考与输出目录没有同名（主干）图片可对比")
    psnr_values = [item["psnr_db"] for item in per_image]
    ssim_values = [item["ssim"] for item in per_image]
    return {
        "schema": 1,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "n_images": len(per_image),
        "psnr_mean_db": round(float(np.mean(psnr_values)), 4),
        "ssim_mean": round(float(np.mean(ssim_values)), 6),
        "psnr_min_db": round(float(np.min(psnr_values)), 4),
        "ssim_min": round(float(np.min(ssim_values)), 6),
        "per_image": per_image,
        "missing_in_output": missing,
        "extra_in_output": extra,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="PSNR / SSIM 图像质量评测")
    parser.add_argument("--ref", help="单对模式：参考图路径")
    parser.add_argument("--out", help="单对模式：待评图路径")
    parser.add_argument("--ref-dir", help="目录模式：参考图目录")
    parser.add_argument("--out-dir", help="目录模式：待评图目录")
    parser.add_argument("--json", help="把汇总结果写入 JSON 文件（可选）")
    args = parser.parse_args()

    if args.ref and args.out:
        result = compare_pair(args.ref, args.out)
        print(f"{result['file']}: PSNR={result['psnr_db']:.2f} dB  SSIM={result['ssim']:.4f}")
        summary = {
            "schema": 1,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "n_images": 1,
            "psnr_mean_db": result["psnr_db"],
            "ssim_mean": result["ssim"],
            "per_image": [result],
            "missing_in_output": [],
            "extra_in_output": [],
        }
    elif args.ref_dir and args.out_dir:
        summary = compare_dirs(args.ref_dir, args.out_dir)
        for item in summary["per_image"]:
            print(f"{item['file']}: PSNR={item['psnr_db']:.2f} dB  SSIM={item['ssim']:.4f}")
        if summary["missing_in_output"]:
            print(
                f"输出目录缺图 {len(summary['missing_in_output'])} 张: "
                f"{summary['missing_in_output']}"
            )
        print(
            f"\n共 {summary['n_images']} 张 | PSNR 均值 {summary['psnr_mean_db']:.2f} dB "
            f"(最低 {summary['psnr_min_db']:.2f}) | SSIM 均值 {summary['ssim_mean']:.4f} "
            f"(最低 {summary['ssim_min']:.4f})"
        )
    else:
        parser.error("请指定 --ref/--out（单对）或 --ref-dir/--out-dir（目录）")
        return

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"结果已写入: {args.json}")


if __name__ == "__main__":
    main()
