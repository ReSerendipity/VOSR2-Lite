#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""VOSR2 推理引擎（组合层）。

封装上游 cswry/VOSR（Apache-2.0，见 vosr/UPSTREAM.md）的加载与采样流程：
  Qwen-Image 2D VAE / SD2.1 VAE  ->  DINOv2-L 视觉编码  ->  LightningDiT
  一步（或多步）flow matching 采样  ->  VAE 解码  ->  颜色对齐（wavelet/adain）。

模块布局（2026-10-28 从单文件 869 行拆出，公开 API 与导入路径保持不变）：
- ``_core``      类型别名 / 参数与状态数据类 / 张量形状辅助 / 硬约束常量
- ``_weights``   args.json 合并、路径改写、权重文件发现
- ``_loaders``   VAE / DINOv2 / DiT 构建与精度补丁
- ``_tiling``    两级分块决策、latent 空间 Gaussian 混合、DINOv2 特征
- ``_sampling``  一步/多步 flow matching 采样
- ``_color``     wavelet / adain 颜色对齐

设计要点：
- 依赖 torch/torchvision（模块顶层导入，测试环境装 CPU 版即可）；
  vendored 的 vosr/ 包不需要 GPU 即可 import。
- 权重落盘目录布局对齐上游 preset/ckpts/，根目录由 config.yaml
  model.pretrained_dir 指定（默认 model/）。
- fp16 适配：DiT 与 DINOv2 加载后转 fp16（上游权重即 fp16），VAE 保持 fp32
  （Qwen VAE 参数量小，fp32 数值更稳），12GB 显存可跑 1.4B one-step。
"""

from __future__ import annotations

import random
import types

import numpy as np
import torch
from loguru import logger
from PIL import Image
from torchvision import transforms

from app.integrated_app.engines._color import align_images
from app.integrated_app.engines._core import (
    MAX_OUTPUT_SIDE_DEFAULT,
    MULTISTEP_MIN_SIDE,
    EngineStatus,
    ProgressCb,
    RestoreParams,
    _empty_cache,
    _pad_to_multiple,
    _pad_to_square,
)
from app.integrated_app.engines._loaders import LoaderMixin
from app.integrated_app.engines._sampling import SamplingMixin
from app.integrated_app.engines._tiling import (
    auto_tile_sizes,
    get_venc_features,
    tiled_vae_latent_inference,
)
from app.integrated_app.engines._weights import _ARG_DEFAULTS, WeightsMixin
from app.integrated_app.exceptions import InferenceCancelled
from vosr import tiled_vae
from vosr.vosr import VOSR

__all__ = [
    "EngineStatus",
    "MAX_OUTPUT_SIDE_DEFAULT",
    "MULTISTEP_MIN_SIDE",
    "ProgressCb",
    "RestoreParams",
    "VOSR2Engine",
    "_ARG_DEFAULTS",
    "_empty_cache",
    "_pad_to_multiple",
    "_pad_to_square",
    "get_venc_features",
    "tiled_vae_latent_inference",
]


class VOSR2Engine(WeightsMixin, LoaderMixin, SamplingMixin):
    """VOSR2 检查点推理引擎（单实例，串行使用）。

    组合三个 mixin：权重/参数（WeightsMixin）、组件加载（LoaderMixin）、
    采样（SamplingMixin）。分块与颜色对齐以静态方法别名暴露，保持既有调用点
    （``VOSR2Engine._auto_tile_sizes`` / ``VOSR2Engine._align``）不变。
    """

    _auto_tile_sizes = staticmethod(auto_tile_sizes)
    _align = staticmethod(align_images)

    def __init__(
        self,
        checkpoint_name: str,
        model_root: str,
        device: str = "auto",
        progress_cb: ProgressCb | None = None,
        max_output_side: int | None = None,
    ):
        self.checkpoint_name = checkpoint_name
        self.model_root = model_root
        self.device_pref = device
        self._progress_cb = progress_cb
        self.max_output_side = int(max_output_side or MAX_OUTPUT_SIDE_DEFAULT)

        self.args: types.SimpleNamespace | None = None
        self.vae = None
        self.venc = None
        self.dit = None
        self.vosr: VOSR | None = None
        self.light_decoder = None
        self.device = "cpu"
        self.status = EngineStatus(checkpoint=checkpoint_name)

    def _cb(self, stage: str, ratio: float) -> None:
        if self._progress_cb:
            try:
                self._progress_cb(stage, ratio)
            except InferenceCancelled:
                raise
            except Exception:  # 进度回调失败不影响推理
                pass

    def load(self) -> None:
        """加载 VAE + DINOv2 + DiT（幂等；已加载则直接返回）。"""
        if self.status.loaded:
            return
        import time

        t0 = time.time()
        if not torch.cuda.is_available():
            raise RuntimeError(
                "未检测到可用的 NVIDIA CUDA GPU。VOSR2-Lite 需要 CUDA 显卡"
                "（≥8GB 显存跑 1.4B one-step）；CPU 推理不在支持范围。"
            )
        self.device = self.device_pref if self.device_pref in ("cuda", "cpu") else "cuda"
        dev = torch.device(self.device)

        args = self._load_args()
        self.args = args

        self._cb("load_vae", 0.05)
        self.vae = self._load_vae(args, dev)
        self.light_decoder = self._build_sd2_decoder(args, dev)
        self._cb("load_venc", 0.25)
        self.venc = self._load_dinov2(args, dev)
        self._cb("load_dit", 0.55)
        self.dit = self._load_dit(args, dev)
        self._cb("init", 0.95)

        accelerator = types.SimpleNamespace(device=dev)
        self.vosr = VOSR(
            time_dist=args.time_dist,
            cfg_ratio=args.cfg_ratio,
            cfg_scale=args.cfg_scale,
            interp_type=args.interp_type,
            accelerator=accelerator,
            t_start=args.t_start,
            t_end=args.t_end,
            args=args,
        )
        self.status = EngineStatus(
            loaded=True,
            checkpoint=self.checkpoint_name,
            device=str(dev),
            dtype=str(next(self.dit.parameters()).dtype),  # type: ignore[attr-defined]
            load_seconds=round(time.time() - t0, 2),
            vram_gb=round(torch.cuda.memory_allocated() / 1024**3, 2),
        )
        self._cb("ready", 1.0)
        logger.info(
            f"引擎就绪: {self.checkpoint_name} device={self.device} "
            f"load={self.status.load_seconds}s vram={self.status.vram_gb}GB"
        )

    def unload(self) -> None:
        self.vae = None
        self.venc = None
        self.dit = None
        self.vosr = None
        self.light_decoder = None
        self.status = EngineStatus(checkpoint=self.checkpoint_name)
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info(f"引擎已卸载: {self.checkpoint_name}")

    def _preflight_output(self, output_side: int, upscale: int) -> int:
        """输出边长前置检查，超过上限抛 ValueError（附可操作建议）。

        output_side: 期望输出的长边像素；upscale 仅用于错误消息。
        """
        if output_side > self.max_output_side:
            input_side = max(1, output_side // max(1, upscale))
            max_factor = max(1, self.max_output_side // input_side)
            raise ValueError(
                f"输出边长 {output_side}px 超过上限 {self.max_output_side}px"
                f"（×{upscale} 放大）。请降低放大倍率"
                f"（本图最高 ×{max_factor}）或先裁剪输入。"
            )
        return output_side

    @staticmethod
    def _check_multistep_side(steps: int, output_w: int, output_h: int) -> None:
        """多步采样最小输出边长守卫（≤256px 实测发散成色块）。

        一步蒸馏（steps=1）不受限；多步模型要求输出长边 ≥ MULTISTEP_MIN_SIDE。
        独立成静态方法是为了让该守卫可被单测直接覆盖（restore() 需要真实权重）。
        """
        if steps > 1 and max(output_w, output_h) < MULTISTEP_MIN_SIDE:
            raise ValueError(
                f"多步采样（steps={steps}）在输出小于 {MULTISTEP_MIN_SIDE}px 时"
                f"不稳定（本图输出 {output_w}×{output_h}）：请提高放大倍率、"
                f"换更大的输入图，或把采样步数改为 1（一步蒸馏）"
            )

    def _effective_args(self, params: RestoreParams) -> types.SimpleNamespace:
        """把单次请求参数叠加到检查点 args 上（不改动 self.args）。"""
        args = types.SimpleNamespace(**vars(self.args))
        args.upscale = params.upscale
        args.infer_steps = params.steps
        args.align_method = params.align_method
        args.tile_size = params.tile_size
        args.tile_overlap = params.tile_overlap
        args.vae_tile_size = params.vae_tile_size
        return args

    def restore(
        self, image: Image.Image, params: RestoreParams, progress_cb: ProgressCb | None = None
    ) -> Image.Image:
        """对单张 PIL 图像执行超分，返回结果 PIL 图像（RGB）。"""
        if not self.status.loaded:
            self.load()
        cb = progress_cb or self._progress_cb
        args = self._effective_args(params)
        dev = torch.device(self.device)

        seed = params.seed if params.seed is not None else -1
        if seed < 0:
            seed = random.randint(0, 2**31 - 1)
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        np.random.seed(seed % (2**32))

        raw = image.convert("RGB")
        w, h = raw.size
        output_w, output_h = w * params.upscale, h * params.upscale

        # 前置检查（按输出长边）+ 自动分块决策（防止大图全图 VAE 编码 OOM）
        output_long = max(output_w, output_h)
        self._preflight_output(output_long, params.upscale)
        self._check_multistep_side(params.steps, output_w, output_h)
        free_gb = torch.cuda.mem_get_info()[0] / 1024**3 if torch.cuda.is_available() else 0.0
        dit_tile, vae_tile = self._auto_tile_sizes(
            output_long, free_gb, params.tile_size, params.vae_tile_size
        )
        args.tile_size = dit_tile
        args.vae_tile_size = vae_tile
        if dit_tile or vae_tile:
            logger.info(
                f"分块推理: 输出 {output_w}x{output_h}px, "
                f"DiT tile={dit_tile or '关'}, VAE tile={vae_tile or '关'} "
                f"(可用显存 {free_gb:.1f}GB)"
            )

        # 非方形图不裁剪内容：bicubic 预放大后 reflect 填充到 16 的倍数
        # （VAE 下采样 8 x DiT patch 2，保证 latent 对齐；VAE tiled 路径内部
        # 还会按 8 补齐），VAE 解码后裁回请求的输出尺寸。方案对齐社区
        # ComfyUI-VOSR2（2026-10-23 采纳）。
        input_img = raw.resize((output_w, output_h), Image.Resampling.BICUBIC)
        lq = transforms.ToTensor()(input_img).unsqueeze(0).to(dev) * 2.0 - 1.0
        lq, _, _ = _pad_to_multiple(lq, 16)  # 补齐到 16 倍数（VAE 8 x DiT patch 2）
        # input_img 保留到颜色对齐之后（wavelet/adain 需要它做参考）

        self._report(cb, "encode", 0.1)
        with torch.no_grad():
            if dit_tile and dit_tile > 0:
                sr_tensor = tiled_vae_latent_inference(
                    self.dit, self.vae, self.venc, lq, args, device=dev, progress_cb=cb
                )
                _empty_cache()
            else:
                lq_latent, latents_mean, latents_std = tiled_vae.encode_dispatch(
                    self.vae, lq, args, dev
                )
                _empty_cache()  # 释放 VAE 编码激活，给采样腾显存
                # dtype 对齐：VAE 输出 fp32，DiT 可能为 fp16（显存适配），
                # 采样前把 latent 转到 DiT 精度，避免 cat  dtype 不匹配
                dit_dtype = next(self.dit.parameters()).dtype  # type: ignore[attr-defined]
                lq_latent = lq_latent.to(dit_dtype)
                # forward_flexible 仅支持方形 latent：reflect 补方，算完裁回
                lq_latent_sq, lat_hw = _pad_to_square(lq_latent)
                self._report(cb, "venc", 0.25)
                z_fea = get_venc_features(self.venc, lq, args)
                _empty_cache()
                self._report(cb, "sample", 0.4)
                sr_latent = self._sample(lq_latent_sq, z_fea, args, cb)[
                    :, :, : lat_hw[0], : lat_hw[1]
                ]
                _empty_cache()
                self._report(cb, "decode", 0.85)
                sr_tensor = tiled_vae.decode_dispatch(
                    self.vae, sr_latent.float(), args, latents_mean, latents_std, self.light_decoder
                )
                _empty_cache()

        # NaN 防护：fp16 分块混合边缘可能产生极个别 NaN/Inf 像素，
        # 不处理会在 ToPILImage cast 时告警并产生黑点（2026-10-23 实测）。
        sr_tensor = torch.nan_to_num(sr_tensor, nan=0.0, posinf=1.0, neginf=-1.0)
        # 裁掉 pad 到 16 倍数多出来的边，回到请求的输出尺寸
        sr_tensor = sr_tensor[:, :, :output_h, :output_w]
        sr_img = transforms.ToPILImage()(sr_tensor[0].cpu().clamp(-1.0, 1.0) * 0.5 + 0.5)
        self._report(cb, "align", 0.95)
        sr_img = self._align(sr_img, input_img, params.align_method)
        del input_img, lq  # 释放 CPU 侧大图与输入张量
        self._report(cb, "done", 1.0)
        return sr_img
