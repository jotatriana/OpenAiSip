"""Tests for conversation state machine phase transitions and escalation."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

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


@pytest.mark.asyncio
async def test_phase_advances_in_order(store, session_manager, call):
    await store.create_call(call)
    with patch("sip_bridge.conversation_fsm.store", store), \
         patch("sip_bridge.prompt_builder.get_settings") as mock_s:
        mock_s.return_value = MagicMock(
            openai_model="gpt-4o-realtime-preview",
            openai_voice="alloy",
            default_language="en-US",
        )
        fsm = ConversationFSM("test-call-1", session_manager)
        assert fsm.phase == ConvPhase.GREETING

        await fsm.advance()
        assert fsm.phase == ConvPhase.VERIFY

        await fsm.advance()
        assert fsm.phase == ConvPhase.DIAGNOSE

        await fsm.advance()
        assert fsm.phase == ConvPhase.RESOLVE

        # Already at final phase — should not raise
        await fsm.advance()
        assert fsm.phase == ConvPhase.RESOLVE


@pytest.mark.asyncio
async def test_escalation_on_frustration_limit(store, session_manager, call):
    await store.create_call(call)
    with patch("sip_bridge.conversation_fsm.store", store), \
         patch("sip_bridge.conversation_fsm.call_controller") as mock_cc, \
         patch("sip_bridge.prompt_builder.get_settings") as mock_s, \
         patch("sip_bridge.conversation_fsm.get_settings") as mock_cfg:
        mock_s.return_value = MagicMock(
            openai_model="gpt-4o-realtime-preview",
            openai_voice="alloy",
            default_language="en-US",
        )
        mock_cfg.return_value = MagicMock(
            escalation_frustration_limit=3,
            escalation_tool_failure_limit=2,
            human_agent_sip_uri="sip:queue@test",
        )
        mock_cc.refer = AsyncMock()

        fsm = ConversationFSM("test-call-1", session_manager)

        # Should not escalate yet
        call.frustration_count = 2
        await store.update_call(call)
        result = await fsm.check_escalation()
        assert result is False

        # Should escalate at limit
        call.frustration_count = 3
        await store.update_call(call)
        result = await fsm.check_escalation()
        assert result is True
        mock_cc.refer.assert_awaited_once_with("test-call-1", "sip:queue@test")


@pytest.mark.asyncio
async def test_escalation_on_tool_failure_limit(store, session_manager, call):
    await store.create_call(call)
    with patch("sip_bridge.conversation_fsm.store", store), \
         patch("sip_bridge.conversation_fsm.call_controller") as mock_cc, \
         patch("sip_bridge.prompt_builder.get_settings") as mock_s, \
         patch("sip_bridge.conversation_fsm.get_settings") as mock_cfg:
        mock_s.return_value = MagicMock(
            openai_model="gpt-4o-realtime-preview",
            openai_voice="alloy",
            default_language="en-US",
        )
        mock_cfg.return_value = MagicMock(
            escalation_frustration_limit=3,
            escalation_tool_failure_limit=2,
            human_agent_sip_uri="sip:queue@test",
        )
        mock_cc.refer = AsyncMock()

        fsm = ConversationFSM("test-call-1", session_manager)
        call.tool_failure_count = 2
        await store.update_call(call)
        result = await fsm.check_escalation()
        assert result is True


@pytest.mark.asyncio
async def test_no_double_escalation(store, session_manager, call):
    """Once escalated, further check_escalation calls should be no-ops."""
    await store.create_call(call)
    with patch("sip_bridge.conversation_fsm.store", store), \
         patch("sip_bridge.conversation_fsm.call_controller") as mock_cc, \
         patch("sip_bridge.prompt_builder.get_settings") as mock_s, \
         patch("sip_bridge.conversation_fsm.get_settings") as mock_cfg:
        mock_s.return_value = MagicMock(
            openai_model="gpt-4o-realtime-preview",
            openai_voice="alloy",
            default_language="en-US",
        )
        mock_cfg.return_value = MagicMock(
            escalation_frustration_limit=3,
            escalation_tool_failure_limit=2,
            human_agent_sip_uri="sip:queue@test",
        )
        mock_cc.refer = AsyncMock()

        fsm = ConversationFSM("test-call-1", session_manager)
        call.frustration_count = 3
        await store.update_call(call)

        await fsm.check_escalation()
        await fsm.check_escalation()  # second call should not re-escalate
        assert mock_cc.refer.await_count == 1
