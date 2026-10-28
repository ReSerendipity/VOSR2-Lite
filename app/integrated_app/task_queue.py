#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""单 Worker 任务队列（家族模式精简版）。

桌面/单 GPU 场景用一个 worker 串行执行推理，避免并发 OOM。
保留家族仓核心机制：有界队列快速拒绝、任务级超时、取消注册表、
worker 异常自动重启（最多 3 次）。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from loguru import logger

from app.integrated_app.exceptions import TaskQueueFullError

TaskFactory = Callable[[], Awaitable[None]]
CancelCallback = Callable[[], None]

MAX_WORKER_RESTARTS = 3


class TaskQueue:
    def __init__(self, *, maxsize: int = 100, task_timeout_seconds: int = 86400):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max(0, maxsize))
        self._cancelled_ids: set[str] = set()
        self._current_task_id: str | None = None
        self._worker_task: asyncio.Task | None = None
        self._running = False
        self._task_timeout = max(1, task_timeout_seconds)
        self._restart_count = 0
        self._cancel_callbacks: dict[str, CancelCallback] = {}

    # ── 生命周期 ────────────────────────────────────────────────────────────
    async def start(self) -> None:
        if self._worker_task is not None:
            return
        self._running = True
        self._restart_count = 0
        self._worker_task = asyncio.create_task(self._worker_guarded())
        logger.info("任务队列 worker 已启动")

    async def stop(self) -> None:
        self._running = False
        if self._worker_task is not None:
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(None)
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
            self._worker_task = None
        logger.info("任务队列 worker 已停止")

    # ── 提交 ────────────────────────────────────────────────────────────────
    async def submit(
        self, task_id: str, coro_factory: TaskFactory, on_cancel: CancelCallback | None = None
    ) -> None:
        """提交任务；coro_factory 在 worker 实际执行时才调用（避免悬挂协程）。"""
        if self._queue.full():
            raise TaskQueueFullError("任务队列已满，请稍后再试")
        self._queue.put_nowait((task_id, coro_factory, on_cancel))
        logger.info(f"任务已入队: {task_id} (队列长度 {self._queue.qsize()})")

    def request_cancel(self, task_id: str) -> None:
        """请求取消：运行中的调用 on_cancel 让推理线程协作退出。"""
        self._cancelled_ids.add(task_id)
        callback = self._cancel_callbacks.pop(task_id, None)
        if callback:
            with contextlib.suppress(Exception):
                callback()

    def is_cancelled(self, task_id: str) -> bool:
        return task_id in self._cancelled_ids

    def current_task_id(self) -> str | None:
        return self._current_task_id

    def stats(self) -> dict:
        """队列运行状态（系统状态页展示用）。"""
        worker = self._worker_task
        return {
            "pending": self._queue.qsize(),
            "capacity": self._queue.maxsize or 0,
            "current_task_id": self._current_task_id,
            "worker_running": bool(worker and not worker.done()),
        }

    # ── worker ──────────────────────────────────────────────────────────────
    async def _worker_guarded(self) -> None:
        while self._running:
            try:
                await self._worker()
                self._restart_count = 0
                break
            except asyncio.CancelledError:
                break
            except Exception:
                self._restart_count += 1
                if self._restart_count > MAX_WORKER_RESTARTS:
                    logger.error("worker 连续异常退出超过 3 次，放弃重启")
                    break
                logger.exception(f"worker 异常，第 {self._restart_count} 次重启")
                await asyncio.sleep(1.0)

    async def _worker(self) -> None:
        while self._running:
            item = await self._queue.get()
            if item is None:  # 哨兵：停止
                return
            task_id, coro_factory, on_cancel = item
            if task_id in self._cancelled_ids:
                self._cancelled_ids.discard(task_id)
                self._cancel_callbacks.pop(task_id, None)
                logger.info(f"任务在排队期间被取消，跳过执行: {task_id}")
                continue
            self._current_task_id = task_id
            if on_cancel:
                self._cancel_callbacks[task_id] = on_cancel
            try:
                await asyncio.wait_for(coro_factory(), timeout=self._task_timeout)
            except asyncio.TimeoutError:
                logger.error(f"任务执行超时（{self._task_timeout}s）: {task_id}")
            except asyncio.CancelledError:
                logger.info(f"任务被取消: {task_id}")
                raise
            except Exception:
                logger.exception(f"任务执行失败: {task_id}")
            finally:
                self._current_task_id = None
                self._cancel_callbacks.pop(task_id, None)
                self._cancelled_ids.discard(task_id)
