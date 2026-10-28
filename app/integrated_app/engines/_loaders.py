#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""组件加载：VAE / DINOv2 / DiT 的构建、精度补丁与 torch.compile 解包。

以 mixin 形式提供，宿主类需提供 ``model_root`` 并持有 ``vae`` / ``venc`` / ``dit``。
权重文件定位见 ``_weights.py``，分块与采样见 ``_tiling.py`` / ``_sampling.py``。
"""

from __future__ import annotations

import os
import types
from typing import Any

import torch
from loguru import logger


class LoaderMixin:
    """按 ae_type 构建各组件，并把 DiT/DINOv2 降到 fp16（VAE 保持 fp32）。"""

    model_root: str
    vae: Any
    venc: Any
    dit: Any

    def _build_sd2_decoder(self, args, dev):
        """sd2 系（0.5B）解码入口：官方轻量解码器优先，缺失时回退标准 VAE 解码。

        `vosr/tiled_vae.py` 的 sd2 分支直接调用 `light_decoder(sr_u)`。
        轻量解码器结构来自上游 `models/light_decoder.py`（已 vendor 到
        `vosr/models/light_decoder.py`），权重为 `model/sd21_lwdecoder.pth`；
        缺权重时回退 SD2.1 VAE 自带解码器（数值空间一致，但更慢、更吃显存）。
        qwen 系（1.4B）不走此路径，返回 None。
        """
        if getattr(args, "ae_type", "qwen") != "sd2":
            return None

        ckpt = os.path.join(self.model_root, "sd21_lwdecoder.pth")
        if os.path.isfile(ckpt):
            from vosr.models.light_decoder import LightDecoder

            decoder = LightDecoder()
            state = torch.load(ckpt, map_location="cpu")
            if isinstance(state, dict):
                for key in ("state_dict", "model", "decoder"):
                    if key in state and isinstance(state[key], dict):
                        state = state[key]
                        break
            missing, unexpected = decoder.load_state_dict(state, strict=False)
            if missing:
                logger.warning(
                    f"轻量解码器缺少 {len(missing)} 个权重，" f"效果可能异常（示例: {missing[:3]}）"
                )
            decoder = decoder.to(dev).eval()
            for param in decoder.parameters():
                param.requires_grad_(False)
            logger.info(f"已加载 SD2.1 轻量解码器: {ckpt}")
            return decoder

        logger.warning(
            "sd2 检查点未找到 sd21_lwdecoder.pth，回退标准 SD2.1 VAE 解码"
            "（更慢、显存占用更高）；如需官方速度请重新运行下载脚本"
        )
        return lambda latent: self.vae.decode(latent, return_dict=False)[0]

    def _load_vae(self, args, dev):
        ae_type = args.ae_type
        if ae_type == "qwen":
            from diffusers.utils import logging as df_logging

            df_logging.set_verbosity_error()
            from vosr.models.qwenimage_vae2d import AutoencoderKLQwenImage2D

            ae_path = self._resolve("Qwen-Image-vae-2d")
            if not os.path.isdir(ae_path):
                raise FileNotFoundError(
                    f"Qwen-Image 2D VAE 不存在: {ae_path}；"
                    "请运行 python scripts/download_model.py"
                )
            vae = AutoencoderKLQwenImage2D.from_pretrained(ae_path)
        elif ae_type == "sd2":
            from diffusers import AutoencoderKL

            ae_path = self._resolve("stable-diffusion-2-1-base")
            if not os.path.isdir(ae_path):
                raise FileNotFoundError(
                    f"SD2.1 VAE 不存在: {ae_path}（0.5B 模型需要）；"
                    "请运行 python scripts/download_model.py --checkpoint VOSR_0.5B_os"
                )
            vae = AutoencoderKL.from_pretrained(ae_path, subfolder="vae")
        else:
            raise ValueError(f"未知 ae_type: {ae_type}")
        return vae.to(dev).eval()

    def _load_dinov2(self, args, dev):
        """从 model/torch_cache 离线加载 DINOv2（本地 vendored 架构 + 官方权重）。

        2026-10-23 采纳社区方案（ComfyUI-VOSR2 的 models/dinov2.py，Apache-2.0）：
        不再用 torch.hub.load（会拉取并执行缓存仓库代码、还要求随仓库 clone
        dinov2 源码），改为本地复刻架构 + 直接加载官方 checkpoint。

        2026-10-24 补齐 ViT-B/14：0.5B 系检查点（VOSR_0.5B_os/_ms）的
        args.enc_type 为 dinov2b、enc_dim 为 768。
        """
        from vosr.models.dinov2_vitl14 import DINOV2_VARIANTS

        variant = DINOV2_VARIANTS.get(getattr(args, "enc_type", ""))
        if variant is None:
            raise NotImplementedError(
                f"不支持的视觉编码器 enc_type={args.enc_type}；"
                f"已 vendored: {', '.join(sorted(DINOV2_VARIANTS))}"
            )
        builder, weight_name, enc_dim = variant

        declared = int(getattr(args, "enc_dim", enc_dim))
        if declared != enc_dim:
            raise ValueError(
                f"args.enc_dim={declared} 与 {args.enc_type} 的 {enc_dim} 不一致，"
                "请检查检查点 args.json"
            )

        ckpt = os.path.join(self.model_root, "torch_cache", "checkpoints", weight_name)
        if not os.path.isfile(ckpt):
            raise FileNotFoundError(
                f"DINOv2 权重不存在: {ckpt}；" "请运行 python scripts/download_model.py"
            )

        encoder = builder()
        state = torch.load(ckpt, map_location="cpu")
        encoder.load_state_dict(state, strict=False)
        encoder = encoder.to(dev).eval()
        for param in encoder.parameters():
            param.requires_grad_(False)
        if dev.type == "cuda":
            encoder = encoder.half()
        return encoder

    def _load_dit(self, args, dev):
        from safetensors.torch import load_file

        LightningDiT = self._import_lightningdit()

        base_channel = 4 if args.ae_type == "sd2" else 16
        model = LightningDiT(
            input_size=args.resolution // 8,
            patch_size=args.patch_size,
            in_channels=2 * base_channel,
            out_channels=base_channel,
            hidden_size=args.dim,
            depth=args.depth,
            num_heads=args.num_heads,
            mlp_ratio=args.mlp_ratio,
            z_dims=args.enc_dim,
            encdim_ratio=args.encdim_ratio,
            auxiliary_time_cond=args.auxiliary_time_cond,
            use_qknorm=args.use_qknorm,
            use_swiglu=args.use_swiglu,
            use_rope=args.use_rope,
            use_rmsnorm=args.use_rmsnorm,
            wo_shift=args.wo_shift,
            num_fused_layers=len(args.layer_dinov2b_list),
        )
        weight_path = self._find_weights()
        logger.info(f"加载 DiT 权重: {weight_path}")
        if weight_path.endswith(".safetensors"):
            state_dict = load_file(weight_path)
        else:
            state_dict = torch.load(weight_path, map_location="cpu")
        model.load_state_dict(state_dict, strict=False)
        if dev.type == "cuda":
            model = model.half()
        model = model.to(dev).eval()
        model.forward = model.forward_flexible
        self._patch_embedder_dtype(model)
        self._unwrap_compiled(model)
        return model

    @staticmethod
    def _patch_embedder_dtype(model) -> None:
        """fp16 适配补丁。

        上游 TimestepEmbedder.timestep_embedding 内部 `t[:, None].float()` 恒产
        fp32，fp16 模型下 `mlp(t_freq)` 会 dtype 不匹配（2026-10-23 实测坑）。
        这里把 t_freq 转到 mlp 权重精度，不改 vendored 代码。
        """
        from vosr.models.lightningdit import TimestepEmbedder

        for name in ("t_embedder", "r_embedder"):
            emb = getattr(model, name, None)
            if not isinstance(emb, TimestepEmbedder):
                continue
            dtype = next(emb.mlp.parameters()).dtype
            if dtype == torch.float32:
                continue  # fp32 模型无需补丁

            def _make_forward(emb, dtype):
                def forward(t: torch.Tensor) -> torch.Tensor:
                    t_freq = TimestepEmbedder.timestep_embedding(t, emb.frequency_embedding_size)
                    return emb.mlp(t_freq.to(dtype))

                return forward

            emb.forward = _make_forward(emb, dtype)  # type: ignore[method-assign]
            logger.debug(f"已为 {name} 应用 fp16 dtype 补丁")

    @staticmethod
    def _import_lightningdit():
        """导入 vendored LightningDiT，期间把 torch.compile 降级为恒等装饰器。

        上游代码在 lightningdit.py / swiglu_ffn.py 用了 @torch.compile
        （inductor 后端）；Windows 无 Triton，编译直接抛 TritonMissing
        （2026-10-23 实测坑）。这里在导入前把 torch.compile 换成恒等，
        使装饰器成为 no-op；导入后恢复原函数。
        """
        import torch as _torch

        orig_compile = _torch.compile

        def _no_compile(fn=None, **_kwargs):
            if fn is None:
                return lambda f: f
            return fn

        _torch.compile = _no_compile  # type: ignore[assignment]
        try:
            from vosr.models.lightningdit import LightningDiT

            return LightningDiT
        finally:
            _torch.compile = orig_compile  # type: ignore[assignment]

    @staticmethod
    def _unwrap_compiled(model) -> int:
        """解包模型上残留的 torch.compile 前向（安全网）。

        若模块在打补丁前已被导入（装饰器已求值），forward 会是编译包装器；
        编译函数对象带 _torchdynamo_orig_callable 属性指向原函数。
        注意：原函数是**未绑定**的，直接挂实例属性会让 ``rope(q)`` 把 q 当
        self（缺参 TypeError）；必须用 MethodType 重新绑定到模块
        （2026-10-23 实测坑：分块路径 tile token 数恰等于训练尺寸时走
        self.feat_rope，命中被解包的 RoPE 而报错）。
        返回解包的 forward 数量。
        """
        count = 0
        for module in model.modules():
            forward = getattr(module, "forward", None)
            orig = getattr(forward, "_torchdynamo_orig_callable", None)
            if orig is not None:
                module.forward = types.MethodType(orig, module)
                count += 1
        if count:
            logger.debug(f"已解包 {count} 个 torch.compile 前向")
        return count
