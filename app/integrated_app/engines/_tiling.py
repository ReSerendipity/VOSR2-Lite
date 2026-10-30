#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""分块推理：自动分块决策、latent 空间 Gaussian 混合、DINOv2 特征提取。

两级分块（DiT latent 分块 + VAE 编解码分块）是 2026-10-23 一次真实 OOM 事故的
修复产物：Qwen VAE 为 fp32，全图编码约 11KB/像素，3096² 输出需 ~100GB。
"""

from __future__ import annotations

import torch
import torch.nn.functional as F
from loguru import logger
from torchvision.transforms import Normalize

from app.integrated_app.engines._core import ProgressCb, _empty_cache
from app.integrated_app.exceptions import InferenceCancelled
from vosr import tiled_vae

IMAGENET_DEFAULT_MEAN = (0.485, 0.456, 0.406)
IMAGENET_DEFAULT_STD = (0.229, 0.224, 0.225)

# 自动分块参数：Qwen VAE 为 fp32，全图编码约 11KB/像素，3096² 需 ~100GB，
# 因此输出超过阈值必须分块（2026-10-23 实测 OOM 后的修复）。
_VAE_TILE_AUTO_LOW_VRAM = 512  # 可用显存 ≤16GB
_VAE_TILE_AUTO_HIGH_VRAM = 1024  # 可用显存 >16GB
_DIT_TILE_AUTO = 512


def auto_tile_sizes(
    output_side: int, free_vram_gb: float, user_tile: int, user_vae_tile: int
) -> tuple[int, int]:
    """按输出边长与可用显存决定 (dit_tile, vae_tile)。

    规则（2026-10-23 实测 OOM 后固化）：
    - 用户显式指定（>0）优先；
    - tile_size=0 且输出 >512²：开 DiT 分块（512px）；
    - vae_tile_size=0 且输出 >1024²：开 VAE 分块（≤16GB 可用显存取 512，
      否则 1024）。Qwen VAE fp32 全图编码约 11KB/像素，3096² 需 ~100GB，
      不分块必然 OOM。
    """
    dit_tile = user_tile
    if dit_tile <= 0 and output_side > 512:
        dit_tile = _DIT_TILE_AUTO
    vae_tile = user_vae_tile
    if vae_tile <= 0 and output_side > 1024:
        vae_tile = _VAE_TILE_AUTO_HIGH_VRAM if free_vram_gb > 16 else _VAE_TILE_AUTO_LOW_VRAM
    return dit_tile, vae_tile


def tiled_vae_latent_inference(
    model, vae, venc, lq_tensor, args, device="cuda", progress_cb: ProgressCb | None = None
):
    """DiT 前向按 latent 分块、Gaussian 混合；VAE 编解码按 vae_tile_size 分块。

    progress_cb: (stage, ratio 0..1)，ratio 在 encode(0.1)->venc(0.3)->
    sample(0.4..0.85, 按步数细分)->decode(0.85)->done(1.0) 之间推进。
    """

    def _report(stage: str, ratio: float) -> None:
        if progress_cb:
            try:
                progress_cb(stage, ratio)
            except InferenceCancelled:
                raise
            except Exception:
                pass

    AE_FACTOR = 8
    b = lq_tensor.shape[0]
    patch_size = getattr(args, "patch_size", 2)

    with torch.no_grad():
        lq_latent, latents_mean, latents_std = tiled_vae.encode_dispatch(
            vae, lq_tensor, args, device
        )
    _report("encode", 0.15)
    _empty_cache()  # 释放 VAE 编码激活
    # dtype 对齐：VAE fp32 输出 -> DiT 精度（可能 fp16），解码前转回 fp32
    dit_dtype = next(model.parameters()).dtype
    lq_latent = lq_latent.to(dit_dtype)
    _, lc, lh, lw = lq_latent.shape

    lt_size = max((args.tile_size // AE_FACTOR // patch_size) * patch_size, patch_size)
    lt_overlap = max(args.tile_overlap // AE_FACTOR, lt_size // 8)
    lt_size = min(lt_size, min(lh, lw))
    lt_overlap = min(lt_overlap, lt_size - 1)

    if lh <= lt_size and lw <= lt_size:
        with torch.no_grad():
            z_fea = get_venc_features(venc, lq_tensor, args)
            _report("venc", 0.3)
            _empty_cache()
            z = torch.randn_like(lq_latent)
            n_steps = args.infer_steps
            t_seq = torch.linspace(1.0, 0.0, n_steps + 1, device=device)
            for i in range(n_steps):
                t_cur, t_nxt = t_seq[i], t_seq[i + 1]
                u = model(torch.cat([lq_latent, z], 1), t_cur.expand(b), t_nxt.expand(b), z_fea)
                z = z - (t_cur - t_nxt) * u
                _report("sample", 0.4 + 0.45 * (i + 1) / n_steps)
            _report("decode", 0.9)
            out = tiled_vae.decode_dispatch(vae, z.float(), args, latents_mean, latents_std, None)
            _empty_cache()
            _report("done", 1.0)
            return out

    h_pos = tiled_vae._make_tile_grid(lh, lt_size, lt_overlap)
    w_pos = tiled_vae._make_tile_grid(lw, lt_size, lt_overlap)
    # 混合累加用 fp32：latent 角落只被单瓦片覆盖，2D 高斯权重可低至 ~3e-11，
    # fp16 下溢为 0 会导致 0/0=NaN、角落出彩色噪点（2026-10-23 实测）。
    # DiT 前向仍走 fp16 省显存，仅累加与归一化升 fp32。
    g_weight = tiled_vae._gaussian_weights(lt_size, lt_size, lc, device).float()
    logger.info(
        f"[分块推理] latent {lh}x{lw}, tile={lt_size}, "
        f"overlap={lt_overlap}, grid={len(h_pos)}x{len(w_pos)}"
    )

    tile_venc = {}
    with torch.no_grad():
        for hi in h_pos:
            for wi in w_pos:
                ph_s, pw_s = hi * AE_FACTOR, wi * AE_FACTOR
                ph_e = min((hi + lt_size) * AE_FACTOR, lq_tensor.shape[2])
                pw_e = min((wi + lt_size) * AE_FACTOR, lq_tensor.shape[3])
                lq_crop = lq_tensor[:, :, ph_s:ph_e, pw_s:pw_e]
                tile_venc[(hi, wi)] = get_venc_features(venc, lq_crop, args)
    _report("venc", 0.3)
    _empty_cache()

    z = torch.randn_like(lq_latent)
    n_steps = args.infer_steps
    t_seq = torch.linspace(1.0, 0.0, n_steps + 1, device=device)

    with torch.no_grad():
        for step_i in range(n_steps):
            t_cur, t_nxt = t_seq[step_i], t_seq[step_i + 1]
            dt = t_cur - t_nxt
            u_acc = torch.zeros(lq_latent.shape, device=device, dtype=torch.float32)
            w_acc = torch.zeros_like(u_acc)
            for hi in h_pos:
                for wi in w_pos:
                    he, we = hi + lt_size, wi + lt_size
                    inp = torch.cat([lq_latent[:, :, hi:he, wi:we], z[:, :, hi:he, wi:we]], dim=1)
                    u_tile = model(inp, t_cur.expand(b), t_nxt.expand(b), tile_venc.get((hi, wi)))
                    u_acc[:, :, hi:he, wi:we] += u_tile.float() * g_weight
                    w_acc[:, :, hi:he, wi:we] += g_weight
            z = (z.float() - dt * (u_acc / w_acc.clamp(min=1e-12))).to(z.dtype)
            _report("sample", 0.4 + 0.45 * (step_i + 1) / n_steps)

        _report("decode", 0.9)
        out = tiled_vae.decode_dispatch(vae, z.float(), args, latents_mean, latents_std, None)
        _empty_cache()
        _report("done", 1.0)
        return out


def get_venc_features(venc, lq_tensor, args):
    """整图 DINOv2 特征提取（与上游 get_venc_features 等价）。

    上游 preprocess_raw_image 的顺序是：先 /255 归一到 [0,1]，再 interpolate，
    再 clip(0,1)，最后 ImageNet 归一化。注意 clip 必须作用在 [0,1] 上——
    若对 [0,255] 做 clip(0,1)，DINOv2 的输入几乎全为 1.0（纯白），
    语义条件被打坏，输出细节与颜色会显著劣化（2026-10-23 用户实测反馈，
    已按上游顺序修复并回归）。
    DINOv2 可能以 fp16 驻留（显存适配），输入需转到同一精度。
    """
    if venc is None:
        return None
    with torch.no_grad():
        raw_image = 0.5 * lq_tensor + 0.5  # [-1,1] -> [0,1]
        raw_image = F.interpolate(raw_image, args.dinov2_size, mode="bicubic").clip(0.0, 1.0)
        raw_image = Normalize(IMAGENET_DEFAULT_MEAN, IMAGENET_DEFAULT_STD)(raw_image)
        raw_image = raw_image.to(next(venc.parameters()).dtype)
        features, x_norm = venc.forward_with_features(raw_image)
        z = [v for k, v in features.items() if k.startswith("layer_")]
        z[-1] = x_norm
        z = [z[i] for i in args.layer_dinov2b_list]
    return z
