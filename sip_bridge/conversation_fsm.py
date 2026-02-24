"""Conversation state machine: GREETING → VERIFY → DIAGNOSE → RESOLVE.

Each phase transition sends a session.update to the OpenAI Realtime API
with phase-specific instructions and tool sets.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config.settings import get_settings
from core.event_bus import bus
from core.models import CallState, ConvPhase, Topic
from core.state_store import store
from sip_bridge import prompt_builder

if TYPE_CHECKING:
    from sip_bridge.session_manager import SessionManager

log = logging.getLogger(__name__)

_PHASE_ORDER = [ConvPhase.GREETING, ConvPhase.VERIFY, ConvPhase.DIAGNOSE, ConvPhase.RESOLVE]


class ConversationFSM:
    def __init__(self, call_id: str, session_manager: SessionManager) -> None:
        self._call_id = call_id
        self._sm = session_manager
        self._phase = ConvPhase.GREETING

    @property
    def phase(self) -> ConvPhase:
        return self._phase

    async def enter(self, phase: ConvPhase) -> None:
        """Enter a phase: update call state, send session.update to OpenAI."""
        self._phase = phase
        call = await store.get_call(self._call_id)
        if call:
            call.phase = phase
            await store.update_call(call)
            await bus.publish(Topic.CALL_UPDATED, call.model_dump(mode="json"))

        caller_name   = call.caller_name   if call else ""
        caller_number = call.caller_number if call else ""
        config = prompt_builder.build(phase, caller_name=caller_name, caller_number=caller_number)
        await self._sm.send_session_update(config)
        log.info("Phase entered: %s", phase.value, extra={"call_id": self._call_id})

    async def advance(self) -> None:
        """Advance to the next phase in sequence."""
        idx = _PHASE_ORDER.index(self._phase)
        if idx < len(_PHASE_ORDER) - 1:
            await self.enter(_PHASE_ORDER[idx + 1])
        else:
            log.info("Already at final phase", extra={"call_id": self._call_id})

    async def check_escalation(self) -> bool:
        """Check escalation thresholds; trigger SIP REFER if exceeded. Returns True if escalated."""
        s = get_settings()
        call = await store.get_call(self._call_id)
        if not call:
            return False

        should_escalate = (
            call.frustration_count >= s.escalation_frustration_limit
            or call.tool_failure_count >= s.escalation_tool_failure_limit
        )

        if should_escalate and not call.escalated:
            log.warning(
                "Escalation triggered (frustration=%d, tool_failures=%d)",
                call.frustration_count,
                call.tool_failure_count,
                extra={"call_id": self._call_id},
            )
            call.escalated = True
            call.hangup_cause = "escalated"
            call.state = CallState.TRANSFERRING
            await store.update_call(call)
            await bus.publish(Topic.CALL_UPDATED, call.model_dump(mode="json"))

            from sip_bridge import call_controller
            await call_controller.refer(self._call_id, s.human_agent_sip_uri)
            return True

        return False

    async def record_frustration(self) -> None:
        """Increment frustration counter and check escalation."""
        call = await store.get_call(self._call_id)
        if call:
            call.frustration_count += 1
            await store.update_call(call)
        await self.check_escalation()
