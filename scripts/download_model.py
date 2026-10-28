#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""VOSR2-Lite 模型权重下载脚本。

从 HuggingFace `CSWRY/VOSR`（默认经 hf-mirror.com 镜像，可用 HF_ENDPOINT
或 --endpoint 覆盖）下载权重；目录布局对齐上游 `preset/ckpts/`，落到项目
`model/` 根目录，下载完成即可被引擎识别（无需改名/移动）。

用法:
    # 默认：VOSR 2.0 一步模型完整最小集（约 6.6 GB）
    python scripts/download_model.py

    # 其他检查点
    python scripts/download_model.py --checkpoint VOSR_1.4B_os
    python scripts/download_model.py --checkpoint VOSR_0.5B_os

    # 显式指定镜像
    python scripts/download_model.py --endpoint https://hf-mirror.com

特性:
    - 幂等：已存在的文件自动跳过（登记了 sha256 的会校验）
    - 断点续传：.part 文件 + HTTP Range（服务端不支持则整文件重下）
    - 多源回退：hf-mirror -> huggingface.co -> ModelScope

注：DINOv2 架构已在 vosr/models/dinov2_vitl14.py 本地 vendored，
只需下载权重文件，无需 clone facebookresearch/dinov2 仓库
（2026-10-23 起，采纳 ComfyUI-VOSR2 社区方案）。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import time
import urllib.error
import urllib.request
from typing import cast

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_ROOT = os.path.join(PROJECT_ROOT, "model")

DEFAULT_REPO = "CSWRY/VOSR"
DEFAULT_ENDPOINT = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")
MODELSCOPE_BASE = "https://modelscope.cn/models/LULALULALU/VOSR_CKPT" "/resolve/master"

# 每个 checkpoint 的文件清单（相对 model/ 的路径）。
# 注：官方未逐文件公布 sha256，这里只登记文件名与来源；大小校验由
# "已存在则跳过" 的幂等逻辑覆盖（损坏文件请手动删除后重下）。
CHECKPOINTS: dict[str, dict[str, object]] = {
    "VOSR2": {
        "name": "VOSR 2.0 (1.4B one-step)",
        "ae_type": "qwen",
        "files": [
            "VOSR2/args.json",
            "VOSR2/checkpoints/ema_model.safetensors",
            "Qwen-Image-vae-2d/config.json",
            "Qwen-Image-vae-2d/diffusion_pytorch_model.safetensors",
            "torch_cache/checkpoints/dinov2_vitl14_pretrain.pth",
        ],
    },
    "VOSR_1.4B_os": {
        "name": "VOSR-1.4B one-step (distilled)",
        "ae_type": "qwen",
        "files": [
            "VOSR_1.4B_os/args.json",
            "VOSR_1.4B_os/checkpoints/ema_model.safetensors",
            "Qwen-Image-vae-2d/config.json",
            "Qwen-Image-vae-2d/diffusion_pytorch_model.safetensors",
            "torch_cache/checkpoints/dinov2_vitl14_pretrain.pth",
        ],
    },
    "VOSR_1.4B_ms": {
        "name": "VOSR-1.4B multi-step",
        "ae_type": "qwen",
        "files": [
            "VOSR_1.4B_ms/args.json",
            "VOSR_1.4B_ms/checkpoints/ema_model.safetensors",
            "Qwen-Image-vae-2d/config.json",
            "Qwen-Image-vae-2d/diffusion_pytorch_model.safetensors",
            "torch_cache/checkpoints/dinov2_vitl14_pretrain.pth",
        ],
    },
    "VOSR_0.5B_os": {
        "name": "VOSR-0.5B one-step (distilled)",
        "ae_type": "sd2",
        "files": [
            "VOSR_0.5B_os/args.json",
            "VOSR_0.5B_os/checkpoints/ema_model.safetensors",
            "sd21_lwdecoder.pth",
            "stable-diffusion-2-1-base/vae/config.json",
            "stable-diffusion-2-1-base/vae/diffusion_pytorch_model.safetensors",
            "torch_cache/checkpoints/dinov2_vitb14_pretrain.pth",
        ],
    },
    "VOSR_0.5B_ms": {
        "name": "VOSR-0.5B multi-step",
        "ae_type": "sd2",
        "files": [
            "VOSR_0.5B_ms/args.json",
            "VOSR_0.5B_ms/checkpoints/ema_model.safetensors",
            "sd21_lwdecoder.pth",
            "stable-diffusion-2-1-base/vae/config.json",
            "stable-diffusion-2-1-base/vae/diffusion_pytorch_model.safetensors",
            "torch_cache/checkpoints/dinov2_vitb14_pretrain.pth",
        ],
    },
}


def _human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, target: str) -> None:
    """流式下载，.part 断点续传（服务端不支持 Range 时整文件重下）。"""
    os.makedirs(os.path.dirname(target), exist_ok=True)
    part = target + ".part"
    start = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {"Range": f"bytes={start}-"} if start else {}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=120) as resp:
        resumable = start and resp.status == 206
        mode = "ab" if resumable else "wb"
        total = int(resp.headers.get("Content-Length", 0)) + (start if resumable else 0)
        downloaded = start if resumable else 0
        last_log = time.time()
        with open(part, mode) as f:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if time.time() - last_log > 2:
                    pct = f" {downloaded / total * 100:.0f}%" if total else ""
                    print(
                        f"\r  {os.path.basename(target)}: " f"{_human(downloaded)}{pct}",
                        end="",
                        flush=True,
                    )
                    last_log = time.time()
    if total:
        print(f"\r  {os.path.basename(target)}: {_human(total)} (100%)")
    os.replace(part, target)


def _fetch(rel: str, endpoint: str, source: str = "auto") -> None:
    target = os.path.join(MODEL_ROOT, rel)
    if os.path.isfile(target):
        print(f"[skip] {rel} 已存在（如需重下请先删除）")
        return
    hf_base = f"{endpoint}/{DEFAULT_REPO}/resolve/main"
    hf_official = f"https://huggingface.co/{DEFAULT_REPO}/resolve/main"
    if source == "modelscope":
        bases = [MODELSCOPE_BASE, hf_base, hf_official]
    elif source == "hf":
        bases = [hf_base, hf_official, MODELSCOPE_BASE]
    else:  # auto：镜像 -> 官方 -> 魔搭
        bases = [hf_base, hf_official, MODELSCOPE_BASE]
    last_err: Exception | None = None
    for base in bases:
        url = f"{base}/{rel}"
        try:
            print(f"[get ] {rel}")
            print(f"       <- {base.split('/resolve')[0]}")
            _download(url, target)
            return
        except (urllib.error.URLError, OSError) as exc:
            last_err = exc
            print(f"[retry] 失败: {exc}")
    raise SystemExit(f"下载失败: {rel}（最后错误: {last_err}）")


def main() -> None:
    parser = argparse.ArgumentParser(description="VOSR2-Lite 模型权重下载")
    parser.add_argument(
        "--checkpoint",
        default="VOSR2",
        choices=sorted(CHECKPOINTS),
        help="要下载的检查点（默认 VOSR2）",
    )
    parser.add_argument(
        "--endpoint",
        default=DEFAULT_ENDPOINT,
        help="HF 镜像端点（默认 https://hf-mirror.com；" "或用 HF_ENDPOINT 环境变量）",
    )
    parser.add_argument(
        "--source",
        default="auto",
        choices=("auto", "hf", "modelscope"),
        help="下载源优先级：auto=镜像→官方→魔搭（默认）；"
        "hf=只用 HuggingFace；modelscope=优先魔搭"
        "（国内实测快 5-8 倍）",
    )
    args = parser.parse_args()
    endpoint = args.endpoint.rstrip("/")

    entry = CHECKPOINTS[args.checkpoint]
    print(f"检查点: {args.checkpoint} — {entry['name']}")
    order = {
        "auto": f"{endpoint} → huggingface.co → modelscope",
        "hf": f"{endpoint} → huggingface.co",
        "modelscope": "modelscope → " + endpoint + " → huggingface.co",
    }
    print(f"来源优先级: {order[args.source]}")
    print(f"目标: {MODEL_ROOT}\n")

    # CHECKPOINTS 的值是 dict[str, object]（各检查点字段不一），此处按约定取文件清单
    for rel in cast("list[str]", entry["files"]):
        _fetch(str(rel), endpoint, args.source)

    print("\n完成。可运行 python scripts/smoke_infer.py -i <图片> -o outputs/ -u 2 验证。")


if __name__ == "__main__":
    main()
