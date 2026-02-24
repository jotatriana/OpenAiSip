from fastapi import APIRouter, Depends, HTTPException

from dashboard.auth import require_auth
from core.state_store import store

router = APIRouter(prefix="/api/calls", tags=["calls"])


@router.get("")
async def list_calls(_: str = Depends(require_auth)) -> list[dict]:
    calls = await store.get_all_calls()
    return [c.model_dump(mode="json") for c in calls]


@router.get("/{call_id}")
async def get_call(call_id: str, _: str = Depends(require_auth)) -> dict:
    call = await store.get_call(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    return call.model_dump(mode="json")
