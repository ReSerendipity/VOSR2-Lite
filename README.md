# VOSR2-Lite

> 治理规范：[ReSerendipity/.github/governance](https://github.com/ReSerendipity/.github/tree/main/governance)

![CI](https://github.com/ReSerendipity/VOSR2-Lite/actions/workflows/ci.yml/badge.svg?branch=main)
![Release](https://img.shields.io/github/v/release/ReSerendipity/VOSR2-Lite?label=release)
![License](https://img.shields.io/badge/license-Apache%202.0-green)
![Python](https://img.shields.io/badge/python-3.12+-yellow?logo=python&logoColor=white)
![GPU](https://img.shields.io/badge/GPU-NVIDIA%20CUDA-76B900?logo=nvidia&logoColor=white)
![Last commit](https://img.shields.io/github/last-commit/ReSerendipity/VOSR2-Lite)
![PRs welcome](https://img.shields.io/badge/PRs-welcome-ff69b4)

**基于 VOSR2 扩散模型的独立图像超分辨率工具箱 — 无需 ComfyUI，一键本地修复。**

[中文](#中文) | [English](#english)

---

## 中文

### 项目简介

VOSR2-Lite 是基于 [VOSR2](https://github.com/cswry/VOSR)（CVPR 2026）开源扩散模型的独立图像超分辨率工具箱。它把原本需要 ComfyUI 才能跑通的生成式超分链路，封装成一个只绑定 `127.0.0.1` 的本地 Web 应用：上传图片、调参数、点开始，即可在浏览器里得到 ×1~×4 放大结果。

- **不依赖 ComfyUI**：FastAPI + Jinja2 + HTMX 自带完整工作台。
- **本地推理**：数据不出机器；Web 服务仅监听 `127.0.0.1`。
- **显存友好**：DiT / VAE 两级分块，fp16 适配消费级显卡（1.4B 建议 8 GB，0.5B 4 GB）。
- **多检查点**：VOSR2（一步，默认）/ VOSR-1.4B（一步蒸馏 / 25 步多步）/ VOSR-0.5B（低显存备选）。

### 核心特性

- **一步生成式超分**：flow matching 单步采样（蒸馏模型），×1~×4 放大。
- **前后对比查看器**：拖动分割线逐处比对原图与修复结果；滚轮缩放、拖拽平移、1:1 原生像素、跟随光标的放大镜（分割线两侧同时可见）。
- **批量处理**：一次多选或拖入多张图片，按队列串行处理，逐张显示状态并可单独打开对比。
- **实时进度**：SSE 推送加载 / 编码 / 特征 / 采样 / 解码 / 对齐各阶段进度与已用 / 剩余时间。
- **历史记录**：SQLite 持久化任务与缩略图，可分页回看、对比、下载、删除。
- **效率细节**：参数预设本地保存、Ctrl+V 粘贴上传、输出尺寸与耗时预估、下载可选 PNG / JPEG / WebP、失败卡一键重试、快捷键（0/1/±/M/C）。
- **多语言界面**：简体中文 / English；主题跟随系统，可手动切换。
- **安全基线**：仅绑 `127.0.0.1`、CSP / 安全响应头、上传魔数校验、限流、路径白名单。

### 展示

> 下列截图在未下载权重的开发模式下通过本地 Web UI 截取；对比查看器中的左右两图为演示素材，仅用于展示 UI，不代表真实推理质量。

**主界面**：项目概览、GPU / 模型状态、最近任务入口。

![主界面](https://gcore.jsdelivr.net/gh/ReSerendipity/VOSR2-Lite@main/docs/assets/readme-vosr2-lite-1-home-20261026.png)

**前后对比查看器**：拖动分割线逐处比对，支持放大镜、缩放、1:1。

![对比查看器](https://gcore.jsdelivr.net/gh/ReSerendipity/VOSR2-Lite@main/docs/assets/readme-vosr2-lite-2-compare.png)

**推理参数面板**：检查点选择、放大倍率、采样步数、颜色对齐、随机种子、DiT / VAE 分块尺寸，参数预设本地保存。

![参数面板](https://gcore.jsdelivr.net/gh/ReSerendipity/VOSR2-Lite@main/docs/assets/readme-vosr2-lite-3-params-20261026.png)

**批量队列**：多选或拖入多张图片后串行排队，逐张显示缩略图与状态，可清空或重试。

![批量队列](https://gcore.jsdelivr.net/gh/ReSerendipity/VOSR2-Lite@main/docs/assets/readme-vosr2-lite-4-batch.png)

**历史记录**：SQLite 持久化，分页查看过往任务，一键重新对比或下载。

![历史记录](https://gcore.jsdelivr.net/gh/ReSerendipity/VOSR2-Lite@main/docs/assets/readme-vosr2-lite-5-history-20261026.png)

**架构总览**：从浏览器模板到 FastAPI 路由 / 中间件 / 服务，再到按需懒加载的 VOSR 引擎与本地存储。

![架构总览](https://gcore.jsdelivr.net/gh/ReSerendipity/VOSR2-Lite@main/docs/assets/readme-vosr2-lite-architecture.svg)

### 快速开始

> 约 10 分钟；Windows 为一等公民，Linux 亦可运行。

1. **安装 Python 3.12+**：<https://www.python.org/downloads/>，Windows 安装时勾选 "Add python.exe to PATH"。
2. **获取代码**：
   ```bash
   git clone https://github.com/ReSerendipity/VOSR2-Lite.git
   cd VOSR2-Lite
   ```
3. **安装依赖**：Windows 双击 `install.bat`；或手动建虚拟环境：
   ```bash
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```
   > torch 需 CUDA 版（≥2.7，cu128；RTX 50 系 sm_120 必须，cu121 不支持）：
   > `pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128`
4. **下载模型**（约 6.6 GB，仅权重；DINOv2 架构已本地 vendored，无需 clone 上游仓库）：
   ```bash
   .venv\Scripts\python scripts\download_model.py
   ```
   大陆网络慢时设置 `set HF_ENDPOINT=https://hf-mirror.com`（脚本默认即走该镜像）。
5. **启动**：双击 `start.bat`，浏览器自动打开 <http://127.0.0.1:7871>。
6. **最小验证**：进入「修复工作台」上传一张 PNG / JPEG → 保持默认参数 → 点击「开始修复」；「系统状态」页可查看 GPU 与权重完整性。

### 系统要求与兼容性

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows（推荐）/ Linux |
| GPU | NVIDIA CUDA GPU（必须，≥8 GB 显存跑 1.4B；不支持 CPU 推理） |
| Python | 3.12+ |
| torch | ≥ 2.7（cu128；RTX 50 系 sm_120 必须） |

#### 模型选型

| 检查点 | 类型 | 架构 | 最低显存 | 体积 | 说明 |
|---|---|---|---|---|---|
| `VOSR2`（默认） | 一步 | 1.4B DiT + Qwen-Image 2D VAE + DINOv2-L | 8 GB | ~6.6 GB | 细节还原最强，默认 |
| `VOSR_1.4B_os` | 一步蒸馏 | 同上 | 8 GB | ~6.6 GB | VOSR 1.0 蒸馏版 |
| `VOSR_1.4B_ms` | 多步(25) | 同上 | 8 GB | ~6.6 GB | CFG 可调，忠实度优先 |
| `VOSR_0.5B_os` | 一步蒸馏 | 0.5B DiT + SD2.1 VAE + DINOv2-B | 4 GB | ~2.5 GB | 低显存备选 |

配套必需文件：1.4B 系用 `Qwen-Image-vae-2d/`（2D VAE）+
`torch_cache/checkpoints/dinov2_vitl14_pretrain.pth`；0.5B 系用
`stable-diffusion-2-1-base/vae/` + `torch_cache/checkpoints/dinov2_vitb14_pretrain.pth`。
DINOv2 架构已本地 vendored，无需 clone 上游仓库。

> 多步模型（`*_ms`）：建议 25 步、输出长边 ≥384 px（训练分辨率 512；实测 ≤256 px 会发散，引擎会前置拒绝）。0.5B 系使用上游轻量解码器 `sd21_lwdecoder.pth`，详见 `model/README.md`。

### 使用示例

- **单图修复**：拖入一张老照片 → 选 ×4 → 一步蒸馏 → 开始；完成后在对比查看器里拖动分割线看细节。
- **大批量照片**：在文件选择框多选 20 张 JPEG → 队列自动串行 → 「历史记录」里逐张回看与下载。
- **保守还原**：切到 `VOSR_1.4B_ms`（25 步），文字 / 文档类图片忠实度更高。
- **低显存机器**：切到 `VOSR_0.5B_os`（4 GB 显存可跑）。
- **命令行冒烟**（不经 Web 服务，验证模型与推理链路）：
  ```bash
  .venv\Scripts\python scripts\smoke_infer.py -i test.png -o outputs/ -u 2
  ```

### 常见问题

| 问题 | 解决 |
|---|---|
| 模型文件未找到 | 运行 `python scripts/download_model.py`；权重须在 `model/` 根下并按上游目录结构摆放 |
| 显存不足（OOM） | 分块保持「自动」：引擎按输出尺寸与显存自动开启 DiT/VAE 分块（3096² 输出实测可跑）；仍 OOM 可手动把 VAE 分块调 512、或换 `VOSR_0.5B_os` |
| 输出过大被拒 | 输出边长上限 6144 px（`config.yaml` `inference.max_output_side`）；超限会提示本图最高倍率，降低倍率或裁剪后重试 |
| `install.bat` 装 PyTorch 失败 | 手动执行 `pip install torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu128`；本机 pip 若配了镜像 extra-index，CPU 版会顶包，需加 `PIP_CONFIG_FILE=NUL` |
| 端口被占用 | 应用自动顺延到下一个可用端口，以日志打印地址为准 |
| HuggingFace 下载慢 | `set HF_ENDPOINT=https://hf-mirror.com` 后重跑下载脚本 |

> VOSR2 是**生成式**模型：输出可能包含输入不支持的"合理"细节；文字密集图像（路牌 / UI / 文档）的小字可能被"重写"，请自行评估可读性风险。详见 [docs/USER_AGREEMENT.md](docs/USER_AGREEMENT.md)。

### 文档与治理

- [docs/release-governance.md](docs/release-governance.md)：发布 / 回滚治理（版本位同步、资产清单与 SHA-256 校验、draft 人工审核、回滚方式）。
- [docs/USER_AGREEMENT.md](docs/USER_AGREEMENT.md)：使用合规声明。
- [docs/agent-contract.md](docs/agent-contract.md)：自动化 agent 协作契约。
- 官方发布：通过 GitHub Release 分发，最新版见 <https://github.com/ReSerendipity/VOSR2-Lite/releases/latest>。发布包只含源代码与清单（`VOSR2-Lite-v<X.Y.Z>.zip` + `SHA256SUMS.txt` + CycloneDX SBOM）；**模型权重不随包分发**，请用 `python scripts/download_model.py` 获取。

### 安全、贡献与支持

**网络绑定警告**：Web UI 默认仅绑定 `127.0.0.1`（`config.yaml` 的 `server.host`）。**严禁改为 `0.0.0.0` 或公网 IP**——默认配置下直接暴露，将导致任意调用推理 API 占用 GPU、上传恶意文件、下载 `outputs/` 内容。应用自带请求限流（默认 30 次/分钟）；如需局域网共享，请自行评估风险并加反向代理 + HTTPS。

**贡献**：欢迎 Issue 与 PR。提交请遵循 [Conventional Commits](https://www.conventionalcommits.org/) + DCO 签名（`git commit -s`），详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

**合规说明**：上游模型与代码为 Apache-2.0（cswry/VOSR，CVPR 2026）；Qwen-Image VAE 遵循 Wan2.1 / Qwen-Image 条款；DINOv2 遵循其仓库条款；第三方组件完整许可见 [NOTICE](NOTICE)。模型权重仅从官方来源（HuggingFace `CSWRY/VOSR` / ModelScope `LULALULALU/VOSR_CKPT`）获取。本项目是独立的第三方社区工具，与 VOSR 论文作者所在机构（香港理工大学 / OPPO 研究院）无隶属、赞助或官方合作关系。

### 许可证

本项目采用 [Apache License 2.0](LICENSE)。Copyright 2026 ReSerendipity。

---

## English

### Overview

VOSR2-Lite is a standalone image super-resolution toolkit built on [VOSR2](https://github.com/cswry/VOSR) (CVPR 2026), a diffusion-based restoration model. What originally requires a ComfyUI graph is packaged as a local web app bound to `127.0.0.1`: drop in an image, tune a few knobs, and get an ×1–×4 upscale directly in the browser.

- **No ComfyUI required**: FastAPI + Jinja2 + HTMX ship a complete workbench.
- **Local inference**: data never leaves your machine; the web server listens on `127.0.0.1` only.
- **VRAM-friendly**: two-level tiling (DiT + VAE), fp16 tuned for consumer GPUs (8 GB for 1.4B, 4 GB for 0.5B).
- **Multiple checkpoints**: VOSR2 (one-step, default) / VOSR-1.4B (distilled one-step / 25-step multi-step) / VOSR-0.5B (low-VRAM fallback).

### Highlights

- **One-step generative SR**: flow-matching single-step sampling on distilled checkpoints, ×1–×4 upscale.
- **Side-by-side viewer**: drag the divider to compare before/after; scroll to zoom, pan, 1:1 pixels, and a loupe that follows the cursor on both sides.
- **Batch queue**: multi-select or drag in many images; they run serially with per-file status and per-item compare.
- **Live progress**: SSE pushes every stage (load / encode / feature / sample / decode / align) plus elapsed and ETA.
- **History**: SQLite-persisted tasks with thumbnails — paginate, compare, download, delete.
- **Workflow details**: local parameter presets, Ctrl+V paste upload, output-size & ETA estimate, PNG/JPEG/WebP download, one-click retry on failure, keyboard shortcuts (0/1/±/M/C).
- **Bilingual UI**: Simplified Chinese / English; theme follows the system and can be overridden.
- **Security baseline**: loopback-only bind, CSP / security headers, upload magic-byte check, rate limit, path allow-list.

### Screenshots

> The shots below were taken from the local web UI in development mode without model weights. The before/after pair in the viewer is placeholder material used to demonstrate the UI — it is not a real inference result.

**Home**: overview, GPU / model status, recent-tasks entry.

![Home](https://gcore.jsdelivr.net/gh/ReSerendipity/VOSR2-Lite@main/docs/assets/readme-vosr2-lite-1-home-20261026.png)

**Before/After viewer**: drag the divider; loupe, zoom, and 1:1 supported.

![Compare viewer](https://gcore.jsdelivr.net/gh/ReSerendipity/VOSR2-Lite@main/docs/assets/readme-vosr2-lite-2-compare.png)

**Inference parameters**: checkpoint picker, upscale factor, steps, color alignment, seed, DiT / VAE tile size, and local presets.

![Parameters](https://gcore.jsdelivr.net/gh/ReSerendipity/VOSR2-Lite@main/docs/assets/readme-vosr2-lite-3-params-20261026.png)

**Batch queue**: multi-select or drag in images; they queue serially with thumbnails and status.

![Batch queue](https://gcore.jsdelivr.net/gh/ReSerendipity/VOSR2-Lite@main/docs/assets/readme-vosr2-lite-4-batch.png)

**History**: SQLite-persisted task list with thumbnails; re-compare or download in one click.

![History](https://gcore.jsdelivr.net/gh/ReSerendipity/VOSR2-Lite@main/docs/assets/readme-vosr2-lite-5-history-20261026.png)

**Architecture**: from browser templates through FastAPI routes / middleware / services down to the lazily-loaded VOSR engine and local storage.

![Architecture](https://gcore.jsdelivr.net/gh/ReSerendipity/VOSR2-Lite@main/docs/assets/readme-vosr2-lite-architecture.svg)

### Quick start

~10 minutes. Windows is the primary target; Linux works too.

1. **Install Python 3.12+**: <https://www.python.org/downloads/> (on Windows, tick "Add python.exe to PATH").
2. **Get the code**:
   ```bash
   git clone https://github.com/ReSerendipity/VOSR2-Lite.git
   cd VOSR2-Lite
   ```
3. **Install dependencies**: on Windows, double-click `install.bat`; or create a venv manually:
   ```bash
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```
   > torch needs the CUDA build (≥2.7, cu128; required for RTX 50-series sm_120 — cu121 will not work):
   > `pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128`
4. **Download weights** (~6.6 GB, weights only; the DINOv2 architecture is vendored locally, no upstream clone needed):
   ```bash
   .venv\Scripts\python scripts\download_model.py
   ```
   On slow connections from mainland China, set `set HF_ENDPOINT=https://hf-mirror.com` (the script already defaults to this mirror).
5. **Launch**: double-click `start.bat`; the browser opens <http://127.0.0.1:7871> automatically.
6. **Smoke check**: open the workbench, upload a PNG / JPEG, keep defaults, hit start; the system-status page shows GPU and weight integrity.

### Requirements & compatibility

| Item | Requirement |
|---|---|
| OS | Windows (recommended) / Linux |
| GPU | NVIDIA CUDA GPU (mandatory; ≥8 GB for 1.4B; CPU inference is not supported) |
| Python | 3.12+ |
| torch | ≥ 2.7 (cu128; required for RTX 50-series sm_120) |

#### Checkpoints

| Checkpoint | Type | Architecture | Min VRAM | Size | Notes |
|---|---|---|---|---|---|
| `VOSR2` (default) | one-step | 1.4B DiT + Qwen-Image 2D VAE + DINOv2-L | 8 GB | ~6.6 GB | Best detail, default |
| `VOSR_1.4B_os` | one-step distilled | same | 8 GB | ~6.6 GB | VOSR 1.0 distilled |
| `VOSR_1.4B_ms` | multi-step (25) | same | 8 GB | ~6.6 GB | CFG tunable, fidelity-first |
| `VOSR_0.5B_os` | one-step distilled | 0.5B DiT + SD2.1 VAE + DINOv2-B | 4 GB | ~2.5 GB | Low-VRAM fallback |

Required companion files: the 1.4B family uses `Qwen-Image-vae-2d/` (2D VAE) plus
`torch_cache/checkpoints/dinov2_vitl14_pretrain.pth`; the 0.5B family uses
`stable-diffusion-2-1-base/vae/` plus `torch_cache/checkpoints/dinov2_vitb14_pretrain.pth`.
The DINOv2 architecture is vendored locally — no upstream clone needed.

> Multi-step checkpoints (`*_ms`): 25 steps recommended, output long edge ≥384 px (training resolution 512; below ~256 px the engine refuses up front to avoid divergence). The 0.5B family uses the upstream lightweight decoder `sd21_lwdecoder.pth`; see `model/README.md`.

### Examples

- **Single photo**: drag in an old photo → ×4 → one-step distilled → start; drag the divider to inspect detail.
- **Batch of photos**: multi-select 20 JPEGs in the file picker → they queue serially → revisit and download from history.
- **Faithful restoration**: switch to `VOSR_1.4B_ms` (25 steps) for text / document-heavy images.
- **Low-VRAM machines**: switch to `VOSR_0.5B_os` (4 GB VRAM).
- **CLI smoke test** (bypasses the web UI, validates model + inference path):
  ```bash
  .venv\Scripts\python scripts\smoke_infer.py -i test.png -o outputs/ -u 2
  ```

### FAQ

| Problem | Fix |
|---|---|
| Weights not found | Run `python scripts/download_model.py`; weights must live under `model/` in the upstream directory layout |
| Out-of-memory (OOM) | Leave tiling on "auto": the engine picks DiT / VAE tiles from output size and free VRAM (3096² verified). Still OOM? drop VAE tile to 512 or switch to `VOSR_0.5B_os` |
| Output rejected as too large | Hard cap is 6144 px per side (`inference.max_output_side` in `config.yaml`); the UI tells you the max ratio for that input — lower the ratio or crop |
| `install.bat` fails to install PyTorch | Run `pip install torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu128`; if your pip config has a mirror extra-index, the CPU wheel can shadow the CUDA one — set `PIP_CONFIG_FILE=NUL` |
| Port already in use | The app auto-bumps to the next free port; use the address printed in the log |
| Slow HuggingFace download | `set HF_ENDPOINT=https://hf-mirror.com` and rerun the download script |

> VOSR2 is a **generative** model: outputs may contain "plausible" details not supported by the input; small text in signage / UI / documents may be "rewritten". Evaluate readability risk yourself. See [docs/USER_AGREEMENT.md](docs/USER_AGREEMENT.md).

### Docs & governance

- [docs/release-governance.md](docs/release-governance.md): release / rollback governance (version-bump sync, asset manifest + SHA-256, draft human review, rollback procedure).
- [docs/USER_AGREEMENT.md](docs/USER_AGREEMENT.md): usage terms.
- [docs/agent-contract.md](docs/agent-contract.md): automation-agent collaboration contract.
- Official releases are cut on GitHub Releases: <https://github.com/ReSerendipity/VOSR2-Lite/releases/latest>. Release assets contain source + manifest only (`VOSR2-Lite-v<X.Y.Z>.zip` + `SHA256SUMS.txt` + CycloneDX SBOM); **weights are not bundled** — fetch them with `python scripts/download_model.py`.

### Security, contributing, support

**Network binding warning**: the web UI binds to `127.0.0.1` only by default (`server.host` in `config.yaml`). **Do not change this to `0.0.0.0` or a public IP** — doing so lets anyone on the network burn your GPU via the inference API, upload files, or download `outputs/`. The app already rate-limits (30 req/min by default); if you really need LAN access, put it behind a reverse proxy with HTTPS and evaluate the risk yourself.

**Contributing**: Issues and PRs are welcome. Follow [Conventional Commits](https://www.conventionalcommits.org/) and sign off with DCO (`git commit -s`). See [CONTRIBUTING.md](CONTRIBUTING.md).

**Compliance**: upstream model and code are Apache-2.0 (cswry/VOSR, CVPR 2026); the Qwen-Image VAE is under the Wan2.1 / Qwen-Image terms; DINOv2 follows its own license; third-party attributions are in [NOTICE](NOTICE). Weights are fetched only from official sources (HuggingFace `CSWRY/VOSR` / ModelScope `LULALULALU/VOSR_CKPT`). This is an independent community tool with no affiliation to the VOSR authors' institutions (The Hong Kong Polytechnic University / OPPO Research Institute).

### License

Licensed under the [Apache License 2.0](LICENSE). Copyright 2026 ReSerendipity.
