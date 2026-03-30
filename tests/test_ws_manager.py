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


async def test_call_event_topic_in_all_topics():
    """CALL_EVENT must be in ALL_TOPICS so the broadcast loop subscribes to it."""
    from dashboard.ws_manager import ALL_TOPICS
    from core.models import Topic
    assert Topic.CALL_EVENT in ALL_TOPICS


async def test_snapshot_includes_transcripts_and_events(manager):
    """SNAPSHOT message must include active_call_transcripts and active_call_events keys."""
    ws = _make_ws()
    snapshot_payload = {
        "active_calls": [],
        "global_tokens": {},
        "recent_logs": [],
        "channel_health": {},
        "active_call_transcripts": {"call-1": [{"turn_index": 0, "role": "assistant", "text": "hi", "phase": "GREETING", "timestamp": None}]},
        "active_call_events": {"call-1": [{"id": 1, "event_type": "phase_entered", "data": {"phase": "GREETING"}, "timestamp": None}]},
    }
    with patch("dashboard.ws_manager.store") as mock_store, \
         patch("dashboard.ws_manager.get_settings") as mock_cfg:
        mock_cfg.return_value.sip_stale_threshold_seconds = 300
        mock_store.snapshot = AsyncMock(return_value=snapshot_payload)
        await manager.connect(ws)

    sent = ws.send_json.call_args[0][0]
    assert sent["type"] == Topic.SNAPSHOT
    assert "active_call_transcripts" in sent["payload"]
    assert "active_call_events" in sent["payload"]
    assert "call-1" in sent["payload"]["active_call_transcripts"]
    assert "call-1" in sent["payload"]["active_call_events"]


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


# ── Snapshot parallel fetch / limit tests ─────────────────────────────────────

def _mock_store_helpers(store):
    """Return a dict of patches that stub out helpers snapshot() calls besides DB queries."""
    return {
        "get_logs": AsyncMock(return_value=[]),
        "build_channel_health": AsyncMock(return_value=MagicMock(model_dump=lambda **_: {})),
        "get_global_tokens": AsyncMock(return_value=MagicMock(model_dump=lambda **_: {})),
    }


async def test_snapshot_ended_call_transcript_limit():
    """Ended-call transcripts in snapshot must use limit=50, not None or 200."""
    from datetime import datetime
    from core.state_store import StateStore
    from core.models import Call, CallState, ConvPhase, TokenAggregate
    import db.repository as repo

    store = StateStore()
    call = Call(
        call_id="ended-1",
        state=CallState.ENDED,
        phase=ConvPhase.WRAP_UP,
        created_at=datetime.utcnow(),
        token_total=TokenAggregate(scope="ended-1"),
    )
    store._calls["ended-1"] = call

    transcript_calls = []
    events_calls = []

    async def fake_transcript(call_id, limit=None):
        transcript_calls.append(limit)
        return []

    async def fake_events(call_id, limit=None):
        events_calls.append(limit)
        return []

    helpers = _mock_store_helpers(store)
    with patch.object(repo, "get_transcript", fake_transcript), \
         patch.object(repo, "get_call_events", fake_events), \
         patch.object(store, "get_logs", helpers["get_logs"]), \
         patch.object(store, "build_channel_health", helpers["build_channel_health"]), \
         patch.object(store, "get_global_tokens", helpers["get_global_tokens"]):
        await store.snapshot(300)

    assert transcript_calls == [50], f"expected [50], got {transcript_calls}"
    assert events_calls == [50], f"expected [50], got {events_calls}"


async def test_snapshot_active_call_transcript_no_limit():
    """Active calls must receive limit=None in snapshot (full live transcript)."""
    from datetime import datetime
    from core.state_store import StateStore
    from core.models import Call, CallState, ConvPhase, TokenAggregate
    import db.repository as repo

    store = StateStore()
    call = Call(
        call_id="active-1",
        state=CallState.ACTIVE,
        phase=ConvPhase.DIAGNOSE,
        created_at=datetime.utcnow(),
        token_total=TokenAggregate(scope="active-1"),
    )
    store._calls["active-1"] = call

    transcript_limits = []

    async def fake_transcript(call_id, limit=None):
        transcript_limits.append(limit)
        return []

    helpers = _mock_store_helpers(store)
    with patch.object(repo, "get_transcript", fake_transcript), \
         patch.object(repo, "get_call_events", AsyncMock(return_value=[])), \
         patch.object(store, "get_logs", helpers["get_logs"]), \
         patch.object(store, "build_channel_health", helpers["build_channel_health"]), \
         patch.object(store, "get_global_tokens", helpers["get_global_tokens"]):
        await store.snapshot(300)

    assert transcript_limits == [None], f"expected [None], got {transcript_limits}"


async def test_snapshot_fetches_run_in_parallel():
    """Snapshot transcript fetches must run concurrently, not sequentially."""
    import time
    from datetime import datetime
    from core.state_store import StateStore
    from core.models import Call, CallState, ConvPhase, TokenAggregate
    import db.repository as repo

    store = StateStore()
    for i in range(3):
        call = Call(
            call_id=f"ended-{i}",
            state=CallState.ENDED,
            phase=ConvPhase.WRAP_UP,
            created_at=datetime.utcnow(),
            token_total=TokenAggregate(scope=f"ended-{i}"),
        )
        store._calls[f"ended-{i}"] = call

    async def slow_query(call_id, limit=None):
        await asyncio.sleep(0.05)  # 50 ms each
        return []

    helpers = _mock_store_helpers(store)
    with patch.object(repo, "get_transcript", slow_query), \
         patch.object(repo, "get_call_events", slow_query), \
         patch.object(store, "get_logs", helpers["get_logs"]), \
         patch.object(store, "build_channel_health", helpers["build_channel_health"]), \
         patch.object(store, "get_global_tokens", helpers["get_global_tokens"]):
        t0 = time.monotonic()
        await store.snapshot(300)
        elapsed = time.monotonic() - t0

    # 3 calls × 2 queries × 50 ms = 300 ms sequential.
    # Parallel (asyncio.gather) completes in ~50 ms.  Allow generous margin.
    assert elapsed < 0.25, f"snapshot took {elapsed:.2f}s — queries may not be parallel"


# ── Transcript snapshot merge tests ───────────────────────────────────────────

async def test_snapshot_transcript_turns_include_all_db_turns():
    """Snapshot active_call_transcripts must include every turn returned by get_transcript."""
    from datetime import datetime
    from core.state_store import StateStore
    from core.models import Call, CallState, ConvPhase, TokenAggregate
    import db.repository as repo

    store = StateStore()
    call = Call(
        call_id="active-merge",
        state=CallState.ACTIVE,
        phase=ConvPhase.GREETING,
        created_at=datetime.utcnow(),
        token_total=TokenAggregate(scope="active-merge"),
    )
    store._calls["active-merge"] = call

    db_turns = [
        {"turn_index": 0, "role": "assistant", "text": "Hello", "phase": "GREETING", "timestamp": None},
        {"turn_index": 1, "role": "caller",    "text": "Hi",    "phase": "GREETING", "timestamp": None},
        {"turn_index": 2, "role": "assistant", "text": "How?",  "phase": "TRIAGE",   "timestamp": None},
    ]

    helpers = _mock_store_helpers(store)
    with patch.object(repo, "get_transcript", AsyncMock(return_value=db_turns)), \
         patch.object(repo, "get_call_events", AsyncMock(return_value=[])), \
         patch.object(store, "get_logs", helpers["get_logs"]), \
         patch.object(store, "build_channel_health", helpers["build_channel_health"]), \
         patch.object(store, "get_global_tokens", helpers["get_global_tokens"]):
        snap = await store.snapshot(300)

    turns = snap["active_call_transcripts"]["active-merge"]
    assert len(turns) == 3
    assert [t["turn_index"] for t in turns] == [0, 1, 2]


async def test_snapshot_sent_before_client_registered(manager):
    """Client must NOT be in _connections while the snapshot is being sent.

    If the client is registered first, the broadcast loop starts delivering live
    events before the snapshot arrives. The JS SNAPSHOT handler then replaces those
    early events, causing data loss. Snapshot-first guarantees a clean baseline.
    """
    ws = _make_ws()
    in_connections_during_snapshot: list[bool] = []

    async def capture_send_json(payload):
        # Capture whether the client is in _connections at snapshot send time
        in_connections_during_snapshot.append(ws in manager._connections)

    ws.send_json = AsyncMock(side_effect=capture_send_json)

    with patch("dashboard.ws_manager.store") as mock_store, \
         patch("dashboard.ws_manager.get_settings") as mock_cfg:
        mock_cfg.return_value.sip_stale_threshold_seconds = 300
        mock_store.snapshot = AsyncMock(return_value={"active_calls": [], "global_tokens": {}, "recent_logs": [], "channel_health": {}})
        await manager.connect(ws)

    assert in_connections_during_snapshot, "send_json was never called"
    assert not in_connections_during_snapshot[0], (
        "Client was already in _connections when the snapshot was sent — "
        "this re-introduces the race condition where live events arrive before the snapshot"
    )
    # After connect() returns, the client must be registered
    assert ws in manager._connections


async def test_snapshot_active_call_transcript_not_capped():
    """Active calls must NOT have their transcript capped — all DB turns returned."""
    from datetime import datetime
    from core.state_store import StateStore
    from core.models import Call, CallState, ConvPhase, TokenAggregate
    import db.repository as repo

    store = StateStore()
    call = Call(
        call_id="active-nocap",
        state=CallState.ACTIVE,
        phase=ConvPhase.DIAGNOSE,
        created_at=datetime.utcnow(),
        token_total=TokenAggregate(scope="active-nocap"),
    )
    store._calls["active-nocap"] = call

    # Simulate 80 turns — more than the 50-turn cap applied to ended calls.
    db_turns = [
        {"turn_index": i, "role": "assistant", "text": f"turn {i}", "phase": "DIAGNOSE", "timestamp": None}
        for i in range(80)
    ]

    helpers = _mock_store_helpers(store)
    with patch.object(repo, "get_transcript", AsyncMock(return_value=db_turns)), \
         patch.object(repo, "get_call_events", AsyncMock(return_value=[])), \
         patch.object(store, "get_logs", helpers["get_logs"]), \
         patch.object(store, "build_channel_health", helpers["build_channel_health"]), \
         patch.object(store, "get_global_tokens", helpers["get_global_tokens"]):
        snap = await store.snapshot(300)

    turns = snap["active_call_transcripts"]["active-nocap"]
    assert len(turns) == 80, f"expected 80 turns for active call, got {len(turns)}"
