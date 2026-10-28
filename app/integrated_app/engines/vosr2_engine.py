"""VOSR2 推理引擎。

封装上游 cswry/VOSR（Apache-2.0，见 vosr/UPSTREAM.md）的加载与采样流程：
  Qwen-Image 2D VAE / SD2.1 VAE  ->  DINOv2-L 视觉编码  ->  LightningDiT
  一步（或多步）flow matching 采样  ->  VAE 解码  ->  颜色对齐（wavelet/adain）。

设计要点：
- 依赖 torch/torchvision（模块顶层导入，测试环境装 CPU 版即可）；
  vendored 的 vosr/ 包不需要 GPU 即可 import。
- 权重落盘目录布局对齐上游 preset/ckpts/，根目录由 config.yaml
  model.pretrained_dir 指定（默认 model/）。
- fp16 适配：DiT 与 DINOv2 加载后转 fp16（上游权重即 fp16），VAE 保持 fp32
  （Qwen VAE 参数量小，fp32 数值更稳），12GB 显存可跑 1.4B one-step。
"""

from __future__ import annotations

import json
import os
import random

# ── vendored 上游代码（vosr/ 包）────────────────────────────────────────────
import sys
import types
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import torch
import torch.nn.functional as F
from loguru import logger
from PIL import Image
from torchvision import transforms
from torchvision.transforms import Normalize

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from vosr import tiled_vae  # noqa: E402
from vosr.vosr import VOSR  # noqa: E402

_pad_to_multiple = tiled_vae._pad_to_multiple  # 8 倍数补齐（VAE 对齐）


def _empty_cache() -> None:
    """阶段间释放未使用的显存（编码激活 / 解码中间量）。"""
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _pad_to_square(x: torch.Tensor) -> tuple[torch.Tensor, tuple[int, int]]:
    """reflect 填充到正方形（上游 forward_flexible 的 H==W 断言要求）。

    Returns: (padded, (orig_h, orig_w))
    """
    _, _, h, w = x.shape
    side = max(h, w)
    if h == side and w == side:
        return x, (h, w)
    padded = torch.nn.functional.pad(
        x, (0, side - w, 0, side - h), mode="reflect")
    return padded, (h, w)


# 多步模型（*_ms）训练分辨率为 512，输出过小时（实测 ≤256px）25 步轨迹会发散；
# 384px 起稳定（2026-10-24 实测：256→糊成色块，384/512/1548 正常）
MULTISTEP_MIN_SIDE = 384

IMAGENET_DEFAULT_MEAN = (0.485, 0.456, 0.406)
IMAGENET_DEFAULT_STD = (0.229, 0.224, 0.225)

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

ProgressCb = Callable[[str, float], None]  # (stage, ratio 0..1)

# 输出边长硬上限（像素）。4x 放大 + 大图很容易产生数小时且必然 OOM 的任务，
# 超过上限直接报错并建议降低倍率（可用 config inference.max_output_side 调整）。
MAX_OUTPUT_SIDE_DEFAULT = 6144
# 自动分块参数：Qwen VAE 为 fp32，全图编码约 11KB/像素，3096² 需 ~100GB，
# 因此输出超过阈值必须分块（2026-10-23 实测 OOM 后的修复）。
_VAE_TILE_AUTO_LOW_VRAM = 512    # 可用显存 ≤16GB
_VAE_TILE_AUTO_HIGH_VRAM = 1024  # 可用显存 >16GB
_DIT_TILE_AUTO = 512


@dataclass
class RestoreParams:
    """单次修复的推理参数（全部有默认值，UI 只传覆盖项）。"""

    upscale: int = 4
    steps: int = 1
    align_method: str = "wavelet"
    seed: int = 42
    tile_size: int = 0
    vae_tile_size: int = 0
    tile_overlap: int = 32
    output_format: str = "png"


@dataclass
class EngineStatus:
    loaded: bool = False
    checkpoint: str = ""
    device: str = ""
    dtype: str = ""
    error: str = ""
    vram_gb: float = 0.0
    load_seconds: float = 0.0


class VOSR2Engine:
    """VOSR2 检查点推理引擎（单实例，串行使用）。"""

    def __init__(self, checkpoint_name: str, model_root: str,
                 device: str = "auto", progress_cb: ProgressCb | None = None,
                 max_output_side: int | None = None):
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

    # ── 路径 ────────────────────────────────────────────────────────────────
    @property
    def ckpt_dir(self) -> str:
        return os.path.join(self.model_root, self.checkpoint_name)

    def _resolve(self, relative: str) -> str:
        """把 args.json 里的 preset/ckpts/... 路径改写到 model_root 下。"""
        tail = os.path.basename(os.path.normpath(relative))
        return os.path.join(self.model_root, tail)

    # ── 加载 ────────────────────────────────────────────────────────────────
    def _cb(self, stage: str, ratio: float) -> None:
        if self._progress_cb:
            try:
                self._progress_cb(stage, ratio)
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
                "（≥8GB 显存跑 1.4B one-step）；CPU 推理不在支持范围。")
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
            dtype=str(next(self.dit.parameters()).dtype),
            load_seconds=round(time.time() - t0, 2),
            vram_gb=round(torch.cuda.memory_allocated() / 1024**3, 2),
        )
        self._cb("ready", 1.0)
        logger.info(
            f"引擎就绪: {self.checkpoint_name} device={self.device} "
            f"load={self.status.load_seconds}s vram={self.status.vram_gb}GB")

    # ── 加载各组件 ──────────────────────────────────────────────────────────
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
                logger.warning(f"轻量解码器缺少 {len(missing)} 个权重，"
                               f"效果可能异常（示例: {missing[:3]}）")
            decoder = decoder.to(dev).eval()
            for param in decoder.parameters():
                param.requires_grad_(False)
            logger.info(f"已加载 SD2.1 轻量解码器: {ckpt}")
            return decoder

        logger.warning(
            "sd2 检查点未找到 sd21_lwdecoder.pth，回退标准 SD2.1 VAE 解码"
            "（更慢、显存占用更高）；如需官方速度请重新运行下载脚本")
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
                    "请运行 python scripts/download_model.py")
            vae = AutoencoderKLQwenImage2D.from_pretrained(ae_path)
        elif ae_type == "sd2":
            from diffusers import AutoencoderKL

            ae_path = self._resolve("stable-diffusion-2-1-base")
            if not os.path.isdir(ae_path):
                raise FileNotFoundError(
                    f"SD2.1 VAE 不存在: {ae_path}（0.5B 模型需要）；"
                    "请运行 python scripts/download_model.py --checkpoint VOSR_0.5B_os")
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
                f"已 vendored: {', '.join(sorted(DINOV2_VARIANTS))}")
        builder, weight_name, enc_dim = variant

        declared = int(getattr(args, "enc_dim", enc_dim))
        if declared != enc_dim:
            raise ValueError(
                f"args.enc_dim={declared} 与 {args.enc_type} 的 {enc_dim} 不一致，"
                "请检查检查点 args.json")

        ckpt = os.path.join(self.model_root, "torch_cache", "checkpoints",
                            weight_name)
        if not os.path.isfile(ckpt):
            raise FileNotFoundError(
                f"DINOv2 权重不存在: {ckpt}；"
                "请运行 python scripts/download_model.py")

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
                    t_freq = TimestepEmbedder.timestep_embedding(
                        t, emb.frequency_embedding_size)
                    return emb.mlp(t_freq.to(dtype))
                return forward

            emb.forward = _make_forward(emb, dtype)
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
                "请运行 python scripts/download_model.py")
        return hits[0]

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

    # ── 推理 ────────────────────────────────────────────────────────────────
    @staticmethod
    def _auto_tile_sizes(output_side: int, free_vram_gb: float,
                         user_tile: int, user_vae_tile: int) -> tuple[int, int]:
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
            vae_tile = (_VAE_TILE_AUTO_HIGH_VRAM if free_vram_gb > 16
                        else _VAE_TILE_AUTO_LOW_VRAM)
        return dit_tile, vae_tile

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
                f"（本图最高 ×{max_factor}）或先裁剪输入。")
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
                f"换更大的输入图，或把采样步数改为 1（一步蒸馏）")

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

    def restore(self, image: Image.Image, params: RestoreParams,
                progress_cb: ProgressCb | None = None) -> Image.Image:
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
        free_gb = (torch.cuda.mem_get_info()[0] / 1024**3
                   if torch.cuda.is_available() else 0.0)
        dit_tile, vae_tile = self._auto_tile_sizes(
            output_long, free_gb, params.tile_size, params.vae_tile_size)
        args.tile_size = dit_tile
        args.vae_tile_size = vae_tile
        if dit_tile or vae_tile:
            logger.info(
                f"分块推理: 输出 {output_w}x{output_h}px, "
                f"DiT tile={dit_tile or '关'}, VAE tile={vae_tile or '关'} "
                f"(可用显存 {free_gb:.1f}GB)")

        # 非方形图不裁剪内容：bicubic 预放大后 reflect 填充到 16 的倍数
        # （VAE 下采样 8 x DiT patch 2，保证 latent 对齐；VAE tiled 路径内部
        # 还会按 8 补齐），VAE 解码后裁回请求的输出尺寸。方案对齐社区
        # ComfyUI-VOSR2（2026-10-23 采纳）。
        input_img = raw.resize((output_w, output_h), Image.BICUBIC)
        lq = transforms.ToTensor()(input_img).unsqueeze(0).to(dev) * 2.0 - 1.0
        lq, _, _ = _pad_to_multiple(lq, 16)  # 补齐到 16 倍数（VAE 8 x DiT patch 2）
        # input_img 保留到颜色对齐之后（wavelet/adain 需要它做参考）

        self._report(cb, "encode", 0.1)
        with torch.no_grad():
            if dit_tile and dit_tile > 0:
                sr_tensor = tiled_vae_latent_inference(
                    self.dit, self.vae, self.venc, lq, args, device=dev,
                    progress_cb=cb)
                _empty_cache()
            else:
                lq_latent, latents_mean, latents_std = tiled_vae.encode_dispatch(
                    self.vae, lq, args, dev)
                _empty_cache()  # 释放 VAE 编码激活，给采样腾显存
                # dtype 对齐：VAE 输出 fp32，DiT 可能为 fp16（显存适配），
                # 采样前把 latent 转到 DiT 精度，避免 cat  dtype 不匹配
                dit_dtype = next(self.dit.parameters()).dtype
                lq_latent = lq_latent.to(dit_dtype)
                # forward_flexible 仅支持方形 latent：reflect 补方，算完裁回
                lq_latent_sq, lat_hw = _pad_to_square(lq_latent)
                self._report(cb, "venc", 0.25)
                z_fea = get_venc_features(self.venc, lq, args)
                _empty_cache()
                self._report(cb, "sample", 0.4)
                sr_latent = self._sample(lq_latent_sq, z_fea, args, cb)[
                    :, :, :lat_hw[0], :lat_hw[1]]
                _empty_cache()
                self._report(cb, "decode", 0.85)
                sr_tensor = tiled_vae.decode_dispatch(
                    self.vae, sr_latent.float(), args, latents_mean, latents_std,
                    self.light_decoder)
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

    # ── 采样辅助 ────────────────────────────────────────────────────────────
    def _sample(self, lq_latent, z_fea, args, cb=None):
        """flow matching 采样。

        one-step（infer_steps=1）走 VOSR.sample_onestep；多步走上游
        sample_multistep_fm（含 CFG 与 weak-condition 逻辑，保持与官方一致）。
        """
        steps = max(1, int(args.infer_steps))
        if steps == 1:
            return self.vosr.sample_onestep(
                self.dit, lq_latent, venc_fea=z_fea, n_steps=1)
        self._report(cb, "sample", 0.45)
        result = self.vosr.sample_multistep_fm(
            self.dit, lq_latent, venc_fea=z_fea, n_steps=steps)
        self._report(cb, "sample", 0.85)
        return result

    @staticmethod
    def _align(sr_img: Image.Image, input_img: Image.Image,
               method: str) -> Image.Image:
        if method == "adain":
            return _adain_color_fix(sr_img, input_img)
        if method == "wavelet":
            return _wavelet_color_fix(sr_img, input_img)
        return sr_img

    @staticmethod
    def _report(cb: ProgressCb | None, stage: str, ratio: float) -> None:
        if cb:
            try:
                cb(stage, ratio)
            except Exception:
                pass


# ── 分块推理（latent 空间，Gaussian 混合；逻辑对齐上游 tiled_latent_inference）──
def tiled_vae_latent_inference(model, vae, venc, lq_tensor, args, device="cuda",
                               progress_cb: ProgressCb | None = None):
    """DiT 前向按 latent 分块、Gaussian 混合；VAE 编解码按 vae_tile_size 分块。

    progress_cb: (stage, ratio 0..1)，ratio 在 encode(0.1)->venc(0.3)->
    sample(0.4..0.85, 按步数细分)->decode(0.85)->done(1.0) 之间推进。
    """

    def _report(stage: str, ratio: float) -> None:
        if progress_cb:
            try:
                progress_cb(stage, ratio)
            except Exception:
                pass

    AE_FACTOR = 8
    b = lq_tensor.shape[0]
    patch_size = getattr(args, "patch_size", 2)

    with torch.no_grad():
        lq_latent, latents_mean, latents_std = tiled_vae.encode_dispatch(
            vae, lq_tensor, args, device)
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
                u = model(torch.cat([lq_latent, z], 1),
                          t_cur.expand(b), t_nxt.expand(b), z_fea)
                z = z - (t_cur - t_nxt) * u
                _report("sample", 0.4 + 0.45 * (i + 1) / n_steps)
            _report("decode", 0.9)
            out = tiled_vae.decode_dispatch(
                vae, z.float(), args, latents_mean, latents_std, None)
            _empty_cache()
            _report("done", 1.0)
            return out

    h_pos = tiled_vae._make_tile_grid(lh, lt_size, lt_overlap)
    w_pos = tiled_vae._make_tile_grid(lw, lt_size, lt_overlap)
    # 混合累加用 fp32：latent 角落只被单瓦片覆盖，2D 高斯权重可低至 ~3e-11，
    # fp16 下溢为 0 会导致 0/0=NaN、角落出彩色噪点（2026-10-23 实测）。
    # DiT 前向仍走 fp16 省显存，仅累加与归一化升 fp32。
    g_weight = tiled_vae._gaussian_weights(lt_size, lt_size, lc, device).float()
    logger.info(f"[分块推理] latent {lh}x{lw}, tile={lt_size}, "
                f"overlap={lt_overlap}, grid={len(h_pos)}x{len(w_pos)}")

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
                    inp = torch.cat([lq_latent[:, :, hi:he, wi:we],
                                     z[:, :, hi:he, wi:we]], dim=1)
                    u_tile = model(inp, t_cur.expand(b), t_nxt.expand(b),
                                   tile_venc.get((hi, wi)))
                    u_acc[:, :, hi:he, wi:we] += u_tile.float() * g_weight
                    w_acc[:, :, hi:he, wi:we] += g_weight
            z = (z.float() - dt * (u_acc / w_acc.clamp(min=1e-12))).to(z.dtype)
            _report("sample", 0.4 + 0.45 * (step_i + 1) / n_steps)

        _report("decode", 0.9)
        out = tiled_vae.decode_dispatch(
            vae, z.float(), args, latents_mean, latents_std, None)
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
        raw_image = (0.5 * lq_tensor + 0.5)  # [-1,1] -> [0,1]
        raw_image = F.interpolate(
            raw_image, args.dinov2_size, mode="bicubic").clip(0.0, 1.0)
        raw_image = Normalize(IMAGENET_DEFAULT_MEAN, IMAGENET_DEFAULT_STD)(raw_image)
        raw_image = raw_image.to(next(venc.parameters()).dtype)
        features, x_norm = venc.forward_with_features(raw_image)
        z = [v for k, v in features.items() if k.startswith("layer_")]
        z[-1] = x_norm
        z = [z[i] for i in args.layer_dinov2b_list]
    return z


# ── 颜色对齐（与上游一致）───────────────────────────────────────────────────
def _adain_color_fix(target: Image.Image, source: Image.Image) -> Image.Image:
    from torchvision.transforms.functional import to_pil_image, to_tensor

    target_tensor = to_tensor(target).unsqueeze(0)
    source_tensor = to_tensor(source).unsqueeze(0)
    eps = 1e-5
    target_mean = torch.mean(target_tensor, dim=[2, 3], keepdim=True)
    target_std = torch.std(target_tensor, dim=[2, 3], keepdim=True) + eps
    source_mean = torch.mean(source_tensor, dim=[2, 3], keepdim=True)
    source_std = torch.std(source_tensor, dim=[2, 3], keepdim=True) + eps
    target_tensor = (target_tensor - target_mean) / target_std * source_std + source_mean
    return to_pil_image(torch.clamp(target_tensor[0], 0, 1))


def _wavelet_color_fix(target: Image.Image, source: Image.Image) -> Image.Image:
    import cv2

    target_np = np.array(target).astype(np.float32) / 255.0
    source_np = np.array(source.resize(target.size, Image.LANCZOS)).astype(np.float32) / 255.0
    sigma = 5
    source_low = cv2.GaussianBlur(source_np, (0, 0), sigma)
    target_low = cv2.GaussianBlur(target_np, (0, 0), sigma)
    target_high = target_np - target_low
    result = np.clip(source_low + target_high, 0, 1) * 255.0
    return Image.fromarray(result.astype(np.uint8))
