# 贡献指南（CONTRIBUTING）

感谢参与 VOSR2-Lite。提交前请阅读本文件与根目录 `AGENTS.md`。

## 提交规范

- Commit message 遵循 [Conventional Commits](https://www.conventionalcommits.org/)：
  `feat: ...` / `fix: ...` / `docs: ...` / `refactor: ...` / `test: ...` / `chore: ...`
- 每个 commit 必须带 DCO 签名（`git commit -s`）。仓库已配置 `.githooks`，
  `commit-msg` 钩子会校验 `Signed-off-by:` 行，缺失将阻断提交。
- 身份建议使用 GitHub noreply 邮箱，避免个人邮箱入库。

## 本地开发

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install -r requirements-dev.txt   # 若存在 / 或 pip install pytest ruff
.venv\Scripts\python -m pytest tests/ -v
.venv\Scripts\python -m ruff check .
```

##  Pull Request

- 一个 PR 只做一件事；改模型加载/推理逻辑必须附冒烟证据（日志或输出图）。
- 不提交 `model/` 权重、`local/`、个人路径与密钥（见 `.gitignore`）。
- CI（ruff + pytest）必须通过。

## 报告问题

提交 Issue 请附：操作系统、GPU 型号与显存、Python/torch 版本、复现步骤与
`logs/app.log` 相关片段。
