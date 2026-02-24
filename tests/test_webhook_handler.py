"""Tests for webhook handler: signature validation and call creation."""
import hashlib
import hmac
import json
import pytest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sip_bridge.app import app


def _make_sig(body: bytes, secret: str) -> str:
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


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
        mock_s.return_value.webhook_secret = "mysecret"
        body = json.dumps(PAYLOAD).encode()
        resp = client.post("/webhooks/sip", content=body,
                           headers={"Content-Type": "application/json"})
        assert resp.status_code == 401


def test_webhook_valid_signature(client):
    secret = "mysecret"
    body = json.dumps(PAYLOAD).encode()
    sig = _make_sig(body, secret)
    with patch("sip_bridge.webhook_handler.get_settings") as mock_s, \
         patch("sip_bridge.webhook_handler._accept_call", new_callable=AsyncMock), \
         patch("sip_bridge.webhook_handler.store") as mock_store, \
         patch("sip_bridge.webhook_handler.bus") as mock_bus:
        mock_s.return_value.webhook_secret = secret
        mock_store.create_call = AsyncMock()
        mock_bus.publish = AsyncMock()
        resp = client.post(
            "/webhooks/sip", content=body,
            headers={"Content-Type": "application/json", "X-OpenAI-Signature": sig},
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
