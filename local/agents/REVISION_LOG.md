# REVISION_LOG（自进化修订记录）

> 本文件由自进化协议维护：每次更新根 `AGENTS.md` 后追加一行，不删不改历史行。
> 路径优先 `local/agents/REVISION_LOG.md`；存量 `docs/agents/REVISION_LOG.md` 视为历史分片。
> 顶部「自进化版本」必须与最后一行版本对应；有 `scripts/check_revision_log.py` 的仓继续用机械闸。

| 自进化版本 | 日期 | 触发原因 | 更新内容摘要 | 对应项目版本 | 已校验 |
| :---: | ---------- | --- | --- | :---: | :-: |
| v1.0 | YYYY-MM-DD | 初始化台账 | 建立 GOTCHAS / REVISION_LOG 标准表头 | 当前版本 | — |
| v1.1 | 2026-11-01 | 同步治理 §17 自主收口条款（ADR-0015） | 根 AGENTS.md §6 增「提交 / 推送 / 合并 / CI 监控在任务结尾自主完成，不询问用户」一行 | — | ✅ |
| v1.2 | 2026-11-10 | 文档收口（WO-DOC-S01）+ restore 取消语义修复 | 建立 `docs/INDEX.md`；取消后 idle 项统一置 cancelled；补 e2e 批量/像素/CSRF 用例；PYSEC-2026-139 / PYSEC-2025-194 显式豁免（复查期限 2027-01-31 或 torch 2.13 cu128 先到） | 当前版本 | ✅ |
