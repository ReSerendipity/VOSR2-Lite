# 技术债登记清单

> 回应 2026-11-05 评估报告 P1「技术债隐形化（TODO=0 且无债项清单，跨四轮未除）」与门禁 E2。
> 2026-11-05 夜整改轮建立。已知限制从「散落各处」收拢成表：**知道自己欠了什么债、欠在哪个文件、打算怎么还。**

## 维护规则

- 新债项：发现即登记（一行不嫌少），给 ID、级别、证据（路径:行号）、处置方向。
- 还债：状态改「已偿还」并写明 commit sha 与偿还方式；**不删行**（留偿还轨迹）。
- 级别：P1（应尽快还）/ P2（排期还）/ P3（长期观察）。
- 复评轮次后同步一次本表（与评估报告 Top Findings 对账，防漏登）。

## 债项

| ID | 级别 | 主题 | 证据（2026-11-05 实测） | 影响 | 处置方向 | 状态 |
|---|---|---|---|---|---|---|
| TD-001 | P1 | 权重无逐文件哈希校验 | `scripts/download_model.py` 旧版 ：49-51 自注「官方未逐文件公布 sha256，只登记文件名与来源」，`_fetch` 跳过已存在文件前无任何校验；docstring 与实现不符 | 损坏/被改动的权重可静默进入推理 | 建 `model/WEIGHTS_SHA256.json` 清单 + 下载侧校验/首下钉入 + 引擎加载时强校验 | **已偿还**（2026-11-05 整改提交：`app/integrated_app/weights_manifest.py` + `model_manager._verify_weights` + 下载侧 verify/pin） |
| TD-002 | P1 | ADR 断号悬空引用 | `docs/adr/0004-tauri-desktop-shell.md:6` 引用 ADR-0001（不存在；本仓 ADR 仅 0004/0005） | 决策链不可回溯 | 改自包含引用 | **已偿还**（同上提交：：6 改写为自包含表述） |
| TD-003 | P1 | 依赖漏洞无主动扫描（仅 dependabot 被动升级）；无 pip 锁文件 | `.github/workflows/ci.yml` 无 pip-audit/trivy/bandit/semgrep（全文复核 0 命中）；`requirements.txt` 除 torch 家族外均为 `>=` 范围版本 | 已知漏洞依赖可能长期滞留 | CI 加 pip-audit 门禁 + 锁文件 | **部分偿还**（同上提交新增 `.github/workflows/dependency-audit.yml`（pip-audit，周更兜底）；`requirements-lock.txt` 由在途工作流生成中，未在本轮提交范围） |
| TD-004 | P1 | 长任务中断不续跑、崩溃后任务永停 processing | `task_queue.py` 纯内存队列；`grep -rniE "stale\|orphan\|resume" task_queue.py app_server.py` = 0；`max_timeout_seconds: 86400` | 僵尸「处理中」任务；长任务成果丢失 | 启动时孤儿对账：processing 标 failed(中断)、pending 输入在则重入队续跑 | **已偿还**（同上提交：`app/integrated_app/orphan_recovery.py` + lifespan 接线；引擎级断点续跑（中间态检查点）不做，见该模块 docstring 说明） |
| TD-005 | P1 | 模型质量零评测（无评估集/指标/基线，跨四轮） | `.benchmarks/` 不存在；全仓 PSNR/SSIM/LPIPS 0 命中 | 画质回归只能靠肉眼，无法回答「这版比上版好多少」 | 冻结评测集 + 可复算指标 + 基线；后续挂 gpu-smoke 门禁 | **部分偿还**（同上提交：`evalset/`（16 张冻结集 + baseline.json 实测）+ `scripts/eval_quality.py`/`build_eval_set.py`/`run_engine_eval.py`；**余留**：合成图集换真实图集、评测挂 CI 门禁——均待属主裁决，见 evalset/README 已知局限） |
| TD-006 | P2 | 视频预留常量无实现无测试 | `app/integrated_app/routes/restore/common.py:18` `ALLOWED_VIDEO_EXTENSIONS` 定义后无使用点；`history_db.HistoryRecord.task_type` 注释「video（预留）」 | 死代码误导读者以为支持视频 | 实现前删除，或注明预留状态与触发条件 | 未偿还 |
| TD-007 | P2 | Windows 下密钥文件权限只能尽力而为 | `app/integrated_app/security/secret_key.py:11`（「Windows 上 chmod 只影响只读位，属已知限制」）与 ：78-83 | 本机其他低权进程可能读取密钥文件 | 接受（单用户桌面形态）或改 DPAPI 保护 | 未偿还（平台限制，接受现状即可，属主知情） |
| TD-008 | P2 | 在线安装 81 行新代码零测试引用 | `app/integrated_app/download_service.py:168-256` `_install_runtime_online`（subprocess pip 调用/取消/退出码处理）；`grep download_service tests/` = 0 | 在线安装失败路径（退出码非 0、取消、镜像顶包）无自动化验证 | mock pip 子进程补用例并上调 coverage 棘轮 | 未偿还 |
| TD-009 | P2 | CHANGELOG 断档 + [Unreleased] 口径过期 | `git log 65f4364..HEAD -- CHANGELOG.md` 零输出（11 笔 0 条目）；`CHANGELOG.md:24` 仍写离线 `pip install --no-index`，而 `app/downloads_catalog.json` `runtime.install_mode = "online"` | 「什么时候改了什么」对账链打折 | 随下个版本补 [Unreleased] 条目（含在线安装切换） | 未偿还 |
| TD-010 | P2 | downloads_catalog 字段自相矛盾 | `app/downloads_catalog.json` runtime 节：`volumes: []`（在线模式无分卷）与 `size_bytes: 2907703182`、`base_url`（仍指 modelscope runtime 仓）并存 | 消费方读到互相矛盾的分卷元数据 | online 模式定稿后清理离线残留字段 | 未偿还 |
| TD-011 | P2 | 主分发物 Setup.exe 本地构建：无 CI 构建、无 provenance、无代码签名 | `scripts/installer/*.exe` 为 `.gitignore` 排除的本地产物；`release.yml` 不构建 Setup；`docs/发布与分发-单文件安装包.md` §一 把校验物写成「建议附带」 | 主分发物投毒无法溯源 | 打包进 CI 出 SHA256+provenance；代码签名需证书采购 | 未偿还（**需属主裁决**：证书采购属外部依赖，不在整改单范围） |
| TD-012 | P2 | 安装器级回滚路径未演练 | `docs/ROLLBACK_DRILL_2026-10-31.md` §5「缺口与未覆盖项」（:128 起）自登记 | 下次回滚若走安装器路径，预案未经验证 | 2027-01 季度演练纳入 Setup 重装回滚 | 未偿还（已排期：随季度演练节奏收口） |
| TD-013 | P2 | DINOv2 权重许可未具名 | `NOTICE`「DINOv2 仓库条款（含附加模型许可，见其 LICENSE 文件）」未给出具体许可名 | 再分发或许可变化时义务边界不清 | 具名模型许可并写明「权重不随包分发」的义务边界 | 未偿还（**需属主裁决**：许可证判定） |
| TD-014 | P3 | 无 E2E/UI 测试层 | 无 Playwright/浏览器测试；desktop 壳窗口交互无 UI 自动化 | 前端回归靠人肉 | 「上传→推理(mock)→对比→下载」主旅程 Playwright 化 | 未偿还（长期建设） |

## 与评估报告的对账说明

本表覆盖 2026-11-05 评估报告 Top Findings #1（TD-005）、#2（TD-004）、#3（TD-011）、
#4（TD-001 与 DINOv2 侧 TD-013）、#5（TD-003）、#6（TD-002）、#7（TD-008）、#8（TD-009）；
#9（演练覆盖缺口）= TD-012，#10（Bus Factor）非债项、属人员风险，已由
ARCHITECTURE.md / SECURITY.md / CODEOWNERS 等文档化缓解，不在此表重复登记。
