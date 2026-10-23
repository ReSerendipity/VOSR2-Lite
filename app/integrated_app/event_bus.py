#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""SSE 事件总线（家族模式精简版）。

发布/订阅：每个订阅者一个 asyncio.Queue；publish 线程安全，
可从推理线程直接调用。用于模型状态等全局事件广播；
单任务进度走 routes/restore/task.py 的 SSE 端点（读 tasks 表快照）。
"""

from __future__ import annotations

import asyncio
import json
import time
import weakref
from typing import Any

from loguru import logger

HEARTBEAT_INTERVAL = 30


class EventBus:
    """线程安全的 SSE 发布/订阅总线。"""

    def __init__(self) -> None:
        self._subscribers: weakref.WeakSet[asyncio.Queue] = weakref.WeakSet()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    def publish(self, event_type: str, data: Any = None) -> None:
        """发布事件（线程安全，可从任意线程调用）。"""
        event = {
            "event": event_type,
            "data": data if data is not None else {},
            "timestamp": time.time(),
        }
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("SSE 订阅者队列已满，丢弃事件")
            except Exception:
                pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


event_bus = EventBus()


def format_sse(event_type: str, data: Any) -> str:
    """格式化为 SSE 消息字符串：event: <type>\\ndata: <json>\\n\\n"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
