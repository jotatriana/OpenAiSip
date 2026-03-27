"""End-to-end scenario tests for the voice agent conversation flow.

These tests verify FSM phase transitions, tool availability per phase,
and escalation behaviour given specific tool call sequences.
They do NOT test actual model output — only the structural behaviour.
"""
from unittest.mock import patch, AsyncMock

from core.models import CallState, ConvPhase
from tests.scenario_harness import ScenarioHarness


# ── Scenario 1: Happy path — unknown caller, full 6-phase flow ────────────────

async def test_happy_path_full_flow():
    """Unknown caller progresses GREETING→VERIFY→TRIAGE→DIAGNOSE→RESOLVE→WRAP_UP."""
    h = await ScenarioHarness.create("sc-1")
    h.db.find_customer.return_value = {
        "account_id": "ACC-001",
        "full_name": "Jane Doe",
        "phone_number": "+10000000001",
        "email": "jane@example.com",
        "account_type": "residential",
        "account_status": "active",
    }

    try:
        await h.fsm.enter(ConvPhase.GREETING)
        h.assert_phase(ConvPhase.GREETING)
        assert "phase_complete" in h.tool_names_in_last_update()
        assert "escalate_to_agent" in h.tool_names_in_last_update()
        # lookup_customer not yet available in GREETING
        assert "lookup_customer" not in h.tool_names_in_last_update()

        await h.call_tool("phase_complete")
        h.assert_phase(ConvPhase.VERIFY)
        assert "lookup_customer" in h.tool_names_in_last_update()

        await h.call_tool("lookup_customer", {"identifier": "+10000000001", "identifier_type": "phone"})
        await h.call_tool("phase_complete")
        h.assert_phase(ConvPhase.TRIAGE)
        # TRIAGE is classify-only: no service status or ticket tools
        assert "get_service_status" not in h.tool_names_in_last_update()
        assert "create_ticket" not in h.tool_names_in_last_update()

        await h.call_tool("phase_complete")
        h.assert_phase(ConvPhase.DIAGNOSE)
        # get_service_status and create_ticket are available from DIAGNOSE onward
        assert "get_service_status" in h.tool_names_in_last_update()
        assert "create_ticket" in h.tool_names_in_last_update()

        await h.call_tool("phase_complete")
        h.assert_phase(ConvPhase.RESOLVE)

        await h.call_tool("phase_complete")
        h.assert_phase(ConvPhase.WRAP_UP)
    finally:
        h.stop()


# ── Scenario 2: Known caller — VERIFY skipped ─────────────────────────────────

async def test_known_caller_skips_verify():
    """Caller identified by caller ID → VERIFY is skipped, jumps to TRIAGE."""
    h = await ScenarioHarness.create("sc-2", account_id="ACC-002")
    try:
        await h.fsm.enter(ConvPhase.GREETING)
        h.assert_phase(ConvPhase.GREETING)

        await h.call_tool("phase_complete")
        h.assert_phase(ConvPhase.TRIAGE)  # skipped VERIFY
    finally:
        h.stop()


# ── Scenario 3: Model-triggered escalation ────────────────────────────────────

async def test_manual_escalation_by_model():
    """Model calls escalate_to_agent → call enters TRANSFERRING state."""
    h = await ScenarioHarness.create("sc-3")
    try:
        await h.fsm.enter(ConvPhase.DIAGNOSE)

        with patch("sip_bridge.call_controller.refer", new_callable=AsyncMock) as mock_refer:
            await h.call_tool("escalate_to_agent", {"reason": "customer demands human"})
            mock_refer.assert_awaited_once()
    finally:
        h.stop()


# ── Scenario 4: Auto-escalation on frustration threshold ──────────────────────

async def test_auto_escalation_frustration():
    """FSM escalates automatically when frustration_count hits the limit."""
    h = await ScenarioHarness.create("sc-4", settings_overrides={"escalation_frustration_limit": 2})
    try:
        await h.fsm.enter(ConvPhase.DIAGNOSE)

        with patch("sip_bridge.call_controller.refer", new_callable=AsyncMock) as mock_refer:
            # First frustration — below limit
            call = await h.store.get_call("sc-4")
            call.frustration_count = 1
            await h.store.update_call(call)
            escalated = await h.fsm.check_escalation()
            assert not escalated
            mock_refer.assert_not_awaited()

            # Second frustration — at limit
            call.frustration_count = 2
            await h.store.update_call(call)
            escalated = await h.fsm.check_escalation()
            assert escalated
            mock_refer.assert_awaited_once()
    finally:
        h.stop()


# ── Scenario 5: Auto-escalation on tool failure threshold ─────────────────────

async def test_auto_escalation_tool_failures():
    """FSM escalates automatically when tool_failure_count hits the limit."""
    h = await ScenarioHarness.create("sc-5", settings_overrides={"escalation_tool_failure_limit": 2})
    try:
        await h.fsm.enter(ConvPhase.DIAGNOSE)
        call = await h.store.get_call("sc-5")
        call.tool_failure_count = 2
        await h.store.update_call(call)

        with patch("sip_bridge.call_controller.refer", new_callable=AsyncMock) as mock_refer:
            escalated = await h.fsm.check_escalation()
            assert escalated
            mock_refer.assert_awaited_once()
    finally:
        h.stop()


# ── Scenario 6: Turn limit auto-advance ───────────────────────────────────────

async def test_turn_limit_auto_advance():
    """When the model never calls phase_complete, the turn limit triggers advance."""
    h = await ScenarioHarness.create("sc-6", settings_overrides={"max_turns_per_phase": 3})
    try:
        await h.fsm.enter(ConvPhase.GREETING)
        h.assert_phase(ConvPhase.GREETING)

        await h.fsm.record_turn()
        await h.fsm.record_turn()
        h.assert_phase(ConvPhase.GREETING)  # not yet

        await h.fsm.record_turn()  # 3rd → auto-advance
        h.assert_phase(ConvPhase.VERIFY)
    finally:
        h.stop()


# ── Scenario 7: Backward transition RESOLVE → DIAGNOSE ────────────────────────

async def test_backward_transition_resolve_to_diagnose():
    """Operator or model can jump backward (e.g. RESOLVE → DIAGNOSE on new symptom)."""
    h = await ScenarioHarness.create("sc-7")
    try:
        await h.fsm.enter(ConvPhase.RESOLVE)
        h.assert_phase(ConvPhase.RESOLVE)

        await h.fsm.transition(ConvPhase.DIAGNOSE, reason="caller reported a new issue")
        h.assert_phase(ConvPhase.DIAGNOSE)
        # Session update should have been sent for DIAGNOSE
        assert h.last_session_update() is not None
    finally:
        h.stop()


# ── Scenario 8: Ticket creation flow ─────────────────────────────────────────

async def test_ticket_creation_in_diagnose():
    """create_ticket tool is available in DIAGNOSE and returns a ticket ID."""
    h = await ScenarioHarness.create("sc-8", account_id="ACC-008")
    h.db.create_ticket.return_value = {
        "ticket_id": "TKT-12345678",
        "account_id": "ACC-008",
        "priority": "high",
        "status": "created",
    }
    try:
        await h.fsm.enter(ConvPhase.DIAGNOSE)
        assert "create_ticket" in h.tool_names_in_last_update()

        await h.call_tool("create_ticket", {
            "account_id": "ACC-008",
            "issue_summary": "No internet connectivity for 2 days",
            "priority": "high",
        })
        h.db.create_ticket.assert_awaited_once()
        # Phase should still be DIAGNOSE (ticket creation doesn't auto-advance)
        h.assert_phase(ConvPhase.DIAGNOSE)
    finally:
        h.stop()


# ── Scenario 9: No double-escalation ─────────────────────────────────────────

async def test_no_double_escalation():
    """Once escalated, subsequent check_escalation calls are no-ops."""
    h = await ScenarioHarness.create("sc-9")
    try:
        await h.fsm.enter(ConvPhase.DIAGNOSE)
        call = await h.store.get_call("sc-9")
        call.frustration_count = 3
        await h.store.update_call(call)

        with patch("sip_bridge.call_controller.refer", new_callable=AsyncMock) as mock_refer:
            await h.fsm.check_escalation()
            await h.fsm.check_escalation()
            await h.fsm.check_escalation()
            assert mock_refer.await_count == 1  # only once
    finally:
        h.stop()


# ── Scenario 10: Email-based lookup in VERIFY ────────────────────────────────

async def test_email_lookup_in_verify():
    """lookup_customer can be called with identifier_type='email'."""
    h = await ScenarioHarness.create("sc-10a")
    h.db.find_customer.return_value = {
        "account_id": "ACC-010",
        "full_name": "Test User",
        "phone_number": "",
        "email": "test@example.com",
        "account_type": "residential",
        "account_status": "active",
    }
    try:
        await h.fsm.enter(ConvPhase.VERIFY)
        await h.call_tool("lookup_customer", {
            "identifier": "test@example.com",
            "identifier_type": "email",
        })
        h.db.find_customer.assert_awaited_once_with("test@example.com", "email")
    finally:
        h.stop()


# ── Scenario 11: VERIFY without caller_number — VERIFY tools include lookup ──

async def test_verify_tools_available_without_known_caller():
    """lookup_customer is always available in VERIFY regardless of caller_number."""
    h = await ScenarioHarness.create("sc-11")
    try:
        await h.fsm.enter(ConvPhase.VERIFY)
        assert "lookup_customer" in h.tool_names_in_last_update()
        assert "get_service_status" not in h.tool_names_in_last_update()
    finally:
        h.stop()


# ── Scenario 13: Hallucinated phone number is hard-rejected ──────────────────

async def test_hallucinated_phone_lookup_rejected():
    """lookup_customer with a phone number that does not match verified caller ID is rejected."""
    h = await ScenarioHarness.create("sc-13", caller_number="+14168489468")
    try:
        await h.fsm.enter(ConvPhase.TRIAGE)

        result_output = None

        async def capture_event(evt):
            nonlocal result_output
            if evt.get("type") == "conversation.item.create":
                item = evt.get("item", {})
                if item.get("type") == "function_call_output":
                    result_output = item.get("output", "")

        h.session_manager.send_event = AsyncMock(side_effect=capture_event)

        await h.call_tool("lookup_customer", {
            "identifier": "+12025550123",
            "identifier_type": "phone",
        })

        assert result_output is not None
        assert "rejected" in result_output.lower()
        h.db.find_customer.assert_not_awaited()
    finally:
        h.stop()


async def test_verified_phone_lookup_allowed():
    """lookup_customer with the actual verified caller ID is allowed through."""
    h = await ScenarioHarness.create("sc-13b", caller_number="+14168489468")
    h.db.find_customer.return_value = {
        "account_id": "ACC-JT001",
        "full_name": "Julio Triana",
        "phone_number": "+14168489468",
        "email": "julio@example.com",
        "account_type": "residential",
        "account_status": "active",
    }
    try:
        await h.fsm.enter(ConvPhase.VERIFY)
        await h.call_tool("lookup_customer", {
            "identifier": "+14168489468",
            "identifier_type": "phone",
        })
        h.db.find_customer.assert_awaited_once_with("+14168489468", "phone")
    finally:
        h.stop()


# ── Scenario 12: WRAP_UP ends session ────────────────────────────────────────

async def test_wrap_up_ends_session():  # noqa: F811
    """phase_complete from WRAP_UP marks call ENDED and closes the WebSocket."""
    h = await ScenarioHarness.create("sc-10")
    try:
        await h.fsm.enter(ConvPhase.WRAP_UP)
        await h.fsm.advance()  # phase_complete from WRAP_UP

        call = await h.store.get_call("sc-10")
        assert call.state == CallState.ENDED
        assert call.hangup_cause == "normal"
        h.session_manager.close.assert_awaited_once()
    finally:
        h.stop()
