# AGENTS.md（公开精简版）

<!-- governance:agent-contract v1 -->

> 本文件允许入库，只写公开安全的最小契约；完整本地上下文放 `local/`，不入库。
> 完整契约：[docs/agent-contract.md](docs/agent-contract.md)

## 开工前

1. 读 `docs/agent-contract.md`、根 `README.md` 与 `vosr/UPSTREAM.md`。
2. 确认本仓定位：`maintenance` 只修 bug / 依赖 / 安全，`active` 才允许新功能。
3. 动 `vosr/`（上游 vendor 代码）前，先在 `vosr/UPSTREAM.md` 记录改动原因。

## 硬约束

- 密钥、token、口令、机器绝对路径不得入库。
- 不提交 `local/`、`model/` 权重、`docs/plans|reports|_devarchive|agents`。
- 提交必须 `git commit -s`（DCO），身份用 noreply。
- 不改分支保护 / ruleset / Secrets / 仓库可见性。
- 不删他人 WIP，不 `git add -A`。
- Web 服务默认只绑 `127.0.0.1`，严禁改为 `0.0.0.0` / 公网 IP（见 README 安全节）。

## 交付前

- `git status` 只含本次改动；跑 `pytest tests/ -v` 与 `ruff check .`；失败如实报告。
- 改推理链路的，附 CLI 冒烟命令与结果（`python scripts/smoke_infer.py --help` 起手）。
