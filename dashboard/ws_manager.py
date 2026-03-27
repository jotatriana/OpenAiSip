"""WebSocket fan-out hub for the operator dashboard.

- Broadcasts all event bus messages to connected dashboard clients
- Sends a SNAPSHOT on new connection (current state + recent logs)
- Drops stalled clients after a 2-second send timeout
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

from config.settings import get_settings
from core.event_bus import bus
from core.models import Topic
from core.state_store import store

log = logging.getLogger(__name__)

ALL_TOPICS = [
    Topic.CALL_CREATED,
    Topic.CALL_UPDATED,
    Topic.CALL_ENDED,
    Topic.TOKEN_USAGE,
    Topic.HEALTH_UPDATE,
    Topic.LOG_ENTRY,
    Topic.TRANSCRIPT_TURN,
    Topic.BUDGET_ALERT,
    Topic.CALL_EVENT,
]


class WSManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._broadcast_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the background broadcast loop. Call once at app startup."""
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())

    async def stop(self) -> None:
        if self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new dashboard WebSocket connection."""
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

        # Send snapshot of current state
        s = get_settings()
        try:
            snapshot = await store.snapshot(s.sip_stale_threshold_seconds)
            await asyncio.wait_for(
                websocket.send_json({"type": Topic.SNAPSHOT, "payload": snapshot, "ts": 0}),
                timeout=2.0,
            )
        except Exception as exc:
            log.warning("Failed to send snapshot: %s", exc)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    async def _broadcast_loop(self) -> None:
        """Subscribe to all event bus topics and fan-out to all connected clients."""
        async with bus.subscribe(*ALL_TOPICS) as queue:
            while True:
                try:
                    message = await queue.get()
                    await self._broadcast(message.model_dump(mode="json"))
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    log.error("Broadcast loop error: %s", exc)

    async def _broadcast(self, payload: dict) -> None:
        async with self._lock:
            connections = list(self._connections)

        dead: list[WebSocket] = []
        for ws in connections:
            try:
                await asyncio.wait_for(ws.send_json(payload), timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)


# Module-level singleton
ws_manager = WSManager()
