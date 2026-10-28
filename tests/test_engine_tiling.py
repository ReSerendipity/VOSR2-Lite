#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""分块决策 / 参数合并 / 权重发现 / 颜色对齐 / 多步守卫的 CPU 单元测试。

与 ``test_engine_unit.py`` 的分工：本文件专攻**阈值交叉点**与**数值断言**，
全部可在 CPU 上跑（不需要 GPU、不需要模型权重）。

背景：自动分块规则是 2026-10-23 一次真实 OOM 事故的修复产物
（3096² 输出下 Qwen VAE fp32 全图编码需 ~100GB）。阈值一旦被误改，
用户侧表现为必然 OOM 而非测试失败，因此这里把每个切换点都钉死。
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest
import torch
from PIL import Image

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


# ── 自动分块决策：DiT 阈值 512 ──────────────────────────────────────────────
@pytest.mark.parametrize(
    "side,expected",
    [
        (128, 0),
        (256, 0),
        (384, 0),
        (511, 0),
        (512, 0),  # ≤512 不分块
        (513, 512),
        (768, 512),
        (1024, 512),
        (2048, 512),  # >512 开 DiT 分块
        (3096, 512),
        (6144, 512),
    ],
)
def test_dit_tile_threshold(side, expected):
    """DiT 分块在输出 >512px 时开启（512 本身不分块，边界不含）。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    dit_tile, _ = VOSR2Engine._auto_tile_sizes(side, 10.8, 0, 0)
    assert dit_tile == expected


# ── 自动分块决策：VAE 阈值 1024 + 显存分档 ──────────────────────────────────
@pytest.mark.parametrize(
    "side,expected_vae",
    [
        (128, 0),
        (512, 0),
        (1024, 0),  # ≤1024 整图编码
        (1025, 512),
        (2048, 512),
        (3096, 512),
        (6144, 512),
    ],
)
def test_vae_tile_threshold_low_vram(side, expected_vae):
    """≤16GB 可用显存：输出 >1024px 时 VAE 分块固定 512。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    _, vae_tile = VOSR2Engine._auto_tile_sizes(side, 10.8, 0, 0)
    assert vae_tile == expected_vae


@pytest.mark.parametrize(
    "free_vram,expected_vae",
    [
        (0.0, 512),
        (8.0, 512),
        (12.0, 512),
        (16.0, 512),  # 16.0 不算「大于 16」
        (16.1, 1024),
        (24.0, 1024),
        (48.0, 1024),  # >16GB 放宽到 1024
    ],
)
def test_vae_tile_vram_boundary(free_vram, expected_vae):
    """显存分档边界：严格大于 16GB 才放宽到 1024（16.0 仍走 512）。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    _, vae_tile = VOSR2Engine._auto_tile_sizes(3096, free_vram, 0, 0)
    assert vae_tile == expected_vae


@pytest.mark.parametrize(
    "side,vram,expected",
    [
        (256, 10.8, (0, 0)),
        (1024, 10.8, (512, 0)),
        (1025, 10.8, (512, 512)),
        (3096, 10.8, (512, 512)),
        (3096, 24.0, (512, 1024)),
        (6144, 24.0, (512, 1024)),
    ],
)
def test_auto_tile_combined(side, vram, expected):
    """两级分块的组合结果（DiT × VAE）必须同时正确。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    assert VOSR2Engine._auto_tile_sizes(side, vram, 0, 0) == expected


# ── 用户显式指定优先 ────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "user_tile,user_vae,expected",
    [
        (256, 0, (256, 512)),  # 只指定 DiT，VAE 仍按规则自动
        (0, 1024, (512, 1024)),  # 只指定 VAE，DiT 仍按规则自动
        (256, 1024, (256, 1024)),  # 两者都指定
        (1, 1, (1, 1)),  # 极小值也算显式指定（>0 即生效）
    ],
)
def test_auto_tile_user_override(user_tile, user_vae, expected):
    """用户显式指定（>0）必须覆盖自动决策。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    assert VOSR2Engine._auto_tile_sizes(3096, 10.8, user_tile, user_vae) == expected


@pytest.mark.parametrize("user_tile,user_vae", [(-1, -1), (0, 0), (-512, -1024)])
def test_auto_tile_non_positive_treated_as_auto(user_tile, user_vae):
    """0 与负数都表示「未指定」，必须回落到自动决策而不是关掉分块。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    assert VOSR2Engine._auto_tile_sizes(3096, 10.8, user_tile, user_vae) == (512, 512)


# ── 输出边长上限前置检查 ────────────────────────────────────────────────────
def test_preflight_allows_exact_cap():
    """正好等于上限必须放行（边界是 >，不是 >=）。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    engine = VOSR2Engine("VOSR2", "/tmp/whatever", max_output_side=6144)
    assert engine._preflight_output(6144, 4) == 6144
    assert engine._preflight_output(6145 - 1, 1) == 6144


@pytest.mark.parametrize(
    "output_side,upscale,expected_factor",
    [
        (8000, 4, 3),  # 输入 2000px -> 6144//2000 = 3
        (7000, 2, 1),  # 输入 3500px -> 6144//3500 = 1
        (12288, 4, 2),  # 输入 3072px -> 6144//3072 = 2
    ],
)
def test_preflight_suggested_factor(output_side, upscale, expected_factor):
    """超限错误必须带「本图最高 ×N」的可操作建议，且 N 可复现。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    engine = VOSR2Engine("VOSR2", "/tmp/whatever", max_output_side=6144)
    with pytest.raises(ValueError, match=f"最高 ×{expected_factor}"):
        engine._preflight_output(output_side, upscale)


def test_max_output_side_defaults():
    """未配置时用内置 6144；显式 0 也回落到默认（0 不能用来关闭上限）。"""
    from app.integrated_app.engines.vosr2_engine import (
        MAX_OUTPUT_SIDE_DEFAULT,
        VOSR2Engine,
    )

    assert MAX_OUTPUT_SIDE_DEFAULT == 6144
    assert VOSR2Engine("VOSR2", "/tmp/x").max_output_side == 6144
    assert VOSR2Engine("VOSR2", "/tmp/x", max_output_side=None).max_output_side == 6144
    assert VOSR2Engine("VOSR2", "/tmp/x", max_output_side=0).max_output_side == 6144
    assert VOSR2Engine("VOSR2", "/tmp/x", max_output_side=2048).max_output_side == 2048


# ── 单次请求参数叠加 ────────────────────────────────────────────────────────
def _engine_with_args(tmp_path, **overrides):
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    engine = VOSR2Engine("VOSR2", str(tmp_path))
    args = {
        "upscale": 4,
        "infer_steps": 1,
        "align_method": "wavelet",
        "tile_size": 0,
        "tile_overlap": 32,
        "vae_tile_size": 0,
        "resolution": 512,
        "dim": 1536,
    }
    args.update(overrides)
    from types import SimpleNamespace

    engine.args = SimpleNamespace(**args)
    return engine


def test_effective_args_overrides(tmp_path):
    """请求参数必须覆盖检查点 args 的对应字段。"""
    from app.integrated_app.engines.vosr2_engine import RestoreParams

    engine = _engine_with_args(tmp_path)
    params = RestoreParams(
        upscale=2, steps=25, align_method="adain", tile_size=256, vae_tile_size=512, tile_overlap=64
    )
    eff = engine._effective_args(params)
    assert (eff.upscale, eff.infer_steps, eff.align_method) == (2, 25, "adain")
    assert (eff.tile_size, eff.vae_tile_size, eff.tile_overlap) == (256, 512, 64)


def test_effective_args_keeps_untouched_fields(tmp_path):
    """未被请求覆盖的字段（如 dim / resolution）必须原样保留。"""
    from app.integrated_app.engines.vosr2_engine import RestoreParams

    engine = _engine_with_args(tmp_path, resolution=1024, dim=2048)
    eff = engine._effective_args(RestoreParams())
    assert eff.resolution == 1024 and eff.dim == 2048


def test_effective_args_does_not_mutate_checkpoint_args(tmp_path):
    """_effective_args 必须返回副本，不能污染检查点级 args（否则跨任务串味）。"""
    from app.integrated_app.engines.vosr2_engine import RestoreParams

    engine = _engine_with_args(tmp_path)
    engine._effective_args(RestoreParams(upscale=1, steps=25, tile_size=512))
    assert engine.args.upscale == 4
    assert engine.args.infer_steps == 1
    assert engine.args.tile_size == 0


# ── args.json 与兜底默认的合并 ──────────────────────────────────────────────
def test_args_defaults_complete_when_json_missing(tmp_path):
    """args.json 缺失时不能崩，且 _ARG_DEFAULTS 的每个键都要出现在结果里。"""
    from app.integrated_app.engines.vosr2_engine import _ARG_DEFAULTS, VOSR2Engine

    engine = VOSR2Engine("VOSR2", str(tmp_path))
    args = engine._load_args()
    assert set(_ARG_DEFAULTS) - set(vars(args)) == set()
    for key, val in _ARG_DEFAULTS.items():
        assert getattr(args, key) == val, key


def test_args_explicit_overrides_default(tmp_path):
    """args.json 里的值优先于兜底默认；未覆盖的键仍用默认。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    ckpt = tmp_path / "VOSR2"
    ckpt.mkdir()
    (ckpt / "args.json").write_text(
        '{"resolution": 1024, "infer_steps": 25, "distill_type": "multistep"}', encoding="utf-8"
    )
    args = VOSR2Engine("VOSR2", str(tmp_path))._load_args()
    assert (args.resolution, args.infer_steps) == (1024, 25)
    assert args.distill_type == "multistep"
    assert args.patch_size == 2 and args.ae_type == "qwen"


def test_args_extra_keys_preserved(tmp_path):
    """上游新增字段（不在 _ARG_DEFAULTS 里）必须原样保留，不能被裁掉。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    ckpt = tmp_path / "VOSR2"
    ckpt.mkdir()
    (ckpt / "args.json").write_text(
        '{"upstream_new_flag": true, "layer_dinov2b_list": [9, 17]}', encoding="utf-8"
    )
    args = VOSR2Engine("VOSR2", str(tmp_path))._load_args()
    assert args.upstream_new_flag is True
    assert args.layer_dinov2b_list == [9, 17]


def test_args_ae_path_rewritten_to_model_root(tmp_path):
    """ae_path 必须被改写到 model_root 下（对齐上游 preset/ckpts 布局）。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    ckpt = tmp_path / "VOSR2"
    ckpt.mkdir()
    (ckpt / "args.json").write_text(
        '{"ae_path": "preset/ckpts/Qwen-Image-vae-2d"}', encoding="utf-8"
    )
    args = VOSR2Engine("VOSR2", str(tmp_path))._load_args()
    assert args.ae_path == os.path.join(str(tmp_path), "Qwen-Image-vae-2d")
    assert os.path.dirname(args.ae_path) == str(tmp_path)


def test_args_without_ae_path_is_untouched(tmp_path):
    """没有 ae_path 时不能凭空造出该字段（sd2 系检查点即如此）。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    ckpt = tmp_path / "VOSR_0.5B_os"
    ckpt.mkdir()
    (ckpt / "args.json").write_text('{"ae_type": "sd2"}', encoding="utf-8")
    args = VOSR2Engine("VOSR_0.5B_os", str(tmp_path))._load_args()
    assert not hasattr(args, "ae_path")
    assert args.ae_type == "sd2"


# ── 权重发现顺序 ────────────────────────────────────────────────────────────
def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake")
    return path


def test_find_weights_priority_clean_weights(tmp_path):
    """clean_weights/ 优先级最高（三个目录都有文件时取它）。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    for rel in ("clean_weights", "checkpoints", ""):
        _touch(tmp_path / "VOSR2" / rel / "ema_model.safetensors")
    engine = VOSR2Engine("VOSR2", str(tmp_path))
    assert engine._find_weights() == os.path.join(
        str(tmp_path), "VOSR2", "clean_weights", "ema_model.safetensors"
    )


def test_find_weights_priority_checkpoints_over_root(tmp_path):
    """checkpoints/ 次之，优先于检查点根目录。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    _touch(tmp_path / "VOSR2" / "ema_model.safetensors")
    _touch(tmp_path / "VOSR2" / "checkpoints" / "ema_model.safetensors")
    engine = VOSR2Engine("VOSR2", str(tmp_path))
    assert engine._find_weights() == os.path.join(
        str(tmp_path), "VOSR2", "checkpoints", "ema_model.safetensors"
    )


def test_find_weights_ema_preferred_over_model(tmp_path):
    """同一目录内 ema_model.safetensors 优先于 model.safetensors。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    _touch(tmp_path / "VOSR2" / "checkpoints" / "model.safetensors")
    _touch(tmp_path / "VOSR2" / "checkpoints" / "ema_model.safetensors")
    engine = VOSR2Engine("VOSR2", str(tmp_path))
    assert os.path.basename(engine._find_weights()) == "ema_model.safetensors"


def test_find_weights_model_safetensors_fallback(tmp_path):
    """ema_model 缺失时回退 model.safetensors。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    _touch(tmp_path / "VOSR2" / "model.safetensors")
    engine = VOSR2Engine("VOSR2", str(tmp_path))
    assert engine._find_weights() == os.path.join(str(tmp_path), "VOSR2", "model.safetensors")


def test_find_weights_recursive_glob(tmp_path):
    """三个固定目录都没有时递归兜底（权重被放在子目录里）。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    target = _touch(tmp_path / "VOSR2" / "sub" / "dir" / "weights.safetensors")
    engine = VOSR2Engine("VOSR2", str(tmp_path))
    assert engine._find_weights() == str(target)


def test_find_weights_ignores_other_formats(tmp_path):
    """.bin / .pth 不算权重（下载脚本只产出 safetensors），应继续报缺失。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    _touch(tmp_path / "VOSR2" / "model.bin")
    _touch(tmp_path / "VOSR2" / "model.pth")
    engine = VOSR2Engine("VOSR2", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        engine._find_weights()


def test_find_weights_missing_message_is_actionable(tmp_path):
    """找不到权重时的报错必须告诉用户下一步命令。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    engine = VOSR2Engine("VOSR2", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="download_model"):
        engine._find_weights()


# ── 颜色对齐：三方法的数值行为 ──────────────────────────────────────────────
def _rgb(arr):
    """把 [0,1] 浮点数组转成 PIL RGB。"""
    return Image.fromarray(np.clip(arr * 255.0, 0, 255).astype(np.uint8), "RGB")


def test_align_nofix_is_passthrough():
    """nofix 必须原对象返回（不做任何转换，避免无谓的量化损失）。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    sr = Image.new("RGB", (32, 32), (128, 64, 32))
    src = Image.new("RGB", (32, 32), (200, 100, 50))
    assert VOSR2Engine._align(sr, src, "nofix") is sr


@pytest.mark.parametrize("method", ["bogus", "", "lab", "reinhard"])
def test_align_unknown_method_is_passthrough(method):
    """未知方法名一律原样返回（配置写错不应崩，也不应静默改色）。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    sr = Image.new("RGB", (16, 16), (10, 20, 30))
    src = Image.new("RGB", (16, 16), (200, 200, 200))
    assert VOSR2Engine._align(sr, src, method) is sr


def test_align_adain_matches_source_stats():
    """adain 后全图均值/标准差应贴近参考图（这是它唯一的职责）。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    rng = np.random.default_rng(0)
    tgt = np.clip(0.25 + rng.normal(0, 0.20, (64, 64, 3)), 0.02, 0.98)
    src = np.clip(0.65 + rng.normal(0, 0.10, (64, 64, 3)), 0.02, 0.98)
    out = np.asarray(VOSR2Engine._align(_rgb(tgt), _rgb(src), "adain"), dtype=np.float32) / 255.0
    assert out.mean() == pytest.approx(src.mean(), abs=0.02)
    assert out.std() == pytest.approx(src.std(), abs=0.02)


def test_align_adain_preserves_target_structure():
    """adain 只改色调不改结构：输出与原目标的相关性应极高。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    rng = np.random.default_rng(2)
    tgt = np.clip(0.5 + rng.normal(0, 0.15, (64, 64, 3)), 0.02, 0.98)
    src = np.clip(0.3 + rng.normal(0, 0.05, (64, 64, 3)), 0.02, 0.98)
    out = np.asarray(VOSR2Engine._align(_rgb(tgt), _rgb(src), "adain"), dtype=np.float32) / 255.0
    assert np.corrcoef(out.ravel(), tgt.ravel())[0, 1] > 0.95


def test_align_adain_accepts_different_sizes():
    """参考图尺寸与结果不同也能对齐（统计量是空间无关的）。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    sr = Image.new("RGB", (64, 64), (10, 10, 10))
    src = Image.new("RGB", (32, 48), (200, 200, 200))
    out = VOSR2Engine._align(sr, src, "adain")
    assert out.size == (64, 64)
    assert out.resize((1, 1)).getpixel((0, 0))[0] == pytest.approx(200, abs=6)


def test_align_wavelet_lowfreq_follows_source():
    """wavelet：低频取参考图（还原真实色调），这是它与 adain 的关键区别。"""
    import cv2

    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    yy, xx = np.mgrid[0:64, 0:64]
    src = np.stack([0.2 + 0.6 * xx / 63.0] * 3, axis=-1)  # 水平渐变
    rng = np.random.default_rng(1)
    tgt = np.clip(0.5 + rng.normal(0, 0.15, (64, 64, 3)), 0, 1)
    out = np.asarray(VOSR2Engine._align(_rgb(tgt), _rgb(src), "wavelet"), dtype=np.float32) / 255.0
    src_low = cv2.GaussianBlur(src, (0, 0), 5)
    out_low = cv2.GaussianBlur(out, (0, 0), 5)
    assert np.abs(out_low - src_low).mean() < 0.03


def test_align_wavelet_keeps_target_highfreq():
    """wavelet：高频取结果图（保住模型生成的细节，不被参考图抹平）。"""
    import cv2

    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    src = np.full((64, 64, 3), 0.5, dtype=np.float32)  # 纯低频参考
    rng = np.random.default_rng(3)
    tgt = np.clip(0.5 + rng.normal(0, 0.15, (64, 64, 3)), 0, 1)
    out = np.asarray(VOSR2Engine._align(_rgb(tgt), _rgb(src), "wavelet"), dtype=np.float32) / 255.0
    tgt_high = tgt - cv2.GaussianBlur(tgt, (0, 0), 5)
    out_high = out - cv2.GaussianBlur(out, (0, 0), 5)
    assert np.abs(out_high - tgt_high).mean() < 0.03


def test_align_wavelet_resizes_source_to_target():
    """参考图尺寸不同时必须先 LANCZOS 放大到结果尺寸，否则尺寸不匹配。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    sr = Image.new("RGB", (96, 96), (60, 60, 60))
    src = Image.new("RGB", (24, 24), (180, 180, 180))
    out = VOSR2Engine._align(sr, src, "wavelet")
    assert out.size == (96, 96)
    assert out.resize((1, 1)).getpixel((0, 0))[0] == pytest.approx(180, abs=8)


# ── 多步采样最小边长守卫 ────────────────────────────────────────────────────
@pytest.mark.parametrize("side", [64, 128, 256, 320, 383])
def test_multistep_guard_rejects_small(side):
    """≤383px 的多步输出必须被拒绝（实测 256px 发散成色块）。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    with pytest.raises(ValueError, match="多步采样"):
        VOSR2Engine._check_multistep_side(25, side, side)


@pytest.mark.parametrize("side", [384, 512, 1024, 1548, 3096])
def test_multistep_guard_allows_stable(side):
    """≥384px 的多步输出放行（384/512/1548 实测稳定）。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    assert VOSR2Engine._check_multistep_side(25, side, side) is None


def test_multistep_guard_uses_long_side():
    """判定用长边：竖长图 384x2048 不算小图，383x383 才算。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    assert VOSR2Engine._check_multistep_side(25, 384, 2048) is None
    assert VOSR2Engine._check_multistep_side(25, 2048, 384) is None
    with pytest.raises(ValueError):
        VOSR2Engine._check_multistep_side(25, 383, 383)


@pytest.mark.parametrize("steps", [1, 0, -1])
def test_onestep_never_guarded(steps):
    """一步蒸馏（及非法步数）不受最小边长限制——小图放大到 64px 也合法。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    assert VOSR2Engine._check_multistep_side(steps, 64, 64) is None


def test_multistep_guard_message_is_actionable():
    """报错必须给出三条可操作出路：提高倍率 / 换大图 / 改一步。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    with pytest.raises(ValueError) as exc:
        VOSR2Engine._check_multistep_side(25, 256, 256)
    msg = str(exc.value)
    assert "384" in msg
    assert "放大倍率" in msg
    assert "一步蒸馏" in msg


# ── 张量形状辅助 ────────────────────────────────────────────────────────────
def test_pad_to_square_is_noop_for_square():
    from app.integrated_app.engines.vosr2_engine import _pad_to_square

    x = torch.randn(1, 3, 32, 32)
    out, hw = _pad_to_square(x)
    assert out is x
    assert hw == (32, 32)


def test_pad_to_square_reflects_bottom_right():
    """只在右侧/下侧 reflect 补边，原始区域内容必须原样保留。

    注意 reflect 要求补边量小于原边长（w < 2h），故用 24x32 这种比例。
    """
    from app.integrated_app.engines.vosr2_engine import _pad_to_square

    x = torch.randn(1, 3, 24, 32)
    out, hw = _pad_to_square(x)
    assert tuple(out.shape) == (1, 3, 32, 32)
    assert hw == (24, 32)
    assert torch.allclose(out[:, :, :24, :32], x)


@pytest.mark.parametrize(
    "h,w,multiple,exp_h,exp_w",
    [
        (32, 48, 16, 32, 48),  # 已对齐，不变
        (30, 45, 16, 32, 48),  # 补到 16 的倍数
        (5, 5, 8, 8, 8),  # 极小图也补齐（补边量 3 < 原边长 5）
        (33, 17, 8, 40, 24),
    ],
)
def test_pad_to_multiple(h, w, multiple, exp_h, exp_w):
    from app.integrated_app.engines.vosr2_engine import _pad_to_multiple

    x = torch.randn(1, 3, h, w)
    out, oh, ow = _pad_to_multiple(x, multiple)
    assert (oh, ow) == (h, w)  # 返回的是原始尺寸
    assert tuple(out.shape) == (1, 3, exp_h, exp_w)


# ── 分块网格与高斯权重（vendored 上游实现）────────────────────────────────
@pytest.mark.parametrize(
    "length,tile,overlap",
    [
        (64, 64, 8),
        (128, 64, 8),
        (100, 64, 16),
        (512, 256, 32),
        (1024, 256, 64),
        (40, 128, 8),
    ],
)
def test_tile_grid_covers_entire_length(length, tile, overlap):
    """瓦片网格必须无缝覆盖 [0, length)：首块从 0 起、末块贴边界、无空洞。"""
    from vosr import tiled_vae

    positions = tiled_vae._make_tile_grid(length, tile, overlap)
    assert positions[0] == 0
    assert positions[-1] + tile >= length
    assert positions == sorted(set(positions))
    covered = set()
    for p in positions:
        covered.update(range(p, min(p + tile, length)))
    assert covered == set(range(length))


def test_tile_grid_single_tile_when_fits():
    from vosr import tiled_vae

    assert tiled_vae._make_tile_grid(64, 64, 8) == [0]
    assert tiled_vae._make_tile_grid(32, 64, 8) == [0]


def test_gaussian_weights_shape_and_channel_uniformity():
    from vosr import tiled_vae

    w = tiled_vae._gaussian_weights(32, 48, 4, "cpu")
    assert tuple(w.shape) == (1, 4, 32, 48)
    assert torch.allclose(w[:, 0], w[:, 3])


def test_gaussian_weights_peak_at_centre():
    """中心峰值应接近 1（偶数边长时中心不落在整数格点上，故容差 1e-2）。"""
    from vosr import tiled_vae

    w = tiled_vae._gaussian_weights(64, 64, 1, "cpu")[0, 0]
    assert float(w.max()) == pytest.approx(1.0, abs=1e-2)
    assert float(w[32, 32]) > float(w[0, 32]) > float(w[0, 0])


def test_gaussian_weights_corners_are_nonzero():
    """角点权重必须严格 >0：为 0 会让只被单瓦片覆盖的角落 0/0 出 NaN。

    这是 2026-10-23 彩色噪点事故的直接成因，故把「非零」钉成硬约束。
    """
    from vosr import tiled_vae

    w = tiled_vae._gaussian_weights(512, 512, 1, "cpu")
    assert float(w.min()) > 0.0
    assert float(w.min()) < 1e-4  # 同时确实很小，说明是真正的边缘衰减
