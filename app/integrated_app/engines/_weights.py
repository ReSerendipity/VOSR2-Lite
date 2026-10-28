#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""检查点发现与参数解析：args.json 合并、权重文件搜索顺序。

以 mixin 形式提供，宿主类需提供 ``checkpoint_name`` / ``model_root``。
"""

from __future__ import annotations

import json
import os
import types
from typing import Any

from loguru import logger

# args.json 中可能缺失、但采样必需的键的兜底值
_ARG_DEFAULTS: dict[str, Any] = {
    "resolution": 512,
    "patch_size": 2,
    "mlp_ratio": 4,
    "use_qknorm": True,
    "use_swiglu": True,
    "use_rope": True,
    "use_rmsnorm": True,
    "wo_shift": False,
    "dim": 1536,
    "depth": 36,
    "num_heads": 24,
    "ae_type": "qwen",
    "time_dist": ["lognorm", 0, 1.0],
    "cfg_ratio": 0.1,
    "cfg_scale": 1.5,
    "dinov2_size": 448,
    "enc_type": "dinov2l",
    "enc_dim": 1024,
    "layer_dinov2b_list": [17],
    "interp_type": "lin",
    "encdim_ratio": 3,
    "weak_cond_strength_aelq_list": [0, 0],
    "cond_strength_aelq_list": [5, 1],
    "t_start": 0,
    "t_end": 1,
    "auxiliary_time_cond": False,
    "distill_type": "onestep",
    # 采样侧（CLI 可覆盖）
    "infer_steps": 1,
    "upscale": 4,
    "align_method": "wavelet",
    "seed": 42,
    "tile_size": 0,
    "tile_overlap": 32,
    "vae_tile_size": 0,
    "vae_tile_overlap": None,
    "posterior_mode": True,
    "process_size": 512,
}


class WeightsMixin:
    """路径解析、参数合并、权重发现。"""

    checkpoint_name: str
    model_root: str

    @property
    def ckpt_dir(self) -> str:
        return os.path.join(self.model_root, self.checkpoint_name)

    def _resolve(self, relative: str) -> str:
        """把 args.json 里的 preset/ckpts/... 路径改写到 model_root 下。"""
        tail = os.path.basename(os.path.normpath(relative))
        return os.path.join(self.model_root, tail)

    def _load_args(self) -> types.SimpleNamespace:
        """读取检查点目录的 args.json，与兜底默认合并。"""
        json_path = os.path.join(self.ckpt_dir, "args.json")
        data: dict[str, Any] = {}
        if os.path.isfile(json_path):
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
        else:
            logger.warning(f"{self.checkpoint_name}/args.json 不存在，使用内置默认参数")
        merged = dict(_ARG_DEFAULTS)
        merged.update(data)
        # ae_path 改写到 model_root 下（布局对齐上游 preset/ckpts/）
        if merged.get("ae_path"):
            merged["ae_path"] = self._resolve(merged["ae_path"])
        return types.SimpleNamespace(**merged)

    def _find_weights(self) -> str:
        search_dirs = [
            os.path.join(self.ckpt_dir, "clean_weights"),
            os.path.join(self.ckpt_dir, "checkpoints"),
            self.ckpt_dir,
        ]
        for d in search_dirs:
            for name in ("ema_model.safetensors", "model.safetensors"):
                candidate = os.path.join(d, name)
                if os.path.isfile(candidate):
                    return candidate
        import glob

        hits = glob.glob(os.path.join(self.ckpt_dir, "**/*.safetensors"), recursive=True)
        if not hits:
            raise FileNotFoundError(
                f"未找到 {self.checkpoint_name} 的权重文件（*.safetensors）；"
                "请运行 python scripts/download_model.py"
            )
        return hits[0]
