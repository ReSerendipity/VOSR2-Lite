# VOSR2-Lite 文档索引

> 治理规范：[ReSerendipity/.github/governance](https://github.com/ReSerendipity/.github/tree/main/governance)

本目录是 VOSR2-Lite 的入库文档区（`docs/` 纯入库语义，见治理规范 §13.1）。
对外入口见根 [README.md](../README.md)；本地维护者说明在 `local/README.internal.md`（本地文档，未随仓库发布）。

## 使用文档

| 文档 | 内容 |
|---|---|
| [用户手册.md](用户手册.md) | 面向使用者的完整手册：参数怎么选、查看器操作、安全开关、故障排查、命令行工具 |
| [USER_AGREEMENT.md](USER_AGREEMENT.md) | 使用合规声明：生成式超分的固有局限与使用边界 |

## 发布与治理

| 文档 | 内容 |
|---|---|
| [release-governance.md](release-governance.md) | 发布 / 回滚治理：版本位同步、资产清单与 SHA-256 校验、draft 人工审核、回滚方式与演练节奏 |
| [ROLLBACK_DRILL_2026-10-31.md](../_archive/执行记录/2026-11/ROLLBACK_DRILL_2026-10-31.md) | 回滚演练记录（2026-10-31，资产回退 + 代码 revert 双演练，含缺口登记） |
| [agent-contract.md](agent-contract.md) | 自动化 agent 协作契约（与 governance 模板逐字节一致） |
| [tech-debt.md](tech-debt.md) | 技术债登记清单：债项 ID/级别/证据/处置方向/偿还状态（2026-11-05 整改建立） |

## 架构决策（ADR）

| 文档 | 状态 | 内容 |
|---|---|---|
| [adr/0004-tauri-desktop-shell.md](adr/0004-tauri-desktop-shell.md) | Accepted（决策 4 已被 ADR-0005 取代） | 对外分发形态采用 Tauri v2 桌面壳 |
| [adr/0005-slim-installer-with-in-app-downloads.md](adr/0005-slim-installer-with-in-app-downloads.md) | Accepted | 安装包瘦身为不含环境与模型，改由程序内下载管理页按需获取 |

## 分发形态（定案中）

| 文档 | 状态 | 内容 |
|---|---|---|
| [发布与分发-单文件安装包.md](发布与分发-单文件安装包.md) | 2026-10-31 定案 / WIP | 单文件安装包 + 环境/模型自助下载（含 runtime-v1 环境包独立发布与模型分发口径） |
