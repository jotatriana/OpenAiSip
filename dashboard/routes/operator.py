"""Operator control endpoints: maintenance mode, CDRs, cost summary."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from dashboard.auth import require_auth
from core.state_store import store

router = APIRouter(prefix="/api/operator", tags=["operator"])


# ── Maintenance mode ──────────────────────────────────────────────────────────

@router.get("/maintenance")
async def get_maintenance(_: str = Depends(require_auth)) -> dict:
    enabled = await store.is_maintenance_mode()
    return {"maintenance_mode": enabled}


@router.post("/maintenance")
async def set_maintenance(enabled: bool, _: str = Depends(require_auth)) -> dict:
    await store.set_maintenance_mode(enabled)
    return {"maintenance_mode": enabled}


# ── CDR endpoints ─────────────────────────────────────────────────────────────

@router.get("/cdrs")
async def list_cdrs(
    limit: int = Query(default=100, le=500),
    _: str = Depends(require_auth),
) -> list[dict]:
    from db import repository
    return await repository.get_recent_cdrs(limit=limit)


@router.get("/cdrs/{call_id}")
async def get_cdr(call_id: str, _: str = Depends(require_auth)) -> dict:
    from db import repository
    cdr = await repository.get_cdr(call_id)
    if not cdr:
        raise HTTPException(status_code=404, detail="CDR not found")
    return cdr


# ── Per-call event timeline ───────────────────────────────────────────────────

@router.get("/events/{call_id}")
async def get_call_events(call_id: str, _: str = Depends(require_auth)) -> list[dict]:
    """Return the full event timeline for a call in chronological order."""
    from db import repository
    return await repository.get_call_events(call_id)


# ── Warm handoff context ──────────────────────────────────────────────────────

@router.get("/handoff/{call_id}")
async def get_handoff_context(call_id: str, _: str = Depends(require_auth)) -> dict:
    """Return escalation context for an agent desktop warm handoff lookup."""
    from db import repository
    ctx = await repository.get_escalation_context(call_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="No handoff context for this call")
    return ctx


# ── Cost summary ──────────────────────────────────────────────────────────────

@router.get("/cost/today")
async def cost_today(_: str = Depends(require_auth)) -> dict:
    """Return in-memory daily cost accumulator (resets on process restart)."""
    daily_cost = await store.get_daily_cost_usd()
    from config.settings import get_settings
    budget = get_settings().daily_budget_usd
    return {
        "daily_cost_usd": round(daily_cost, 6),
        "daily_budget_usd": budget,
        "budget_remaining_usd": round(max(0.0, budget - daily_cost), 6) if budget > 0 else None,
        "budget_exceeded": (daily_cost >= budget) if budget > 0 else False,
    }
