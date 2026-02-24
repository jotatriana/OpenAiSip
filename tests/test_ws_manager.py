"""Tests for dashboard WebSocket manager: fan-out, snapshot, auth."""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from dashboard.ws_manager import WSManager
from core.models import Topic


@pytest.fixture
def manager():
    return WSManager()


def _make_ws():
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    ws.headers = {}
    return ws


@pytest.mark.asyncio
async def test_connect_sends_snapshot(manager):
    ws = _make_ws()
    with patch("dashboard.ws_manager.store") as mock_store, \
         patch("dashboard.ws_manager.get_settings") as mock_cfg:
        mock_cfg.return_value.sip_stale_threshold_seconds = 300
        mock_store.snapshot = AsyncMock(return_value={"active_calls": [], "global_tokens": {}, "recent_logs": [], "channel_health": {}})
        await manager.connect(ws)
        ws.accept.assert_awaited_once()
        ws.send_json.assert_awaited_once()
        call_args = ws.send_json.call_args[0][0]
        assert call_args["type"] == Topic.SNAPSHOT


@pytest.mark.asyncio
async def test_disconnect_removes_client(manager):
    ws = _make_ws()
    with patch("dashboard.ws_manager.store") as mock_store, \
         patch("dashboard.ws_manager.get_settings") as mock_cfg:
        mock_cfg.return_value.sip_stale_threshold_seconds = 300
        mock_store.snapshot = AsyncMock(return_value={})
        await manager.connect(ws)
        assert ws in manager._connections
        await manager.disconnect(ws)
        assert ws not in manager._connections


@pytest.mark.asyncio
async def test_broadcast_reaches_all_clients(manager):
    clients = [_make_ws() for _ in range(3)]
    with patch("dashboard.ws_manager.store") as mock_store, \
         patch("dashboard.ws_manager.get_settings") as mock_cfg:
        mock_cfg.return_value.sip_stale_threshold_seconds = 300
        mock_store.snapshot = AsyncMock(return_value={})
        for ws in clients:
            await manager.connect(ws)

    payload = {"type": "CALL_CREATED", "payload": {"call_id": "x"}, "ts": 0.0}
    await manager._broadcast(payload)

    for ws in clients:
        assert ws.send_json.await_count >= 1  # snapshot + broadcast
        last_call = ws.send_json.call_args_list[-1][0][0]
        assert last_call["type"] == "CALL_CREATED"


@pytest.mark.asyncio
async def test_stalled_client_removed(manager):
    """A client that times out on send should be removed from the connection set."""
    ws = _make_ws()
    ws.send_json = AsyncMock(side_effect=asyncio.TimeoutError)

    with patch("dashboard.ws_manager.store") as mock_store, \
         patch("dashboard.ws_manager.get_settings") as mock_cfg:
        mock_cfg.return_value.sip_stale_threshold_seconds = 300
        mock_store.snapshot = AsyncMock(return_value={})
        # Manually add without snapshot to isolate the broadcast test
        async with manager._lock:
            manager._connections.add(ws)

    await manager._broadcast({"type": "TEST", "payload": {}, "ts": 0.0})
    assert ws not in manager._connections
