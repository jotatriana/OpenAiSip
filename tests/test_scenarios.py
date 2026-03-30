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


# ── Epic 1: Multi-service intent routing scenarios ────────────────────────────

async def test_scenario_triage_routes_to_billing():
    """TRIAGE phase_complete(service_category='billing') routes DIAGNOSE to billing tools."""
    h = await ScenarioHarness.create("sc-routing-billing")
    try:
        await h.fsm.enter(ConvPhase.GREETING)
        await h.call_tool("phase_complete", {"summary": "caller wants billing info"})
        h.assert_phase(ConvPhase.VERIFY)
        await h.call_tool("phase_complete", {"summary": "verified"})
        h.assert_phase(ConvPhase.TRIAGE)

        await h.call_tool("phase_complete", {"summary": "billing inquiry", "service_category": "billing"})
        h.assert_phase(ConvPhase.DIAGNOSE)

        call = await h.store.get_call(h.call_id)
        assert call.service_category == "billing"

        diagnose_tools = h.tool_names_in_last_update()
        assert "get_account_balance" in diagnose_tools
        assert "get_payment_history" in diagnose_tools
        assert "get_service_status" not in diagnose_tools
        assert "create_ticket" not in diagnose_tools
    finally:
        h.stop()


async def test_scenario_triage_routes_to_appointment():
    """TRIAGE phase_complete(service_category='appointment') routes DIAGNOSE to appointment tools."""
    h = await ScenarioHarness.create("sc-routing-appointment")
    try:
        await h.fsm.enter(ConvPhase.GREETING)
        await h.call_tool("phase_complete", {"summary": "caller about appointment"})
        h.assert_phase(ConvPhase.VERIFY)
        await h.call_tool("phase_complete", {"summary": "verified"})
        h.assert_phase(ConvPhase.TRIAGE)

        await h.call_tool("phase_complete", {"summary": "appointment question", "service_category": "appointment"})
        h.assert_phase(ConvPhase.DIAGNOSE)

        call = await h.store.get_call(h.call_id)
        assert call.service_category == "appointment"

        diagnose_tools = h.tool_names_in_last_update()
        assert "get_appointments" in diagnose_tools
        assert "get_service_status" not in diagnose_tools

        await h.call_tool("phase_complete", {"summary": "appointment reported"})
        h.assert_phase(ConvPhase.RESOLVE)

        resolve_tools = h.tool_names_in_last_update()
        assert "confirm_appointment" in resolve_tools
        assert "cancel_appointment" in resolve_tools
        assert "reschedule_appointment" in resolve_tools
    finally:
        h.stop()


async def test_scenario_triage_routes_to_sales():
    """TRIAGE phase_complete(service_category='sales') routes DIAGNOSE to sales tools."""
    h = await ScenarioHarness.create("sc-routing-sales")
    try:
        await h.fsm.enter(ConvPhase.GREETING)
        await h.call_tool("phase_complete", {"summary": "caller about plans"})
        h.assert_phase(ConvPhase.VERIFY)
        await h.call_tool("phase_complete", {"summary": "verified"})
        h.assert_phase(ConvPhase.TRIAGE)

        await h.call_tool("phase_complete", {"summary": "plan inquiry", "service_category": "sales"})
        h.assert_phase(ConvPhase.DIAGNOSE)

        call = await h.store.get_call(h.call_id)
        assert call.service_category == "sales"

        diagnose_tools = h.tool_names_in_last_update()
        assert "get_product_catalog" in diagnose_tools
        assert "get_promotions" in diagnose_tools
        assert "get_service_status" not in diagnose_tools
    finally:
        h.stop()


async def test_scenario_triage_routes_to_technical_support_unchanged():
    """TRIAGE with service_category='technical_support' keeps existing tool set."""
    h = await ScenarioHarness.create("sc-routing-ts")
    try:
        await h.fsm.enter(ConvPhase.GREETING)
        await h.call_tool("phase_complete", {"summary": "caller about outage"})
        h.assert_phase(ConvPhase.VERIFY)
        await h.call_tool("phase_complete", {"summary": "verified"})
        h.assert_phase(ConvPhase.TRIAGE)

        await h.call_tool("phase_complete", {"summary": "internet issue", "service_category": "technical_support"})
        h.assert_phase(ConvPhase.DIAGNOSE)

        call = await h.store.get_call(h.call_id)
        assert call.service_category == "technical_support"

        diagnose_tools = h.tool_names_in_last_update()
        assert "get_service_status" in diagnose_tools
        assert "create_ticket" in diagnose_tools
        assert "get_ticket" in diagnose_tools
        assert "get_account_history" in diagnose_tools
        assert "get_account_balance" not in diagnose_tools
    finally:
        h.stop()


async def test_scenario_triage_unknown_category_ignored():
    """phase_complete with an invalid service_category value does not set the category."""
    h = await ScenarioHarness.create("sc-routing-invalid")
    try:
        await h.fsm.enter(ConvPhase.GREETING)
        await h.call_tool("phase_complete")
        h.assert_phase(ConvPhase.VERIFY)
        await h.call_tool("phase_complete")
        h.assert_phase(ConvPhase.TRIAGE)

        await h.call_tool("phase_complete", {"summary": "unknown", "service_category": "not_a_real_category"})
        h.assert_phase(ConvPhase.DIAGNOSE)

        call = await h.store.get_call(h.call_id)
        assert call.service_category is None
    finally:
        h.stop()


async def test_scenario_triage_routes_to_move_transfer():
    """TRIAGE phase_complete(service_category='move_transfer') routes DIAGNOSE to move tools."""
    h = await ScenarioHarness.create("sc-routing-move")
    try:
        await h.fsm.enter(ConvPhase.GREETING)
        await h.call_tool("phase_complete", {"summary": "caller moving"})
        h.assert_phase(ConvPhase.VERIFY)
        await h.call_tool("phase_complete", {"summary": "verified"})
        h.assert_phase(ConvPhase.TRIAGE)

        await h.call_tool("phase_complete", {"summary": "move inquiry", "service_category": "move_transfer"})
        h.assert_phase(ConvPhase.DIAGNOSE)

        call = await h.store.get_call(h.call_id)
        assert call.service_category == "move_transfer"

        diagnose_tools = h.tool_names_in_last_update()
        assert "get_service_eligibility" in diagnose_tools
        assert "get_service_status" not in diagnose_tools

        await h.call_tool("phase_complete", {"summary": "eligibility reported"})
        h.assert_phase(ConvPhase.RESOLVE)

        resolve_tools = h.tool_names_in_last_update()
        assert "initiate_service_move" in resolve_tools
        assert "cancel_service" in resolve_tools
    finally:
        h.stop()


async def test_diagnose_entry_response_create_has_tool_choice_only():
    """DIAGNOSE entry response.create must have only tool_choice=required — no instructions override.

    Per-response instructions REPLACE session-level instructions, which would lose the
    CONFIRMED ACCOUNT block and break account_id resolution. The fix is tool_choice only.
    """
    h = await ScenarioHarness.create("sc-diagnose-entry", account_id="ACC-JT001")
    try:
        await h.fsm.enter(ConvPhase.GREETING)
        await h.call_tool("phase_complete")  # → VERIFY (known caller skips to TRIAGE)
        h.assert_phase(ConvPhase.TRIAGE)

        h.sent_events.clear()
        await h.call_tool("phase_complete", {"service_category": "technical_support"})  # → DIAGNOSE
        h.assert_phase(ConvPhase.DIAGNOSE)

        # Find the last response.create event
        response_creates = [e for e in h.sent_events if e.get("type") == "response.create"]
        assert response_creates, "Expected response.create after entering DIAGNOSE"
        last = response_creates[-1]
        response_payload = last.get("response", {})
        assert response_payload.get("tool_choice") == "required"
        assert "instructions" not in response_payload, (
            "DIAGNOSE entry must NOT include per-response instructions — they replace "
            "session-level instructions and strip the CONFIRMED ACCOUNT block"
        )
    finally:
        h.stop()


async def test_get_service_status_diagnose_response_prohibits_follow_up_question():
    """After get_service_status in DIAGNOSE, response.create instructions must say 'Do NOT ask'."""
    h = await ScenarioHarness.create("sc-diagnose-gss", account_id="ACC-JT001")
    h.db.get_service_status.return_value = {
        "services": [], "open_incidents": [], "open_support_tickets": [],
    }
    try:
        await h.fsm.enter(ConvPhase.DIAGNOSE)

        h.sent_events.clear()
        await h.call_tool("get_service_status", {"account_id": "ACC-JT001"})

        response_creates = [e for e in h.sent_events if e.get("type") == "response.create"]
        last = response_creates[-1]
        instructions = last.get("response", {}).get("instructions", "")
        assert "Do NOT ask" in instructions or "do not ask" in instructions.lower(), (
            "DIAGNOSE get_service_status response.create must prohibit follow-up questions"
        )
        assert last.get("response", {}).get("tool_choice") == "required"
    finally:
        h.stop()


async def test_create_ticket_response_forces_readback_and_phase_complete():
    """After create_ticket, response.create instructions must tell model to read ticket_id and call phase_complete."""
    h = await ScenarioHarness.create("sc-create-ticket", account_id="ACC-JT001")
    h.db.create_ticket.return_value = {
        "ticket_id": "TKT-12345678",
        "account_id": "ACC-JT001",
        "status": "created",
    }
    try:
        await h.fsm.enter(ConvPhase.RESOLVE)

        h.sent_events.clear()
        await h.call_tool("create_ticket", {
            "account_id": "ACC-JT001",
            "issue_summary": "Internet down for 2 days",
            "priority": "high",
        })

        response_creates = [e for e in h.sent_events if e.get("type") == "response.create"]
        last = response_creates[-1]
        response_payload = last.get("response", {})
        instructions = response_payload.get("instructions", "")
        assert "ticket_id" in instructions, "Must tell model to read back ticket_id"
        assert "phase_complete" in instructions, "Must tell model to call phase_complete"
        assert response_payload.get("tool_choice") == "required"
    finally:
        h.stop()


async def test_resolve_entry_does_not_force_tool_choice_required():
    """RESOLVE entry response.create must NOT have tool_choice=required.

    Forcing tool_choice=required at RESOLVE entry causes the model to call create_ticket
    in the same response as the confirmation question — before the caller can answer.
    """
    h = await ScenarioHarness.create("sc-resolve-entry", account_id="ACC-JT001")
    try:
        await h.fsm.enter(ConvPhase.DIAGNOSE)

        h.sent_events.clear()
        await h.call_tool("phase_complete")  # → RESOLVE
        h.assert_phase(ConvPhase.RESOLVE)

        response_creates = [e for e in h.sent_events if e.get("type") == "response.create"]
        assert response_creates, "Expected response.create after entering RESOLVE"
        last = response_creates[-1]
        response_payload = last.get("response", {})
        assert response_payload.get("tool_choice") != "required", (
            "RESOLVE entry must not force tool_choice=required — model needs to speak first "
            "(ask caller to confirm before calling create_ticket)"
        )
    finally:
        h.stop()


async def test_scenario_triage_routes_to_account():
    """TRIAGE phase_complete(service_category='account') routes DIAGNOSE to account tools."""
    h = await ScenarioHarness.create("sc-routing-account")
    try:
        await h.fsm.enter(ConvPhase.GREETING)
        await h.call_tool("phase_complete", {"summary": "account question"})
        h.assert_phase(ConvPhase.VERIFY)
        await h.call_tool("phase_complete", {"summary": "verified"})
        h.assert_phase(ConvPhase.TRIAGE)

        await h.call_tool("phase_complete", {"summary": "account update", "service_category": "account"})
        h.assert_phase(ConvPhase.DIAGNOSE)

        call = await h.store.get_call(h.call_id)
        assert call.service_category == "account"

        diagnose_tools = h.tool_names_in_last_update()
        assert "get_account_details" in diagnose_tools
        assert "get_service_status" not in diagnose_tools

        await h.call_tool("phase_complete", {"summary": "details reported"})
        h.assert_phase(ConvPhase.RESOLVE)

        resolve_tools = h.tool_names_in_last_update()
        assert "update_contact_info" in resolve_tools
    finally:
        h.stop()
