"""FastAPI app for the SIP bridge (webhook receiver + call management REST API)."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from sip_bridge import webhook_handler

app = FastAPI(title="SIP Bridge", version="1.0.0")


@app.post("/webhooks/sip")
async def sip_webhook(request: Request) -> JSONResponse:
    result = await webhook_handler.handle_incoming(request)
    return JSONResponse(result)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
