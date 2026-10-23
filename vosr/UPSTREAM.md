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
