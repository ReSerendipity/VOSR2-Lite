# 架构 — VOSR2-Lite

> 治理定位：`delivery=release`、`lifecycle=active`（`governance/desired-state.json`）。
> 本文按 2026-11-03 的实际目录结构生成；数据流与边界须由维护者在实现变更时同步复核。

## 1. 定位

**基于 VOSR2 扩散模型的独立图像超分辨率工具箱 — 无需 ComfyUI，一键本地修复。**

## 2. 组件与依赖方向

```text
应用层：app/ (77)、desktop/ (54)
工具层：scripts/ (11)
资产层：model/ (2)
测试层：tests/ (9)
支撑层：docs/ (14)、.github/ (8)
其他层：vosr/ (12)、data/ (2)、.githooks/ (1)、logs/ (1)、outputs/ (1)
根级层：根级文件 (16)
```

| 组件 | 目录 | 文件数 | 代表文件 |
|---|---|---|---|
| `app` | `app/` | 77 | `app/__init__.py`, `app/downloads_catalog.json`, `app/integrated_app/__init__.py` |
| `desktop` | `desktop/` | 54 | `desktop/README.md`, `desktop/package.json`, `desktop/src-tauri/Cargo.lock` |
| 根级文件 | `/` | 16 | `.editorconfig`, `.gitattributes`, `.gitignore` |
| `docs` | `docs/` | 13 | `_archive/执行记录/2026-11/ROLLBACK_DRILL_2026-10-31.md`, `docs/USER_AGREEMENT.md`, `docs/adr/0004-tauri-desktop-shell.md` |
| `vosr` | `vosr/` | 12 | `vosr/UPSTREAM.md`, `vosr/__init__.py`, `vosr/models/__init__.py` |
| `scripts` | `scripts/` | 11 | `scripts/build_desktop_bundle.ps1`, `scripts/build_runtime_bundle.py`, `scripts/check_layout.py` |
| `tests` | `tests/` | 9 | `tests/conftest.py`, `tests/test_api_extended.py`, `tests/test_app_smoke.py` |
| `data` | `data/` | 2 | `data/checkpoints/.gitkeep`, `data/uploads/.gitkeep` |
| `model` | `model/` | 2 | `model/.gitkeep`, `model/README.md` |
| `.githooks` | `.githooks/` | 1 | `.githooks/commit-msg` |
| `logs` | `logs/` | 1 | `logs/.gitkeep` |
| `outputs` | `outputs/` | 1 | `outputs/.gitkeep` |

依赖方向：入口层（应用 / 页面）调用领域层（业务模块），领域层依赖基础设施层（脚本、配置、模型资产）；反向引用不允许。

## 3. 数据流

图像输入 → 推理修复 → 输出；桌面前端与后端服务分离。

## 4. 外部依赖

| 依赖 | 用途 | 不可用时 |
|---|---|---|
| Python 依赖 | 推理后端 | 无法修复 |
| Node.js / npm | 桌面壳 | 仅剩 Web 端 |

## 5. 边界与非目标

- 发布链只走 §10：远端优先、tag 驱动、资产附 `SHA256SUMS.txt`。
- 分发产物由 CI 生成，不入库。
- 不入库：密钥 / token / 口令 / 连接串、本机绝对路径、`local/` 与 `AGENTS.md`、构建产物。
- 治理正文只在 `ReSerendipity/.github/governance`，本仓不复制规则正文。

## 6. 变更影响面

| 改什么 | 必须同步 | 由谁校验 |
|---|---|---|
| 目录结构 / 入口 | 本文「组件与依赖方向」与「数据流」 | 维护者 |
| CI 与门禁 | 本仓 `README.md` 的测试命令；治理仓 §20 远端载体口径 | `audit-drift.py` |
| 依赖清单 | 锁文件与 `.github/dependabot.yml` 的生态段 | Dependabot / `lock-consistency` |
| README 展示图 | §8.1 真实性红线（图必须来自真实运行） | 治理仓 `readme-image-provenance` |
