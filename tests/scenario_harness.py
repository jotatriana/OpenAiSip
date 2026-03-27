"""Scenario test harness for end-to-end FSM + tool executor flows.

The harness creates a ConversationFSM backed by a real StateStore but with all
external I/O mocked:
  - session_manager.send_session_update  → captured in harness.session_updates
  - session_manager.send_event           → captured in harness.sent_events
  - db.repository.*                      → configurable per-test via harness.db
  - call_controller.refer / hangup       → no-op AsyncMocks
  - get_settings                         → test defaults (overridable)

Usage:

    async def test_happy_path():
        h = await ScenarioHarness.create("call-1")
        h.db.find_customer.return_value = {"account_id": "ACC-1", ...}

        await h.fsm.enter(ConvPhase.GREETING)
        h.assert_phase(ConvPhase.GREETING)

        # Simulate model calling phase_complete
        await h.call_tool("phase_complete")
        h.assert_phase(ConvPhase.VERIFY)
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from core.models import Call, CallState, ConvPhase
from core.state_store import StateStore
from sip_bridge.conversation_fsm import ConversationFSM


def _mock_settings(**overrides) -> MagicMock:
    defaults = dict(
        openai_model="gpt-realtime-mini",
        openai_voice="alloy",
        default_language="en-US",
        escalation_frustration_limit=3,
        escalation_tool_failure_limit=2,
        human_agent_sip_uri="sip:queue@test",
        max_turns_per_phase=8,
        tool_timeout_seconds=5.0,
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


class MockDB:
    """Configurable mock for db.repository functions."""
    def __init__(self) -> None:
        self.find_customer = AsyncMock(return_value=None)
        self.get_service_status = AsyncMock(return_value={"services": [], "open_incidents": []})
        self.create_ticket = AsyncMock(return_value={"ticket_id": "TKT-00000001", "status": "created"})
        self.save_escalation_context = AsyncMock()
        self.get_escalation_context = AsyncMock(return_value=None)
        self.emit_call_event = MagicMock()  # sync fire-and-forget


class ScenarioHarness:
    """Sets up a complete FSM scenario with all external I/O mocked."""

    def __init__(
        self,
        call_id: str,
        store: StateStore,
        session_manager: MagicMock,
        fsm: ConversationFSM,
        settings: MagicMock,
        db: MockDB,
        patches: list,
    ) -> None:
        self.call_id = call_id
        self.store = store
        self.session_manager = session_manager
        self.fsm = fsm
        self.settings = settings
        self.db = db
        self._patches = patches

        # Captured call records
        self.session_updates: list[dict] = []
        self.sent_events: list[dict] = []

        session_manager.send_session_update = AsyncMock(
            side_effect=lambda cfg: self.session_updates.append(cfg)
        )
        session_manager.send_event = AsyncMock(
            side_effect=lambda evt: self.sent_events.append(evt)
        )
        session_manager.close = AsyncMock()
        session_manager.wait_for_response_done = AsyncMock(return_value=True)
        # tool_lock must be a real asyncio.Lock — MagicMock doesn't support async with
        import asyncio
        session_manager.tool_lock = asyncio.Lock()

    @classmethod
    async def create(
        cls,
        call_id: str = "test-call",
        account_id: str = "",
        caller_number: str = "",
        settings_overrides: dict | None = None,
    ) -> "ScenarioHarness":
        store = StateStore()
        call = Call(call_id=call_id, state=CallState.ACTIVE, account_id=account_id,
                    caller_number=caller_number)
        await store.create_call(call)

        session_manager = MagicMock()
        db = MockDB()
        settings = _mock_settings(**(settings_overrides or {}))

        patches = [
            patch("sip_bridge.conversation_fsm.store", store),
            patch("sip_bridge.conversation_fsm.get_settings", return_value=settings),
            patch("sip_bridge.prompt_builder.get_settings", return_value=settings),
            patch("sip_bridge.call_controller.refer", new_callable=AsyncMock),
            patch("sip_bridge.call_controller.hangup", new_callable=AsyncMock),
            patch("sip_bridge.session_manager.get_session", return_value=session_manager),
            patch("db.repository.emit_call_event", db.emit_call_event),
            patch("core.state_store.store", store),
        ]
        for p in patches:
            p.start()

        fsm = ConversationFSM(call_id, session_manager)

        harness = cls(call_id, store, session_manager, fsm, settings, db, patches)
        return harness

    def stop(self) -> None:
        for p in self._patches:
            p.stop()

    # ── Convenience helpers ───────────────────────────────────────────────────

    def assert_phase(self, expected: ConvPhase) -> None:
        assert self.fsm.phase == expected, (
            f"Expected phase {expected.value}, got {self.fsm.phase.value}"
        )

    def last_session_update(self) -> dict:
        assert self.session_updates, "No session.update calls recorded"
        return self.session_updates[-1]

    def tool_names_in_last_update(self) -> list[str]:
        update = self.last_session_update()
        tools = update.get("tools", [])
        return [t["name"] for t in tools]

    async def call_tool(
        self,
        tool_name: str,
        tool_args: dict | None = None,
        item_id: str = "item-1",
        response_id: str = "resp-1",
    ) -> None:
        """Simulate the model invoking a tool call."""
        from sip_bridge import tool_executor
        with patch("config.settings.get_settings", return_value=self.settings), \
             patch("db.repository.find_customer", self.db.find_customer), \
             patch("db.repository.get_service_status", self.db.get_service_status), \
             patch("db.repository.create_ticket", self.db.create_ticket), \
             patch("db.repository.save_escalation_context", self.db.save_escalation_context), \
             patch("db.repository.get_escalation_context", self.db.get_escalation_context), \
             patch("db.repository.emit_call_event", self.db.emit_call_event), \
             patch("sip_bridge.tool_executor._write_handoff_context", new_callable=AsyncMock):
            await tool_executor.handle(
                session_manager=self.session_manager,
                call_id=self.call_id,
                response_id=response_id,
                item_id=item_id,
                tool_name=tool_name,
                tool_args=tool_args or {},
                fsm=self.fsm,
            )
