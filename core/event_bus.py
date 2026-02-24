"""In-process asyncio pub/sub hub.

Usage:
    bus = EventBus()

    # Publisher:
    await bus.publish("CALL_CREATED", {"call_id": "abc"})

    # Subscriber:
    async with bus.subscribe("CALL_CREATED") as queue:
        msg = await queue.get()

Scale path: replace this module with a Redis pub/sub adapter
keeping the same publish/subscribe interface.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from core.models import EventBusMessage


class EventBus:
    def __init__(self) -> None:
        # topic -> list of subscriber queues
        self._subscribers: dict[str, list[asyncio.Queue[EventBusMessage]]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def publish(self, topic: str, payload: dict) -> None:
        message = EventBusMessage(type=topic, payload=payload, ts=time.time())
        async with self._lock:
            queues = list(self._subscribers.get(topic, []))
        for q in queues:
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                pass  # drop for slow consumers; they'll get a snapshot on reconnect

    @asynccontextmanager
    async def subscribe(self, *topics: str) -> AsyncGenerator[asyncio.Queue[EventBusMessage], None]:
        queue: asyncio.Queue[EventBusMessage] = asyncio.Queue(maxsize=256)
        async with self._lock:
            for topic in topics:
                self._subscribers[topic].append(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                for topic in topics:
                    try:
                        self._subscribers[topic].remove(queue)
                    except ValueError:
                        pass


# Module-level singleton shared across both FastAPI apps within the same process
bus = EventBus()
