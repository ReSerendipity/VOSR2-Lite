# VOSR2-Lite 全仓文档总索引

> 最后复核：2026-11-10 ｜ 维护者：文档治理轮（WO-DOC-S01）
> 治理规范：[ReSerendipity/.github/governance](https://github.com/ReSerendipity/.github/tree/main/governance) §12.1 / §12.5 / §13.1
> 状态口径：现行 = 当前有效；历史 = 决策/发布记录，原文保留；废弃 = 已被取代，仅存归档区。

## 入口文档

| 文档 | 作用 | 状态 |
|---|---|---|
| [README.md](../README.md) | 项目总入口：定位、安装、使用、分发（中英双语） | 现行 |
| [docs/README.md](README.md) | `docs/` 入库文档区索引 | 现行 |
| [docs/INDEX.md](INDEX.md) | 本文件：全仓文档总索引 | 现行 |

## 根目录文档

| 文档 | 作用 | 状态 |
|---|---|---|
| [ARCHITECTURE.md](../ARCHITECTURE.md) | 架构总览：组件分层、数据流、目录清单与文件数 | 现行 |
| [CHANGELOG.md](../CHANGELOG.md) | 发布变更记录（历史发布记录，原文保留） | 历史 |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | 贡献指南：提交规范、本地开发、PR 要求 | 现行 |
| [SECURITY.md](../SECURITY.md) | 安全策略：漏洞报告入口与响应承诺 | 现行 |
| [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) | 行为准则（Contributor Covenant 2.1） | 现行 |

## docs/ 文档

### 使用文档

| 文档 | 作用 | 状态 |
|---|---|---|
| [docs/用户手册.md](用户手册.md) | 面向使用者的完整手册：参数选择、查看器操作、安全开关、故障排查、CLI 工具 | 现行 |
| [docs/USER_AGREEMENT.md](USER_AGREEMENT.md) | 使用合规声明：生成式超分的固有局限与使用边界 | 现行 |
| [docs/e2e-testing.md](e2e-testing.md) | 浏览器级 E2E 测试层：跑法、断言清单、负控记录与刻意未覆盖面 | 现行 |

### 发布与治理

| 文档 | 作用 | 状态 |
|---|---|---|
| [docs/release-governance.md](release-governance.md) | 发布 / 回滚治理：版本位同步、资产清单与 SHA-256 校验、draft 人工审核、回滚方式与演练节奏 | 现行 |
| [docs/agent-contract.md](agent-contract.md) | 自动化 agent 协作契约 v2（与 governance 模板逐字节一致） | 现行 |
| [docs/tech-debt.md](tech-debt.md) | 技术债登记清单：TD-001~TD-014，含级别、证据、处置方向与偿还状态 | 现行 |
| [docs/安装包构建与溯源.md](安装包构建与溯源.md) | 主分发物 Setup.exe 的构建、SHA256SUMS / provenance 强制校验与可复现实测口径（TD-011） | 现行 |
| [docs/发布与分发-单文件安装包.md](发布与分发-单文件安装包.md) | 单文件安装包 + 环境/模型自助下载：环境默认在线安装（分卷仅作离线路径）+ 模型分发口径 | 现行 |

### 架构决策（ADR）

| 文档 | 作用 | 状态 |
|---|---|---|
| [docs/adr/0004-tauri-desktop-shell.md](adr/0004-tauri-desktop-shell.md) | 对外分发形态采用 Tauri v2 桌面壳（决策 4 已被 ADR-0005 取代，其余有效） | 历史 |
| [docs/adr/0005-slim-installer-with-in-app-downloads.md](adr/0005-slim-installer-with-in-app-downloads.md) | 安装包瘦身为不含环境与模型，改由程序内下载管理页按需获取 | 现行 |

## 子目录文档

| 文档 | 作用 | 状态 |
|---|---|---|
| [desktop/README.md](../desktop/README.md) | Tauri v2 桌面壳打包与版本口径：分发模型、构建命令、运行时安装 | 现行 |
| [model/README.md](../model/README.md) | 模型资产说明：权重来源、目录结构、校验方式 | 现行 |
| [evalset/README.md](../evalset/README.md) | 评测集说明：数据来源、使用方式 | 现行 |
| [vosr/UPSTREAM.md](../vosr/UPSTREAM.md) | 上游 cswry/VOSR vendored 代码说明（Apache-2.0 原样保留） | 现行 |

## 归档区

| 文档 | 作用 | 状态 |
|---|---|---|
| [_archive/INDEX.md](../_archive/INDEX.md) | 归档索引：已归档文件清单与未归档判定理由 | 现行 |
| [_archive/执行记录/2026-11/ROLLBACK_DRILL_2026-10-31.md](../_archive/执行记录/2026-11/ROLLBACK_DRILL_2026-10-31.md) | 回滚演练记录（2026-10-31，资产回退 + 代码 revert 双演练） | 历史 |

## 统计

-  tracked `.md` 文件总数：**24**（含本索引）
- 现行：19 ｜ 历史：4 ｜ 废弃：0
- 主要目录：`docs/`（12）、根目录（6）、`_archive/`（2）、`desktop/`（1）、`model/`（1）、`evalset/`（1）、`vosr/`（1）
