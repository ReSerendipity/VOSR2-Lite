#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""全局 SSE 事件端点（模型状态等广播事件）。"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from loguru import logger

from app.integrated_app.event_bus import HEARTBEAT_INTERVAL, event_bus, format_sse

router = APIRouter(tags=["SSE事件流"])


@router.get("/api/sse/events")
async def sse_events(request: Request):
    queue = await event_bus.subscribe()

    async def event_stream():
        last_heartbeat = time.time()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=5.0)
                    yield format_sse(event.get("event", "unknown"), event.get("data", {}))
                except TimeoutError:
                    pass
                now = time.time()
                if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                    yield format_sse("heartbeat", {"ts": int(now)})
                    last_heartbeat = now
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(f"SSE stream error: {exc}")
        finally:
            await event_bus.unsubscribe(queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
