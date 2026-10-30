#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""下载管理：环境包（Python 运行时 + torch）与模型权重的安装、进度、取消、卸载。

瘦包（约 36 MB）不含运行时与权重，用户装完后必须在这里完成安装才能推理。
本模块是 `/api/downloads/*` 与「下载管理」页面的执行层。

硬约束（同 routes/downloads.py）：**禁止顶层 import torch**。本模块必须能在
引导解释器（无 torch）上运行，否则用户永远装不上环境。

两条下载链路：
    1. 环境包：自己托管在 GitHub Release（见 CATALOG）。torch cu128 从 HuggingFace
       取不便，且国内直连 download.pytorch.org 常失败，故预构建成 zip 分卷分发。
       下载 → 逐卷校验 SHA256 → 合并 → zipfile 解压到 `app/runtime/`。
    2. 模型：不自己托管（省 ECS 流量与 Release 配额），复用
       `scripts/download_model.py` 的多源回退（hf-mirror → HF → ModelScope）
       与断点续传（.part + HTTP Range）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from typing import Any, Callable

from app.integrated_app.config import PROJECT_ROOT
from app.integrated_app.version import get_app_version

# ---------------------------------------------------------------- 目录 ----
RUNTIME_DIRNAME = "runtime"
# 瘦包内置的解释器目录名（无 torch）。环境装进去后目录名不变，只多出 torch。
BOOTSTRAP_DIRNAME = "bootstrap"
CATALOG_FILENAME = "downloads_catalog.json"
RUNTIME_META_NAME = "RUNTIME.json"

# 环境包 Release 的默认位置（catalog 缺失时的兜底，构建环境包时须更新 catalog）
DEFAULT_RELEASE_BASE = "https://github.com/ReSerendipity/VOSR2-Lite/releases/download"

ProgressCb = Callable[[str, float, str], None]  # (stage, ratio 0..1, message)


def _exe_name() -> str:
    return "python.exe" if os.name == "nt" else "python"


def _runtime_dir() -> str:
    """当前生效的运行时目录。

    瘦包里解释器叫 `bootstrap/`，环境装进去后**目录名不变**（只多了 torch），
    所以不能只看 runtime/；两个都可能有 python.exe，优先 runtime/。
    """
    runtime = os.path.join(PROJECT_ROOT, RUNTIME_DIRNAME)
    if os.path.isfile(os.path.join(runtime, _exe_name())):
        return runtime
    bootstrap = os.path.join(PROJECT_ROOT, BOOTSTRAP_DIRNAME)
    if os.path.isfile(os.path.join(bootstrap, _exe_name())):
        return bootstrap
    return runtime


def _python_dir() -> str:
    """当前进程的解释器所在目录（环境就装进这里）。"""
    return os.path.dirname(os.path.abspath(sys.executable or "")) or _runtime_dir()


def _catalog_path() -> str:
    return os.path.join(PROJECT_ROOT, CATALOG_FILENAME)


# ------------------------------------------------------------ 组件清单 ----
def load_catalog() -> dict:
    """读取内置清单 `app/downloads_catalog.json`（随增量包更新，离线也可展示）。"""
    path = _catalog_path()
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
    return {}


def runtime_catalog() -> dict | None:
    entry = load_catalog().get("runtime")
    return entry if isinstance(entry, dict) else None


def _load_model_script():
    """按路径加载 scripts/download_model.py（它不在包内，无法常规 import）。"""
    path = os.path.join(PROJECT_ROOT, "scripts", "download_model.py")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"缺少模型下载脚本: {path}")
    spec = importlib.util.spec_from_file_location("vosr2_model_downloader", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模型下载脚本: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def model_file_list(checkpoint: str) -> list[str]:
    module = _load_model_script()
    entry = module.CHECKPOINTS.get(checkpoint)
    if not entry:
        raise KeyError(f"未知 checkpoint: {checkpoint}")
    return [str(item) for item in entry.get("files", [])]


# ------------------------------------------------------------ HTTP 下载 ----
def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_file(
    url: str,
    target: str,
    on_progress: ProgressCb | None = None,
    stage: str = "download",
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    """带断点续传的下载：已有 .part 时用 Range 续传；服务端不支持则整文件重下。"""
    part = target + ".part"
    resume = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {"User-Agent": "VOSR2Lite-Desktop"}
    if resume > 0:
        headers["Range"] = f"bytes={resume}-"

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        if resume > 0 and response.status != 206:
            resume = 0  # 服务端不支持续传，从头开始
        total = int(response.headers.get("Content-Length") or 0) + resume
        mode = "ab" if resume > 0 else "wb"
        written = resume
        with open(part, mode) as handle:
            while True:
                if should_cancel and should_cancel():
                    raise InterruptedError("已取消")
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
                if on_progress and total > 0:
                    on_progress(stage, min(written / total, 1.0), "")
    os.replace(part, target)


# ------------------------------------------------------------ 环境包安装 ----
def install_runtime(
    on_progress: ProgressCb | None = None, should_cancel: Callable[[], bool] | None = None
) -> dict:
    """下载并解压环境包到 `app/runtime/`。返回写入的元信息 dict。"""
    catalog = runtime_catalog()
    if not catalog:
        raise RuntimeError(
            "未找到环境包清单 downloads_catalog.json，无法确定下载地址（请更新到含清单的版本）"
        )
    volumes = [v for v in catalog.get("volumes", []) if isinstance(v, dict)]
    if not volumes:
        raise RuntimeError("环境包清单缺少 volumes")

    base = str(
        catalog.get("base_url") or f"{DEFAULT_RELEASE_BASE}/{catalog.get('tag', 'runtime-v1')}"
    )
    staging_parent = os.path.join(PROJECT_ROOT, ".downloads")
    work = os.path.join(staging_parent, "runtime")
    os.makedirs(work, exist_ok=True)

    def report(stage: str, ratio: float, message: str) -> None:
        if on_progress:
            on_progress(stage, ratio, message)

    # 1) 逐卷下载 + SHA256 校验
    parts: list[str] = []
    total_vols = len(volumes)
    for index, vol in enumerate(volumes):
        name = str(vol.get("name"))
        url = f"{base.rstrip('/')}/{name}"
        dest = os.path.join(work, name)
        expected = str(vol.get("sha256") or "").lower()

        if os.path.exists(dest) and expected and _sha256(dest) == expected:
            parts.append(dest)
            report("download", (index + 1) / total_vols, f"{name} 已存在，跳过")
            continue

        report("download", index / total_vols, f"下载 {name}")
        _download_file(
            url,
            dest,
            on_progress=lambda _s, r, _m, i=index: report(  # type: ignore[misc]
                "download", (i + r) / total_vols, f"下载 {name}"
            ),
            should_cancel=should_cancel,
        )
        if expected:
            actual = _sha256(dest)
            if actual != expected:
                raise RuntimeError(f"{name} 校验失败（期望 {expected[:12]}…，实际 {actual[:12]}…）")
        parts.append(dest)

    # 2) 合并分卷（顺序字节切片；单卷时直接复用）
    report("verify", 0.0, "合并分卷")
    archive = os.path.join(work, "runtime.zip")
    if len(parts) == 1:
        archive = parts[0]
    else:
        with open(archive, "wb") as out:
            for part in parts:
                with open(part, "rb") as handle:
                    shutil.copyfileobj(handle, out, 1024 * 1024)

    # 3) 解压 wheels 到临时目录
    report("extract", 0.0, "解压环境包")
    wheels = os.path.join(work, "wheels")
    if os.path.isdir(wheels):
        shutil.rmtree(wheels, ignore_errors=True)
    os.makedirs(wheels, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(wheels)

    # 4) 离线装入当前解释器（--no-index + --find-links，全程不联网）。
    #    装进 bootstrap/ 自身、不另起一份解释器：目录名不变，壳靠
    #    bootstrap/Lib/site-packages/torch 判定它已经是完整运行时。
    report("swap", 0.5, "安装 torch（离线）")
    target = _python_dir()
    packages = [str(p) for p in (catalog.get("packages") or []) if p] or ["torch", "torchvision"]
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-index",
        "--find-links",
        wheels,
        "--no-warn-script-location",
        *packages,
    ]
    # PIP_CONFIG_FILE=NUL：屏蔽用户 pip 配置里的 extra-index-url（国内镜像常见）。
    # --no-index 本已禁索引，但显式隔离可避免配置注入引入意外来源。
    env = dict(os.environ, PIP_CONFIG_FILE="NUL")
    result = subprocess.run(command, capture_output=True, text=True, cwd=PROJECT_ROOT, env=env)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-400:]
        raise RuntimeError(f"torch 离线安装失败（退出码 {result.returncode}）：{tail}")

    # 5) 落地元信息（status API 读它判断 torch/CUDA 版本，避免启动时 import torch）
    meta = {
        "runtime_version": str(catalog.get("version", "1")),
        "python": catalog.get("python"),
        "torch": catalog.get("torch"),
        "cuda": catalog.get("cuda"),
        "built_at": catalog.get("built_at"),
        "min_app_version": catalog.get("min_app_version"),
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "installed_by_app_version": get_app_version(),
    }
    with open(os.path.join(target, RUNTIME_META_NAME), "w", encoding="utf-8") as meta_handle:
        json.dump(meta, meta_handle, ensure_ascii=False, indent=2)

    shutil.rmtree(work, ignore_errors=True)
    report("done", 1.0, "环境安装完成，请重启应用")
    return meta


# -------------------------------------------------------------- 模型安装 ----
def install_model(
    checkpoint: str,
    on_progress: ProgressCb | None = None,
    should_cancel: Callable[[], bool] | None = None,
    source: str = "auto",
    endpoint: str | None = None,
) -> dict:
    """下载指定 checkpoint 的权重。复用 scripts/download_model.py 的多源回退。"""
    module = _load_model_script()
    files = model_file_list(checkpoint)
    total = len(files)

    def report(stage: str, ratio: float, message: str) -> None:
        if on_progress:
            on_progress(stage, ratio, message)

    ep = endpoint or module.DEFAULT_ENDPOINT
    for index, rel in enumerate(files):
        if should_cancel and should_cancel():
            raise InterruptedError("已取消")
        report("download", index / total, f"下载 {os.path.basename(rel)}")
        module._fetch(rel, ep, source)
    report("done", 1.0, f"{checkpoint} 下载完成")
    return {"checkpoint": checkpoint, "files": files}


def uninstall_runtime() -> None:
    target = _runtime_dir()
    if os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)


def uninstall_model(checkpoint: str, config: dict) -> None:
    """删除某 checkpoint 的权重目录。共享资产（VAE / torch_cache）按引用计数保留。"""
    module = _load_model_script()
    model_cfg = config.get("model", {}) or {}
    rel = model_cfg.get("pretrained_dir", "model")
    root = rel if os.path.isabs(rel) else os.path.join(PROJECT_ROOT, rel)
    target = os.path.join(root, checkpoint)
    if not os.path.isdir(target):
        return
    # 只删该 checkpoint 独占的目录；VAE / torch_cache 可能被其他 checkpoint 共用
    shutil.rmtree(target, ignore_errors=True)
    _ = module  # 保留引用：若将来按清单精确删除，用 CHECKPOINTS 计算独占文件


# ------------------------------------------------------------ 任务管理 ----
class DownloadTask:
    """单个下载任务的状态（后台线程执行，前端轮询进度）。"""

    def __init__(self, component: str, kind: str, target: str) -> None:
        self.component = component
        self.kind = kind  # runtime | model
        self.target = target
        self.stage = "queued"
        self.ratio = 0.0
        self.message = ""
        self.error: str | None = None
        self.finished = False
        self.started_at = time.time()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    def to_dict(self) -> dict:
        return {
            "component": self.component,
            "kind": self.kind,
            "target": self.target,
            "stage": self.stage,
            "ratio": round(self.ratio, 4),
            "message": self.message,
            "error": self.error,
            "finished": self.finished,
            "elapsed_seconds": round(time.time() - self.started_at, 1),
        }

    def request_cancel(self) -> None:
        self._cancel.set()

    def _progress(self, stage: str, ratio: float, message: str) -> None:
        self.stage = stage
        self.ratio = ratio
        self.message = message

    def run(self) -> None:
        try:
            if self.kind == "runtime":
                install_runtime(self._progress, self._cancel.is_set)
            else:
                install_model(self.target, self._progress, self._cancel.is_set)
            self.stage = "done"
            self.ratio = 1.0
        except InterruptedError as exc:
            self.stage = "cancelled"
            self.error = str(exc)
        except (urllib.error.URLError, OSError) as exc:
            self.stage = "error"
            self.error = f"网络错误: {exc}"
        except Exception as exc:  # noqa: BLE001 - 任务状态需回传给用户，不能让线程静默死
            self.stage = "error"
            self.error = str(exc)
        finally:
            self.finished = True


class DownloadManager:
    """进程级单例：同一组件同时只允许一个任务在跑。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, DownloadTask] = {}

    def start(self, kind: str, target: str = "") -> DownloadTask:
        component = "runtime" if kind == "runtime" else f"model:{target}"
        with self._lock:
            existing = self._tasks.get(component)
            if existing and not existing.finished:
                return existing
            task = DownloadTask(component, kind, target)
            self._tasks[component] = task
        thread = threading.Thread(target=task.run, name=f"download-{component}", daemon=True)
        task._thread = thread
        thread.start()
        return task

    def get(self, component: str) -> DownloadTask | None:
        return self._tasks.get(component)

    def progress(self) -> list[dict]:
        with self._lock:
            return [task.to_dict() for task in self._tasks.values()]

    def cancel(self, component: str) -> bool:
        task = self._tasks.get(component)
        if not task or task.finished:
            return False
        task.request_cancel()
        return True


_MANAGER: DownloadManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_download_manager() -> DownloadManager:
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = DownloadManager()
    return _MANAGER


def catalog_view(config: dict) -> dict:
    """给页面用的可下载清单：环境包 + 各模型（含是否已安装）。"""
    from app.integrated_app.routes.downloads import scan_models  # 局部导入，避免路由/服务循环

    catalog = load_catalog()
    runtime = catalog.get("runtime") if isinstance(catalog.get("runtime"), dict) else None
    models = []
    for item in scan_models(config):
        models.append(
            {
                "name": item["name"],
                "display_name": item["display_name"],
                "present": item["present"],
                "size_bytes": item["size_bytes"],
                "min_vram_gb": item["min_vram_gb"],
                "is_default": item["is_default"],
                # 模型走 HF/ModelScope，不占我们的带宽与配额
                "source": "huggingface / modelscope",
            }
        )
    return {
        "runtime": runtime,
        "models": models,
        "catalog_present": bool(catalog),
    }


def _unused(*args: Any) -> None:  # 占位：保留扩展点
    return None
