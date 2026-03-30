"""Conversation state machine: GREETING → VERIFY → TRIAGE → DIAGNOSE → RESOLVE → WRAP_UP.

Each phase transition sends a session.update to the OpenAI Realtime API
with phase-specific instructions and tool sets.

Key behaviours:
- VERIFY is skipped when the caller was identified by caller ID (account_id set at call start).
- phase_complete tool calls drive forward advancement; backward jumps use transition().
- A per-phase turn counter auto-advances the FSM if the model forgets to call phase_complete.
- phase_complete from WRAP_UP ends the session cleanly.
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

_PHASE_ORDER = [
    ConvPhase.GREETING,
    ConvPhase.VERIFY,
    ConvPhase.TRIAGE,
    ConvPhase.DIAGNOSE,
    ConvPhase.RESOLVE,
    ConvPhase.WRAP_UP,
]


class ConversationFSM:
    def __init__(self, call_id: str, session_manager: SessionManager) -> None:
        self._call_id = call_id
        self._sm = session_manager
        self._phase = ConvPhase.GREETING
        self._turns_in_phase = 0
        self._phase_tools_called: set[str] = set()

    @property
    def phase(self) -> ConvPhase:
        return self._phase

    def is_tool_already_called(self, tool_name: str) -> bool:
        """Return True if this tool has already been called in the current phase."""
        return tool_name in self._phase_tools_called

    def record_tool_called(self, tool_name: str) -> None:
        """Mark a tool as called for the current phase."""
        self._phase_tools_called.add(tool_name)

    async def enter(self, phase: ConvPhase) -> None:
        """Enter a phase: update call state, send session.update to OpenAI."""
        self._phase = phase
        self._turns_in_phase = 0
        self._phase_tools_called = set()
        call = await store.get_call(self._call_id)
        if call:
            call.phase = phase
            await store.update_call(call)
            await bus.publish(Topic.CALL_UPDATED, call.model_dump(mode="json"))

        caller_name      = call.caller_name      if call else ""
        caller_number    = call.caller_number    if call else ""
        account_id       = call.account_id       if call else ""
        service_names    = call.service_names    if call else []
        service_category = call.service_category if call else None
        config = prompt_builder.build(
            phase,
            caller_name=caller_name,
            caller_number=caller_number,
            account_id=account_id,
            service_names=service_names,
            service_category=service_category,
        )
        await self._sm.send_session_update(config)
        log.info("Phase entered: %s", phase.value, extra={"call_id": self._call_id})
        from db.repository import emit_call_event, EVENT_PHASE_ENTERED
        emit_call_event(self._call_id, EVENT_PHASE_ENTERED, {"phase": phase.value})

    async def advance(self) -> None:
        """Advance to the next phase, skipping VERIFY for known callers.

        Called by phase_complete tool. Ends the session cleanly when called from WRAP_UP.
        """
        idx = _PHASE_ORDER.index(self._phase)

        if idx >= len(_PHASE_ORDER) - 1:
            # WRAP_UP is complete — caller confirmed no more issues
            log.info("WRAP_UP complete — ending session", extra={"call_id": self._call_id})
            await self._end_session()
            return

        next_phase = _PHASE_ORDER[idx + 1]

        # Skip VERIFY when caller was identified by caller ID
        if next_phase == ConvPhase.VERIFY:
            call = await store.get_call(self._call_id)
            if call and call.account_id:
                log.info(
                    "Skipping VERIFY — caller already identified (account %s)",
                    call.account_id,
                    extra={"call_id": self._call_id},
                )
                next_phase = _PHASE_ORDER[idx + 2]  # jump past VERIFY to TRIAGE

        await self.enter(next_phase)

    async def transition(self, target: ConvPhase, reason: str = "") -> None:
        """Jump to any phase (forward or backward). Used for loopback scenarios."""
        if target == self._phase:
            return
        log.info(
            "Phase transition %s → %s%s",
            self._phase.value,
            target.value,
            f" (reason: {reason})" if reason else "",
            extra={"call_id": self._call_id},
        )
        await self.enter(target)

    async def record_turn(self) -> None:
        """Called on each model response. Auto-advances if the turn limit is exceeded."""
        self._turns_in_phase += 1
        s = get_settings()
        # Fast phases have hard turn caps to prevent the model from stalling.
        # TRIAGE: max 2 turns (classify and advance — no conversation allowed).
        # DIAGNOSE: max 4 turns (tool call + report + bridge phrase; if still stuck, force advance).
        _TRIAGE_MAX_TURNS = 2
        _DIAGNOSE_MAX_TURNS = 4
        if self._phase == ConvPhase.TRIAGE:
            limit = _TRIAGE_MAX_TURNS
        elif self._phase == ConvPhase.DIAGNOSE:
            limit = _DIAGNOSE_MAX_TURNS
        else:
            limit = s.max_turns_per_phase
        if self._turns_in_phase >= limit:
            log.warning(
                "Turn limit (%d) reached in phase %s — auto-advancing",
                limit,
                self._phase.value,
                extra={"call_id": self._call_id},
            )
            await self.advance()

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

            # Tell the caller before the transfer so they aren't silently dropped
            from sip_bridge.session_manager import get_session
            sm = get_session(self._call_id)
            if sm:
                await sm.send_event({
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{
                            "type": "text",
                            "text": "Let me connect you with one of our agents who can better assist you — please hold.",
                        }],
                    },
                })
                await sm.send_event({"type": "response.create"})
                await sm.wait_for_response_done(timeout=8.0)

            from sip_bridge import call_controller
            await call_controller.refer(self._call_id, s.human_agent_sip_uri)
            if sm:
                await sm.close()
            return True

        return False

    async def record_frustration(self) -> None:
        """Increment frustration counter and check escalation."""
        call = await store.get_call(self._call_id)
        if call:
            call.frustration_count += 1
            await store.update_call(call)
        await self.check_escalation()

    async def _end_session(self) -> None:
        """Mark call ENDED and close the WebSocket (normal wrap-up completion)."""
        call = await store.get_call(self._call_id)
        if call:
            call.state = CallState.ENDED
            call.hangup_cause = "normal"
            await store.update_call(call)
            await bus.publish(Topic.CALL_UPDATED, call.model_dump(mode="json"))
        from sip_bridge.session_manager import get_session
        sm = get_session(self._call_id)
        if sm:
            await sm.close()
