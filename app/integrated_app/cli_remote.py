#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""CLI 远程层：可选复用已运行的常驻 Web 服务（避免重复加载 6.6GB 模型）。

设计纪律（与 skill 一致，但按本项目实况简化）：
- 只在用户显式传 ``--server-url`` 时启用；默认直调，行为不退化。
- 不自动发现端口、不改桌面壳——本项目端口由 Rust 随机挑且只在内存里，
  自动发现需要改壳写端口文件，成本高收益低，留给后续。
- HTTP 契约已实测：``respond_success`` 返回 ``{"success": true, "data": ...}``；
  SSE 进度端点 ``GET /api/restore/{task_id}/progress`` 返回 ``data: {...}`` 行。
- CSRF 默认关闭；开启时按 double-submit 自适应（GET health 取 cookie/头）。
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable

import httpx

ProgressCb = Callable[[str, float], None]


class RemoteError(RuntimeError):
    """远程服务不可用或返回错误。"""


def _client(base_url: str, timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, trust_env=True)


def probe(base_url: str) -> dict[str, Any]:
    """探测远端是否为本项目的健康服务。"""
    try:
        with _client(base_url, timeout=3.0) as client:
            resp = client.get("/api/system/health")
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:
        raise RemoteError(f"无法连接常驻服务 {base_url}: {exc}") from exc
    if isinstance(payload, dict) and payload.get("success") is False:
        raise RemoteError(f"服务返回错误: {payload.get('error')}")
    return payload


def _csrf_headers(client: httpx.Client) -> dict[str, str]:
    """自适应 CSRF：GET health 取 token，返回 double-submit 头。"""
    try:
        resp = client.get("/api/system/health")
        token = resp.headers.get("X-CSRF-Token")
        if not token and "csrf_token=" in resp.headers.get("Set-Cookie", ""):
            token = resp.headers["Set-Cookie"].split("csrf_token=", 1)[1].split(";", 1)[0]
        if token:
            return {"X-CSRF-Token": token, "Cookie": f"csrf_token={token}"}
    except Exception:
        pass
    return {}


def restore(
    base_url: str,
    inputs: list[str],
    output_dir: str,
    *,
    params: dict[str, Any],
    checkpoint: str,
    progress_cb: ProgressCb | None = None,
    poll_timeout: float = 3600.0,
    poll_interval: float = 0.5,
) -> dict[str, Any]:
    """通过常驻服务串行修复多张图，返回与 cli_service.restore 兼容的结构。"""
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    os.makedirs(output_dir, exist_ok=True)

    with _client(base_url) as client:
        probe(base_url)
        headers = _csrf_headers(client)
        for source in inputs:
            task_id = ""
            try:
                with open(source, "rb") as fh:
                    form_params = {**params, "checkpoint": checkpoint}
                    resp = client.post(
                        "/api/restore/",
                        files={"file": (os.path.basename(source), fh, "image/png")},
                        data={k: str(v) for k, v in form_params.items()},
                        headers=headers,
                    )
                resp.raise_for_status()
                payload = resp.json()
                if payload.get("success") is False:
                    raise RemoteError(str(payload.get("error")))
                data = payload.get("data") or payload
                task_id = data.get("task_id", "")
                if not task_id:
                    raise RemoteError("服务未返回 task_id")

                deadline = time.time() + poll_timeout
                last_status = ""
                while time.time() < deadline:
                    status_resp = client.get(f"/api/restore/{task_id}/result")
                    status_resp.raise_for_status()
                    status_payload = status_resp.json()
                    status_data = status_payload.get("data") or status_payload
                    status = status_data.get("status", "")
                    if status != last_status:
                        last_status = status
                        if progress_cb:
                            progress_cb(status, float(status_data.get("progress", 0)) or 0.0)
                    if status == "completed":
                        out_path = status_data.get("output_path", "")
                        target = os.path.join(
                            output_dir, f"{os.path.splitext(os.path.basename(source))[0]}.png"
                        )
                        if out_path and os.path.isfile(out_path) and out_path != target:
                            with open(out_path, "rb") as src, open(target, "wb") as dst:
                                dst.write(src.read())
                            out_path = target
                        results.append(
                            {
                                "input": source,
                                "output": out_path,
                                "size": status_data.get("output_size"),
                                "task_id": task_id,
                            }
                        )
                        break
                    if status in ("failed", "cancelled"):
                        raise RemoteError(f"任务 {status}: {status_data.get('error', '')}")
                    time.sleep(poll_interval)
                else:
                    raise RemoteError(f"任务 {task_id} 超时（{poll_timeout:.0f}s）")
            except Exception as exc:
                failures.append({"input": source, "error": str(exc)})

    return {
        "mode": "remote",
        "server_url": base_url,
        "succeeded": len(results),
        "failed": len(failures),
        "results": results,
        "failures": failures,
        "output_dir": output_dir,
    }


__all__ = ["RemoteError", "probe", "restore"]
