"""Background task that collects channel health metrics every N seconds."""
from __future__ import annotations

import asyncio
import logging

from config.settings import get_settings
from core.event_bus import bus
from core.models import Topic
from core.state_store import store

log = logging.getLogger(__name__)


async def run() -> None:
    """Run continuously until cancelled."""
    s = get_settings()
    while True:
        try:
            health = await store.build_channel_health(s.sip_stale_threshold_seconds)
            await bus.publish(Topic.HEALTH_UPDATE, health.model_dump(mode="json"))
        except Exception as exc:
            log.error("Health collector error: %s", exc)
        await asyncio.sleep(s.health_poll_interval_seconds)
