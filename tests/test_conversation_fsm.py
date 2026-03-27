"""Tests for conversation state machine phase transitions and escalation."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import sip_bridge.call_controller  # ensure module is importable for patching
from core.models import Call, CallState, ConvPhase
from core.state_store import StateStore
from sip_bridge.conversation_fsm import ConversationFSM


@pytest.fixture
def store():
    return StateStore()


@pytest.fixture
def session_manager():
    sm = MagicMock()
    sm.send_session_update = AsyncMock()
    return sm


@pytest.fixture
def call():
    return Call(call_id="test-call-1", state=CallState.ACTIVE)


def _mock_settings(**overrides):
    defaults = dict(
        openai_model="gpt-realtime-mini",
        openai_voice="alloy",
        default_language="en-US",
        escalation_frustration_limit=3,
        escalation_tool_failure_limit=2,
        human_agent_sip_uri="sip:queue@test",
        max_turns_per_phase=8,
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


async def test_phase_advances_in_order(store, session_manager, call):
    await store.create_call(call)
    with patch("sip_bridge.conversation_fsm.store", store), \
         patch("sip_bridge.conversation_fsm.get_settings") as mock_cfg, \
         patch("sip_bridge.prompt_builder.get_settings") as mock_s:
        mock_s.return_value = _mock_settings()
        mock_cfg.return_value = _mock_settings()
        fsm = ConversationFSM("test-call-1", session_manager)
        assert fsm.phase == ConvPhase.GREETING

        await fsm.advance()
        assert fsm.phase == ConvPhase.VERIFY

        await fsm.advance()
        assert fsm.phase == ConvPhase.TRIAGE

        await fsm.advance()
        assert fsm.phase == ConvPhase.DIAGNOSE

        await fsm.advance()
        assert fsm.phase == ConvPhase.RESOLVE

        await fsm.advance()
        assert fsm.phase == ConvPhase.WRAP_UP


async def test_verify_skipped_for_known_caller(store, session_manager):
    """When account_id is set (caller ID match), VERIFY is skipped."""
    call = Call(call_id="test-call-known", state=CallState.ACTIVE, account_id="ACC-001")
    await store.create_call(call)
    with patch("sip_bridge.conversation_fsm.store", store), \
         patch("sip_bridge.conversation_fsm.get_settings") as mock_cfg, \
         patch("sip_bridge.prompt_builder.get_settings") as mock_s:
        mock_s.return_value = _mock_settings()
        mock_cfg.return_value = _mock_settings()
        fsm = ConversationFSM("test-call-known", session_manager)
        await fsm.advance()  # GREETING → should skip VERIFY, go to TRIAGE
        assert fsm.phase == ConvPhase.TRIAGE


async def test_backward_transition(store, session_manager, call):
    """transition() allows backward jumps (e.g. RESOLVE → DIAGNOSE)."""
    await store.create_call(call)
    with patch("sip_bridge.conversation_fsm.store", store), \
         patch("sip_bridge.conversation_fsm.get_settings") as mock_cfg, \
         patch("sip_bridge.prompt_builder.get_settings") as mock_s:
        mock_s.return_value = _mock_settings()
        mock_cfg.return_value = _mock_settings()
        fsm = ConversationFSM("test-call-1", session_manager)
        # Manually put FSM in RESOLVE
        await fsm.enter(ConvPhase.RESOLVE)
        assert fsm.phase == ConvPhase.RESOLVE

        await fsm.transition(ConvPhase.DIAGNOSE, reason="caller reported new symptom")
        assert fsm.phase == ConvPhase.DIAGNOSE


async def test_turn_count_fallback(store, session_manager, call):
    """record_turn() auto-advances after max_turns_per_phase responses."""
    await store.create_call(call)
    with patch("sip_bridge.conversation_fsm.store", store), \
         patch("sip_bridge.conversation_fsm.get_settings") as mock_cfg, \
         patch("sip_bridge.prompt_builder.get_settings") as mock_s:
        mock_s.return_value = _mock_settings(max_turns_per_phase=3)
        mock_cfg.return_value = _mock_settings(max_turns_per_phase=3)
        fsm = ConversationFSM("test-call-1", session_manager)
        await fsm.enter(ConvPhase.GREETING)
        assert fsm.phase == ConvPhase.GREETING

        await fsm.record_turn()
        await fsm.record_turn()
        assert fsm.phase == ConvPhase.GREETING  # not yet

        await fsm.record_turn()  # 3rd turn triggers auto-advance
        assert fsm.phase == ConvPhase.VERIFY


async def test_wrap_up_ends_session(store, session_manager, call):
    """phase_complete from WRAP_UP marks the call ENDED and closes the session."""
    await store.create_call(call)
    mock_sm_instance = session_manager  # already an AsyncMock-equipped mock
    mock_sm_instance.close = AsyncMock()

    with patch("sip_bridge.conversation_fsm.store", store), \
         patch("sip_bridge.conversation_fsm.get_settings") as mock_cfg, \
         patch("sip_bridge.prompt_builder.get_settings") as mock_s, \
         patch("sip_bridge.session_manager.get_session", return_value=mock_sm_instance):
        mock_s.return_value = _mock_settings()
        mock_cfg.return_value = _mock_settings()
        fsm = ConversationFSM("test-call-1", session_manager)
        await fsm.enter(ConvPhase.WRAP_UP)
        await fsm.advance()  # phase_complete from WRAP_UP

        updated_call = await store.get_call("test-call-1")
        assert updated_call.state == CallState.ENDED
        assert updated_call.hangup_cause == "normal"
        mock_sm_instance.close.assert_awaited_once()


async def test_escalation_on_frustration_limit(store, session_manager, call):
    await store.create_call(call)
    with patch("sip_bridge.conversation_fsm.store", store), \
         patch("sip_bridge.call_controller.refer", new_callable=AsyncMock) as mock_refer, \
         patch("sip_bridge.prompt_builder.get_settings") as mock_s, \
         patch("sip_bridge.conversation_fsm.get_settings") as mock_cfg:
        mock_s.return_value = _mock_settings()
        mock_cfg.return_value = _mock_settings()
        fsm = ConversationFSM("test-call-1", session_manager)

        call.frustration_count = 2
        await store.update_call(call)
        result = await fsm.check_escalation()
        assert result is False

        call.frustration_count = 3
        await store.update_call(call)
        result = await fsm.check_escalation()
        assert result is True
        mock_refer.assert_awaited_once_with("test-call-1", "sip:queue@test")


async def test_escalation_on_tool_failure_limit(store, session_manager, call):
    await store.create_call(call)
    with patch("sip_bridge.conversation_fsm.store", store), \
         patch("sip_bridge.call_controller.refer", new_callable=AsyncMock) as mock_refer, \
         patch("sip_bridge.prompt_builder.get_settings") as mock_s, \
         patch("sip_bridge.conversation_fsm.get_settings") as mock_cfg:
        mock_s.return_value = _mock_settings()
        mock_cfg.return_value = _mock_settings()
        fsm = ConversationFSM("test-call-1", session_manager)
        call.tool_failure_count = 2
        await store.update_call(call)
        result = await fsm.check_escalation()
        assert result is True


async def test_no_double_escalation(store, session_manager, call):
    """Once escalated, further check_escalation calls should be no-ops."""
    await store.create_call(call)
    with patch("sip_bridge.conversation_fsm.store", store), \
         patch("sip_bridge.call_controller.refer", new_callable=AsyncMock) as mock_refer, \
         patch("sip_bridge.prompt_builder.get_settings") as mock_s, \
         patch("sip_bridge.conversation_fsm.get_settings") as mock_cfg:
        mock_s.return_value = _mock_settings()
        mock_cfg.return_value = _mock_settings()
        fsm = ConversationFSM("test-call-1", session_manager)
        call.frustration_count = 3
        await store.update_call(call)

        await fsm.check_escalation()
        await fsm.check_escalation()  # second call should not re-escalate
        assert mock_refer.await_count == 1


async def test_auto_escalation_sends_spoken_message_before_refer(store, session_manager, call):
    """Auto-escalation must inject a spoken transfer message before firing the SIP REFER."""
    session_manager.send_event = AsyncMock()
    session_manager.wait_for_response_done = AsyncMock(return_value=True)
    session_manager.close = AsyncMock()

    await store.create_call(call)
    with patch("sip_bridge.conversation_fsm.store", store), \
         patch("sip_bridge.call_controller.refer", new_callable=AsyncMock) as mock_refer, \
         patch("sip_bridge.session_manager.get_session", return_value=session_manager), \
         patch("sip_bridge.prompt_builder.get_settings") as mock_s, \
         patch("sip_bridge.conversation_fsm.get_settings") as mock_cfg:
        mock_s.return_value = _mock_settings()
        mock_cfg.return_value = _mock_settings()
        fsm = ConversationFSM("test-call-1", session_manager)
        call.frustration_count = 3
        await store.update_call(call)

        await fsm.check_escalation()

        # send_event must have been called (conversation.item.create + response.create)
        assert session_manager.send_event.await_count >= 2
        # wait_for_response_done must be awaited so the message plays before the transfer
        session_manager.wait_for_response_done.assert_awaited_once()
        # refer still fires after the message
        mock_refer.assert_awaited_once()


# ── Teardown duration / CDR tests ─────────────────────────────────────────────

async def test_teardown_transferring_sets_ended_state_and_duration():
    """_teardown with a TRANSFERRING call must set state=ENDED, ended_at, duration_seconds,
    hangup_cause='transferred', publish CALL_ENDED, and trigger CDR save."""
    from datetime import datetime, timezone, timedelta
    from core.models import TokenAggregate
    from sip_bridge.session_manager import SessionManager

    local_store = StateStore()
    answered = datetime.now(timezone.utc) - timedelta(seconds=45)
    call = Call(
        call_id="transfer-1",
        state=CallState.TRANSFERRING,
        answered_at=answered,
        token_total=TokenAggregate(scope="transfer-1"),
    )
    await local_store.create_call(call)

    published_events = []

    async def fake_publish(topic, payload):
        published_events.append((topic, payload))

    with patch("sip_bridge.session_manager.store", local_store), \
         patch("sip_bridge.session_manager.bus") as mock_bus, \
         patch("sip_bridge.call_controller.hangup", new_callable=AsyncMock), \
         patch("sip_bridge.session_manager.get_settings") as mock_cfg:
        mock_bus.publish = AsyncMock(side_effect=fake_publish)
        mock_cfg.return_value.transfer_hangup_delay_seconds = 0

        sm = SessionManager("transfer-1")
        # Stub out CDR save so it does not require a DB
        sm._save_cdr = AsyncMock()
        await sm._teardown()

    ended_call = await local_store.get_call("transfer-1")
    assert ended_call.state == CallState.ENDED
    assert ended_call.ended_at is not None
    assert ended_call.hangup_cause == "transferred"
    assert ended_call.duration_seconds is not None
    assert ended_call.duration_seconds >= 44  # ~45 s, allow 1 s tolerance

    # CALL_ENDED must have been published
    from core.models import Topic
    topics_published = [t for t, _ in published_events]
    assert Topic.CALL_ENDED in topics_published

    # CDR save must have been triggered (scheduled via create_task, not directly awaited)
    sm._save_cdr.assert_called_once()
