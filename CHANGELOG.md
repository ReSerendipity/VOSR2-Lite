# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- GitHub Release 发布链（§10 合规）：`.github/workflows/release.yml`——`v*` tag 推送 /
  `workflow_dispatch` 触发；版本一致性门禁（tag == `pyproject.toml` == `version.json`）、
  `git archive` 源码打包、`SHA256SUMS.txt`、CycloneDX SBOM、SLSA build provenance 证明、
  draft Release 创建与缺失资产补传（不自动 publish）。
- 发布 / 回滚治理规范 `docs/release-governance.md`（公共条款：`ReSerendipity/.github` §10）。

## [0.1.0] - 2026-10-24

### Added

- 初始版本：基于 VOSR2 扩散模型的独立图像超分辨率工具箱（FastAPI + Web UI，无需 ComfyUI）。
- 本地推理：DiT / VAE 两级分块（Gaussian 混合），fp16 适配消费级显卡（12GB 可跑）。
- 多检查点支持：VOSR2（一步，默认）/ VOSR-1.4B / VOSR-0.5B（一步蒸馏 / 多步）。
- Web UI：Jinja2 + HTMX 多语言界面（简体中文 / English）、SSE 实时进度、SQLite 历史记录。
- 模型权重外置：`scripts/download_model.py` 从官方来源获取（不入库、不随发布包分发）。
