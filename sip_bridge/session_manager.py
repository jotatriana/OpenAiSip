"""Per-call outbound WebSocket to OpenAI Realtime API.

Manages the full event loop for a single call:
- Connects to OpenAI Realtime WS
- Dispatches server-sent events
- Extracts token usage from response.done
- Drives the conversation FSM
- Handles tool calls via tool_executor
- Tears down on session close
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from config.settings import get_settings
from core.event_bus import bus
from core.models import CallState, Session, Topic, TokenUsage, WSState
from core.state_store import store

log = logging.getLogger(__name__)


class SessionManager:
    def __init__(self, call_id: str) -> None:
        self._call_id = call_id
        self._ws: Any = None
        self._session_id: str = ""
        self._fsm: Any = None  # set after connect to avoid circular import

    async def connect(self, session_data: dict) -> None:
        """Open WebSocket to OpenAI Realtime for this call and start event loop."""
        s = get_settings()
        call_id = self._call_id
        session_id = session_data.get("id", "")
        self._session_id = session_id

        session = Session(
            session_id=session_id,
            call_id=call_id,
            model=s.openai_model,
            ws_state=WSState.CONNECTING,
        )
        await store.set_session(session)

        url = f"wss://api.openai.com/v1/realtime?call_id={call_id}"
        headers = {
            "Authorization": f"Bearer {s.openai_api_key}",
            "OpenAI-Beta": "realtime=v1",
        }

        try:
            async with websockets.connect(url, additional_headers=headers) as ws:
                self._ws = ws
                session.ws_state = WSState.OPEN
                await store.set_session(session)
                log.info("WebSocket connected", extra={"call_id": call_id})

                # Import here to avoid circular import
                from core.models import ConvPhase
                from sip_bridge.conversation_fsm import ConversationFSM
                self._fsm = ConversationFSM(call_id, self)
                await self._fsm.enter(ConvPhase.GREETING)

                await self._event_loop(session)

        except ConnectionClosed as exc:
            log.warning("WebSocket closed: %s", exc, extra={"call_id": call_id})
            await store.record_ws_error()
        except Exception as exc:
            log.error("WebSocket error: %s", exc, extra={"call_id": call_id})
            await store.record_ws_error()
        finally:
            self._ws = None
            session.ws_state = WSState.CLOSED
            await store.set_session(session)
            await self._teardown()

    async def _event_loop(self, session: Session) -> None:
        call_id = self._call_id
        async for raw in self._ws:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            print(f"Received OpenAI event: {event}")

            event_type = event.get("type", "")
            session.last_event_at = datetime.now(timezone.utc)
            await store.set_session(session)

            # Fan-out raw event to dashboard
            await bus.publish(Topic.CALL_UPDATED, {"call_id": call_id, "event": event})

            await self._dispatch_event(event, session)

    async def _dispatch_event(self, event: dict, session: Session) -> None:
        t = event.get("type", "")
        call_id = self._call_id

        if t == "session.created":
            log.info("Session created", extra={"call_id": call_id})

        elif t == "response.done":
            session.response_count += 1
            await store.set_session(session)
            usage_data = event.get("response", {}).get("usage", {})
            if usage_data:
                usage = TokenUsage(
                    call_id=call_id,
                    session_id=self._session_id,
                    response_id=event.get("response", {}).get("id", ""),
                    total_tokens=usage_data.get("total_tokens", 0),
                    input_tokens=usage_data.get("input_tokens", 0),
                    output_tokens=usage_data.get("output_tokens", 0),
                    input_text_tokens=usage_data.get("input_token_details", {}).get("text_tokens", 0),
                    input_audio_tokens=usage_data.get("input_token_details", {}).get("audio_tokens", 0),
                    input_cached_tokens=usage_data.get("input_token_details", {}).get("cached_tokens", 0),
                    output_text_tokens=usage_data.get("output_token_details", {}).get("text_tokens", 0),
                    output_audio_tokens=usage_data.get("output_token_details", {}).get("audio_tokens", 0),
                )
                global_agg = await store.record_token_usage(usage)
                await bus.publish(Topic.TOKEN_USAGE, {
                    "call_id": call_id,
                    "usage": usage.model_dump(mode="json"),
                    "global": global_agg.model_dump(mode="json"),
                })
                log.debug("Token usage recorded: %d total", usage.total_tokens, extra={"call_id": call_id})

        elif t == "response.function_call_arguments.done":
            # Tool call complete — hand off to tool_executor
            from sip_bridge import tool_executor
            asyncio.create_task(tool_executor.handle(
                session_manager=self,
                call_id=call_id,
                response_id=event.get("response_id", ""),
                item_id=event.get("call_id", ""),
                tool_name=event.get("name", ""),
                tool_args=json.loads(event.get("arguments", "{}")),
            ))

        elif t == "session.closed":
            log.info("Session closed by server", extra={"call_id": call_id})

        elif t == "error":
            log.error("Realtime API error: %s", event.get("error", {}), extra={"call_id": call_id})

    async def _teardown(self) -> None:
        """Mark call as ENDED and publish CALL_ENDED."""
        from datetime import datetime, timezone
        call = await store.get_call(self._call_id)
        if call and call.state not in (CallState.ENDED, CallState.FAILED, CallState.TRANSFERRING):
            call.state = CallState.ENDED
            call.ended_at = datetime.now(timezone.utc)
            if not call.hangup_cause:
                call.hangup_cause = "normal"
            if call.answered_at and call.ended_at:
                call.duration_seconds = (call.ended_at - call.answered_at).total_seconds()
            await store.update_call(call)
            await bus.publish(Topic.CALL_ENDED, call.model_dump(mode="json"))
            log.info("Call ended", extra={"call_id": self._call_id})

    async def send_event(self, event: dict) -> None:
        """Send a client event to the OpenAI Realtime WebSocket."""
        if self._ws:
            try:
                await self._ws.send(json.dumps(event))
            except Exception as exc:
                log.warning("Failed to send event: %s", exc, extra={"call_id": self._call_id})

    async def send_session_update(self, config: dict) -> None:
        """Send a session.update event to dynamically reconfigure the session."""
        await self.send_event({"type": "session.update", "session": config})
        log.debug("session.update sent", extra={"call_id": self._call_id})
