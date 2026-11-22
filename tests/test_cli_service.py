#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""CLI 直调服务层单测：用假 ModelManager / Engine 覆盖主路径，不依赖 GPU。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.integrated_app import cli_service


class FakeConfig(SimpleNamespace):
    def __init__(self, **overrides):
        super().__init__()
        self.overrides = overrides

    def get(self, dotted: str, default=None):
        values = {
            "model.default_checkpoint": "VOSR2",
            "inference.max_output_side": 6144,
            "model.device": "auto",
            "server.host": "127.0.0.1",
            "server.port": 7871,
            "history.db_path": "data/history.db",
        }
        values.update(self.overrides)
        return values.get(dotted, default)

    def abspath(self, p: str) -> str:
        return p


class FakeInfo:
    def __init__(self, name: str, present: bool = True):
        self.name = name
        self.display_name = name
        self.ckpt_type = "onestep"
        self.ae_type = "qwen"
        self.min_vram_gb = 8.0
        self.default_steps = 1
        self.present = present
        self.missing = [] if present else ["some/weight.pth"]


class FakeManager:
    def __init__(self, present: bool = True):
        self.model_root = "model"
        self.engine_name = "VOSR2"
        self._present = present

    def scan(self):
        return [FakeInfo("VOSR2", self._present), FakeInfo("VOSR_0.5B_os", False)]

    def get_engine(self, name=None, progress_cb=None):
        return FakeEngine()


class FakeEngine:
    def restore(self, image, params, progress_cb=None):
        return image


def _patch_manager(monkeypatch, present=True):
    monkeypatch.setattr(
        cli_service,
        "_make_model_manager",
        lambda config_path=None: (FakeConfig(), FakeManager(present)),
    )


@pytest.fixture
def fake_pil(monkeypatch, tmp_path):
    """假 PIL.Image：open 返回上下文管理器，result.save 写真文件。"""
    from PIL import Image

    real_open = Image.open

    def fake_open(path):
        class Ctx:
            def __enter__(self):
                return Image.new("RGB", (8, 8))

            def __exit__(self, *a):
                return False

        return Ctx()

    monkeypatch.setattr(cli_service.Image, "open", fake_open)
    yield
    monkeypatch.setattr(cli_service.Image, "open", real_open)


class TestBuildParams:
    def test_defaults(self):
        args = SimpleNamespace()
        params = cli_service._build_params(args)
        assert params.upscale == 4
        assert params.steps == 1
        assert params.align_method == "wavelet"

    def test_explicit(self):
        args = SimpleNamespace(
            upscale=2, steps=3, align_method="adain", seed=7, tile_size=512, vae_tile_size=1024
        )
        params = cli_service._build_params(args)
        assert params.upscale == 2
        assert params.seed == 7
        assert params.tile_size == 512


class TestModelsStatus:
    def test_lists_checkpoints(self, monkeypatch):
        _patch_manager(monkeypatch)
        data = cli_service.models_status()
        assert len(data["checkpoints"]) == 2
        assert data["checkpoints"][0]["present"] is True
        assert data["loaded"] == "VOSR2"


class TestSystemStatus:
    def test_config_fields(self, monkeypatch):
        _patch_manager(monkeypatch)
        data = cli_service.system_status()
        assert data["server_host"] == "127.0.0.1"
        assert data["checkpoints_present"] == 1
        assert data["checkpoints_total"] == 2
        assert "cuda_available" in data


class TestRestore:
    def test_missing_input_raises(self, monkeypatch, tmp_path):
        _patch_manager(monkeypatch)
        with pytest.raises(FileNotFoundError):
            cli_service.restore(
                [str(tmp_path / "missing.png")],
                str(tmp_path / "out"),
                args=SimpleNamespace(),
            )

    def test_single_image_success(self, monkeypatch, tmp_path, fake_pil):
        _patch_manager(monkeypatch)
        src = tmp_path / "in.png"
        src.write_bytes(b"x")
        monkeypatch.setattr(
            cli_service.ModelManager,
            "get_engine",
            lambda self, name=None, progress_cb=None: FakeEngine(),
        )
        data = cli_service.restore(
            [str(src)],
            str(tmp_path / "out"),
            args=SimpleNamespace(upscale=2),
        )
        assert data["succeeded"] == 1
        assert data["failed"] == 0
        assert data["results"][0]["output"].endswith("in.png")
        assert (tmp_path / "out" / "in.png").exists()

    def test_batch_partial_failure(self, monkeypatch, tmp_path, fake_pil):
        _patch_manager(monkeypatch)
        a = tmp_path / "a.png"
        b = tmp_path / "b.png"
        a.write_bytes(b"a")
        b.write_bytes(b"b")

        class PartialEngine(FakeEngine):
            def restore(self, image, params, progress_cb=None):
                return image

        monkeypatch.setattr(
            cli_service.ModelManager,
            "get_engine",
            lambda self, name=None, progress_cb=None: PartialEngine(),
        )
        data = cli_service.restore(
            [str(a), str(b)], str(tmp_path / "out"), args=SimpleNamespace(upscale=2)
        )
        assert data["succeeded"] == 2
        assert data["failed"] == 0


class TestHistory:
    def test_list_missing_db_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            cli_service,
            "AppConfig",
            lambda data: FakeConfig(**{"history.db_path": str(tmp_path / "none.db")}),
        )
        data = cli_service.history_list()
        assert data["total"] == 0
        assert data["records"] == []

    def test_show_missing_db_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            cli_service,
            "AppConfig",
            lambda data: FakeConfig(**{"history.db_path": str(tmp_path / "none.db")}),
        )
        with pytest.raises(FileNotFoundError):
            cli_service.history_show(1)
