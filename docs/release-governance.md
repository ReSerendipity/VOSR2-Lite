# 发布 / 回滚 规范（VOSR2-Lite）

> **来源与权威**：公共条款见 `ReSerendipity/.github` 仓库 `REPOSITORY_GUIDELINES.md` §10（发布 / 分发统一规范）。
> 本文件是本仓本地化副本：公共条款（远端优先、tag 驱动、版本位一致、不可变标签、无 GPG；
> 镜像类仓另有扫描阻断，本仓无镜像不适用）与 §10 保持一致，不自行改写；
> 下文只列本仓特有项。
> **收敛依据**：`RELEASE_GOVERNANCE_TEMPLATE.md`（`.github` 仓根目录）。

---

## 0. 远端优先（根原则，不可改写）

发布链的构建、打包、扫描、上传、推送**全部在 GitHub Actions 远端完成**；
本地只做三件事：改版本位、落 CHANGELOG、`git tag` + `git push`。
本地不执行打包 / 发布上传。

## 1. 版本号规范

- 遵循 SemVer `MAJOR.MINOR.PATCH`；预发布用 `-alpha.N` / `-beta.N` / `-rc.N`（公共条款，§10.2）。
- **唯一权威版本位**：`pyproject.toml` 顶层 `project.version`（当前 0.1.0）。
- 必须同步的其它版本位：
  - `version.json` 的 `version` 字段（元数据，当前同为 0.1.0）。
  - `CHANGELOG.md` 每个发版小节标题。
- **漂移判定**：`release.yml` 的「版本一致性校验」步骤钉住 `tag == pyproject.toml == version.json`
  三者一致——漂一处即判红、直接失败。
- 自动化方式：手动 tag（本仓未引入 release-please）。

## 2. CHANGELOG 管理

- 遵循 Keep a Changelog：`Added / Changed / Fixed / Deprecated / Removed / Security`（公共条款，§10.2）。
- 每次合并 `main` 在 `[Unreleased]` 追加；type 映射 `feat→Added`、`fix→Fixed`、`refactor→Changed`。
- 发版时 `[Unreleased]` → `[VERSION] - YYYY-MM-DD`。

## 3. 发布流程（远端）

1. 确认 `[Unreleased]` 条目完整。
2. 同步版本位（见 §1 清单）。
3. 本地打 tag 并推送：`git tag v<X.Y.Z>` → `git push origin v<X.Y.Z>`。
4. 远端凭 tag 触发 `.github/workflows/release.yml`：版本一致性门禁 → `git archive` 打包
   tracked 源码 → `SHA256SUMS.txt` → CycloneDX SBOM → SLSA build provenance 证明 →
   创建 **draft** Release 并挂资产。
   `workflow_dispatch` 也可手动触发；此时要求 tag `v<X.Y.Z>` 已存在（`git archive` 与
   `gh release create --verify-tag` 均以 tag 为准）。
5. 远端建 draft Release → **人工审核后 `published`**；workflow 不自动 publish。
6. 若同 tag 的 draft 已存在：只补传缺失资产，**不覆盖既有 notes**。
7. `gh run list` 盯到终态；红了当场修或 revert 止损。

## 4. 分发产物与完整性

**分发通道**：`distribution = github-release`（GitHub Release；无容器镜像、无 Pages 产物）。

| 产物 | 构建位置 | 标签 / 命名 | 完整性手段 |
|---|---|---|---|
| 源码发布包 `VOSR2-Lite-v<X.Y.Z>.zip` | 远端（`release.yml`，`git archive`，仅 tracked 文件） | GitHub Release 资产，不可变（已发布资产不覆盖） | `SHA256SUMS.txt` + CycloneDX SBOM + SLSA build provenance |

- **资产清单**：
  - `VOSR2-Lite-v<X.Y.Z>.zip`（tracked 源码；模型权重、`data/`、`outputs/`、`logs/` 不入库也不打包）
  - `SHA256SUMS.txt`（对 zip 计算）
  - `VOSR2-Lite-v<X.Y.Z>-sbom.cdx.json`（CycloneDX，基于 `requirements.txt`）
  - Release 页 SLSA provenance（`actions/attest-build-provenance` 绑定 zip digest）
- **用户校验**：`sha256sum -c SHA256SUMS.txt`；SBOM 供供应链审计；provenance 证明 zip
  由本仓 Actions 构建。
- **不使用 GPG 分离签名**（2026-10-18 裁决，§10.1 / §10.3）。完整性由 `SHA256SUMS.txt`
  （防传输损坏）+ SLSA provenance（防伪造）承担。

## 5. 回滚

- **判定**（满足任一即触发）：
  1. 新版在用户机器出现 P0/P1 启动或推理故障（权重完整性校验失败、进程无法启动、核心修复功能失效）；
  2. 发布包完整性校验失败（SHA-256 与 `SHA256SUMS.txt` 不符）。
- **执行**：`git revert <bad-release-commit>`（保历史、可再 forward）；配置回滚
  `git revert` 对应 `config.yaml`；发布资产不可变——用户按**上一个 tag 的 Release 资产
  重新下载并替换**（重下 `VOSR2-Lite-v<上一版>.zip` → 校验 SHA-256 → 替换安装目录）。
- **破坏性动作**：重指向 / 删除 tag、移动任何远端 ref 须仓库所有者单独授权（§10.2）。
- **回滚后验证**：`python scripts/smoke_infer.py -i <test.png> -o outputs/` 跑通一次真实推理；
  Web UI 健康接口 `/api/system/health` 返回 200 且响应体含 `data.gpu` 与 `data.queue` 字段。

## 6. SLA / 错误预算

- 不适用：本地运行桌面工具（无对外在线服务，无可用性 SLA）。发布回归风险由 §5 回滚 +
  §7 检查清单兜底。

## 7. 发布前检查清单

- [ ] 版本位全部同步（`pyproject.toml == version.json == tag`，见 §1）
- [ ] CHANGELOG `[Unreleased]` 已改版本 + 日期
- [ ] `pytest tests/ -v` 通过（ci.yml 门禁）
- [ ] `ruff check .` 全绿（ci.yml 门禁）
- [ ] 模型权重完整性：`python scripts/download_model.py` 幂等可重跑（权重不入包）
- [ ] **本地未产出任何发布交付物**（全部远端构建）

---

## 差异点采集清单（本仓取值）

| 占位符 | 采集来源 | 本仓取值 |
|---|---|---|
| 版本位文件与字段 | `pyproject.toml` | `project.version`（唯一权威，当前 0.1.0） |
| 其它需同步版本位 | `grep -rn` 版本串 | `version.json` → `version`；`CHANGELOG.md` 发版小节标题 |
| 版本一致性校验入口 | `.github/workflows/` | `release.yml`「版本一致性校验」步骤（tag == pyproject == version.json） |
| 自动化方式 | `.github/workflows/` | 手动 tag（无 release-please） |
| 发布 workflow 文件名 | `.github/workflows/` | `release.yml` |
| 产物形态与标签规则 | 构建 workflow | `VOSR2-Lite-v<X.Y.Z>.zip`（`git archive` tracked 源码，不可变） |
| 完整性手段 | Release 资产清单 | `SHA256SUMS.txt` + CycloneDX SBOM + SLSA provenance |
| 回滚判定阈值 | 运行手册 / 监控 | 见 §5（用户侧 P0/P1 故障 / 校验失败） |
| 回滚后验证命令 | `tests/smoke` 等 | `python scripts/smoke_infer.py -i <test.png> -o outputs/` |
| 技术栈 | `pyproject.toml` | Python 3.12、FastAPI、PyTorch (CUDA) |
| 部署目标 | `deploy/` / Pages workflow | 无——`distribution=github-release`（本地运行，§10.4） |

## 禁止事项

- 不得重写 §10 的公共条款（远端优先、tag 驱动、版本位一致、不可变标签、无 GPG）。
- 不得引入 `latest` 作发布基线；不得保留或新建 GPG 签名流程。
- 不得将模型权重（`model/`）或运行产物（`data/`、`outputs/`、`logs/`）打入发布包。
- 不得留空占位符，不得编造阈值。
