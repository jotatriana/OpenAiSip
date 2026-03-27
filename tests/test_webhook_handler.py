"""Tests for webhook handler: signature validation and call creation."""
import hashlib
import hmac
import json
import pytest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sip_bridge.app import app


def _make_svix_sig(body: bytes, secret_bytes: bytes, webhook_id: str = "wh_123", timestamp: str = "1234567890") -> dict:
    """Build Svix-style webhook headers with a valid HMAC-SHA256 signature.

    secret_bytes must be the raw key bytes (same as base64.b64decode(whsec_part)).
    """
    import base64
    signed_content = f"{webhook_id}.{timestamp}.".encode() + body
    sig = base64.b64encode(
        hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()
    ).decode()
    return {
        "webhook-id": webhook_id,
        "webhook-timestamp": timestamp,
        "webhook-signature": f"v1,{sig}",
    }


PAYLOAD = {
    "type": "realtime.call.incoming",
    "call_id": "test-call-abc",
    "sip_headers": {
        "From": "sip:+12025551234@pstn.twilio.com",
        "To": "sip:proj123@sip.api.openai.com",
        "Call-ID": "abc123@twilio",
    },
}


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def test_webhook_missing_signature_with_secret(client):
    with patch("sip_bridge.webhook_handler.get_settings") as mock_s:
        mock_s.return_value.webhook_secret = "whsec_c29tZXNlY3JldA=="
        body = json.dumps(PAYLOAD).encode()
        resp = client.post("/webhooks/sip", content=body,
                           headers={"Content-Type": "application/json"})
        assert resp.status_code == 401


def test_webhook_valid_signature(client):
    import base64
    # Simulate a real Svix whsec_ secret: raw bytes base64-encoded with the whsec_ prefix
    raw_key = b"supersecretkey32byteslong!!!!!"
    whsec = "whsec_" + base64.b64encode(raw_key).decode()
    body = json.dumps(PAYLOAD).encode()
    svix_headers = _make_svix_sig(body, raw_key)
    with patch("sip_bridge.webhook_handler.get_settings") as mock_s, \
         patch("sip_bridge.webhook_handler._accept_call", new_callable=AsyncMock), \
         patch("sip_bridge.webhook_handler.store") as mock_store, \
         patch("sip_bridge.webhook_handler.bus") as mock_bus:
        mock_s.return_value.webhook_secret = whsec
        mock_store.create_call = AsyncMock()
        mock_bus.publish = AsyncMock()
        resp = client.post(
            "/webhooks/sip", content=body,
            headers={"Content-Type": "application/json", **svix_headers},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_webhook_no_secret_skips_validation(client):
    body = json.dumps(PAYLOAD).encode()
    with patch("sip_bridge.webhook_handler.get_settings") as mock_s, \
         patch("sip_bridge.webhook_handler._accept_call", new_callable=AsyncMock), \
         patch("sip_bridge.webhook_handler.store") as mock_store, \
         patch("sip_bridge.webhook_handler.bus") as mock_bus:
        mock_s.return_value.webhook_secret = ""
        mock_store.create_call = AsyncMock()
        mock_bus.publish = AsyncMock()
        resp = client.post("/webhooks/sip", content=body,
                           headers={"Content-Type": "application/json"})
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_accept_call_fetches_services_for_known_caller():
    """When a caller is identified by phone, service names must be stored on the call."""
    from core.models import Call, CallState
    from core.state_store import StateStore
    from sip_bridge.webhook_handler import _accept_call

    call_id = "svc-test-call"
    store = StateStore()
    call = Call(call_id=call_id, caller_number="+15550001234", state=CallState.ACTIVE)
    await store.create_call(call)

    customer = {
        "account_id": "ACC-TS001",
        "full_name": "Test User",
        "phone_number": "+15550001234",
        "email": "test@example.com",
        "account_type": "residential",
        "account_status": "active",
    }
    service_status = {
        "account_id": "ACC-TS001",
        "services": [
            {"service_type": "internet", "plan_name": "Fiber 500", "status": "active"},
            {"service_type": "TV", "plan_name": "Basic TV", "status": "active"},
        ],
        "open_incidents": [],
        "open_support_tickets": [],
    }

    with patch("sip_bridge.webhook_handler.store", store), \
         patch("sip_bridge.webhook_handler.get_settings") as mock_s, \
         patch("db.repository.find_customer", AsyncMock(return_value=customer)), \
         patch("db.repository.get_service_status", AsyncMock(return_value=service_status)), \
         patch("sip_bridge.call_controller.accept", AsyncMock(return_value={"id": "sess-1"})), \
         patch("sip_bridge.session_manager.SessionManager") as mock_sm_cls, \
         patch("db.repository.emit_call_event"), \
         patch("sip_bridge.webhook_handler.bus") as mock_bus:
        mock_s.return_value = AsyncMock(
            webhook_secret="", daily_budget_usd=0,
            circuit_breaker_failure_threshold=5,
            circuit_breaker_window_seconds=60,
            circuit_breaker_cooldown_seconds=30,
            openai_model="gpt-realtime-mini",
            openai_voice="alloy",
            default_language="en-US",
        )
        mock_bus.publish = AsyncMock()
        mock_sm_instance = AsyncMock()
        mock_sm_cls.return_value = mock_sm_instance

        await _accept_call(call_id)

        updated = await store.get_call(call_id)
        assert updated is not None
        assert updated.account_id == "ACC-TS001"
        assert "internet" in updated.service_names
        assert "TV" in updated.service_names
