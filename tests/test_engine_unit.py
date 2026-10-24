#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""引擎与核心模块单元测试（不需要 GPU / 模型权重）。

覆盖：参数默认合并、路径解析、颜色对齐、任务队列、历史库、i18n。
"""

from __future__ import annotations

import asyncio
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def test_auto_tile_sizes():
    """自动分块决策：小图不分块，大图按显存开 VAE 分块（防 OOM 回归）。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    auto = VOSR2Engine._auto_tile_sizes
    # 小输出（<=512）：完全不分块
    assert auto(256, 10.8, 0, 0) == (0, 0)
    assert auto(512, 10.8, 0, 0) == (0, 0)
    # 1024² 输出：开 DiT 分块，VAE 仍整图（阈值 1024 不含）
    assert auto(1024, 10.8, 0, 0) == (512, 0)
    # 3096² 输出（774x774 @4x，用户实测 OOM 尺寸）：两级分块全开
    assert auto(3096, 10.8, 0, 0) == (512, 512)
    # 大显存（>16GB）：VAE 分块放宽到 1024
    assert auto(3096, 24.0, 0, 0) == (512, 1024)
    # 用户显式指定优先
    assert auto(3096, 10.8, 256, 1024) == (256, 1024)


def test_preflight_output_cap():
    """输出边长超上限时抛 ValueError，且消息带可操作建议。"""
    import pytest

    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    engine = VOSR2Engine("VOSR2", "/tmp/whatever", max_output_side=6144)
    assert engine._preflight_output(3096, 4) == 3096  # 正常尺寸不拦
    with pytest.raises(ValueError, match="降低放大倍率"):
        engine._preflight_output(8000, 4)  # 8000 > 6144
    with pytest.raises(ValueError, match="×3"):
        engine._preflight_output(8000, 4)  # 建议倍率 = 6144//2000 = 3


def test_project_root_resolution():
    """PROJECT_ROOT 必须指向仓库根（含 config.yaml 与 app/），防路径层级写错。

    2026-10-23 实测坑：config.py 曾多写一级 dirname，导致 model_root 解析到
    仓库父目录，模型全部"找不到"。
    """
    from app.integrated_app.config import PROJECT_ROOT

    assert os.path.isfile(os.path.join(PROJECT_ROOT, "config.yaml")), PROJECT_ROOT
    assert os.path.isdir(os.path.join(PROJECT_ROOT, "app")), PROJECT_ROOT
    assert os.path.basename(PROJECT_ROOT) == "VOSR2-Lite", PROJECT_ROOT


def test_arg_defaults_merge(tmp_path):
    """args.json 缺键时用兜底默认合并；ae_path 改写到 model_root。"""
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    ckpt_dir = tmp_path / "VOSR2"
    ckpt_dir.mkdir()
    (ckpt_dir / "args.json").write_text(
        '{"dim": 1536, "depth": 36, "ae_type": "qwen", '
        '"ae_path": "preset/ckpts/Qwen-Image-vae-2d"}',
        encoding="utf-8")

    engine = VOSR2Engine("VOSR2", str(tmp_path))
    args = engine._load_args()
    assert args.dim == 1536 and args.depth == 36
    # 兜底默认生效
    assert args.resolution == 512
    assert args.infer_steps == 1
    # ae_path 被改写到 model_root 下（对齐 preset/ckpts 布局）
    assert args.ae_path == str(tmp_path / "Qwen-Image-vae-2d")


def test_find_weights(tmp_path):
    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    ckpt_dir = tmp_path / "VOSR2" / "checkpoints"
    ckpt_dir.mkdir(parents=True)
    weights = ckpt_dir / "ema_model.safetensors"
    weights.write_bytes(b"fake")

    engine = VOSR2Engine("VOSR2", str(tmp_path))
    assert engine._find_weights() == str(weights)


def test_color_align_wavelet():
    from PIL import Image

    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    sr = Image.new("RGB", (32, 32), (128, 64, 32))
    src = Image.new("RGB", (32, 32), (200, 100, 50))
    out = VOSR2Engine._align(sr, src, "wavelet")
    assert out.size == (32, 32)
    # wavelet 对齐后均值应接近 source 的低频
    r, g, b = out.resize((1, 1)).getpixel((0, 0))
    assert abs(r - 200) < 40


def test_task_queue_serial(tmp_path):
    """单 worker 串行执行 + 完成顺序。"""
    from app.integrated_app.task_queue import TaskQueue

    async def run():
        queue = TaskQueue(maxsize=10)
        await queue.start()
        order = []

        async def make(tag):
            await asyncio.sleep(0.02)
            order.append(tag)

        await queue.submit("t1", lambda: make("a"))
        await queue.submit("t2", lambda: make("b"))
        await asyncio.sleep(0.3)
        await queue.stop()
        return order

    order = asyncio.run(run())
    assert order == ["a", "b"]


def test_history_db_roundtrip(tmp_path):
    from app.integrated_app.history_db import HistoryDB, HistoryRecord

    async def run():
        db = HistoryDB(db_path=str(tmp_path / "t.db"))
        await db.initialize()
        rid = await db.add_record(HistoryRecord(
            input_file="/tmp/in.png", checkpoint="VOSR2"))
        await db.create_task("task123", rid)
        await db.update_task("task123", status="processing", progress=0.5)
        task = await db.get_task("task123")
        await db.close()
        return rid, task

    rid, task = asyncio.run(run())
    assert rid == 1
    assert task["status"] == "processing"
    assert task["progress"] == 0.5
    assert task["record_id"] == 1


def test_i18n():
    from app.integrated_app.i18n import I18n

    i18n = I18n()
    assert i18n.t("nav.home") == "首页"
    assert i18n.t("nav.home", locale="en") == "Home"
    # 未知 key 回退 key 本身
    assert i18n.t("no.such.key") == "no.such.key"
    assert i18n.set_locale("en") == "en"
    assert i18n.set_locale("fr") == "zh"  # 非法值回退默认


def test_dinov2_variant_registry():
    """1.4B 用 dinov2l、0.5B 用 dinov2b；两者架构维度必须与 args.enc_dim 对齐。"""
    from vosr.models.dinov2_vitl14 import (
        DINOV2_VARIANTS,
        build_dinov2_vitb14,
        build_dinov2_vitl14,
    )

    assert set(DINOV2_VARIANTS) == {"dinov2l", "dinov2b"}
    builder_l, weight_l, dim_l = DINOV2_VARIANTS["dinov2l"]
    builder_b, weight_b, dim_b = DINOV2_VARIANTS["dinov2b"]
    assert (dim_l, dim_b) == (1024, 768)
    assert weight_l == "dinov2_vitl14_pretrain.pth"
    assert weight_b == "dinov2_vitb14_pretrain.pth"

    vitl = build_dinov2_vitl14()
    assert vitl.embed_dim == 1024 and len(vitl.blocks) == 24
    vitb = build_dinov2_vitb14()
    assert vitb.embed_dim == 768 and len(vitb.blocks) == 12
    assert builder_l is build_dinov2_vitl14 and builder_b is build_dinov2_vitb14


def test_sd2_decoder_fallback(tmp_path):
    """sd2 缺轻量解码器权重时回退标准 VAE 解码；qwen 系返回 None。"""
    from types import SimpleNamespace

    import torch

    from app.integrated_app.engines.vosr2_engine import VOSR2Engine

    dev = torch.device("cpu")
    engine = VOSR2Engine("VOSR_0.5B_os", str(tmp_path))
    assert engine._build_sd2_decoder(SimpleNamespace(ae_type="qwen"), dev) is None

    calls = {}

    class _FakeVAE:
        def decode(self, latent, return_dict=False):
            calls["latent"] = latent
            return ("decoded",)

    engine.vae = _FakeVAE()
    decoder = engine._build_sd2_decoder(SimpleNamespace(ae_type="sd2"), dev)
    assert callable(decoder)
    assert decoder("z") == "decoded"
    assert calls["latent"] == "z"


def test_sd2_light_decoder_loads(tmp_path):
    """有 sd21_lwdecoder.pth 时应加载官方轻量解码器（不是回退 lambda）。"""
    from types import SimpleNamespace

    import torch

    from app.integrated_app.engines.vosr2_engine import VOSR2Engine
    from vosr.models.light_decoder import LightDecoder

    ckpt = tmp_path / "sd21_lwdecoder.pth"
    torch.save(LightDecoder().state_dict(), ckpt)

    engine = VOSR2Engine("VOSR_0.5B_os", str(tmp_path))
    decoder = engine._build_sd2_decoder(SimpleNamespace(ae_type="sd2"),
                                        torch.device("cpu"))
    assert isinstance(decoder, LightDecoder)
    out = decoder(torch.zeros(1, 4, 8, 8))
    assert tuple(out.shape) == (1, 3, 64, 64)


def test_multistep_min_side_guard():
    """多步模型的最小输出边长门槛必须存在且覆盖已知失效区间（≤256px）。"""
    from app.integrated_app.engines.vosr2_engine import MULTISTEP_MIN_SIDE

    assert 256 < MULTISTEP_MIN_SIDE <= 512
