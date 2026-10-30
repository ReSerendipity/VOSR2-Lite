#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""VOSR2-Lite 便携版启动脚本（供 Tauri v2 桌面壳调用）。

与 ``python -m app.integrated_app.app_server`` 的区别：
- 支持 ``--port`` / ``--host``（壳先挑空闲端口再传入，避免与其它实例抢 7871）；
- 不自动打开浏览器（由 Tauri WebView 加载页面）；
- 日志走 stdout/stderr（壳重定向到 ``logs/python_<ts>.log``）；
- 复用 ``app_server.serve()``，因此 ``POST /api/system/shutdown`` 可优雅终止本进程。

开发态位于 ``<repo>/scripts/start_portable.py``（app 包在上一级）；
打包态位于 ``<app_dir>/start_portable.py``（app 包同级）。
"""

import argparse
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.isdir(os.path.join(SCRIPT_DIR, "app", "integrated_app")):
    PROJECT_ROOT = SCRIPT_DIR
else:
    PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 环境隔离：清掉可能指向其它解释器/用户包的变量，避免便携运行时被宿主环境污染
for _var in ("PYTHONHOME", "PYTHONSTARTUP", "PYTHONPATH"):
    os.environ.pop(_var, None)
sys.path = [
    p
    for p in sys.path
    if not any(
        exclude in p.lower() for exclude in ("\\appdata\\", "\\program files\\", "\\programdata\\")
    )
]
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.integrated_app.app_server import (  # noqa: E402
    create_app,
    load_config,
    serve,
    setup_logging,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="VOSR2-Lite 便携版启动器")
    parser.add_argument(
        "--port", type=int, default=None, help="监听端口（默认读 config.yaml，7871）"
    )
    parser.add_argument("--host", type=str, default="127.0.0.1", help="监听地址")
    parser.add_argument("--config", type=str, default=None, help="配置文件路径")
    args = parser.parse_args()

    config = load_config(args.config) if args.config else load_config()

    host = args.host
    port = int(args.port or config.get("server", {}).get("port", 7871))

    server_cfg = config.setdefault("server", {})
    server_cfg["host"] = host
    server_cfg["port"] = port
    # 桌面壳负责显示，禁止服务端再拉起系统浏览器
    server_cfg["auto_open_browser"] = False

    # 动态端口 + Tauri WebView origin 加入 CORS 白名单
    origins = server_cfg.setdefault("allowed_origins", [])
    for extra in (f"http://{host}:{port}", "http://tauri.localhost"):
        if extra not in origins:
            origins.append(extra)

    setup_logging(config)
    app = create_app(config)

    log_level = str(config.get("logging", {}).get("level", "INFO")).lower()
    print(f"VOSR2-Lite 启动中... http://{host}:{port}", flush=True)
    print(f"工作目录: {PROJECT_ROOT}", flush=True)

    serve(app, host, port, log_level)


if __name__ == "__main__":
    main()
