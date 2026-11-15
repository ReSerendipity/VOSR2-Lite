#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""TD-008：在线安装 `_install_runtime_online` 的失败路径与契约。

覆盖点（对应 docs/tech-debt.md TD-008 的三条缺口）：
- 退出码非 0 → RuntimeError，且报错里带 pip 输出尾巴；
- 取消 → terminate 子进程 + InterruptedError，且**不留下** RUNTIME.json（不能把半装环境写成「已安装」）；
- 镜像顶包 → 用 PIP_CONFIG_FILE=NUL 屏蔽用户镜像、显式钉 --index-url/--extra-index-url。

另钉命令构造与成功路径的元信息写入，避免「只是没人测」变成「改了没人拦」。
全部用假 Popen，不真跑 pip（CI 无网/无 GPU 也要能过）。
"""

from __future__ import annotations

import json
import os

import pytest

from app.integrated_app import download_service


class _FakeProc:
    """假 pip 子进程：可指定输出行、退出码，并记录 terminate 是否被调用。"""

    def __init__(self, lines, code=0):
        self.stdout = list(lines)
        self._code = code
        self.terminated = False

    def wait(self):
        return self._code

    def terminate(self):
        self.terminated = True


@pytest.fixture()
def fake_popen(monkeypatch):
    """替换 subprocess.Popen，返回一个可注入脚本的工厂。"""

    calls = {}

    def _install(lines, code=0):
        def _factory(command, **kwargs):
            calls["command"] = command
            calls["kwargs"] = kwargs
            calls["proc"] = _FakeProc(lines, code)
            return calls["proc"]

        monkeypatch.setattr("subprocess.Popen", _factory)
        return calls

    return _install


@pytest.fixture()
def meta_dir(tmp_path, monkeypatch):
    """把 RUNTIME.json 的落地目录指到 tmp，禁止写进真实解释器目录。"""
    monkeypatch.setattr(download_service, "_python_dir", lambda: str(tmp_path))
    return tmp_path


def _read_meta(meta_dir):
    path = os.path.join(str(meta_dir), download_service.RUNTIME_META_NAME)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def test_command_contract_and_pip_config_isolation(fake_popen, meta_dir):
    """命令必须钉死 --progress-bar off、显式索引，并用 PIP_CONFIG_FILE 屏蔽用户镜像。"""
    catalog = {
        "packages": ["torch", "torchvision", "numpy"],
        "index_url": "https://download.pytorch.org/whl/cu132",
        "pip_extra_index": "https://pypi.org/simple",
        "version": "2.13",
        "python": "3.12",
        "torch": "2.13.0+cu132",
    }
    calls = fake_popen(["Collecting torch\n"], code=0)

    download_service._install_runtime_online(catalog)

    command = calls["command"]
    assert command[1:4] == ["-m", "pip", "install"]
    assert command[4:7] == ["torch", "torchvision", "numpy"]
    assert "--progress-bar" in command and command[command.index("--progress-bar") + 1] == "off"
    assert "--no-warn-script-location" in command
    assert command[command.index("--index-url") + 1] == "https://download.pytorch.org/whl/cu132"
    assert command[command.index("--extra-index-url") + 1] == "https://pypi.org/simple"

    kwargs = calls["kwargs"]
    assert kwargs["cwd"] == download_service.PROJECT_ROOT
    # 防镜像顶包：用户 pip.ini 里的 +cpu 源必须被屏蔽
    assert kwargs["env"]["PIP_CONFIG_FILE"] == "NUL"
    assert kwargs["stdout"] is not None
    assert kwargs["stderr"] is not None


def test_empty_packages_falls_back_to_torch_family(fake_popen, meta_dir):
    """catalog 没写 packages 时兜底 torch+torchvision，不能装成空命令。"""
    calls = fake_popen([], code=0)
    download_service._install_runtime_online({"packages": []})

    command = calls["command"]
    assert command[4:6] == ["torch", "torchvision"]
    assert len(command) == 6 + 3  # 两个包 + --no-warn-script-location/--progress-bar/off


def test_blank_package_entries_are_dropped(fake_popen, meta_dir):
    """空串/None 条目被过滤；只要还剩有效包就**不**兜底（兜底只针对全空）。"""
    calls = fake_popen([], code=0)
    download_service._install_runtime_online({"packages": ["torch", "", None]})

    assert calls["command"][4:5] == ["torch"]
    assert calls["command"][5] == "--no-warn-script-location"


def test_no_index_url_means_no_flag(fake_popen, meta_dir):
    """catalog 不写索引时不能传空 --index-url（pip 会把它当非法参数）。"""
    calls = fake_popen([], code=0)
    download_service._install_runtime_online({"packages": ["torch"], "index_url": ""})

    assert "--index-url" not in calls["command"]
    assert "--extra-index-url" not in calls["command"]


def test_success_writes_meta_and_reports_done(fake_popen, meta_dir):
    catalog = {
        "version": "2.13",
        "python": "3.12",
        "torch": "2.13.0+cu132",
        "cuda": "13.2",
        "built_at": "2026-09-01",
        "min_app_version": "1.6",
        "packages": ["torch"],
    }
    fake_popen(["Looking in indexes\n", "Successfully installed torch\n"], code=0)
    events = []

    meta = download_service._install_runtime_online(
        catalog, on_progress=lambda *a: events.append(a)
    )

    assert meta["install_mode"] == "online"
    assert meta["runtime_version"] == "2.13"
    assert meta["torch"] == "2.13.0+cu132"
    assert meta["packages"] == ["torch"]
    assert meta["installed_at"].endswith("Z")

    on_disk = _read_meta(meta_dir)
    assert on_disk == meta

    stages = [e[0] for e in events]
    assert stages[0] == "download" and events[0][1] == 0.01
    assert "swap" in stages and "done" in stages
    assert events[-1] == ("done", 1.0, "环境安装完成，请重启应用")


def test_nonzero_exit_raises_with_output_tail(fake_popen, meta_dir):
    """退出码非 0：RuntimeError 必须带「退出码 + 最后几行」，且不写 RUNTIME.json。"""
    fake_popen(
        ["line-1\n", "line-2\n", "line-3\n", "ERROR: Could not find a version\n"],
        code=1,
    )

    with pytest.raises(RuntimeError) as exc:
        download_service._install_runtime_online({"packages": ["torch"]})

    message = str(exc.value)
    assert "退出码 1" in message
    assert "Could not find a version" in message
    # tail 只留最后 3 行：最早的 line-1 不应出现
    assert "line-1" not in message
    assert _read_meta(meta_dir) is None


def test_cancel_during_stream_terminates_and_leaves_no_meta(fake_popen, meta_dir):
    """取消：terminate 子进程 + InterruptedError，且不写「已安装」。"""
    calls = fake_popen(["a\n", "b\n", "c\n"], code=0)
    seen = {"n": 0}

    def should_cancel():
        seen["n"] += 1
        return seen["n"] > 2  # 第一次进循环就取消

    with pytest.raises(InterruptedError):
        download_service._install_runtime_online(
            {"packages": ["torch"]}, should_cancel=should_cancel
        )

    assert calls["proc"].terminated is True
    assert calls["proc"].wait() == 0  # 取消路径不应再走 wait 的结论
    assert _read_meta(meta_dir) is None


def test_cancel_flag_checked_before_first_line(fake_popen, meta_dir):
    """取消标志在建流后立刻生效——不能先吞掉几行输出再响应。"""
    calls = fake_popen(["a\n"], code=0)

    with pytest.raises(InterruptedError):
        download_service._install_runtime_online(
            {"packages": ["torch"]}, should_cancel=lambda: True
        )

    assert calls["proc"].terminated is True
    assert _read_meta(meta_dir) is None


def test_progress_line_is_truncated_to_160(fake_popen, meta_dir):
    """pip 日志可能极长，进度回调里的消息必须截断（否则 UI 会被撑爆）。"""
    long_line = "x" * 400
    fake_popen([long_line + "\n"], code=0)
    events = []

    download_service._install_runtime_online(
        {"packages": ["torch"]}, on_progress=lambda *a: events.append(a)
    )

    stream_events = [e for e in events if e[1] == 0.5]
    assert stream_events and len(stream_events[0][2]) == 160


def test_install_runtime_delegates_when_catalog_says_online(monkeypatch, fake_popen, meta_dir):
    """install_runtime 在 install_mode=online 时必须走在线分支（不能回头读 volumes）。"""
    monkeypatch.setattr(
        download_service, "runtime_catalog", lambda: {"install_mode": "ONLINE", "packages": ["torch"]}
    )
    fake_popen([], code=0)

    meta = download_service.install_runtime()

    assert meta["install_mode"] == "online"
    assert _read_meta(meta_dir) is not None
