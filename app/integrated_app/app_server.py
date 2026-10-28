#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""VOSR2-Lite 应用服务器入口。

职责（对齐家族仓 SeedVR2-Lite 的 create_app/lifespan/main 结构，精简版）：
- FastAPI 应用装配（中间件 LIFO：RequestID -> SecurityHeaders 最外层）
- lifespan：历史库初始化 / 任务队列启动 / 模型空闲卸载看门狗
- main()：端口自动顺延 + uvicorn 启动
- 安全底线：默认仅绑 127.0.0.1（config.yaml server.host）
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import logging.handlers
import os
import sys
import webbrowser
from contextlib import asynccontextmanager

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

LOG_FORMAT = (
    "[%(asctime)s] [%(levelname)s] [%(name)s:%(filename)s:%(lineno)d] "
    "[req=%(request_id)s] %(message)s"
)


def setup_logging(config: dict | None = None) -> None:
    """控制台 + 按大小轮转的文件双通道日志。"""
    log_cfg = (config or {}).get("logging", {}) or {}
    level_name = str(os.environ.get("LOG_LEVEL", log_cfg.get("level", "INFO"))).upper()
    level = getattr(logging, level_name, logging.INFO)
    log_file = str(os.environ.get("LOG_PATH", log_cfg.get("file", "logs/app.log")))
    max_bytes = int(log_cfg.get("max_size_mb", 50)) * 1024 * 1024
    backup_count = int(log_cfg.get("backup_count", 3))

    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            with contextlib.suppress(AttributeError, ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if max_bytes > 0:
        try:
            log_path = os.path.join(PROJECT_ROOT, log_file)
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            handlers.append(
                logging.handlers.RotatingFileHandler(
                    log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
                )
            )
        except OSError as exc:
            logging.getLogger("vosr2").warning(f"文件日志初始化失败，降级控制台: {exc}")

    formatter = logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(_RequestIDLogFilterProxy())
    logging.basicConfig(level=level, handlers=handlers, force=True)


class _RequestIDLogFilterProxy(logging.Filter):
    def filter(self, record) -> bool:
        from app.integrated_app.middleware.request_id import current_request_id

        record.request_id = current_request_id.get("-")
        return True


from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.responses import Response  # noqa: E402

from app.integrated_app.config import load_config  # noqa: E402
from app.integrated_app.event_bus import event_bus  # noqa: E402
from app.integrated_app.history_db import HistoryDB  # noqa: E402
from app.integrated_app.i18n import init_i18n  # noqa: E402
from app.integrated_app.middleware.basic_auth import (  # noqa: E402
    BasicAuthMiddleware,
    resolve_auth_settings,
    should_enable_auth,
)
from app.integrated_app.middleware.csrf import CSRFMiddleware, should_enable_csrf  # noqa: E402
from app.integrated_app.middleware.error_handler import (  # noqa: E402
    ErrorHandlerMiddleware,
    register_error_handlers,
)
from app.integrated_app.middleware.rate_limit import RateLimitMiddleware  # noqa: E402
from app.integrated_app.middleware.request_id import RequestIDMiddleware  # noqa: E402
from app.integrated_app.middleware.security_headers import (  # noqa: E402
    SecurityHeadersMiddleware,
)
from app.integrated_app.model_manager import ModelManager  # noqa: E402
from app.integrated_app.task_queue import TaskQueue  # noqa: E402
from app.integrated_app.version import get_app_version  # noqa: E402

logger = logging.getLogger(__name__)


class VersionedStaticFiles(StaticFiles):
    """差异化 Cache-Control：CSS/JS 不缓存，字体 30 天，图片 1 天。"""

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        if args:
            path = str(args[0])
            if path.endswith((".css", ".js")):
                response.headers["Cache-Control"] = "no-cache, must-revalidate"
                response.headers["Pragma"] = "no-cache"
            elif path.endswith((".woff2", ".woff", ".ttf", ".eot", ".otf")):
                response.headers["Cache-Control"] = "public, max-age=2592000"
            elif path.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp")):
                response.headers["Cache-Control"] = "public, max-age=86400"
        return response


async def _model_idle_unload_loop(model_manager: ModelManager, idle_minutes: int) -> None:
    while True:
        await asyncio.sleep(60)
        with contextlib.suppress(Exception):
            model_manager.unload_if_idle()


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = app.state.config

    history_db: HistoryDB = app.state.history_db
    await history_db.initialize()

    task_queue: TaskQueue = app.state.task_queue
    await task_queue.start()

    idle_minutes = int(config.get("model", {}).get("idle_unload_minutes", 15) or 0)
    if idle_minutes > 0:
        app.state.model_idle_unload_task = asyncio.create_task(
            _model_idle_unload_loop(app.state.model_manager, idle_minutes)
        )
        logger.info(f"模型空闲自动卸载已启用: {idle_minutes} 分钟")

    if config.get("model", {}).get("auto_load", False):
        try:
            app.state.model_manager.get_engine()
        except Exception as exc:
            logger.warning(f"自动加载模型失败（不影响启动，将在首次推理时重试）: {exc}")

    host = config.get("server", {}).get("host", "127.0.0.1")
    port = config.get("server", {}).get("port", 7871)
    if config.get("server", {}).get("auto_open_browser", True):
        asyncio.get_running_loop().call_later(1.5, lambda: webbrowser.open(f"http://{host}:{port}"))

    logger.info(f"VOSR2-Lite 已启动: http://{host}:{port}")
    yield

    # ── 关闭 ──
    idle_task = getattr(app.state, "model_idle_unload_task", None)
    if idle_task:
        idle_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await idle_task
    await task_queue.stop()
    await history_db.close()
    logger.info("VOSR2-Lite 已关闭")


def create_app(config: dict | None = None) -> FastAPI:
    if config is None:
        config = load_config()

    enable_docs = os.environ.get("VOSR2_ENABLE_DOCS", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    app = FastAPI(
        title="VOSR2-Lite",
        description="基于 VOSR2 扩散模型的独立图像超分辨率工具箱",
        version=get_app_version(),
        lifespan=lifespan,
        docs_url="/docs" if enable_docs else None,
        redoc_url="/redoc" if enable_docs else None,
        openapi_url="/openapi.json" if enable_docs else None,
    )

    # CORS（环境变量优先于 config.yaml）
    allowed_origins = config.get("server", {}).get(
        "allowed_origins", ["http://127.0.0.1:7871", "http://localhost:7871"]
    )
    env_origins = os.environ.get("VOSR2_ALLOWED_ORIGINS", "").strip()
    if env_origins:
        allowed_origins = [o.strip() for o in env_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials="*" not in allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    rate_limit = int(config.get("runtime", {}).get("security", {}).get("rate_limit_per_minute", 30))
    if rate_limit >= 1:
        app.add_middleware(
            RateLimitMiddleware,
            rate_limit_per_minute=rate_limit,
            get_rate_limit_per_minute=min(rate_limit * 4, 240),
        )

    app.add_middleware(ErrorHandlerMiddleware)
    register_error_handlers(app)

    # ── 核心组件挂载 app.state ──
    from app.integrated_app.config import AppConfig

    app.state.config = config
    app.state.model_manager = ModelManager(AppConfig.load())
    app.state.history_db = HistoryDB(
        db_path=config.get("history", {}).get("db_path", "data/history.db"),
        max_records=config.get("history", {}).get("max_records", 10000),
    )
    app.state.task_queue = TaskQueue(
        maxsize=config.get("runtime", {}).get("task", {}).get("queue_maxsize", 100),
        task_timeout_seconds=config.get("runtime", {})
        .get("task", {})
        .get("max_timeout_seconds", 86400),
    )
    app.state.event_bus = event_bus
    app.state.i18n = init_i18n(
        locales_dir=os.path.join(os.path.dirname(__file__), "locales"),
        default_locale=config.get("i18n", {}).get("default_locale", "zh"),
    )

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    templates_dir = os.path.join(os.path.dirname(__file__), "templates")
    if os.path.isdir(static_dir):
        app.mount("/static", VersionedStaticFiles(directory=static_dir), name="static")

    import jinja2

    os.makedirs(templates_dir, exist_ok=True)
    app.state.jinja_env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(templates_dir),
        autoescape=jinja2.select_autoescape(["html", "xml"]),
    )

    from app.integrated_app.routes import auto_discover_routes, register_page_routes

    # 顺序敏感：先页面路由，后自动发现 API 路由（FastAPI 0.141 懒加载失配坑）
    register_page_routes(app)
    auto_discover_routes(app)

    # 安全中间件（默认关闭，config 开关控制）：CSRF 在内、Auth 在外，
    # 未认证请求应先拿到 401 而不是 403；两者都在 ErrorHandler 之外，
    # 其响应仍会经过 RequestID / SecurityHeaders。
    if should_enable_csrf(config):
        app.add_middleware(CSRFMiddleware)
        logger.info("CSRF 保护已启用（Double Submit Cookie）")
    if should_enable_auth(config):
        auth = resolve_auth_settings(config)
        app.add_middleware(
            BasicAuthMiddleware,
            username=auth["username"],
            password=auth["password"],
            realm=auth["realm"],
            max_auth_failures=auth["max_auth_failures"],
            auth_failure_window_seconds=auth["auth_failure_window_seconds"],
            auth_ban_seconds=auth["auth_ban_seconds"],
        )

    # LIFO：后添加 = 最外层。RequestID 最先执行，SecurityHeaders 最后装配
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, config=config)
    return app


def find_available_port(start_port: int, max_attempts: int = 200) -> int:
    """从 start_port 向上找第一个可绑定端口（占用则顺延，不必死守）。"""
    import socket

    for offset in range(max_attempts):
        candidate = start_port + offset
        if candidate > 65535:
            break
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", candidate))
                return candidate
            except OSError:
                continue
    raise OSError(f"在 {start_port}~{start_port + max_attempts} 范围内未找到可用端口")


def main() -> None:
    import uvicorn

    config = load_config()
    host = config.get("server", {}).get("host", "127.0.0.1")
    base_port = int(config.get("server", {}).get("port", 7871))
    debug = bool(config.get("server", {}).get("debug", False))

    port = find_available_port(base_port)
    if port != base_port:
        logger.warning(f"端口 {base_port} 已被占用，自动切换到 {port}")
        config["server"]["port"] = port
        origins = config["server"].setdefault("allowed_origins", [])
        origin = f"http://{host}:{port}"
        if origin not in origins:
            origins.append(origin)

    setup_logging(config)
    app = create_app(config)
    logger.info(f"VOSR2-Lite 启动中... http://{host}:{port}")
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=str(config.get("logging", {}).get("level", "INFO")).lower(),
        reload=debug,
    )


if __name__ == "__main__":
    main()
