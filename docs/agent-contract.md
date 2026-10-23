# Agent 完整契约（docs/agent-contract.md）

> 本文件可入库（对外可见），写清协作契约；纯本地上下文放 `local/`（不入库）。

## 1. 仓库定位

- 当前定位：**active**（新项目搭建期，允许新功能）。
- 模型权重与运行产物一律不入库；`model/` 仅保留 `.gitkeep` 与说明。

## 2. 环境与验证基线

- Python >= 3.12；torch >= 2.7（cu128；RTX 50 系 sm_120 必须，cu121 不支持）。
- 验证命令：
  - `pytest tests/ -v`
  - `ruff check .`
  - `python scripts/smoke_infer.py -i <图片> -o outputs/ -u 2`（需模型就位）
- 端口：默认 `127.0.0.1:7871`（README 与 `config.yaml` 为唯一事实来源）。

## 3. 上游 vendor 边界

- `vosr/` 为上游 cswry/VOSR（Apache-2.0）的 vendor；只允许最小必要适配，
  每次改动必须记录到 `vosr/UPSTREAM.md`（改了什么、为什么）。
- 新功能写在 `app/` 侧，不污染 `vosr/`。

## 4. 安全红线

- 仅绑 `127.0.0.1`；局域网共享必须显式开启 Basic Auth / 反向代理 HTTPS。
- 上传文件类型白名单（png/jpg/jpeg/webp/bmp/tif/tiff），大小上限见
  `config.yaml` 的 `runtime.security.max_upload_image_mb`。
- 输出路径必须落在 `runtime.security.allowed_base_dirs` 之内。

## 5. 提交与分支

- Conventional Commits + DCO（`-s`），noreply 邮箱。
- 一个 commit 一件事；不 force push、不 `--no-verify`。
- 远程同步：默认本地修改 → 用户确认 → push；push 后 `git pull` 防分叉。
