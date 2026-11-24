# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.4] - 2026-11-24

### Added

* **desktop**: 补齐桌面自更新第七步——安装完自动重启应用。更新器传 `/S /RELAUNCH` 静默安装并有序退出，NSIS 用 `$CMDLINE` + `StrStr` 检测标志后 `Exec` 拉起主程序，实现「点一下 → 退出 → 自动安装 → 自动回来」闭环；前端 `update-installing` 提示改为自动重启，新增 4 项契约测试与 CI 端到端验收 workflow（runner 内网取包）。应用版本 0.4.3 → 0.4.4；壳 1.0.1 不变。

## [0.4.3] - 2026-11-23

### Changed

* **桌面 CLI 全局命令**：安装器把安装目录加入当前用户 PATH（`AddToUserPath`，幂等 + 广播 WM_WININICHANGE），新开终端可直接敲 `vosr2 --help` / `vosr2 restore ...`；卸载时 `un.RemoveFromUserPath` 精确移除，开始菜单新增「VOSR2 Lite CLI」快捷方式。此前 CLI 只能用全路径调用，与 TTS 的 `tts-multimodel` 体验不一致。应用版本 0.4.2 → 0.4.3；壳 1.0.1 不变。GOTCHAS #52。

## [0.4.2] - 2026-11-22

### Fixed

* **Release workflow**：`scripts/installer/VOSR2Lite-Data.7z.001`（116MB 构建产物）此前误入 Git LFS 但远端对象从未上传成功，导致 v0.4.0/v0.4.1 的 `git archive` 报 LFS smudge 404、Release 无法创建。用 `git rm --cached` 从索引移除（保留本地文件）并加入 `.gitignore`；CI 本就自行重建数据卷。应用版本 0.4.1 → 0.4.2；壳版本 1.0.1 不变。GOTCHAS #50。

## [0.4.1] - 2026-11-22

### Fixed

* **发布门禁**：补 `tests/test_cli_remote.py`（probe / CSRF / 远程修复主路径与错误分支）与 `tests/test_cli_service.py`（参数构造 / models / system / restore / history），把覆盖率从 67.1% 拉回 70.5%，修复 v0.4.0 的 Release workflow 因覆盖率低于 68% 而失败、draft Release 未创建的问题。
* **发布版本号**：应用版本 0.3.2 → 0.4.0 → 0.4.1（v0.4.0 因上述门禁失败已推 tag 不可撤回，按治理发补丁版）；壳版本 1.0.1 不变（本次未改壳代码）。
* GOTCHAS #49：提交前必须跑 `pytest --cov`，不能只跑 `pytest -q`。

## [0.4.0] - 2026-11-22

### Added

* **统一 CLI**：`python -m app.integrated_app.cli` 提供 `restore`（单图/批量，引擎只加载一次）、`models status`、`system status`、`history list/show` 四类命令；Result 信封 + Human/JSON 双输出 + 语义退出码（0/1/2/3），`--json` 任意位置生效，供 AI agent 自动化调用。直调统一走 `ModelManager`（继承权重 sha256 校验与设备配置）；`--server-url` 显式指定时可复用常驻服务。桌面包随附 `vosr2.cmd` 启动器（不注册 PATH）。
* **CLI 契约测试**：`tests/test_cli.py` 覆盖信封/JSON/退出码/命令分派与 stdout 污染回归，CI 无 GPU 可跑。

### Changed

* **更新路线收敛为单一完整安装包**：官网下载与「检查更新」拿同一个 `VOSR2Lite-Setup-v<版本>.exe`；更新器改为下载 → SHA256 校验 → `/S` 启动 NSIS 覆盖安装 → 用户手动重开。删除代码增量产线（`app-v*.zip` + 原子换载/回滚/PRESERVE 整套）、官方 `tauri-plugin-updater` 死配置（pubkey/endpoint/插件注册）与 tracked 死公钥 `tauri.key.pub`。
* **安装器保留用户配置**：`setup.nsi` 覆盖解压前备份 `config.yaml`、解压后还原，避免 CSRF/Basic Auth 等安全开关被数据卷默认配置冲掉（TTS 样板没有这一步）。
* **壳版本 1.0.0 → 1.0.1**（三处镜像同步），应用版本 0.3.2 → 0.4.0；`minimum_shell_version` 保持 1.0.0（老壳仍可覆盖安装）。
* 文档同步：根 README 中英段、`desktop/README.md` 更新契约与 CLI 启动器段、`docs/用户手册.md` §10、ADR-0005。

### Fixed

* **CLI stdout 污染**：vendored `vosr/tiled_vae.py` 直接 print 分块信息混进 `--json` 输出导致 `json.loads` 失败；`begin_json_mode()` 前置隔离 stdout 到 stderr，`emit()` 写回原始流，并加回归测试钉住。
* **DINO 权重下载源**：`download_model.py --source auto`（魔搭优先）拿到的 DINOv2-L 权重与清单 sha256 不符（1.1GB vs 1.217GB）且失败后不换源；实测 hf-mirror 版本与清单一致，改用 `--source hf` 下载校验通过。已写入 GOTCHAS #46。

## [0.3.2] - 2026-11-18

### Fixed

* **发布版本位**：`pyproject.toml` 同步到 0.3.1/0.3.2；`v0.3.1` 因该版本位仍是 0.3.0 导致 `release.yml` 的 tag==pyproject==version.json 门禁失败，改以 `v0.3.2` 重新发布。

## [0.3.1] - 2026-11-18

### Fixed

* **模型就绪判定**：下载管理页不再以目录内任意文件判断“已安装”；改为按 `scripts/download_model.py` 的必需文件清单逐项校验，只有全部存在且非空才算 `present`，半成品返回 `partial` 与 `missing_files`。只有 `args.json` 的目录现在显示“不完整（缺 N 个文件）”，并提供“修复下载”。
* **模型下载崩溃**：`install_model()` 复用 `_fetch()` 时补齐 `WeightsManifest` 参数，修复把 `source` 字符串当成清单对象导致的 `'str' object has no attribute 'verify'`。
* **UI**：半成品状态开放删除入口；新增“不完整”“修复下载”中英繁文案。

## [0.3.0] - 2026-11-18

### Added

* **魔搭公开源迁移**：`download_model.py --source auto` 改为魔搭 `LULALULALU/VOSR_CKPT` 优先、HF 镜像兜底；运行时改为 `ReSerendipity/VOSR2Lite-Runtime`（与 SeedVR2-Lite 共用 `runtime-v1`，Python 3.12.10 + torch 2.10.0+cu128）。
* **品牌视觉**：桌面壳换奶油底橙色主题与品牌图标。（`b121cd7`）

### Fixed

* **下载管理页**：清单补到 `app/downloads_catalog.json` 根路径，修复“未找到环境包清单”；引导态不再误报“运行环境已就位”，按钮改为“安装运行环境”并禁用卸载。
* **bootstrap** 缺 torch 时跳过孤儿任务恢复，不再在无 GPU 环境启动失败。（`f918e42`）
* **依赖更新**：dependabot 补 `/desktop` npm 生态覆盖（Tauri 壳依赖此前不在自动发现范围，见审查 2026-11-18 F-V2）。

## [0.2.2] - 2026-11-16

### Added

- **桌面版（Tauri v2）**：新增 `desktop/` 原生壳（窗口 / 托盘 / 通知 / 单实例 / 崩溃自启 / 增量更新），
  `scripts/start_portable.py` 便携启动器，以及 `POST /api/system/shutdown` 优雅关闭端点
  （仅回环，走 uvicorn `should_exit` 触发 lifespan 清理）。壳版本 `1.0.0` 独立于应用版本，
  最低兼容壳版本由 `version.json` 的 `minimum_shell_version` 声明。
- `scripts/installer/setup.nsi`：NSIS 安装器（数据分卷解压 / 杀进程树 / 卸载重试），
  与 1900MB 分卷产物分离，换壳无需重打数据卷。
- `scripts/check_shell_version.py` + `.github/workflows/desktop-shell.yml`：壳版本三处镜像、
  权限镜像完整性、`cargo clippy -D warnings` 与 `cargo test` 门禁。
- `scripts/portable_bundle_lib.ps1` + `scripts/build_desktop_bundle.ps1`：桌面发布打包脚本。
  数据卷归档根为 `app/`（安装器解到 `$INSTDIR`），增量包归档根为 `app/` 的内容（壳直接解到 `app.new/`）。
- **瘦包分发 + 程序内下载管理**（ADR-0005）：安装包不再含 torch 运行时与模型权重。
  - `app/integrated_app/download_service.py`：环境包/模型安装（**默认在线**：子进程 pip 直连官方
    cuXXX 索引 + PyPI 装 torch 家族，写 `RUNTIME.json`；分卷离线路径保留：逐卷下载 → 逐卷 SHA256 →
    合并 → 解压 → `pip install --no-index --find-links`）、任务管理（后台线程 + 取消）、卸载
    （VAE/torch_cache 属共享资产，只删目标目录）。
  - `app/integrated_app/routes/downloads.py`：状态/清单/启动/进度/取消/卸载 API，
    重操作加回环白名单守卫。**顶层禁止 `import torch`**（引导态下下载页必须打得开）。
  - `/downloads` 页面（与首页/设置同级）+ 导航入口 + 三语文案；进度 1s 轮询。
  - 壳：`resolve_runtime_dir` 增加 `app/bootstrap/` 引导解释器层，新增 `PythonMode`；
    引导态自动把入口切到 `/downloads` 而非首页。
  - 打包脚本 `-Mode slim|runtime|all`：slim 产出瘦包（引导解释器落 `app/bootstrap/`，不含模型），
    runtime 产出 torch cu128 wheels 环境包 + `downloads_catalog.json`。
    实测瘦包载荷 32315 文件 / 1.02 GB → 7z **128.8 MB 单卷**（旧全量数据卷需 8×1900MB 分卷）。
  - 环境包装进 `bootstrap/` 自身 site-packages，不另起一份解释器；壳靠
    后端安装成功后写入的 `bootstrap/RUNTIME.json` 判定已是完整运行时
    （不能只看 `site-packages/torch`：WinPython 自带 CPU 版 torch 会把未装环境误判为已装）。
- **回滚演练记录** `docs/ROLLBACK_DRILL_2026-10-31.md`：按 §5 完成首次真演练——
  v0.2.1 资产下载 / SHA-256 逐件校验 / 解压与版本位断言；发布提交 `c7b7b16` 的
  revert 冲突处置与合成回滚（版本位 0.2.0 断言 + ruff / pytest）；GPU 真实推理
  冒烟通过（18.4 s，收尾显存回落 0 MiB）。`docs/release-governance.md` §5 补演练
  留痕与季度演练节奏。已登记缺口：v0.2.0 无 Release 资产（回滚目标 v0.2.1 / v0.1.0）。
- **关于页社交平台链接卡片**：`templates/settings.html` 新增社交卡片区块，简 / 繁 / 英三语
  文案同步（`locales/zh.json`、`zh-TW.json`、`en.json`）。
- **工程与治理文件**（§12.5 通用工程件）：`ARCHITECTURE.md`（分层与数据流）、
  `.github/CODEOWNERS`、第二层社区健康文件 `CODE_OF_CONDUCT.md` / `SECURITY.md`；
  `docs/README.md` 补文档索引，并同步 ADR-0004 / `release-governance.md` 里的历史引用路径。
- **浏览器级 E2E 测试层**（TD-014）：新增 `tests/e2e/`——真 uvicorn（后台线程、真端口）+ 桩推理引擎
  （`ModelManager.get_engine` 打桩，仅按 `upscale` 放大并回调阶段进度）+ Playwright，覆盖
  「上传 → 推理(mock) → 对比 → 下载」主旅程 3 用例 24 条断言（SSE 中间态、viewer 两图解码后才有的
  `is-ready`、右图 `naturalWidth` == 原图 × upscale、分屏拖拽的 `clipPath`、真实 download 事件的
  PNG/JPEG 魔数与尺寸、伪造魔数被拒后不进推理）。依赖单独放 `requirements-e2e.txt`
  （`playwright==1.64.1`），说明见 `docs/e2e-testing.md`，CI 走 `.github/workflows/e2e.yml`
  （手动 + 每周定时；未装 Playwright 时用例显式 skip 而非静默 0 用例）。
- **桌面壳桥接契约闸**：新增 `tests/test_desktop_bridge_contract.py` 6 用例，机械校验 Tauri v2 的
  `#[tauri::command]` ↔ `generate_handler!` 注册 ↔ `permissions/autogenerated/<cmd>.toml` ↔
  `capabilities/main.json` 放行四向对齐（含「桥接 invoke 字面量必须是已注册命令」与
  「`window.rs` 必须 `include_str!` 并以 `initialization_script` 注入桥接」两条反向约束）。

### Changed

- `app_server.main()` 改为持有 `uvicorn.Server` 句柄（退出走正常 shutdown 流程），
  新增 `serve()` 与 `request_shutdown()`；`--port/--host` 由便携启动器传入。
- **分发形态变更**（ADR-0005）：`VOSR2Lite-Data.7z.001..` 由「含运行时与全量模型」变为
  「仅含应用代码 + 引导解释器」。已装用户如需离线使用，请自行预置 `app/model/` 与
  `app/bootstrap/` 的 torch；新装用户首次启动后需在「下载管理」页安装环境与模型。
- **环境安装默认改「在线拉取」**（`downloads_catalog.json` `runtime.install_mode = "online"`）：
  仓库不再托管 GB 级环境分卷——`volumes` 置空，`size_bytes` 只表示**安装后磁盘占用**估算
  （随该字段新增 `size_bytes_kind: "installed_footprint"` 标注），后端新增
  `download_service._install_runtime_online`：子进程 pip 直连官方索引装 torch 家族，
  按退出码 / 取消分档报错，并用 `PIP_CONFIG_FILE=NUL` 屏蔽用户镜像配置（防止 torch 被顶成 `+cpu`）。
  分卷离线安装路径**未删除**，把 `install_mode` 切回非 online 即生效。

### Fixed

- **CI 红灯（ruff E501 / black）**：修复 `app/integrated_app/download_service.py`、
  `scripts/build_runtime_bundle.py`、`tests/test_download_online_install.py` 的超长行，
  并对 5 个存量文件执行 black（仅格式，不改逻辑）；复用合并 PR #12，
  lint / format / mypy / pytest / 覆盖率门禁全绿。
- **单张修复失败时失败原因不上屏**：`restore.js` 的失败汇聚分支只显示「有任务失败: N」（N 是计数），
  而单文件时队列行是隐藏的，`item.error` 里的真实原因（如「文件内容与扩展名不符，疑似伪造格式」）
  没有任何地方显示。改为单张失败时把 `item.error` 拼进消息，批量仍显示计数。该问题由新增的 E2E
  用例首跑暴露，现已由同一条断言守住（`"不符" in msg`、不 endswith `": 1"`）。

- **安装器解压步骤静默失败**：`setup.nsi` 用 `-DSRC_7ZA` 指定的文件原样嵌入（编译机多为
  `7z.exe`），但 `ExecWait` 里写死调用 `$TOOLSDIR\7za.exe`。文件名对不上 → 进程起不来 →
  `ExecWait` 拿到空错误码，弹窗只显示「数据解压失败（7z 错误码 ）」，安装目录空着。
  改为 `File /oname=7za.exe` 统一嵌入名，编译机用 `7z.exe` 或 `7za.exe` 均可。
  真机验证：静默安装 `D:\VOSR2Lite` 17264 文件 / 538 MB，壳日志 `运行时形态: Bootstrap，
  导航入口: 'downloads'`，`/downloads` 与 `/api/downloads/status` 均 200。
- **P1 五项整改**（对应 2026-11-05 评估报告）：画质回归可复算（`evalset/` 16 张冻结图 +
  `baseline.json` + `scripts/eval_quality.py`）、崩溃后僵尸任务回收（`orphan_recovery.py`
  并对账 lifespan，`processing` 不再永停）、权重逐文件哈希校验（`weights_manifest.py` +
  `model_manager._verify_weights`，下载侧 verify/pin）、依赖漏洞主动扫描
  （`dependency-audit.yml` 跑 pip-audit，周更兜底）、债项清单 `docs/tech-debt.md`（含维护规则）。
- **数据卷缺模型下载脚本**：`scripts/build_desktop_bundle.ps1` 把 `scripts/download_model.py`
  一并打进数据卷，瘦包用户装完环境后可直接按脚本拉权重，不必另行获取。

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
