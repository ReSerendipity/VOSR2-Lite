# VOSR2-Lite

> 治理规范：[ReSerendipity/.github/governance](https://github.com/ReSerendipity/.github/tree/main/governance)

![Version](https://img.shields.io/badge/version-0.1.0-blue?style=for-the-badge) ![License](https://img.shields.io/badge/license-Apache%202.0-green?style=for-the-badge) ![Python](https://img.shields.io/badge/python-3.12+-yellow?style=for-the-badge&logo=python&logoColor=white) ![GPU](https://img.shields.io/badge/GPU-NVIDIA%20CUDA-76B900?style=for-the-badge&logo=nvidia&logoColor=white) ![Model](https://img.shields.io/badge/model-VOSR2%201.4B-ff69b4?style=for-the-badge)

**基于 VOSR2 扩散模型的独立图像超分辨率工具箱 — 无需 ComfyUI，一键修复**

> VOSR2-Lite: a standalone image super-resolution toolkit powered by the VOSR2 (CVPR 2026) diffusion model. One-click restoration via Web UI — no ComfyUI required.

## 它能做什么

- **一步生成式超分**：基于 CVPR 2026 开源模型 VOSR2（1.4B LightningDiT，vision-only），×1~×4 放大
- **本地推理**：数据不出机器，FastAPI + Jinja2 + HTMX 提供完整 Web UI
- **显存友好**：DiT / VAE 两级分块（Gaussian 混合），fp16 适配消费级显卡（12GB 可跑）
- **多检查点**：VOSR2（一步，默认）/ VOSR-1.4B / VOSR-0.5B（一步蒸馏 / 多步）
- **前后对比查看器**：拖动分割线逐处比对原图与修复结果；滚轮缩放、拖拽平移、
  1:1 原生像素、跟随光标的放大镜（分割线两侧同时可见）
- **批量处理**：一次多选或拖入多张图片，按队列串行处理，逐张显示状态并可单独打开对比
- **效率细节**：参数预设（本地保存）、Ctrl+V 粘贴上传、输出尺寸与耗时预估、
  下载可选 PNG / JPEG / WebP、失败卡一键重试、最近任务缩略图、快捷键（0/1/±/M/C）
- **实时进度**：SSE 推送加载与推理各阶段进度 + 已用时/剩余时间；SQLite 历史记录带缩略图
- **多语言界面**：简体中文 / English；主题跟随系统，可手动切换

## 快速上手（约 10 分钟）

1. **安装 Python 3.12+**：<https://www.python.org/downloads/>，勾选 "Add python.exe to PATH"
2. **获取代码**：`git clone https://github.com/ReSerendipity/VOSR2-Lite.git`
3. **安装依赖**：Windows 双击 `install.bat`；或手动：
   ```bash
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```
   > torch 需 CUDA 版（≥2.7，cu128；RTX 50 系 sm_120 必须，cu121 不支持）：
   > `pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128`
4. **下载模型**（约 6.6 GB，仅权重；DINOv2 架构已本地 vendored 无需 clone 仓库）：
   ```bash
   .venv\Scripts\python scripts/download_model.py
   ```
   大陆网络慢时设置 `set HF_ENDPOINT=https://hf-mirror.com`（默认即走该镜像）
5. **启动**：双击 `start.bat`，浏览器自动打开 <http://127.0.0.1:7871>
6. **开始修复**：进入「修复工作台」上传图片 → 调整参数 → 开始修复；「系统状态」页查看 GPU 与权重完整性

## 发布与安装包

- **官方发布**：通过 GitHub Release 分发（`distribution=github-release`），最新版见
  <https://github.com/ReSerendipity/VOSR2-Lite/releases/latest>；发布 / 回滚治理规范
  （版本位同步、资产清单与 SHA-256 校验、draft 人工审核、回滚方式）见
  [docs/release-governance.md](docs/release-governance.md)。
- **发布包只含源代码与清单**（`VOSR2-Lite-v<X.Y.Z>.zip` + `SHA256SUMS.txt` + CycloneDX SBOM）；
  **模型权重不随包分发**，请用 `python scripts/download_model.py` 获取（见上文「快速上手」第 4 步）。
- 下载后先校验完整性：`sha256sum -c SHA256SUMS.txt`，再按「快速上手」安装依赖并启动。

## 模型选型

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

> 0.5B 系：上游的轻量解码器 `sd21_lwdecoder.pth` 结构未公开，本仓回退到
> SD2.1 VAE 自带解码器（数值一致、速度较慢），详见 `model/README.md`。

> VOSR2 是**生成式**模型：输出可能包含输入不支持的"合理"细节；文字密集图像
> （路牌 / UI / 文档）的小字可能被"重写"，请自行评估可读性风险。
> 详见 [docs/USER_AGREEMENT.md](docs/USER_AGREEMENT.md)。

## 环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows（推荐）/ Linux |
| GPU | NVIDIA CUDA GPU（必须，≥8GB 显存跑 1.4B；不支持 CPU 推理） |
| Python | 3.12+ |
| torch | ≥ 2.7（cu128；RTX 50 系 sm_120 必须） |

### 常见问题

| 问题 | 解决 |
|---|---|
| 模型文件未找到 | 运行 `python scripts/download_model.py`；权重须在 `model/` 根下并按上游目录结构摆放 |
| 显存不足（OOM） | 分块保持「自动」：引擎按输出尺寸与显存自动开启 DiT/VAE 分块（3096² 输出实测可跑）；仍 OOM 可手动把 VAE 分块调 512、或换 `VOSR_0.5B_os` |
| 输出过大被拒 | 输出边长上限 6144px（`config.yaml` `inference.max_output_side`）；超限会提示本图最高倍率，降低倍率或裁剪后重试 |
| `install.bat` 装 PyTorch 失败 | 手动执行 `pip install torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu128`；注意本机 pip 若配了镜像 extra-index，CPU 版会顶包，需加 `PIP_CONFIG_FILE=NUL` |
| 端口被占用 | 应用自动顺延到下一个可用端口，以日志打印地址为准 |
| HuggingFace 下载慢 | `set HF_ENDPOINT=https://hf-mirror.com` 后重跑下载脚本 |

## 技术特点

| 特性 | 说明 |
|---|---|
| 一步扩散修复 | flow matching 单步采样（蒸馏模型），高效完成图像超分 |
| 独立运行 | 脱离 ComfyUI，FastAPI + Jinja2 + HTMX |
| DiT 架构 | LightningDiT（MM-DiT + Window Attention + RoPE + SwiGLU） |
| 视觉条件 | DINOv2-L 多层特征（无文本 prompt，vision-only） |
| Qwen-Image 2D VAE | 16 通道潜空间，官方 3D 因果 VAE 的 2D 变体 |
| 两级分块 | latent 空间 DiT 分块 + VAE 分块，Gaussian 权重混合无接缝 |
| 颜色对齐 | wavelet（默认）/ adain / nofix |
| 实时进度 | 加载 / 编码 / 特征 / 采样 / 解码 / 对齐全阶段 SSE 推送 |
| 安全 | 仅绑 127.0.0.1、Basic 安全响应头 + CSP、上传魔数校验、限流、PathGuard |

## 项目结构（概览）

```
VOSR2-Lite/
├── app/integrated_app/     # 核心应用（FastAPI、引擎、路由、模板、static、locales）
│   ├── engines/            # vosr2_engine.py：VOSR2 推理引擎（封装上游代码）
│   ├── routes/             # restore（上传/任务/SSE/媒体）与 system（health/history/locale）
│   ├── middleware/         # 安全头 / 限流 / 请求ID / 错误处理
│   └── templates/ static/  # Jinja2 模板；static/js 下 viewer.js（对比查看器）+ restore.js（工作台）
├── vosr/                   # 上游 cswry/VOSR（Apache-2.0）vendor 代码，改动记录见 UPSTREAM.md
├── model/                  # 模型权重目录（gitignore，用脚本下载）
├── scripts/                # download_model.py / smoke_infer.py
├── tests/                  # pytest（应用冒烟 + 媒体端点 + 引擎单元）
└── docs/                   # agent-contract / USER_AGREEMENT
```

### 主要 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/restore/` | 上传图片并创建任务（返回 `task_id`） |
| GET | `/api/restore/{id}/progress` | SSE 实时进度（阶段 / 百分比 / 错误） |
| POST | `/api/restore/{id}/cancel` | 取消排队或运行中的任务 |
| GET | `/api/restore/{id}/result` | 任务结果元信息 |
| GET | `/api/restore/{id}/input` | 原图（对比查看器左侧） |
| GET | `/api/restore/{id}/preview?kind=input\|output&max=1600` | 缩放预览（磁盘缓存） |
| GET | `/api/restore/{id}/download?format=png\|jpeg\|webp&quality=92` | 下载结果（可即时转格式） |
| GET | `/api/system/history` | 分页历史（含 `task_id`、耗时、大小） |
| GET | `/api/system/history/{record_id}/thumb` | 历史缩略图 |

## 开发

```bash
.venv\Scripts\pip install pytest ruff
.venv\Scripts\python -m pytest tests/ -v
.venv\Scripts\python -m ruff check .

# CLI 冒烟（不经 Web 服务，验证模型与推理链路）
.venv\Scripts\python scripts/smoke_infer.py -i test.png -o outputs/ -u 2
```

## 安全与合规

**网络绑定警告**：Web UI 默认仅绑定 `127.0.0.1`（`config.yaml` 的 `server.host`）。
**严禁改为 `0.0.0.0` 或公网 IP**——默认配置下直接暴露，将导致任意调用推理 API
占用 GPU、上传恶意文件、下载 outputs/ 内容。应用自带请求限流（默认 30 次/分钟）；
如需局域网共享，请自行评估风险并加反向代理 + HTTPS。

**合规说明**：使用前请阅读 [docs/USER_AGREEMENT.md](docs/USER_AGREEMENT.md)。
上游模型与代码为 Apache-2.0（cswry/VOSR，CVPR 2026）；Qwen-Image VAE 遵循
Wan2.1 / Qwen-Image 条款；DINOv2 遵循其仓库条款；第三方组件完整许可见
[NOTICE](NOTICE)。模型权重仅从官方来源（HuggingFace `CSWRY/VOSR` /
ModelScope `LULALULALU/VOSR_CKPT`）获取。

**第三方声明**：本项目是独立的第三方社区工具，与 VOSR 论文作者所在机构
（香港理工大学 / OPPO 研究院）无隶属、赞助或官方合作关系。

## 技术栈

| 层级 | 技术 |
|---|---|
| 推理框架 | PyTorch (CUDA)、LightningDiT、Qwen-Image 2D VAE、DINOv2 |
| 后端 | Python 3.12、FastAPI、Uvicorn |
| 前端 | Jinja2 模板、HTMX、Bootstrap 5、原生 CSS/JS |
| 数据 | SQLite（历史记录 + 任务状态）、SSE 实时推送 |
| 工具链 | Ruff、Pytest |

## 贡献

遵循 [Conventional Commits](https://www.conventionalcommits.org/) + DCO 签名
（`git commit -s`）。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

本项目采用 [Apache License 2.0](LICENSE)。版权所有 Copyright 2026 ReSerendipity。
