#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""评测产线测试：PSNR/SSIM 指标正确性 + 评测集构建确定性（纯 CPU，不碰权重）。"""

from __future__ import annotations

import math
import os

import numpy as np
import pytest
from PIL import Image

from scripts import build_eval_set, eval_quality


def _gradient_image(size: int = 64) -> np.ndarray:
    ys, xs = np.mgrid[0:size, 0:size]
    return np.stack(
        [xs * (255.0 / size), ys * (255.0 / size), np.full((size, size), 128.0)], axis=-1
    )


# ── PSNR ────────────────────────────────────────────────────────────────────
def test_psnr_identical_is_capped():
    arr = _gradient_image()
    assert eval_quality.psnr(arr, arr) == eval_quality.MAX_PSNR_DB


def test_psnr_known_value():
    ref = np.zeros((8, 8, 3))
    out = ref + 10.0  # MSE = 100
    expected = 10.0 * math.log10((255.0**2) / 100.0)
    assert abs(eval_quality.psnr(ref, out) - expected) < 1e-9


def test_psnr_size_mismatch_raises():
    with pytest.raises(ValueError, match="尺寸不一致"):
        eval_quality.psnr(np.zeros((4, 4, 3)), np.zeros((8, 8, 3)))


# ── SSIM ────────────────────────────────────────────────────────────────────
def test_ssim_identical_is_one():
    arr = _gradient_image()
    assert eval_quality.ssim(arr, arr) > 0.999


def test_ssim_noise_degradation_lower_and_bounded():
    rng = np.random.RandomState(7)
    arr = _gradient_image()
    noisy = np.clip(arr + rng.normal(0.0, 25.0, arr.shape), 0.0, 255.0)
    clean = eval_quality.ssim(arr, arr)
    degraded = eval_quality.ssim(arr, noisy)
    assert degraded < clean
    assert 0.0 <= degraded <= 1.0


def test_ssim_symmetric():
    rng = np.random.RandomState(11)
    a = _gradient_image()
    b = np.clip(a + rng.normal(0.0, 10.0, a.shape), 0.0, 255.0)
    assert abs(eval_quality.ssim(a, b) - eval_quality.ssim(b, a)) < 1e-12


def test_ssim_deterministic():
    arr = _gradient_image()
    first = eval_quality.ssim(arr, arr + 1.0)
    second = eval_quality.ssim(arr, arr + 1.0)
    assert first == second


# ── 目录对比 ────────────────────────────────────────────────────────────────
def _write_png(path: str, seed: int) -> None:
    rng = np.random.RandomState(seed)
    arr = rng.randint(0, 256, (32, 32, 3)).astype(np.uint8)
    Image.fromarray(arr).save(path)


def test_compare_dirs_summary(tmp_path):
    hr_dir = tmp_path / "hr"
    out_dir = tmp_path / "out"
    hr_dir.mkdir()
    out_dir.mkdir()
    for i in range(3):
        _write_png(str(hr_dir / f"{i:02d}.png"), i)
        _write_png(str(out_dir / f"{i:02d}.png"), i)
    _write_png(str(out_dir / "extra.png"), 99)  # 输出目录多一张

    summary = eval_quality.compare_dirs(str(hr_dir), str(out_dir))
    assert summary["n_images"] == 3
    assert summary["missing_in_output"] == []
    assert summary["extra_in_output"] == ["extra"]
    assert set(summary["per_image"][0]) == {"file", "psnr_db", "ssim"}
    assert 0.0 <= summary["ssim_mean"] <= 1.0


def test_compare_dirs_missing_reports(tmp_path):
    hr_dir = tmp_path / "hr"
    out_dir = tmp_path / "out"
    hr_dir.mkdir()
    out_dir.mkdir()
    _write_png(str(hr_dir / "a.png"), 1)
    _write_png(str(hr_dir / "b.png"), 2)
    _write_png(str(out_dir / "a.png"), 1)

    summary = eval_quality.compare_dirs(str(hr_dir), str(out_dir))
    assert summary["missing_in_output"] == ["b"]
    assert summary["n_images"] == 1


# ── 评测集构建 ──────────────────────────────────────────────────────────────
def test_build_eval_set_deterministic(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    stems_a = build_eval_set.build_set(str(first), num=3, size=64, upscale=4, seed=1234)
    stems_b = build_eval_set.build_set(str(second), num=3, size=64, upscale=4, seed=1234)
    assert stems_a == stems_b
    for stem in stems_a:
        for sub in ("hr", "lr", "bicubic_x4"):
            with open(os.path.join(first, sub, f"{stem}.png"), "rb") as fa:
                with open(os.path.join(second, sub, f"{stem}.png"), "rb") as fb:
                    assert fa.read() == fb.read(), f"{sub}/{stem} 两次构建不一致"


def test_build_eval_set_sizes(tmp_path):
    out = tmp_path / "evalset"
    stems = build_eval_set.build_set(str(out), num=2, size=64, upscale=4, seed=7)
    for stem in stems:
        with Image.open(out / "hr" / f"{stem}.png") as hr:
            assert hr.size == (64, 64)
        with Image.open(out / "lr" / f"{stem}.png") as lr:
            assert lr.size == (16, 16)
        with Image.open(out / "bicubic_x4" / f"{stem}.png") as bic:
            assert bic.size == (64, 64)


def test_build_eval_set_refuses_overwrite(tmp_path):
    out = tmp_path / "evalset"
    build_eval_set.build_set(str(out), num=1, size=32, upscale=2, seed=1)
    with pytest.raises(SystemExit, match="冻结评测集"):
        build_eval_set.build_set(str(out), num=1, size=32, upscale=2, seed=1)
