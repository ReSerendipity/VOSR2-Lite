# VOSR2-Lite 归档索引（_archive/）

> 归档区按治理规范 §12.1 使用仓根 `_archive/`（下划线前缀＝非活动区，§10 审计自动豁免）。
> 归档只移动文件、不改写正文；引用处已同步改为新路径（`CHANGELOG.md` 属历史发布记录，保留原文不改）。

| 原路径 | 归档后路径 | 归档日 | 类别 | 一句话结论 | 是否仍被引用 |
|---|---|---|---|---|---|
| `docs/ROLLBACK_DRILL_2026-10-31.md` | `_archive/执行记录/2026-11/ROLLBACK_DRILL_2026-10-31.md` | 2026-11-08 | 执行记录（回滚演练） | 按 `release-governance` §5 完成首次真演练（资产回退 + 代码 revert 双演练），并自登记安装器级回滚路径未覆盖 | 是：`docs/README.md`、`docs/release-governance.md`、`docs/tech-debt.md`（TD-012 证据，已改新路径）；`ARCHITECTURE.md` 目录清单已更新路径与文件数；`CHANGELOG.md` 保留历史原文（例外） |

## 未归档（活动文档，判定理由）

| 文档 | 保留理由 |
|---|---|
| `docs/安装包构建与溯源.md` | TD-011 的现行构建与校验流程（2026-11-08 落地） |
| `docs/发布与分发-单文件安装包.md` | 现行发布形态规范（单文件安装包 + 自助下载） |
| `docs/e2e-testing.md` | 现行端到端测试口径 |
| `docs/tech-debt.md` | 技术债台账（长期维护） |
| `docs/用户手册.md`、`docs/USER_AGREEMENT.md` | 面向用户的手册与协议，长期有效 |
| `docs/agent-contract.md`、`docs/release-governance.md` | 契约与发布治理规范 |
| `docs/adr/*`（2 篇） | ADR 不迁移（§12.5 条款 8） |
| `docs/README.md` | 文档索引 |
