#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""构建冻结评测集：参考图（HR）+ 低清输入（LR）+ 双三次参照（bicubic）。

回应「模型质量零评测」整改项（2026-11-05 评估报告 P1 #1）。产线：

    HR（参考图，size×size）--LANCZOS 缩小 1/upscale--> LR（引擎输入）
    LR --BICUBIC 放大 upscale--> bicubic_x4（最朴素无模型参照基线）

冻结纪律：
- 默认合成模式用固定种子逐图派生（RandomState(seed + 序号)），MT19937 流
  跨 numpy 版本稳定，同名图片逐字节可复现；
- hr/ 已存在时拒绝覆盖（防止评测集被悄悄换掉），需要重建用 --force；
- 后续要换真实图集：--from-images <目录>，center-crop 方形后同管线处理，
  指标脚本与基线格式不变。

用法:
    python scripts/build_eval_set.py                       # 默认 evalset/，16 张
    python scripts/build_eval_set.py --num 20 --size 256
    python scripts/build_eval_set.py --from-images photos/ --output-dir evalset
"""

from __future__ import annotations

import argparse
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

DEFAULT_SEED = 20261105
DEFAULT_NUM = 16
DEFAULT_SIZE = 256
DEFAULT_UPSCALE = 4

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


# ── 合成图案（每张一个确定性种子，顺序无关）────────────────────────────────
# 约定：fn(x, y, rng) -> float64 HxWx3（0-255，后续统一 clip/转 uint8）。
def _p_grating_vertical(x: np.ndarray, y: np.ndarray, rng: np.random.RandomState) -> np.ndarray:
    del y, rng
    n = x.shape[0]
    lum = 127.5 + 110.0 * np.sin(2.0 * np.pi * x * 12.0 / n)
    return np.stack([lum, lum, lum], axis=-1)


def _p_grating_diagonal(x: np.ndarray, y: np.ndarray, rng: np.random.RandomState) -> np.ndarray:
    del rng
    lum = 127.5 + 110.0 * np.sin(2.0 * np.pi * (x + y) / 9.0)
    return np.stack([lum, lum, lum], axis=-1)


def _p_radial_gradient(x: np.ndarray, y: np.ndarray, rng: np.random.RandomState) -> np.ndarray:
    del rng
    c = x.shape[0] / 2.0
    r = np.sqrt((x - c) ** 2 + (y - c) ** 2) / c
    return np.stack([255.0 * (1.0 - r), 255.0 * r, np.full_like(x, 127.5)], axis=-1)


def _p_concentric_rings(x: np.ndarray, y: np.ndarray, rng: np.random.RandomState) -> np.ndarray:
    del rng
    c = x.shape[0] / 2.0
    lum = 127.5 + 110.0 * np.sin(np.sqrt((x - c) ** 2 + (y - c) ** 2) / 3.0)
    return np.stack([lum, lum, lum], axis=-1)


def _p_checkerboard(x: np.ndarray, y: np.ndarray, rng: np.random.RandomState) -> np.ndarray:
    del rng
    cell = max(x.shape[0] // 16, 2)
    lum = 255.0 * (((x // cell).astype(int) + (y // cell).astype(int)) % 2)
    return np.stack([lum, lum, lum], axis=-1)


def _p_checkerboard_fine(x: np.ndarray, y: np.ndarray, rng: np.random.RandomState) -> np.ndarray:
    del rng
    cell = max(x.shape[0] // 64, 2)
    lum = 255.0 * (((x // cell).astype(int) + (y // cell).astype(int)) % 2)
    return np.stack([lum, lum, lum], axis=-1)


def _p_diagonal_stripes(x: np.ndarray, y: np.ndarray, rng: np.random.RandomState) -> np.ndarray:
    del rng
    width = max(x.shape[0] // 12, 3)
    band = ((x + y) // width).astype(int) % 2
    lum = 40.0 + 175.0 * band
    return np.stack([lum, lum, lum], axis=-1)


def _p_color_ramps(x: np.ndarray, y: np.ndarray, rng: np.random.RandomState) -> np.ndarray:
    del rng
    n = float(x.shape[0])
    r = 255.0 * x / n
    g = 255.0 * y / n
    b = 255.0 * (x + y) / (2.0 * n)
    return np.stack([r, g, b], axis=-1)


def _p_blobs(x: np.ndarray, y: np.ndarray, rng: np.random.RandomState) -> np.ndarray:
    n = x.shape[0]
    lum = np.full_like(x, 60.0)
    for _ in range(8):
        cx, cy = rng.uniform(0, n, 2)
        radius = rng.uniform(n / 12.0, n / 4.0)
        lum += rng.uniform(20, 60) * np.exp(-(((x - cx) ** 2 + (y - cy) ** 2) / (radius**2)))
    return np.stack([lum, lum * 0.9, lum * 0.8], axis=-1)


def _p_noise_texture(x: np.ndarray, y: np.ndarray, rng: np.random.RandomState) -> np.ndarray:
    del y
    n = x.shape[0]
    noise = rng.normal(127.5, 40.0, (n, n))
    # 3×3 均值平滑两轮形成低频纹理（纯 numpy，可复现，不依赖 scipy）
    smooth = noise.copy()
    for _ in range(2):
        padded = np.pad(smooth, 1, mode="reflect")
        smooth = (
            padded[:-2, :-2]
            + padded[:-2, 1:-1]
            + padded[:-2, 2:]
            + padded[1:-1, :-2]
            + padded[1:-1, 1:-1]
            + padded[1:-1, 2:]
            + padded[2:, :-2]
            + padded[2:, 1:-1]
            + padded[2:, 2:]
        ) / 9.0
    return np.stack([smooth, smooth, smooth], axis=-1)


def _p_plaid(x: np.ndarray, y: np.ndarray, rng: np.random.RandomState) -> np.ndarray:
    del rng
    lum = 127.5 + 90.0 * np.sin(2.0 * np.pi * x / 32.0) * np.sin(2.0 * np.pi * y / 32.0)
    return np.stack([lum, lum, lum], axis=-1)


def _p_starburst(x: np.ndarray, y: np.ndarray, rng: np.random.RandomState) -> np.ndarray:
    del rng
    c = x.shape[0] / 2.0
    angle = np.arctan2(y - c, x - c)
    radius = np.sqrt((x - c) ** 2 + (y - c) ** 2)
    lum = 127.5 + 110.0 * np.sign(np.cos(12.0 * angle)) * (radius > c / 4.0)
    return np.stack([lum, lum, lum], axis=-1)


def _p_circles(x: np.ndarray, y: np.ndarray, rng: np.random.RandomState) -> np.ndarray:
    n = x.shape[0]
    img = np.stack([80.0 + 0.4 * x, 80.0 + 0.3 * y, 110.0 * np.ones_like(x)], axis=-1)
    for _ in range(6):
        cx, cy = rng.uniform(0, n, 2)
        radius = rng.uniform(n / 16.0, n / 6.0)
        mask = (x - cx) ** 2 + (y - cy) ** 2 <= radius**2
        img[mask] = rng.uniform(40, 220, 3)
    return img


def _p_rectangles(x: np.ndarray, y: np.ndarray, rng: np.random.RandomState) -> np.ndarray:
    n = x.shape[0]
    img = np.stack(
        [200.0 * np.ones_like(x), 190.0 * np.ones_like(x), 180.0 * np.ones_like(x)], axis=-1
    )
    for _ in range(7):
        x0, y0 = rng.uniform(0, n * 0.8, 2)
        w, h = rng.uniform(n / 16.0, n / 3.0, 2)
        mask = (x >= x0) & (x <= x0 + w) & (y >= y0) & (y <= y0 + h)
        img[mask] = rng.uniform(20, 235, 3)
    return img


def _p_chirp(x: np.ndarray, y: np.ndarray, rng: np.random.RandomState) -> np.ndarray:
    del y, rng
    n = x.shape[0]
    t = x / n
    phase = 2.0 * np.pi * (3.0 * t + 9.0 * t * t)
    lum = 127.5 + 110.0 * np.sin(phase)
    return np.stack([lum, lum, lum], axis=-1)


def _p_mixed_hires(x: np.ndarray, y: np.ndarray, rng: np.random.RandomState) -> np.ndarray:
    base = 110.0 + 60.0 * np.sin(2.0 * np.pi * x / 7.0) * np.sin(2.0 * np.pi * y / 11.0)
    lum = np.clip(base + rng.normal(0.0, 12.0, x.shape), 0.0, 255.0)
    return np.stack([lum, lum, lum], axis=-1)


PATTERN_FNS = [
    _p_grating_vertical,
    _p_grating_diagonal,
    _p_radial_gradient,
    _p_concentric_rings,
    _p_checkerboard,
    _p_checkerboard_fine,
    _p_diagonal_stripes,
    _p_color_ramps,
    _p_blobs,
    _p_noise_texture,
    _p_plaid,
    _p_starburst,
    _p_circles,
    _p_rectangles,
    _p_chirp,
    _p_mixed_hires,
]

PATTERN_NAMES = [
    "grating_vertical",
    "grating_diagonal",
    "radial_gradient",
    "concentric_rings",
    "checkerboard_coarse",
    "checkerboard_fine",
    "diagonal_stripes",
    "color_ramps",
    "gaussian_blobs",
    "noise_texture",
    "plaid",
    "starburst",
    "random_circles",
    "random_rectangles",
    "chirp",
    "mixed_hires",
]


def _synthetic_hr(index: int, size: int, seed: int) -> Image.Image:
    fn = PATTERN_FNS[index % len(PATTERN_FNS)]
    rng = np.random.RandomState(seed + index)  # 每图独立种子：顺序无关、可复现
    ys, xs = np.mgrid[0:size, 0:size]
    xs = xs.astype(np.float64)
    ys = ys.astype(np.float64)
    arr = fn(xs, ys, rng)
    arr = np.clip(arr, 0.0, 255.0).astype(np.uint8)
    return Image.fromarray(arr, mode="RGB")


def _downscale(hr: Image.Image, upscale: int) -> Image.Image:
    low = (hr.width // upscale, hr.height // upscale)
    return hr.resize(low, Image.Resampling.LANCZOS)


def _upscale_bicubic(lr: Image.Image, upscale: int) -> Image.Image:
    return lr.resize((lr.width * upscale, lr.height * upscale), Image.Resampling.BICUBIC)


def _collect_source_images(source_dir: str, num: int, size: int) -> list[tuple[str, Image.Image]]:
    names = sorted(
        name
        for name in os.listdir(source_dir)
        if os.path.splitext(name)[1].lower() in IMAGE_EXTENSIONS
    )[:num]
    if not names:
        raise SystemExit(f"--from-images 目录里没有可用图片: {source_dir}")
    images: list[tuple[str, Image.Image]] = []
    for name in names:
        with Image.open(os.path.join(source_dir, name)) as img:
            rgb = img.convert("RGB")
            side = min(rgb.width, rgb.height)
            left, top = (rgb.width - side) // 2, (rgb.height - side) // 2
            cropped = rgb.crop((left, top, left + side, top + side))
            images.append(
                (os.path.splitext(name)[0], cropped.resize((size, size), Image.Resampling.LANCZOS))
            )
    return images


def build_set(
    output_dir: str,
    num: int = DEFAULT_NUM,
    size: int = DEFAULT_SIZE,
    upscale: int = DEFAULT_UPSCALE,
    seed: int = DEFAULT_SEED,
    from_images: str | None = None,
    force: bool = False,
) -> list[str]:
    """产出 hr/ lr/ bicubic_x<upscale>/ 三个子目录，返回写入的文件主干列表。"""
    hr_dir = os.path.join(output_dir, "hr")
    lr_dir = os.path.join(output_dir, "lr")
    bic_dir = os.path.join(output_dir, f"bicubic_x{upscale}")
    if os.path.isdir(hr_dir) and os.listdir(hr_dir) and not force:
        raise SystemExit(
            f"评测集已存在: {hr_dir}（冻结评测集不允许被悄悄覆盖；确认重建请加 --force）"
        )
    for path in (hr_dir, lr_dir, bic_dir):
        os.makedirs(path, exist_ok=True)

    if from_images:
        sources = _collect_source_images(from_images, num, size)
    else:
        sources = [
            (f"{i + 1:02d}_{PATTERN_NAMES[i % len(PATTERN_NAMES)]}", _synthetic_hr(i, size, seed))
            for i in range(num)
        ]

    stems: list[str] = []
    for stem, hr in sources:
        lr = _downscale(hr, upscale)
        bic = _upscale_bicubic(lr, upscale)
        hr.save(os.path.join(hr_dir, f"{stem}.png"))
        lr.save(os.path.join(lr_dir, f"{stem}.png"))
        bic.save(os.path.join(bic_dir, f"{stem}.png"))
        stems.append(stem)
    return stems


def main() -> None:
    parser = argparse.ArgumentParser(description="构建冻结评测集（hr / lr / bicubic 参照）")
    parser.add_argument("--output-dir", default="evalset", help="输出根目录（默认 evalset/）")
    parser.add_argument("--num", type=int, default=DEFAULT_NUM, help="图片数量（默认 16）")
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE, help="HR 边长（默认 256）")
    parser.add_argument("--upscale", type=int, default=DEFAULT_UPSCALE, help="放大倍率（默认 4）")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="合成种子（默认 20261105）")
    parser.add_argument(
        "--from-images", help="用指定目录的真实图片构建（center-crop 方形），替换合成图案"
    )
    parser.add_argument("--force", action="store_true", help="允许覆盖已有评测集")
    args = parser.parse_args()

    stems = build_set(
        args.output_dir,
        num=args.num,
        size=args.size,
        upscale=args.upscale,
        seed=args.seed,
        from_images=args.from_images,
        force=args.force,
    )
    print(
        f"评测集已生成: {args.output_dir}/hr lr bicubic_x{args.upscale}，"
        f"共 {len(stems)} 张（HR {args.size}px，LR {args.size // args.upscale}px）"
    )


if __name__ == "__main__":
    main()
