"""FastAPI app for the operator dashboard (REST API + WebSocket events)."""
from __future__ import annotations

import asyncio

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

from dashboard.auth import ws_require_auth
from dashboard.ws_manager import ws_manager
from dashboard import health_collector
from dashboard.routes import calls, config, health, logs, operator, tokens

app = FastAPI(title="Operator Dashboard", version="1.0.0")

# REST routes
app.include_router(calls.router)
app.include_router(tokens.router)
app.include_router(health.router)
app.include_router(logs.router)
app.include_router(config.router)
app.include_router(operator.router)

# Static frontend
import os
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(os.path.join(_static_dir, "index.html"))


# Primary dashboard WebSocket endpoint
@app.websocket("/ws/events")
async def dashboard_ws(websocket: WebSocket) -> None:
    if not await ws_require_auth(websocket):
        return
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except Exception:
        pass
    finally:
        await ws_manager.disconnect(websocket)


@app.on_event("startup")
async def startup() -> None:
    await ws_manager.start()
    asyncio.create_task(health_collector.run())


@app.on_event("shutdown")
async def shutdown() -> None:
    await ws_manager.stop()
