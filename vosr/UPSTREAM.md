# 上游代码来源与改动记录（UPSTREAM.md）

## 来源

- 仓库：https://github.com/cswry/VOSR
- 论文：VOSR: A Vision-Only Generative Model for Image Super-Resolution（CVPR 2026）
  作者：Rongyuan Wu, Lingchen Sun, Zhengqiang Zhang, Xiangtao Kong, Jixin Zhao,
  Shihao Wang, Lei Zhang（香港理工大学 / OPPO 研究院）
- 许可证：Apache-2.0（见上游 LICENSE）
- vendor 基线：2026-10-23 克隆的 main 分支（最后提交 eb71477，"update"，2026-04-10）

## vendor 范围

| 本仓路径 | 上游路径 | 说明 |
|---|---|---|
| `vosr/models/` | `models/` | LightningDiT / Qwen-Image 2D VAE / LightDecoder / RoPE / RMSNorm / SwiGLU |
| `vosr/vosr.py` | `vosr.py` | VOSR 采样封装（sample_onestep / sample_multistep_fm） |
| `vosr/tiled_vae.py` | `tiled_vae.py` | VAE 分块编解码（Gaussian 混合） |

## 已做的适配（相对上游的改动）

1. 上游推理脚本（`inference_vosr*.py`）为 CLI 单文件脚本，硬编码
   `preset/ckpts/...` 路径；本仓将其中的模型加载/采样流程重构为
   `app/integrated_app/engines/vosr2_engine.py`（路径走 `config.yaml` 的
   `model.pretrained_dir`，默认 `model/`）。
2. 上游通过 `torch.hub.set_dir('preset/ckpts/torch_cache')` 加载 DINOv2；
   本仓改为 `model/torch_cache`，行为一致。
3. 上游训练/数据加载相关代码（`train_*.py`、`dataloaders/`、`configs/`）不在
   本仓范围内，未 vendor。

## 模型权重来源

- HuggingFace：https://huggingface.co/CSWRY/VOSR （默认经 hf-mirror.com 镜像）
- ModelScope：https://modelscope.cn/models/LULALULALU/VOSR_CKPT
- 布局与上游 `preset/ckpts/` 一致，落到本仓 `model/` 目录。

## 社区复用记录（2026-10-23，按"拥抱社区、不重复造轮子"原则采纳）

| 本仓路径 | 来源 | 说明 |
|---|---|---|
| `vosr/models/dinov2_vitl14.py` | [ylchen333/ComfyUI-VOSR2](https://github.com/ylchen333/ComfyUI-VOSR2) `models/dinov2.py`（Apache-2.0，其本身是 Meta DINOv2 的本地复刻） | 替换 torch.hub 加载：规避"拉取并执行缓存仓库代码"的供应链风险与随仓库 clone 依赖；`forward_intermediate_layer` 可跳过无用 block。适配点：移除 comfy.ops 依赖（ComfyUI 专用），补 `forward_with_features` 保持与上游特征接口一致 |

同时参照该社区节点对齐的行为（inference.py / nodes_vosr.py）：
- 非方形图 reflect 填充到方形/16 倍数后裁回，**不中心裁剪**（避免丢内容）
- dtype 策略：VAE 恒 fp32，DiT 与视觉编码器 fp16（与其 "The VAE always runs in fp32" 一致）
- 分块阈值：输出 >512² 开 DiT 分块、>1024² 开 VAE 分块（其文档明确 "tiling is not optional above 512px"）
- 优于社区版的一点：其 DiT 分块混合累加用 fp16（latent 角落高斯权重 ~3e-11 会下溢），
  本仓已改 fp32 累加修复该隐患

## 后续适配（2026-10-24）

| 位置 | 说明 |
|---|---|
| `vosr/models/dinov2_vitl14.py` | 在原有 ViT-L/14 基础上补 ViT-B/14（`DINOV2_VITB14_CONFIG`）与 `DINOV2_VARIANTS` 注册表，覆盖 0.5B 系检查点的 `enc_type=dinov2b`（enc_dim=768）；架构仍来自 ComfyUI-VOSR2 社区复刻（Apache-2.0） |
| 引擎侧（非 vendored） | `vosr/tiled_vae.py` 的 sd2 分支要求传入 `light_decoder`（`sd21_lwdecoder.pth`），但上游未公开其结构，无法据权重复刻；引擎在 `_build_sd2_decoder()` 中回退到 SD2.1 VAE 自带解码器（`vae.decode`），数值空间一致、速度更慢。**未修改 vendored 代码** |
| `vosr/models/light_decoder.py` | 新增：原样 vendor 上游 `models/light_decoder.py`（SD2.1 轻量解码器，通道数减半），供 0.5B 系解码；引擎在 `_build_sd2_decoder()` 中优先加载 `sd21_lwdecoder.pth`，缺失时回退标准 VAE 解码 |
