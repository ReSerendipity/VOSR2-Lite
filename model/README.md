# model/ — 模型权重目录（不入库）

本目录存放 VOSR2 系列模型权重，布局对齐上游 `preset/ckpts/`：

```
model/
├── VOSR2/                          # VOSR 2.0 一步模型（默认，1.4B）
│   ├── args.json                   # 模型结构参数（引擎据此构建 LightningDiT）
│   └── checkpoints/ema_model.safetensors
├── Qwen-Image-vae-2d/              # Qwen-Image 2D VAE（1.4B 系共用）
│   ├── config.json
│   └── diffusion_pytorch_model.safetensors
├── stable-diffusion-2-1-base/      # SD2.1 VAE（0.5B 系需要）
├── sd21_lwdecoder.pth              # SD2.1 轻量解码器（0.5B 系需要）
└── torch_cache/                    # DINOv2 权重（架构已本地 vendored）
    └── checkpoints/dinov2_vitl14_pretrain.pth
```

下载：`python scripts/download_model.py`（默认 VOSR2，约 6.6 GB，
走 hf-mirror 镜像，可用 `HF_ENDPOINT` 覆盖）。其他检查点：
`--checkpoint VOSR_0.5B_os` 等。

DINOv2 架构已在 `vosr/models/dinov2_vitl14.py` 本地 vendored
（来自 ComfyUI-VOSR2 社区节点，Apache-2.0），只需权重文件，
无需 clone facebookresearch/dinov2 仓库。

本目录内容被 .gitignore 忽略，请勿把权重提交入库。
