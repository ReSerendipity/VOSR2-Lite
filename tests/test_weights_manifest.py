#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""权重 sha256 清单测试：读写回环、校验语义、加载侧强校验。"""

from __future__ import annotations

import os

import pytest

from app.integrated_app.model_manager import CheckpointInfo, ModelManager
from app.integrated_app.weights_manifest import (
    MANIFEST_FILENAME,
    STATUS_MISMATCH,
    STATUS_OK,
    STATUS_UNREGISTERED,
    WeightsManifest,
    sha256_file,
)

_KNOWN_ABC_SHA = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


class _FakeConfig:
    """只实现 ModelManager 用到的 get/abspath 面。"""

    def __init__(self, model_root: str):
        self._model_root = model_root
        self._data = {"model": {"pretrained_dir": model_root, "checkpoints": {}}}

    def get(self, key: str, default=None):  # noqa: ANN001
        node: object = self._data
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def abspath(self, relative: str) -> str:
        return relative if os.path.isabs(relative) else os.path.join(os.getcwd(), relative)


def _make_model_root(tmp_path, with_manifest: bool = True):
    root = tmp_path / "model"
    (root / "ckpt" / "checkpoints").mkdir(parents=True)
    files = {
        "ckpt/args.json": b'{"resolution": 512}',
        "ckpt/checkpoints/ema_model.safetensors": b"weights-bytes-here",
    }
    for rel, blob in files.items():
        (root / rel).write_bytes(blob)
    if with_manifest:
        manifest = WeightsManifest()
        for rel in files:
            manifest.record(rel, str(root / rel))
        manifest.save(str(root))
    return root, files


# ── 基础语义 ────────────────────────────────────────────────────────────────
def test_sha256_file_known_vector(tmp_path):
    target = tmp_path / "abc.bin"
    target.write_bytes(b"abc")
    assert sha256_file(str(target)) == _KNOWN_ABC_SHA


def test_manifest_roundtrip_and_verify(tmp_path):
    root, files = _make_model_root(tmp_path)
    manifest = WeightsManifest.load(str(root))
    for rel in files:
        assert manifest.verify(rel, str(root / rel)) == STATUS_OK
    saved = WeightsManifest.load(str(root))
    assert saved.get("ckpt/args.json") is not None
    assert saved.get("ckpt/args.json")["size_bytes"] == len(files["ckpt/args.json"])


def test_manifest_detects_tamper(tmp_path):
    root, files = _make_model_root(tmp_path)
    ckpt_rel = "ckpt/checkpoints/ema_model.safetensors"
    (root / ckpt_rel).write_bytes(b"tampered!")
    manifest = WeightsManifest.load(str(root))
    assert manifest.verify(ckpt_rel, str(root / ckpt_rel)) == STATUS_MISMATCH
    assert manifest.verify("ckpt/args.json", str(root / "ckpt/args.json")) == STATUS_OK


def test_manifest_unregistered(tmp_path):
    root, _ = _make_model_root(tmp_path, with_manifest=False)
    (root / "other.bin").write_bytes(b"x")
    manifest = WeightsManifest.load(str(root))
    assert manifest.entries == {}
    assert manifest.verify("other.bin", str(root / "other.bin")) == STATUS_UNREGISTERED


def test_manifest_corrupt_file_treated_as_empty(tmp_path):
    root = tmp_path / "model"
    root.mkdir()
    (root / MANIFEST_FILENAME).write_text("{not json", encoding="utf-8")
    manifest = WeightsManifest.load(str(root))
    assert manifest.entries == {}
    assert manifest.updated_at == ""


# ── 加载侧强校验（ModelManager._verify_weights）────────────────────────────
def _make_info(name: str = "ckpt") -> CheckpointInfo:
    return CheckpointInfo(name=name, ae_type="qwen", present=True, missing=[])


def _qwen_extra_files(root, files: dict[str, bytes]) -> dict[str, bytes]:
    """_verify_weights 走 qwen 公共文件集，测试目录补齐。"""
    common = {
        "Qwen-Image-vae-2d/config.json": b"{}",
        "Qwen-Image-vae-2d/diffusion_pytorch_model.safetensors": b"vae",
        "torch_cache/checkpoints/dinov2_vitl14_pretrain.pth": b"dinov2",
    }
    for rel, blob in common.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(blob)
        files[rel] = blob
    return files


def test_verify_weights_passes_when_all_registered(tmp_path):
    root, files = _make_model_root(tmp_path)
    _qwen_extra_files(root, files)
    manifest = WeightsManifest()
    for rel in files:
        manifest.record(rel, str(root / rel))
    manifest.save(str(root))

    manager = ModelManager(_FakeConfig(str(root)))
    manager._verify_weights(_make_info())  # 不抛即通过


def test_verify_weights_rejects_mismatch(tmp_path):
    root, files = _make_model_root(tmp_path)
    _qwen_extra_files(root, files)
    manifest = WeightsManifest()
    for rel in files:
        manifest.record(rel, str(root / rel))
    manifest.save(str(root))
    (root / "ckpt/checkpoints/ema_model.safetensors").write_bytes(b"corrupted")

    manager = ModelManager(_FakeConfig(str(root)))
    with pytest.raises(RuntimeError, match="sha256 校验失败"):
        manager._verify_weights(_make_info())


def test_verify_weights_warns_only_for_unregistered(tmp_path):
    from loguru import logger

    root, files = _make_model_root(tmp_path, with_manifest=False)
    _qwen_extra_files(root, files)
    manifest = WeightsManifest()
    # 只登记一个文件，其余保持未登记
    manifest.record("ckpt/args.json", str(root / "ckpt/args.json"))
    manifest.save(str(root))

    lines: list[str] = []
    handler_id = logger.add(lines.append, level="WARNING")
    try:
        manager = ModelManager(_FakeConfig(str(root)))
        manager._verify_weights(_make_info())  # 未登记只告警，不抛
    finally:
        logger.remove(handler_id)
    assert any("未登记" in line for line in lines)
