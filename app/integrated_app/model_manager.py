"""模型权重盘查与加载状态管理。

职责：
- 扫描 model/ 目录，报告各 checkpoint 的完整性（缺哪些文件）。
- 持有当前引擎实例，管理懒加载与空闲卸载。
- 不直接 import torch（引擎内部自行处理），本模块可在无 GPU 环境运行。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from loguru import logger

# 每个 checkpoint 运行所需的最小文件集（相对 model_root）
_REQUIRED_COMMON_QWEN = [
    "Qwen-Image-vae-2d/config.json",
    "Qwen-Image-vae-2d/diffusion_pytorch_model.safetensors",
    "torch_cache/checkpoints/dinov2_vitl14_pretrain.pth",
    "torch_cache/facebookresearch_dinov2_main/hubconf.py",
]
_REQUIRED_COMMON_SD2 = [
    "stable-diffusion-2-1-base/vae/config.json",
    "stable-diffusion-2-1-base/vae/diffusion_pytorch_model.safetensors",
    "sd21_lwdecoder.pth",
    "torch_cache/checkpoints/dinov2_vitb14_pretrain.pth",
    "torch_cache/facebookresearch_dinov2_main/hubconf.py",
]
_REQUIRED_CKPT = [
    "{name}/args.json",
    "{name}/checkpoints/ema_model.safetensors",
]


@dataclass
class CheckpointInfo:
    name: str
    display_name: str = ""
    ckpt_type: str = "onestep"
    ae_type: str = "qwen"
    min_vram_gb: float = 8
    default_steps: int = 1
    present: bool = False
    missing: list[str] = field(default_factory=list)


class ModelManager:
    def __init__(self, config):
        self.config = config
        self.model_root = config.abspath(config.get("model.pretrained_dir", "model"))
        self.checkpoints_cfg = config.get("model.checkpoints", {}) or {}
        self._engine = None
        self._engine_name = ""
        self._last_used = 0.0
        self.idle_unload_minutes = float(config.get("model.idle_unload_minutes", 15))

    # ── 盘查 ────────────────────────────────────────────────────────────────
    def scan(self) -> list[CheckpointInfo]:
        infos: list[CheckpointInfo] = []
        for name, cfg in self.checkpoints_cfg.items():
            cfg = cfg or {}
            ae_type = cfg.get("ae_type", "qwen")
            required = list(_REQUIRED_CKPT)
            required = [p.format(name=name) for p in required]
            required += _REQUIRED_COMMON_QWEN if ae_type == "qwen" else _REQUIRED_COMMON_SD2
            missing = [p for p in required
                       if not os.path.isfile(os.path.join(self.model_root, p))]
            infos.append(CheckpointInfo(
                name=name,
                display_name=cfg.get("name", name),
                ckpt_type=cfg.get("type", "onestep"),
                ae_type=ae_type,
                min_vram_gb=float(cfg.get("min_vram_gb", 8)),
                default_steps=int(cfg.get("default_steps", 1)),
                present=not missing,
                missing=missing,
            ))
        return infos

    def get_info(self, name: str) -> CheckpointInfo | None:
        for info in self.scan():
            if info.name == name:
                return info
        return None

    # ── 引擎生命周期 ────────────────────────────────────────────────────────
    @property
    def engine(self):
        return self._engine

    @property
    def engine_name(self) -> str:
        return self._engine_name

    def get_engine(self, name: str | None = None, progress_cb=None):
        """获取（必要时懒加载）引擎。模型缺失时抛 FileNotFoundError。"""
        from app.integrated_app.engines.vosr2_engine import VOSR2Engine

        name = name or self.config.get("model.default_checkpoint", "VOSR2")
        if self._engine is not None and self._engine_name == name:
            self._last_used = time.time()
            return self._engine

        info = self.get_info(name)
        if info is None:
            raise ValueError(f"未知 checkpoint: {name}（见 config.yaml model.checkpoints）")
        if not info.present:
            raise FileNotFoundError(
                f"checkpoint {name} 权重不完整，缺少: {', '.join(info.missing)}；"
                "请运行 python scripts/download_model.py 下载")

        if self._engine is not None:
            self._engine.unload()
        device = self.config.get("model.device", "auto")
        self._engine = VOSR2Engine(name, self.model_root, device=device,
                                   progress_cb=progress_cb)
        self._engine.load()
        self._engine_name = name
        self._last_used = time.time()
        return self._engine

    def unload_if_idle(self) -> bool:
        if (self._engine is not None and self.idle_unload_minutes > 0
                and time.time() - self._last_used
                > self.idle_unload_minutes * 60):
            self._engine.unload()
            self._engine = None
            self._engine_name = ""
            logger.info("引擎空闲超时，已卸载")
            return True
        return False

    def touch(self) -> None:
        self._last_used = time.time()
