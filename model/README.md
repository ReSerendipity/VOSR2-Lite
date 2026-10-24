# model/ — 模型权重目录（不入库）

本目录存放 VOSR2 系列模型权重，布局对齐上游 `preset/ckpts/`：

```
model/
├── VOSR2/                          # VOSR 2.0 一步模型（默认，1.4B）
│   ├── args.json                   # 模型结构参数（引擎据此构建 LightningDiT）
│   └── checkpoints/ema_model.safetensors
├── VOSR_1.4B_os/                   # VOSR-1.4B 一步蒸馏（可选）
├── VOSR_1.4B_ms/                   # VOSR-1.4B 多步（25 步，可选，cfg_scale=2.0）
├── VOSR_0.5B_os/                   # VOSR-0.5B 一步蒸馏（可选，低显存）
├── VOSR_0.5B_ms/                   # VOSR-0.5B 多步（可选，低显存）
├── Qwen-Image-vae-2d/              # Qwen-Image 2D VAE（1.4B 系共用）
│   ├── config.json
│   └── diffusion_pytorch_model.safetensors
├── stable-diffusion-2-1-base/      # SD2.1 VAE（0.5B 系共用）
│   └── vae/
├── sd21_lwdecoder.pth              # SD2.1 轻量解码器（0.5B 系，见下方说明）
└── torch_cache/                    # DINOv2 权重（架构已本地 vendored）
    └── checkpoints/
        ├── dinov2_vitl14_pretrain.pth   # 1.4B 系（enc_type=dinov2l, enc_dim=1024）
        └── dinov2_vitb14_pretrain.pth   # 0.5B 系（enc_type=dinov2b, enc_dim=768）
```

下载：

```bash
python scripts/download_model.py                       # 默认 VOSR2（约 6.6 GB）
python scripts/download_model.py --checkpoint VOSR_1.4B_ms
python scripts/download_model.py --checkpoint VOSR_0.5B_os --source modelscope
```

- 来源优先级用 `--source` 控制：`auto`（默认，镜像→官方→魔搭）/ `hf` / `modelscope`
  （国内实测魔搭比 hf-mirror 快 5–8 倍）；镜像端点可用 `HF_ENDPOINT` 覆盖。
- 已存在的文件自动跳过，`.part` 支持断点续传。

## 架构说明

- **DINOv2**：`vosr/models/dinov2_vitl14.py` 本地复刻了 ViT-L/14 与 ViT-B/14
  （来自 ComfyUI-VOSR2 社区节点，Apache-2.0），只需权重文件，无需 clone
  facebookresearch/dinov2 仓库。`enc_type` 由检查点 `args.json` 决定。
- **0.5B 系的解码**：上游推理代码对 sd2 分支调用轻量解码器
  `sd21_lwdecoder.pth`，但其网络结构未公开，本仓无法据权重复刻；引擎因此
  回退到 SD2.1 VAE 自带解码器（数值空间一致，速度更慢、显存占用更高），
  启动时会在日志中给出提示。该文件仍会下载，仅为将来接入官方解码器预留。

本目录内容被 .gitignore 忽略，请勿把权重提交入库。
