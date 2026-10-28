#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""路由注册与模板渲染模块。

关键顺序（家族仓实测坑）：**必须先注册页面路由、后自动发现 API 路由**。
FastAPI 0.141 的懒加载 include（_IncludedRouter）下，若 API 路由先注册，
页面路由注册后 restore/system 等懒加载路由会整体失配（全部 404）。
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
import secrets
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from app.integrated_app.version import get_app_version

logger = logging.getLogger(__name__)


def _auto_discover_routers(routes_package: Any, prefix: str = "") -> list[Any]:
    routers: list[Any] = []
    if not hasattr(routes_package, "__path__"):
        return routers
    for _importer, modname, ispkg in pkgutil.iter_modules(routes_package.__path__):
        full_name = f"{routes_package.__name__}.{modname}"
        try:
            mod = importlib.import_module(full_name)
            if hasattr(mod, "router"):
                routers.append(mod.router)
        except Exception as exc:
            logger.warning(f"导入路由模块失败 {full_name}: {exc}")
        if ispkg:
            try:
                subpkg = importlib.import_module(full_name)
                routers.extend(_auto_discover_routers(subpkg))
            except Exception as exc:
                logger.warning(f"递归扫描子包失败 {full_name}: {exc}")
    return routers


def auto_discover_routes(app: FastAPI) -> None:
    from app.integrated_app import routes as routes_pkg

    routers = _auto_discover_routers(routes_pkg)
    for router in routers:
        app.include_router(router)
    logger.info(f"自动发现并注册 {len(routers)} 个路由模块")


def render_page(request: Request, template_name: str, active_page: str = "", **ctx) -> HTMLResponse:
    """渲染页面模板，注入通用上下文（t / locales / version / i18n_flat / nonce）。"""
    i18n = request.app.state.i18n
    from app.integrated_app.i18n import get_flat_translations

    page_ctx = {
        "request": request,
        "t": i18n.t,
        "active_page": active_page,
        "current_locale": i18n.current_locale,
        "locale_name": i18n.get_locale_name(i18n.current_locale),
        "locales": [{"code": c, "name": i18n.get_locale_name(c)} for c in i18n.available_locales],
        "app_version": get_app_version(),
        "i18n_flat": get_flat_translations(i18n.current_locale),
        "csp_nonce": secrets.token_urlsafe(16),
    }
    page_ctx.update(ctx)
    env = request.app.state.jinja_env
    template = env.get_template(template_name)
    return HTMLResponse(content=template.render(**page_ctx))


def register_page_routes(app: FastAPI) -> None:
    """注册页面路由（必须在 auto_discover_routes 之前调用）。"""

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return render_page(request, "index.html", active_page="index")

    @app.get("/restore", response_class=HTMLResponse)
    async def restore_page(request: Request):
        return render_page(request, "restore.html", active_page="restore")

    @app.get("/history", response_class=HTMLResponse)
    async def history_page(request: Request):
        return render_page(request, "history.html", active_page="history")

    @app.get("/system-status", response_class=HTMLResponse)
    async def system_status_page(request: Request):
        return render_page(request, "system_status.html", active_page="system")

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        return render_page(request, "settings.html", active_page="settings")

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc):
        from fastapi.responses import RedirectResponse
        from starlette.exceptions import HTTPException as StarletteHTTPException

        from app.integrated_app.utils.response import respond_error

        detail = getattr(exc, "detail", None)
        if isinstance(exc, StarletteHTTPException) and detail not in (None, "Not Found"):
            return respond_error(
                code="NOT_FOUND",
                message=detail if isinstance(detail, str) else "请求的资源不存在",
                status=404,
            )
        if request.url.path.startswith("/api/"):
            return respond_error(code="NOT_FOUND", message="API endpoint not found", status=404)
        return RedirectResponse(url="/", status_code=302)
