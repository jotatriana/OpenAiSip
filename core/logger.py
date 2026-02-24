"""Structured logger that hooks into the event bus and state store ring buffer."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from core.models import LogEntry, Topic


class EventBusLogHandler(logging.Handler):
    """Logging handler that publishes every record to the event bus and state store."""

    def __init__(self) -> None:
        super().__init__()
        self._loop: asyncio.AbstractEventLoop | None = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            loop = self._get_loop()
            if loop is None or not loop.is_running():
                return

            # Import here to avoid circular imports at module load time
            from core.event_bus import bus
            from core.state_store import store

            seq = store.next_log_seq()
            entry = LogEntry(
                sequence_id=seq,
                timestamp=datetime.fromtimestamp(record.created, tz=timezone.utc),
                level=record.levelname,
                logger_name=record.name,
                call_id=getattr(record, "call_id", None),
                message=self.format(record),
                extra={k: v for k, v in record.__dict__.items()
                       if k not in logging.LogRecord.__dict__ and not k.startswith("_")
                       and isinstance(v, (str, int, float, bool, type(None)))},
            )

            asyncio.run_coroutine_threadsafe(
                self._publish(bus, store, entry), loop
            )
        except Exception:
            self.handleError(record)

    async def _publish(self, bus: Any, store: Any, entry: LogEntry) -> None:
        await store.append_log(entry)
        await bus.publish(Topic.LOG_ENTRY, entry.model_dump(mode="json"))

    def _get_loop(self) -> asyncio.AbstractEventLoop | None:
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            return None


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def setup_logging(level: int = logging.INFO) -> None:
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # Console handler
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(level)

    # Event bus handler
    bus_handler = EventBusLogHandler()
    bus_handler.setFormatter(fmt)
    bus_handler.setLevel(level)

    root = logging.getLogger()
    root.setLevel(level)

    # Avoid duplicate handlers on reload
    if not any(isinstance(h, EventBusLogHandler) for h in root.handlers):
        root.addHandler(bus_handler)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, EventBusLogHandler) for h in root.handlers):
        root.addHandler(console)
