from fastapi import APIRouter, Depends, HTTPException

from dashboard.auth import require_auth
from core.state_store import store

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


@router.get("/summary")
async def token_summary(_: str = Depends(require_auth)) -> dict:
    agg = await store.get_global_tokens()
    return agg.model_dump(mode="json")


@router.get("/{call_id}")
async def call_tokens(call_id: str, _: str = Depends(require_auth)) -> dict:
    agg = await store.get_call_tokens(call_id)
    if not agg:
        raise HTTPException(status_code=404, detail="Call not found")
    return agg.model_dump(mode="json")
