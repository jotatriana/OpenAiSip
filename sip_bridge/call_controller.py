"""Thin async HTTP client wrapping OpenAI Realtime Call REST endpoints."""
from __future__ import annotations

import logging

import httpx

from config.settings import get_settings
from core.event_bus import bus
from core.models import Call, CallState, Topic
from core.state_store import store

log = logging.getLogger(__name__)

_BASE = "https://api.openai.com/v1/realtime/calls"


def _headers() -> dict[str, str]:
    s = get_settings()
    return {
        "Authorization": f"Bearer {s.openai_api_key}",
        "Content-Type": "application/json",
        "OpenAI-Beta": "realtime=v1",
    }


async def accept(call_id: str, session_config: dict) -> dict:
    """Accept an incoming call and start a Realtime session."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{_BASE}/{call_id}/accept",
            headers=_headers(),
            json=session_config,
        )
        resp.raise_for_status()
        data = resp.json() if resp.content else {}

    call = await store.get_call(call_id)
    if call:
        from datetime import datetime, timezone
        call.state = CallState.ACTIVE
        call.answered_at = datetime.now(timezone.utc)
        await store.update_call(call)
        await bus.publish(Topic.CALL_UPDATED, call.model_dump(mode="json"))
        log.info("Call accepted", extra={"call_id": call_id})
        from db.repository import emit_call_event, EVENT_CALL_ANSWERED
        emit_call_event(call_id, EVENT_CALL_ANSWERED)

    return data


async def reject(call_id: str, sip_status_code: int = 603) -> None:
    """Reject an incoming call."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{_BASE}/{call_id}/reject",
            headers=_headers(),
            json={"sip_status_code": sip_status_code},
        )
        resp.raise_for_status()

    call = await store.get_call(call_id)
    if call:
        call.state = CallState.FAILED
        call.hangup_cause = "rejected"
        await store.update_call(call)
        await bus.publish(Topic.CALL_ENDED, call.model_dump(mode="json"))
        log.info("Call rejected", extra={"call_id": call_id})
        from db.repository import emit_call_event, EVENT_CALL_REJECTED
        emit_call_event(call_id, EVENT_CALL_REJECTED, {"sip_status_code": sip_status_code})


async def refer(call_id: str, target_uri: str) -> None:
    """Transfer a call via SIP REFER to target_uri (tel: or sip: URI)."""
    log.debug("Sending SIP REFER to %s", target_uri, extra={"call_id": call_id})
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{_BASE}/{call_id}/refer",
            headers=_headers(),
            json={"target_uri": target_uri},
        )
        log.debug("REFER response: %s", resp.status_code, extra={"call_id": call_id})
        resp.raise_for_status()

    call = await store.get_call(call_id)
    if call:
        call.state = CallState.TRANSFERRING
        call.hangup_cause = "transferred"
        await store.update_call(call)
        await bus.publish(Topic.CALL_UPDATED, call.model_dump(mode="json"))
        log.info("Call transferred", extra={"call_id": call_id, "target": target_uri})
        from db.repository import emit_call_event, EVENT_ESCALATED
        emit_call_event(call_id, EVENT_ESCALATED, {"target_uri": target_uri})


async def hangup(call_id: str) -> None:
    """Hang up an active call."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{_BASE}/{call_id}/hangup",
            headers=_headers(),
        )
        resp.raise_for_status()

    call = await store.get_call(call_id)
    if call:
        from datetime import datetime, timezone
        call.state = CallState.ENDED
        call.ended_at = datetime.now(timezone.utc)
        call.hangup_cause = "normal"
        if call.answered_at:
            call.duration_seconds = (call.ended_at - call.answered_at).total_seconds()
        await store.update_call(call)
        await bus.publish(Topic.CALL_ENDED, call.model_dump(mode="json"))
        log.info("Call hung up", extra={"call_id": call_id})
        from db.repository import emit_call_event, EVENT_CALL_ENDED
        emit_call_event(call_id, EVENT_CALL_ENDED, {"hangup_cause": "normal"})
