"""Entrypoint: runs both FastAPI apps in the same process so they share
the in-process EventBus and StateStore singletons.

SIP bridge:  http://host:8000  (webhook + SIP call management)
Dashboard:   http://host:8001  (operator dashboard + WebSocket)

Run:
    python main.py
"""
import asyncio
import logging

import uvicorn

from core.logger import setup_logging
from sip_bridge.app import app as sip_app
from dashboard.app import app as dash_app

setup_logging(logging.INFO)

__all__ = ["sip_app", "dash_app"]


async def _transcript_retention_loop() -> None:
    """Delete transcript turns older than TRANSCRIPT_RETENTION_DAYS once per day."""
    log = logging.getLogger(__name__)
    while True:
        await asyncio.sleep(86400)  # 24 hours
        try:
            from config.settings import get_settings
            from db import repository
            deleted = await repository.cleanup_old_transcripts(get_settings().transcript_retention_days)
            if deleted:
                log.info("Transcript retention: deleted %d turns", deleted)
        except Exception as exc:
            log.error("Transcript retention cleanup failed: %s", exc)


async def _run_both() -> None:
    log = logging.getLogger(__name__)

    # Initialise DB tables and seed sample data
    from db.engine import init_db
    from db.seed import seed
    await init_db()
    await seed()

    # Pre-populate StateStore with recent CDRs so the dashboard shows historical
    # calls immediately after a restart without waiting for new calls to arrive.
    from config.settings import get_settings
    from core.state_store import store
    loaded = await store.load_recent_cdrs(get_settings().cdr_history_limit)
    if loaded:
        log.info("Loaded %d historical CDR(s) into snapshot", loaded)

    asyncio.create_task(_transcript_retention_loop())

    sip_cfg  = uvicorn.Config(sip_app,  host="0.0.0.0", port=8000, log_level="info")
    dash_cfg = uvicorn.Config(dash_app, host="0.0.0.0", port=8001, log_level="info")
    await asyncio.gather(
        uvicorn.Server(sip_cfg).serve(),
        uvicorn.Server(dash_cfg).serve(),
    )


if __name__ == "__main__":
    asyncio.run(_run_both())
