"""Handles POST /webhooks/sip from OpenAI.

OpenAI uses Svix for webhook delivery. Signature verification:
  signed_content = webhook_id + "." + webhook_timestamp + "." + body
  expected       = base64(hmac-sha256(base64decode(secret), signed_content))
  header         = "webhook-signature: v1,<base64sig> [v1,<base64sig> ...]"

Validates signature, creates the Call record, and dispatches
the accept flow as a background task so we return 200 immediately.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import time

from fastapi import HTTPException, Request

from config.settings import get_settings
from core.event_bus import bus
from core.models import Call, Topic
from core.state_store import store

log = logging.getLogger(__name__)


async def handle_incoming(request: Request) -> dict:
    body = await request.body()

    # Signature validation — raises 401 on failure
    _verify_signature(request, body)

    try:
        payload = await request.json()
    except Exception as exc:
        log.warning("Failed to parse webhook body: %s", exc)
        return {"status": "ok"}  # return 200 to stop Svix retries

    event_type = payload.get("type")
    log.info("Webhook event: %s", event_type)
    log.debug(
        "RAW webhook — headers: %s\nRAW webhook — body: %s",
        dict(request.headers), json.dumps(payload, indent=2),
    )

    try:
        if event_type == "realtime.call.incoming":
            # Svix wraps the actual event data under "data"
            data = payload.get("data", payload)
            await _handle_call_incoming(data)
        else:
            log.info("Unhandled webhook event type: %s", event_type)
    except Exception as exc:
        # Always return 200 so OpenAI/Svix does not retry the webhook
        log.error("Error handling webhook event %s: %s", event_type, exc, exc_info=True)

    return {"status": "ok"}


def _parse_from_header(from_value: str) -> tuple[str, str]:
    """Extract display name and E.164 number from a SIP From header.

    Example input:
      "JULIO  TRIANA" <sip:+14372455896@72.39.247.111:18876>;tag=abc123
    Returns:
      ("Julio Triana", "+14372455896")
    """
    name = ""
    number = ""

    name_match = re.search(r'"([^"]+)"', from_value)
    if name_match:
        # Normalise: title-case and collapse extra spaces
        name = " ".join(name_match.group(1).split()).title()

    number_match = re.search(r'sip:(\+?[\d]+)@', from_value)
    if number_match:
        number = number_match.group(1)

    return name, number


async def _handle_call_incoming(data: dict) -> None:
    # call_id may be top-level or nested; handle both formats
    call_id = data.get("call_id") or data.get("id", "")
    sip_headers = data.get("sip_headers", {})

    # OpenAI may send sip_headers as a list of {"name": ..., "value": ...} objects
    if isinstance(sip_headers, list):
        sip_headers = {h["name"]: h["value"] for h in sip_headers if "name" in h and "value" in h}

    from_value = sip_headers.get("From", "")
    caller_name, caller_number = _parse_from_header(from_value)
    log.info("Caller: name=%r number=%r", caller_name, caller_number, extra={"call_id": call_id})

    call = Call(
        call_id=call_id,
        sip_call_id=sip_headers.get("Call-ID", ""),
        from_uri=from_value,
        to_uri=sip_headers.get("To", ""),
        caller_name=caller_name,
        caller_number=caller_number,
    )
    await store.create_call(call)
    await bus.publish(Topic.CALL_CREATED, call.model_dump(mode="json"))
    log.info("Incoming call", extra={"call_id": call_id})
    from db.repository import emit_call_event, EVENT_CALL_CREATED
    emit_call_event(call_id, EVENT_CALL_CREATED, {"caller_number": caller_number, "caller_name": caller_name})

    # Reject new calls in maintenance mode
    if await store.is_maintenance_mode():
        log.warning("Maintenance mode active — rejecting call with 503", extra={"call_id": call_id})
        asyncio.create_task(_reject_call(call_id))
        return

    s = get_settings()

    # Hard stop when daily budget is exhausted (budget=0 means no limit)
    if s.daily_budget_usd > 0:
        daily_cost = await store.get_daily_cost_usd()
        if daily_cost >= s.daily_budget_usd:
            log.warning(
                "Daily budget $%.2f exhausted (spent $%.4f) — rejecting call",
                s.daily_budget_usd, daily_cost,
                extra={"call_id": call_id},
            )
            asyncio.create_task(_reject_call(call_id))
            return

    # Reject new calls while circuit breaker is open
    if await store.is_circuit_open(
        s.circuit_breaker_failure_threshold,
        s.circuit_breaker_window_seconds,
        s.circuit_breaker_cooldown_seconds,
    ):
        log.warning("Circuit breaker open — rejecting call with 503", extra={"call_id": call_id})
        asyncio.create_task(_reject_call(call_id))
        return

    # Fire-and-forget: accept call without blocking webhook response
    asyncio.create_task(_accept_call(call_id))


async def _reject_call(call_id: str) -> None:
    """Reject a call with SIP 503 (circuit breaker open)."""
    from sip_bridge import call_controller
    try:
        await call_controller.reject(call_id, sip_status_code=503)
    except Exception as exc:
        log.error("Failed to reject call %s: %s", call_id, exc, extra={"call_id": call_id})


async def _accept_call(call_id: str) -> None:
    from sip_bridge import call_controller
    from sip_bridge import prompt_builder
    from core.models import ConvPhase

    try:
        call = await store.get_call(call_id)
        caller_name   = call.caller_name   if call else ""
        caller_number = call.caller_number if call else ""

        # Pre-lookup: identify caller by phone number before the session starts
        customer: dict | None = None
        if caller_number:
            try:
                from db import repository
                customer = await repository.find_customer(caller_number, "phone")
                if customer and call:
                    call.account_id = customer["account_id"]
                    # Use DB name if SIP headers didn't provide one
                    if not call.caller_name:
                        call.caller_name = customer["full_name"]
                        caller_name = call.caller_name
                    # Fetch service names so the greeting can mention them
                    try:
                        status = await repository.get_service_status(customer["account_id"])
                        call.service_names = [
                            s["service_type"] for s in status.get("services", [])
                            if s.get("service_type")
                        ]
                    except Exception as svc_exc:
                        log.warning("Service pre-fetch failed: %s", svc_exc, extra={"call_id": call_id})
                    await store.update_call(call)
                    log.info(
                        "Caller identified via caller ID: %s (%s) services=%s",
                        customer["full_name"], customer["account_id"], call.service_names,
                        extra={"call_id": call_id},
                    )
            except Exception as exc:
                log.warning("Caller ID pre-lookup failed: %s", exc, extra={"call_id": call_id})

        account_id    = call.account_id    if call else ""
        service_names = call.service_names if call else []
        session_config = prompt_builder.build(
            ConvPhase.GREETING,
            caller_name=caller_name,
            caller_number=caller_number,
            account_id=account_id,
            service_names=service_names,
        )
        session_data = await call_controller.accept(call_id, session_config)

        # Open the per-call WebSocket session
        from sip_bridge.session_manager import SessionManager
        sm = SessionManager(call_id)
        # Run session in background task — it runs until the call ends
        asyncio.create_task(sm.connect(session_data))

    except Exception as exc:
        log.error("Failed to accept call %s: %s", call_id, exc, extra={"call_id": call_id})
        call = await store.get_call(call_id)
        if call:
            from core.models import CallState
            call.state = CallState.FAILED
            call.hangup_cause = "error"
            await store.update_call(call)
            await bus.publish(Topic.CALL_ENDED, call.model_dump(mode="json"))


def _verify_timestamp(webhook_ts: str, tolerance_seconds: int) -> None:
    """Reject webhooks whose timestamp is missing or outside the tolerance window.

    The Svix scheme signs the timestamp specifically to enable replay protection;
    bounding its age (and rejecting far-future values) closes the replay window.
    """
    try:
        ts = int(webhook_ts)
    except (TypeError, ValueError):
        log.warning("Missing or non-numeric webhook-timestamp: %r", webhook_ts)
        raise HTTPException(status_code=401, detail="Invalid signature timestamp")

    age = abs(int(time.time()) - ts)
    if age > tolerance_seconds:
        log.warning(
            "Webhook timestamp outside tolerance (%ds > %ds) — possible replay",
            age, tolerance_seconds,
        )
        raise HTTPException(status_code=401, detail="Signature timestamp out of range")


def _verify_signature(request: Request, body: bytes) -> None:
    """Verify Svix webhook signature used by OpenAI.

    Signed content: {webhook-id}.{webhook-timestamp}.{raw_body}
    Key:            base64-decode(secret), stripping optional "whsec_" prefix
    Signature:      base64(hmac-sha256(key, signed_content))
    Header:         webhook-signature: v1,<b64> [v1,<b64> ...]  (space-separated)
    """
    s = get_settings()
    if not s.webhook_secret:
        # Fail closed: a missing secret must not silently disable verification on
        # a public endpoint. Only skip when explicitly opted in for development.
        if s.allow_unsigned_webhooks:
            log.warning(
                "WEBHOOK_SECRET is empty and ALLOW_UNSIGNED_WEBHOOKS is enabled — "
                "accepting webhook WITHOUT signature verification (development only)."
            )
            return
        log.error("WEBHOOK_SECRET is not configured — rejecting webhook (fail closed).")
        raise HTTPException(status_code=500, detail="Webhook verification not configured")

    webhook_id = request.headers.get("webhook-id", "")
    webhook_ts = request.headers.get("webhook-timestamp", "")
    sig_header = request.headers.get("webhook-signature", "")

    if not sig_header:
        log.warning("Missing webhook-signature header")
        raise HTTPException(status_code=401, detail="Missing signature")

    # Reject stale/forged timestamps before checking the signature. The signature
    # covers the timestamp, so a valid signature on an old timestamp is a replay.
    _verify_timestamp(webhook_ts, s.webhook_tolerance_seconds)

    # Decode the secret (strip optional whsec_ prefix, then base64-decode)
    secret = s.webhook_secret
    if secret.startswith("whsec_"):
        secret = secret[6:]
    try:
        secret_bytes = base64.b64decode(secret)
    except Exception:
        # If not valid base64, treat as raw bytes (plain secret)
        secret_bytes = secret.encode()

    # Build signed content
    signed_content = f"{webhook_id}.{webhook_ts}.".encode() + body

    # Compute expected signature
    expected_b64 = base64.b64encode(
        hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()
    ).decode()

    # Check against each signature in the header (space-separated, prefixed with "v1,")
    for sig in sig_header.split(" "):
        if sig.startswith("v1,"):
            if hmac.compare_digest(sig[3:], expected_b64):
                return  # Valid

    log.warning("Invalid webhook signature — header: %r", sig_header)
    raise HTTPException(status_code=401, detail="Invalid signature")
