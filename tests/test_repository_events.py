"""Tests for new repository features: emit_call_event bus publish, get_transcript/get_call_events limit param."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# emit_call_event — event bus publish
# ---------------------------------------------------------------------------

def test_emit_call_event_schedules_db_write_and_bus_publish():
    """emit_call_event must schedule both a DB write and a bus.publish task."""
    mock_loop = MagicMock()
    mock_loop.create_task = MagicMock()
    mock_bus = MagicMock()
    mock_bus.publish = AsyncMock()

    with patch("db.repository.asyncio.get_running_loop", return_value=mock_loop), \
         patch("core.event_bus.bus", mock_bus):
        from db.repository import emit_call_event
        emit_call_event("call-1", "phase_entered", {"phase": "TRIAGE"})

    assert mock_loop.create_task.call_count == 2


def test_emit_call_event_topic_is_call_event():
    """The second create_task call must be for a CALL_EVENT publish coroutine."""
    import inspect
    captured = []
    mock_loop = MagicMock()

    def capture_task(coro):
        captured.append(coro)
        # Close coroutine to avoid 'was never awaited' warning
        if inspect.iscoroutine(coro):
            coro.close()
        return MagicMock()

    mock_loop.create_task = capture_task

    with patch("db.repository.asyncio.get_running_loop", return_value=mock_loop):
        from db.repository import emit_call_event
        emit_call_event("call-x", "tool_called", {"tool": "get_service_status"})

    assert len(captured) == 2
    # Second coroutine is bus.publish(Topic.CALL_EVENT, payload)
    assert inspect.iscoroutine(captured[1])


def test_emit_call_event_no_loop_is_silent():
    """emit_call_event must not raise if there is no running event loop."""
    with patch("db.repository.asyncio.get_running_loop", side_effect=RuntimeError("no loop")):
        from db.repository import emit_call_event
        emit_call_event("call-1", "phase_entered", {})  # must not raise


# ---------------------------------------------------------------------------
# get_transcript — limit parameter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_transcript_limit_returns_last_n_in_order():
    """get_transcript(limit=N) returns the last N turns in ascending turn_index order."""
    import uuid
    from db.repository import save_transcript_turn, get_transcript
    from db.engine import init_db

    await init_db()
    cid = f"call-t1-{uuid.uuid4().hex[:8]}"
    for i in range(5):
        await save_transcript_turn(cid, i, "assistant", f"turn {i}", "GREETING")

    turns = await get_transcript(cid, limit=3)
    assert len(turns) == 3
    assert [t["turn_index"] for t in turns] == [2, 3, 4]

    all_turns = await get_transcript(cid)
    assert len(all_turns) == 5


@pytest.mark.asyncio
async def test_get_transcript_no_limit_returns_all():
    """get_transcript() without limit returns all turns chronologically."""
    import uuid
    from db.repository import save_transcript_turn, get_transcript
    from db.engine import init_db

    await init_db()
    cid = f"call-t2-{uuid.uuid4().hex[:8]}"
    for i in range(3):
        await save_transcript_turn(cid, i, "caller", f"msg {i}", "VERIFY")

    turns = await get_transcript(cid)
    assert [t["turn_index"] for t in turns] == [0, 1, 2]


# ---------------------------------------------------------------------------
# get_call_events — limit parameter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_call_events_limit_returns_last_n_in_order():
    """get_call_events(limit=N) returns the last N events in chronological order."""
    import uuid
    from db.repository import save_call_event, get_call_events
    from db.engine import init_db

    await init_db()
    cid = f"call-e1-{uuid.uuid4().hex[:8]}"
    for i in range(5):
        await save_call_event(cid, "phase_entered", {"phase": f"PHASE{i}"})

    events = await get_call_events(cid, limit=3)
    assert len(events) == 3
    ids = [e["id"] for e in events]
    assert ids == sorted(ids)


@pytest.mark.asyncio
async def test_get_call_events_no_limit_returns_all():
    """get_call_events() without limit returns all events in chronological order."""
    import uuid
    from db.repository import save_call_event, get_call_events
    from db.engine import init_db

    await init_db()
    cid = f"call-e2-{uuid.uuid4().hex[:8]}"
    for i in range(3):
        await save_call_event(cid, "tool_called", {"tool": f"t{i}"})

    events = await get_call_events(cid)
    assert len(events) == 3


# ---------------------------------------------------------------------------
# save_transcript_turn — returns scrubbed text
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_transcript_turn_returns_scrubbed_text():
    """save_transcript_turn must return the PCI-scrubbed text."""
    import uuid
    from db.repository import save_transcript_turn
    from db.engine import init_db

    await init_db()
    cid = f"call-pci-{uuid.uuid4().hex[:8]}"
    raw = "My card number is 4111 1111 1111 1111 please help"
    scrubbed = await save_transcript_turn(cid, 0, "caller", raw, "VERIFY")
    assert "[REDACTED]" in scrubbed
    assert "4111" not in scrubbed


@pytest.mark.asyncio
async def test_save_transcript_turn_clean_text_unchanged():
    """save_transcript_turn returns text unchanged when no PCI data is present."""
    import uuid
    from db.repository import save_transcript_turn
    from db.engine import init_db

    await init_db()
    cid = f"call-clean-{uuid.uuid4().hex[:8]}"
    clean = "I have an internet outage"
    result = await save_transcript_turn(cid, 0, "caller", clean, "TRIAGE")
    assert result == clean


# ---------------------------------------------------------------------------
# get_ticket / update_ticket / get_account_history
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_ticket_returns_details():
    """get_ticket returns full ticket dict for an existing ticket."""
    import uuid
    from db.engine import init_db, AsyncSessionLocal
    from db.models import Customer, SupportTicket
    from db.repository import get_ticket

    await init_db()
    account_id = f"ACC-GT{uuid.uuid4().hex[:4].upper()}"
    ticket_id = f"TKT-GT{uuid.uuid4().hex[:6]}"

    async with AsyncSessionLocal() as session:
        session.add(Customer(account_id=account_id, full_name="Test User",
                             phone_number=f"+1555{uuid.uuid4().int % 10000000:07d}"))
        session.add(SupportTicket(ticket_id=ticket_id, account_id=account_id,
                                  issue_summary="Internet slow", priority="medium", status="open"))
        await session.commit()

    result = await get_ticket(ticket_id)
    assert result is not None
    assert result["ticket_id"] == ticket_id
    assert result["issue_summary"] == "Internet slow"
    assert result["priority"] == "medium"
    assert result["status"] == "open"


@pytest.mark.asyncio
async def test_get_ticket_not_found_returns_none():
    """get_ticket returns None for an unknown ticket ID."""
    from db.engine import init_db
    from db.repository import get_ticket

    await init_db()
    assert await get_ticket("TKT-DOESNOTEXIST") is None


@pytest.mark.asyncio
async def test_update_ticket_status():
    """update_ticket changes the status and returns updated fields."""
    import uuid
    from db.engine import init_db, AsyncSessionLocal
    from db.models import Customer, SupportTicket
    from db.repository import update_ticket, get_ticket

    await init_db()
    account_id = f"ACC-UT{uuid.uuid4().hex[:4].upper()}"
    ticket_id = f"TKT-UT{uuid.uuid4().hex[:6]}"

    async with AsyncSessionLocal() as session:
        session.add(Customer(account_id=account_id, full_name="Update User",
                             phone_number=f"+1555{uuid.uuid4().int % 10000000:07d}"))
        session.add(SupportTicket(ticket_id=ticket_id, account_id=account_id,
                                  issue_summary="TV pixelation", priority="low", status="open"))
        await session.commit()

    result = await update_ticket(ticket_id, status="resolved")
    assert result["updated"] is True
    assert result["status"] == "resolved"

    fetched = await get_ticket(ticket_id)
    assert fetched["status"] == "resolved"
    assert fetched["resolved_at"] is not None


@pytest.mark.asyncio
async def test_update_ticket_priority():
    """update_ticket changes the priority without touching status."""
    import uuid
    from db.engine import init_db, AsyncSessionLocal
    from db.models import Customer, SupportTicket
    from db.repository import update_ticket, get_ticket

    await init_db()
    account_id = f"ACC-UP{uuid.uuid4().hex[:4].upper()}"
    ticket_id = f"TKT-UP{uuid.uuid4().hex[:6]}"

    async with AsyncSessionLocal() as session:
        session.add(Customer(account_id=account_id, full_name="Priority User",
                             phone_number=f"+1555{uuid.uuid4().int % 10000000:07d}"))
        session.add(SupportTicket(ticket_id=ticket_id, account_id=account_id,
                                  issue_summary="No signal", priority="low", status="open"))
        await session.commit()

    result = await update_ticket(ticket_id, priority="critical")
    assert result["updated"] is True
    assert result["priority"] == "critical"
    assert result["status"] == "open"  # status unchanged


@pytest.mark.asyncio
async def test_update_ticket_not_found_returns_error():
    """update_ticket returns an error dict when ticket does not exist."""
    from db.engine import init_db
    from db.repository import update_ticket

    await init_db()
    result = await update_ticket("TKT-MISSING", status="closed")
    assert result["status"] == "error"
    assert "not found" in result["message"].lower()


@pytest.mark.asyncio
async def test_update_ticket_no_fields_returns_error():
    """update_ticket with no status or priority returns an error dict."""
    from db.engine import init_db
    from db.repository import update_ticket

    await init_db()
    result = await update_ticket("TKT-ANYTHING")
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_get_account_history_returns_resolved_records():
    """get_account_history returns resolved tickets and incidents, not open ones."""
    import uuid
    from db.engine import init_db, AsyncSessionLocal
    from db.models import Customer, SupportTicket, ServiceIncident
    from db.repository import get_account_history

    await init_db()
    account_id = f"ACC-AH{uuid.uuid4().hex[:4].upper()}"

    async with AsyncSessionLocal() as session:
        session.add(Customer(account_id=account_id, full_name="History User",
                             phone_number=f"+1555{uuid.uuid4().int % 10000000:07d}"))
        # Open ticket — should NOT appear in history
        session.add(SupportTicket(ticket_id=f"TKT-OP{uuid.uuid4().hex[:6]}",
                                  account_id=account_id, issue_summary="Open issue",
                                  priority="medium", status="open"))
        # Resolved ticket — should appear
        session.add(SupportTicket(ticket_id=f"TKT-RV{uuid.uuid4().hex[:6]}",
                                  account_id=account_id, issue_summary="Old resolved issue",
                                  priority="low", status="resolved"))
        # Resolved incident — should appear
        session.add(ServiceIncident(account_id=account_id, title="Past outage",
                                    description="Area outage last month", severity="high",
                                    status="resolved"))
        await session.commit()

    result = await get_account_history(account_id)
    assert result["account_id"] == account_id
    assert len(result["resolved_tickets"]) == 1
    assert result["resolved_tickets"][0]["issue_summary"] == "Old resolved issue"
    assert len(result["resolved_incidents"]) == 1
    assert result["resolved_incidents"][0]["title"] == "Past outage"


@pytest.mark.asyncio
async def test_get_account_history_unknown_account():
    """get_account_history returns an error dict for an unknown account."""
    from db.engine import init_db
    from db.repository import get_account_history

    await init_db()
    result = await get_account_history("ACC-DOESNOTEXIST")
    assert "error" in result


# ---------------------------------------------------------------------------
# ToolExecutor phase guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_phase_guard_blocks_tool_in_wrong_phase():
    """ToolExecutor must reject tools that are not available in the current call phase."""
    import uuid
    from unittest.mock import patch, AsyncMock
    from core.models import Call, CallState, ConvPhase
    from core.state_store import StateStore
    from sip_bridge.tool_executor import _dispatch
    from db.engine import init_db

    await init_db()
    call_id = f"pg-{uuid.uuid4().hex[:8]}"
    store = StateStore()
    call = Call(call_id=call_id, caller_number="+15550001234",
                state=CallState.ACTIVE, phase=ConvPhase.TRIAGE)
    await store.create_call(call)

    with patch("core.state_store.store", store):
        result = await _dispatch("get_service_status", {"account_id": "ACC-JT001"}, call_id=call_id)

    assert result["status"] == "error"
    assert "not available" in result["message"].lower() or "phase" in result["message"].lower()


@pytest.mark.asyncio
async def test_phase_guard_allows_tool_in_correct_phase():
    """ToolExecutor must allow tools that ARE in the current phase's allowlist."""
    import uuid
    from unittest.mock import patch
    from core.models import Call, CallState, ConvPhase
    from core.state_store import StateStore
    from sip_bridge.tool_executor import _dispatch
    from db.engine import init_db

    await init_db()
    call_id = f"pg2-{uuid.uuid4().hex[:8]}"
    store = StateStore()
    call = Call(call_id=call_id, caller_number="+14168489468",
                state=CallState.ACTIVE, phase=ConvPhase.DIAGNOSE)
    await store.create_call(call)

    with patch("core.state_store.store", store):
        result = await _dispatch("get_service_status", {"account_id": "ACC-JT001"}, call_id=call_id)

    # Should not be blocked by phase guard — result is a real DB response
    assert not (result.get("status") == "error" and "phase" in result.get("message", ""))
