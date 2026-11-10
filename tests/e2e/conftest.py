#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""浏览器级 E2E 夹具（TD-014）：真 uvicorn + 桩推理引擎 + Playwright。

这里与 ``tests/`` 其它单测的唯一区别是**被测对象是浏览器里的真实前端**，
所以三条搭法上的选择都有具体理由：

1. **真 uvicorn，不用 TestClient**：``EventSource`` 进度流、相对 URL 的静态资源、
   下载的 ``Content-Disposition``、CSRF cookie 这些只有真连接才成立；TestClient
   会把它们全部短路掉，测出来的是「代码看起来能跑」而不是「页面真的能跑」。
2. **桩引擎而不是真推理**：E2E 要验的是「上传 → 推理(mock) → 对比 → 下载」这条链，
   不是 GPU 与权重（CI 与本机都没有可用的检查点）。桩引擎只做两件真实引擎也会做的
   事——按 ``upscale`` 放大结果、按阶段回调进度，于是被测的仍然是**路由 / 任务队列 /
   SSE / 模板 / 前端 JS**这一整条链。
3. **临时目录与白名单同源**：上传、输出、导出目录全部指向 ``tmp_path``，并把
   ``runtime.security.allowed_base_dirs`` 指到同一批目录上。少一个就会踩
   ``security/path_guard.py`` 的 403——那是守卫在正常工作，不是 bug。

跑法见 ``docs/e2e-testing.md``；未安装 Playwright 时本目录的用例会**显式 skip**
（而不是静默 0 个用例），以免「测试层消失」被误读成「全部通过」。
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
from PIL import Image

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 桩引擎上报的阶段名与 ``routes/restore/upload.py::_STAGE_LABELS`` 的键对齐，
# 这样前端 ``stageLabel()`` 会真的走到映射分支（否则测不到阶段文案）。
_PROGRESS_STAGES: tuple[tuple[str, float], ...] = (
    ("encode", 0.12),
    ("venc", 0.26),
    ("sample", 0.58),
    ("decode", 0.86),
    ("align", 0.94),
)


class StubEngine:
    """只做「按 upscale 放大 + 报进度」的假引擎（不 import torch、不读权重）。"""

    def __init__(self, stage_delay: float = 0.01) -> None:
        self.stage_delay = stage_delay
        self.restore_calls: list[dict[str, Any]] = []

    def restore(self, image: Image.Image, params: Any, progress_cb: Any = None) -> Image.Image:
        cb = progress_cb if callable(progress_cb) else (lambda *_a: None)
        upscale = int(getattr(params, "upscale", 1) or 1)
        self.restore_calls.append(
            {
                "size": image.size,
                "upscale": upscale,
                "steps": getattr(params, "steps", None),
                "seed": getattr(params, "seed", None),
                "align_method": getattr(params, "align_method", None),
            }
        )
        for stage, ratio in _PROGRESS_STAGES:
            cb(stage, ratio)
            time.sleep(self.stage_delay)
        width, height = image.size
        out = image.convert("RGB").resize(
            (max(1, width * upscale), max(1, height * upscale)), Image.Resampling.BICUBIC
        )
        cb("done", 1.0)
        return out


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_until(predicate: Any, timeout: float, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _launch(app: Any, label: str) -> SimpleNamespace:
    """在后台线程起一个真 uvicorn，等 lifespan 真的就绪后返回句柄。"""
    import uvicorn

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="on",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, name=f"e2e-uvicorn-{label}", daemon=True)
    thread.start()

    if not _wait_until(lambda: bool(server.started), timeout=30.0):
        _halt(server, thread)
        pytest.fail(f"E2E 服务（{label}）未能在 30 秒内启动（uvicorn 线程未就绪）")

    return SimpleNamespace(server=server, thread=thread, base_url=f"http://127.0.0.1:{port}")


def _wait_restore_ready(base_url: str, label: str) -> None:
    """``server.started`` 只说明 lifespan 起了，再打一次首页确认真能应答。"""
    import httpx

    def _ready() -> bool:
        try:
            return httpx.get(f"{base_url}/restore", timeout=2.0).status_code == 200
        except Exception:
            return False

    if not _wait_until(_ready, timeout=15.0, interval=0.2):
        pytest.fail(f"E2E 服务（{label}）已启动但 {base_url}/restore 不可访问")


def _halt(server: Any, thread: threading.Thread) -> None:
    server.should_exit = True
    thread.join(10)


@pytest.fixture(scope="session")
def e2e_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SimpleNamespace]:
    """起一个真 uvicorn（桩引擎、临时目录、独立 history.db），供浏览器连。"""
    uploads = tmp_path_factory.mktemp("e2e-uploads")
    outputs = tmp_path_factory.mktemp("e2e-outputs")
    exports = tmp_path_factory.mktemp("e2e-exports")
    work = tmp_path_factory.mktemp("e2e-work")

    mp = pytest.MonkeyPatch()
    try:
        from app.integrated_app.model_manager import ModelManager
        from app.integrated_app.routes.restore import media as media_mod
        from app.integrated_app.routes.restore import upload as upload_mod

        # 三处目录指向 tmp：否则 E2E 会往仓里的 data/uploads、outputs 写垃圾。
        mp.setattr(upload_mod, "get_uploads_dir", lambda: str(uploads))
        mp.setattr(upload_mod, "get_outputs_dir", lambda: str(outputs))
        mp.setattr(media_mod, "_EXPORT_DIR", str(exports))

        # 每个阶段 0.25s：慢到足以让浏览器看见 SSE 的中间态（否则进度条 0 → 100 一闪而过，
        # 「进度是真的」这条就测不出来），快到一个用例仍在秒级完成。
        stub = StubEngine(stage_delay=0.25)

        def _get_engine(self: Any, name: str | None = None, progress_cb: Any = None) -> StubEngine:
            return stub

        # 只要 get_engine 换成桩，任务链路其余部分全走真实实现。
        mp.setattr(ModelManager, "get_engine", _get_engine)

        from app.integrated_app.app_server import create_app

        config = {
            "history": {"db_path": str(work / "e2e_history.db")},
            "server": {"auto_open_browser": False},
            "model": {"auto_load": False, "idle_unload_minutes": 0},
            "runtime": {
                "security": {
                    # 白名单必须与上面三个目录同源，否则 path_guard 会 403。
                    "allowed_base_dirs": [str(uploads), str(outputs), str(exports)],
                    # 默认 30 写/分钟对「一次跑完整目录」太紧，放宽到 600 避免假 429。
                    "rate_limit_per_minute": 600,
                },
                "task": {"queue_maxsize": 8},
            },
        }
        app = create_app(config)

        runtime = _launch(app, "default")
        _wait_restore_ready(runtime.base_url, "default")

        yield SimpleNamespace(
            base_url=runtime.base_url,
            uploads=str(uploads),
            outputs=str(outputs),
            exports=str(exports),
            stub=stub,
            config=config,
            work=str(work),
        )

        _halt(runtime.server, runtime.thread)
        assert not runtime.thread.is_alive(), "uvicorn 线程未能优雅退出"
    finally:
        mp.undo()


@pytest.fixture(scope="session")
def e2e_server_csrf(e2e_server: SimpleNamespace) -> Iterator[SimpleNamespace]:
    """同一套临时目录与桩引擎，但 ``runtime.security.csrf.enable=true`` 的服务。

    为什么另起一个服务而不是改默认配置：CSRF 只在 ``create_app()`` 时按开关装配，
    默认服务必须保持「关闭」形态（其余用例与线上默认一致），同一个 app 实例上加不了
    第二次中间件。两个服务**共用**上传/输出/导出目录与桩引擎，于是这里同样用
    「目录快照差集」判断本次落盘；历史库各自独立（同一个 sqlite 文件被两个服务同时
    写会撞锁）。
    """
    import copy

    from app.integrated_app.app_server import create_app

    mp = pytest.MonkeyPatch()
    try:
        # 固定 32 字节 hex 密钥：既让用例能离线复算签名（证明 cookie 是服务端签的、
        # 不是随机串），也避免往仓里的 data/.vosr2_secret 落一份密钥文件。
        secret_key_hex = "ee" * 32
        mp.setenv("VOSR2_SECRET_KEY", secret_key_hex)

        config = copy.deepcopy(e2e_server.config)
        config["history"] = {"db_path": os.path.join(e2e_server.work, "e2e_history_csrf.db")}
        security = config.setdefault("runtime", {}).setdefault("security", {})
        security["csrf"] = {"enable": True}
        app = create_app(config)

        runtime = _launch(app, "csrf")
        _wait_restore_ready(runtime.base_url, "csrf")

        yield SimpleNamespace(
            base_url=runtime.base_url,
            uploads=e2e_server.uploads,
            outputs=e2e_server.outputs,
            exports=e2e_server.exports,
            stub=e2e_server.stub,
            secret_key_hex=secret_key_hex,
        )

        _halt(runtime.server, runtime.thread)
        assert not runtime.thread.is_alive(), "CSRF E2E 服务线程未能优雅退出"
    finally:
        mp.undo()


@pytest.fixture()
def browser() -> Iterator[Any]:
    """Playwright Chromium；没装包或没装浏览器都显式 skip（不静默通过）。

    注意 scope 必须是 function：Playwright 的同步 API 会在**当前线程**里起一个事件循环
    （跑在 greenlet 里）。若把它做成 session 级，这个循环会一直「运行中」到整个测试会话结束，
    之后任何 `asyncio.run(...)` 形式的单测都会直接抛
    「asyncio.run() cannot be called from a running event loop」——
    用 `pytest tests/e2e tests/test_engine_unit.py` 就能复现这个跨文件污染。
    每次用例重启 Chromium 只多花约 0.3 秒，换来的是「E2E 不干扰其它测试」。
    """
    playwright_sync = pytest.importorskip(
        "playwright.sync_api",
        reason="未安装 Playwright：pip install -r requirements-e2e.txt",
    )
    pw = playwright_sync.sync_playwright().start()
    try:
        try:
            chromium = pw.chromium.launch(headless=True)
        except Exception as exc:  # 浏览器二进制缺失时是这条路径
            pytest.skip(
                f"Playwright 浏览器不可用（{exc}）。先跑：python -m playwright install chromium"
            )
        try:
            yield chromium
        finally:
            chromium.close()
    finally:
        pw.stop()


@pytest.fixture()
def page(browser: Any) -> Iterator[Any]:
    context = browser.new_context(accept_downloads=True, viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.set_default_timeout(20_000)
    try:
        yield page
    finally:
        context.close()
