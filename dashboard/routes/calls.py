from fastapi import APIRouter, Depends, HTTPException

from dashboard.auth import require_auth
from core.state_store import store
from sip_bridge import call_controller
from sip_bridge.session_manager import get_session

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


@router.get("/{call_id}/transcript")
async def get_call_transcript(call_id: str, _: str = Depends(require_auth)) -> list[dict]:
    call = await store.get_call(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    from db import repository
    return await repository.get_transcript(call_id)


@router.post("/{call_id}/hangup")
async def hangup_call(call_id: str, _: str = Depends(require_auth)) -> dict:
    call = await store.get_call(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    await call_controller.hangup(call_id)
    sm = get_session(call_id)
    if sm:
        await sm.close()
    return {"status": "ok", "call_id": call_id}


@router.post("/{call_id}/escalate")
async def force_escalate(call_id: str, _: str = Depends(require_auth)) -> dict:
    """Force-transfer an active call to the human agent queue via SIP REFER."""
    from config.settings import get_settings
    from core.models import CallState
    call = await store.get_call(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    if call.state not in (CallState.RINGING, CallState.ACTIVE):
        raise HTTPException(status_code=409, detail=f"Call is not active (state: {call.state.value})")
    target = get_settings().human_agent_sip_uri
    await call_controller.refer(call_id, target)
    sm = get_session(call_id)
    if sm:
        await sm.close()
    return {"status": "transferring", "call_id": call_id, "target": target}
