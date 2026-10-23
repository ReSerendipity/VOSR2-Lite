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
