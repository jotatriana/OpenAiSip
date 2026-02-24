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


async def _run_both() -> None:
    # Initialise DB tables and seed sample data
    from db.engine import init_db
    from db.seed import seed
    await init_db()
    await seed()

    sip_cfg  = uvicorn.Config(sip_app,  host="0.0.0.0", port=8000, log_level="info")
    dash_cfg = uvicorn.Config(dash_app, host="0.0.0.0", port=8001, log_level="info")
    await asyncio.gather(
        uvicorn.Server(sip_cfg).serve(),
        uvicorn.Server(dash_cfg).serve(),
    )


if __name__ == "__main__":
    asyncio.run(_run_both())
