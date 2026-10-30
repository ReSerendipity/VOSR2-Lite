# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **桌面版（Tauri v2）**：新增 `desktop/` 原生壳（窗口 / 托盘 / 通知 / 单实例 / 崩溃自启 / 增量更新），
  `scripts/start_portable.py` 便携启动器，以及 `POST /api/system/shutdown` 优雅关闭端点
  （仅回环，走 uvicorn `should_exit` 触发 lifespan 清理）。壳版本 `1.0.0` 独立于应用版本，
  最低兼容壳版本由 `version.json` 的 `minimum_shell_version` 声明。
- `scripts/installer/setup.nsi`：NSIS 安装器（数据分卷解压 / 杀进程树 / 卸载重试），
  与 1900MB 分卷产物分离，换壳无需重打数据卷。
- `scripts/check_shell_version.py` + `.github/workflows/desktop-shell.yml`：壳版本三处镜像、
  权限镜像完整性、`cargo clippy -D warnings` 与 `cargo test` 门禁。

### Changed

- `app_server.main()` 改为持有 `uvicorn.Server` 句柄（退出走正常 shutdown 流程），
  新增 `serve()` 与 `request_shutdown()`；`--port/--host` 由便携启动器传入。

## [0.2.1] - 2026-10-28

### Fixed

- **CI 类型门禁在 v0.2.0 提交上失败**：`scripts/ci_import_smoke.py` 用
  `client.request(method, path, **kwargs)` 拼装请求参数，`kwargs` 被推断为
  `dict[str, dict[str, str]]`，在 CI 的依赖版本下触发 4 条 `arg-type`。改为显式分支
  （仅语言切换端点传 `json=`），与 httpx/starlette 版本无关。
  > v0.2.0 的 tag 受 `tag-protection-v` 规则保护不可移动，故以 0.2.1 修复前进；
  > v0.2.0 运行时功能与此版一致，差异仅在该 CI 辅助脚本。

## [0.2.0] - 2026-10-28

本轮为「向家族仓 SeedVR2-Lite 吸收工程能力」的成果，并反向补齐了若干实测缺陷。

### Added

- **可选认证与 CSRF 防护**（默认关闭，`config.yaml` 开关控制）：
  `runtime.security.auth` 提供 HTTP Basic Auth（`hmac.compare_digest` 常量时间比较、
  失败滑动窗口 + 临时 IP 封禁、401 带 `WWW-Authenticate`）；`runtime.security.csrf`
  提供 Double Submit Cookie 防护（HMAC 签名 token、`SameSite=Strict`、坏 cookie 自愈）。
  密码可用 `VOSR2_AUTH_PASSWORD` 环境变量注入，避免明文入库。前端 `app.js` 已自动回传
  `X-CSRF-Token`，开启后无需改前端。
- **繁体中文界面**（`locales/zh-TW.json`，167 键全量）：语言切换支持
  简体中文 / 繁體中文 / English。
- **用户手册** `docs/用户手册.md`：参数逐项语义、查看器手势、数据落盘清单、安全开关、
  配置速查、命令行工具与 9 条故障排查。
- **质量门禁**：覆盖率门禁（`fail_under=68`，基线 71%）、`black --check`、`mypy`
  0 容忍（49 文件）、gitleaks 密钥扫描、Dependabot（pip + Actions，7 天冷却期）、
  仓库布局守卫（必需路径 / 本地产物忽略 / 禁入文件入库）。
- **GPU 冒烟工作流** `gpu-smoke.yml`：每次必跑 CPU 导入 + 16 个端点探测；
  存在在线 `gpu` runner 时自动升级为真机推理冒烟（无 runner 时跳过，不排队干等）。
- **测试扩充**：用例函数 36 → 111（pytest 收集 136 → 256），新增分块阈值交叉点、
  颜色对齐数值断言、参数合并、权重发现顺序、API 契约矩阵、任务状态机、SSE 终止条件、
  安全中间件双路径、语言包一致性等。
- **GitHub Release 发布链**（§10 合规）：`.github/workflows/release.yml`——`v*` tag 推送 /
  `workflow_dispatch` 触发；版本一致性门禁（tag == `pyproject.toml` == `version.json`）、
  `git archive` 源码打包、`SHA256SUMS.txt`、CycloneDX SBOM、SLSA build provenance 证明、
  draft Release 创建与缺失资产补传（不自动 publish）。
- **发布 / 回滚治理规范** `docs/release-governance.md`（公共条款：`ReSerendipity/.github` §10）。

### Changed

- **引擎拆分**：`engines/vosr2_engine.py`（869 行 / 37KB）按职责拆为 6 个模块
  （`_core` / `_weights` / `_loaders` / `_tiling` / `_sampling` / `_color`），
  公开 API 与导入路径完全不变。验证方式：246 用例零修改通过 + 逐函数源码比对
  32 项逐字一致 + 真机推理输出 SHA256 完全相同。
- 语言切换的可用列表改为「实际加载到的语言包 ∩ config 白名单」，避免新增语言包后
  接口仍拒绝、或 config 缺 `i18n` 段时退化成硬编码两语言。
- README 中英双版同步三语支持与安全开关说明。

### Fixed

- `locales/en.json` 的 `history.total_prefix` 为空字符串，英文界面会显示成裸数字
  （`12` 而不是 `Total 12`）。
- 11 处类型问题：`Image.LANCZOS` / `Image.BICUBIC` 改用 Pillow 10+ 的
  `Image.Resampling.*`；`lastrowid` / `COUNT(*)` 的 Optional 收窄；
  `_headers_config` 逐层 isinstance 校验（配置写错时不再 TypeError）；
  `download_model` 的文件清单用 `cast` 显式化。

## [0.1.0] - 2026-10-24

### Added

- 初始版本：基于 VOSR2 扩散模型的独立图像超分辨率工具箱（FastAPI + Web UI，无需 ComfyUI）。
- 本地推理：DiT / VAE 两级分块（Gaussian 混合），fp16 适配消费级显卡（12GB 可跑）。
- 多检查点支持：VOSR2（一步，默认）/ VOSR-1.4B / VOSR-0.5B（一步蒸馏 / 多步）。
- Web UI：Jinja2 + HTMX 多语言界面（简体中文 / English）、SSE 实时进度、SQLite 历史记录。
- 模型权重外置：`scripts/download_model.py` 从官方来源获取（不入库、不随发布包分发）。
