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
from websockets.exceptions import ConnectionClosed, InvalidStatus

from config.settings import get_settings
from core.event_bus import bus
from core.models import CallState, ConvPhase, Session, Topic, TokenUsage, WSState
from core.state_store import store

log = logging.getLogger(__name__)

# Registry of active sessions keyed by call_id
_sessions: dict[str, "SessionManager"] = {}


def get_session(call_id: str) -> "SessionManager | None":
    return _sessions.get(call_id)


_RECONNECT_BACKOFFS = [0.5, 1.0, 2.0]  # seconds; len() == max reconnect attempts


class SessionManager:
    def __init__(self, call_id: str) -> None:
        self._call_id = call_id
        self._ws: Any = None
        self._session_id: str = ""
        self._fsm: Any = None  # set after first connect to avoid circular import
        self._closing_intentionally = False  # set by close() to suppress reconnect
        self._turn_counter = 0  # sequential index for transcript turns
        self._greeting_triggered = False  # SIP calls never send session.created; track this
        self._response_ready = asyncio.Event()  # cleared while a response is active
        self._response_ready.set()  # initially ready
        self._tool_lock = asyncio.Lock()  # serialises concurrent tool call handlers

    async def connect(self, session_data: dict) -> None:
        """Open WebSocket to OpenAI Realtime for this call and start event loop.

        On unexpected disconnection, retries up to len(_RECONNECT_BACKOFFS) times
        with exponential backoff before giving up and marking the call as failed.
        """
        s = get_settings()
        call_id = self._call_id
        self._session_id = session_data.get("id", "")

        session = Session(
            session_id=self._session_id,
            call_id=call_id,
            model=s.openai_model,
            ws_state=WSState.CONNECTING,
        )
        await store.set_session(session)

        url = f"wss://api.openai.com/v1/realtime?call_id={call_id}"
        headers = {
            "Authorization": f"Bearer {s.openai_api_key}",
        }
        log.debug("Attempting WS connect: url=%s", url, extra={"call_id": call_id})

        _sessions[call_id] = self

        for attempt in range(len(_RECONNECT_BACKOFFS) + 1):
            try:
                async with websockets.connect(
                    url,
                    additional_headers=headers,
                    ping_interval=s.ws_ping_interval,
                    ping_timeout=s.ws_ping_timeout,
                ) as ws:
                    self._ws = ws
                    session.ws_state = WSState.OPEN
                    await store.set_session(session)

                    if attempt == 0:
                        # First connection — initialise FSM and enter GREETING
                        log.info("WebSocket connected", extra={"call_id": call_id})
                        from core.models import ConvPhase
                        from sip_bridge.conversation_fsm import ConversationFSM
                        self._fsm = ConversationFSM(call_id, self)
                        await self._fsm.enter(ConvPhase.GREETING)
                    else:
                        # Reconnect — restore session config for current phase
                        log.info(
                            "WebSocket reconnected (attempt %d), re-entering phase %s",
                            attempt, self._fsm.phase.value,
                            extra={"call_id": call_id},
                        )
                        from db.repository import emit_call_event, EVENT_WS_RECONNECTED
                        emit_call_event(call_id, EVENT_WS_RECONNECTED, {"attempt": attempt})
                        await self._fsm.enter(self._fsm.phase)

                    await self._event_loop(session)
                    break  # clean exit — no reconnect needed

            except ConnectionClosed as exc:
                if self._closing_intentionally:
                    log.debug("WebSocket closed intentionally", extra={"call_id": call_id})
                    break

                await store.record_ws_error()

                if attempt < len(_RECONNECT_BACKOFFS):
                    delay = _RECONNECT_BACKOFFS[attempt]
                    log.warning(
                        "WebSocket disconnected (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1, len(_RECONNECT_BACKOFFS), delay, exc,
                        extra={"call_id": call_id},
                    )
                    session.ws_state = WSState.CONNECTING
                    await store.set_session(session)
                    await asyncio.sleep(delay)
                else:
                    log.error(
                        "WebSocket reconnection failed after %d attempts",
                        len(_RECONNECT_BACKOFFS) + 1,
                        extra={"call_id": call_id},
                    )
                    from db.repository import emit_call_event, EVENT_WS_FAILED
                    emit_call_event(call_id, EVENT_WS_FAILED, {"attempts": len(_RECONNECT_BACKOFFS) + 1})
                    await self._handle_ws_failure()

            except InvalidStatus as exc:
                body = exc.response.body.decode(errors="replace") if exc.response.body else "<empty>"
                log.error(
                    "WebSocket handshake rejected: HTTP %d — %s",
                    exc.response.status_code, body,
                    extra={"call_id": call_id},
                )
                await store.record_ws_error()
                await self._handle_ws_failure()
                break

            except Exception as exc:
                log.error("WebSocket error: %s", exc, extra={"call_id": call_id})
                await store.record_ws_error()
                break

        _sessions.pop(call_id, None)
        self._ws = None
        session.ws_state = WSState.CLOSED
        await store.set_session(session)
        await self._teardown()

    async def _handle_ws_failure(self) -> None:
        """Called after all reconnect attempts are exhausted."""
        call = await store.get_call(self._call_id)
        if call:
            call.hangup_cause = "ws_failure"
            await store.update_call(call)
        await store.record_reconnect_failure()
        try:
            from sip_bridge import call_controller
            await call_controller.hangup(self._call_id)
        except Exception as exc:
            log.warning("hangup after ws_failure failed: %s", exc, extra={"call_id": self._call_id})

    async def _event_loop(self, session: Session) -> None:
        call_id = self._call_id
        async for raw in self._ws:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type", "")
            log.info("OpenAI event: %s", event_type, extra={"call_id": call_id})
            session.last_event_at = datetime.now(timezone.utc)
            await store.set_session(session)

            # Fan-out raw event to dashboard
            await bus.publish(Topic.CALL_UPDATED, {"call_id": call_id, "event": event})

            await self._dispatch_event(event, session)

    async def _dispatch_event(self, event: dict, session: Session) -> None:
        t = event.get("type", "")
        call_id = self._call_id

        if t == "session.created":
            # Fires for direct API sessions; SIP calls skip this and use session.updated below
            if not self._greeting_triggered:
                log.info("Session created — triggering greeting", extra={"call_id": call_id})
                self._greeting_triggered = True
                await self.send_event({"type": "response.create"})

        elif t == "session.updated":
            # SIP calls never send session.created — trigger the greeting on the first
            # session.updated (which confirms our initial session.update was applied).
            if not self._greeting_triggered:
                log.info("Session updated — triggering greeting (SIP path)", extra={"call_id": call_id})
                self._greeting_triggered = True
                await self.send_event({"type": "response.create"})

        elif t == "response.created":
            self._response_ready.clear()

        elif t == "response.done":
            self._response_ready.set()
            session.response_count += 1
            await store.set_session(session)
            if self._fsm:
                await self._fsm.record_turn()
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

        elif t == "response.audio_transcript.done":
            text = event.get("transcript", "")
            if text:
                asyncio.create_task(self._save_transcript_turn("assistant", text))

        elif t == "conversation.item.input_audio_transcription.completed":
            text = event.get("transcript", "")
            if text:
                if self._fsm:
                    await self._check_frustration(text)
                asyncio.create_task(self._save_transcript_turn("caller", text))

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
                fsm=self._fsm,
            ))

        elif t == "session.closed":
            log.info("Session closed by server", extra={"call_id": call_id})

        elif t == "error":
            log.error("Realtime API error: %s", event.get("error", {}), extra={"call_id": call_id})

    async def _teardown(self) -> None:
        """Mark call as ENDED, publish CALL_ENDED, and persist CDR."""
        from datetime import datetime, timezone
        from sip_bridge import call_controller
        call = await store.get_call(self._call_id)
        if not call:
            return
        if call.state == CallState.TRANSFERRING:
            # REFER was accepted but OpenAI won't send BYE without an explicit hangup.
            # Delay before BYE to give the SBC time to complete its new INVITE to the
            # transfer target and bridge the caller before the original leg is torn down.
            delay = get_settings().transfer_hangup_delay_seconds
            log.info(
                "Waiting %ds before BYE to allow transfer to complete",
                delay,
                extra={"call_id": self._call_id},
            )
            await asyncio.sleep(delay)
            try:
                await call_controller.hangup(self._call_id)
            except Exception as exc:
                log.warning("hangup after transfer failed: %s", exc, extra={"call_id": self._call_id})
            # Re-fetch after hangup; mark ENDED so CDR is saved and duration is set.
            call = await store.get_call(self._call_id)
            if call and call.state == CallState.TRANSFERRING:
                call.state = CallState.ENDED
                call.ended_at = datetime.now(timezone.utc)
                call.hangup_cause = "transferred"
                if call.answered_at and call.ended_at:
                    call.duration_seconds = (call.ended_at - call.answered_at).total_seconds()
                await store.update_call(call)
                await bus.publish(Topic.CALL_ENDED, call.model_dump(mode="json"))
                log.info("Call ended (transferred)", extra={"call_id": self._call_id})
        elif call.state not in (CallState.ENDED, CallState.FAILED):
            call.state = CallState.ENDED
            call.ended_at = datetime.now(timezone.utc)
            if not call.hangup_cause:
                call.hangup_cause = "normal"
            if call.answered_at and call.ended_at:
                call.duration_seconds = (call.ended_at - call.answered_at).total_seconds()
            await store.update_call(call)
            await bus.publish(Topic.CALL_ENDED, call.model_dump(mode="json"))
            log.info("Call ended", extra={"call_id": self._call_id})

        # Persist CDR regardless of final state (ENDED or FAILED)
        call = await store.get_call(self._call_id)
        if call and call.state in (CallState.ENDED, CallState.FAILED):
            asyncio.create_task(self._save_cdr(call))

    async def _save_cdr(self, call: Any) -> None:
        """Persist a Call Detail Record to the database."""
        try:
            from db import repository
            cdr_data = {
                "call_id": call.call_id,
                "sip_call_id": call.sip_call_id,
                "from_uri": call.from_uri,
                "to_uri": call.to_uri,
                "caller_number": call.caller_number,
                "account_id": call.account_id,
                "state": call.state.value,
                "phase_at_end": call.phase.value if call.phase else None,
                "service_category": call.service_category,
                "hangup_cause": call.hangup_cause,
                "escalated": int(call.escalated),
                "frustration_count": call.frustration_count,
                "tool_failure_count": call.tool_failure_count,
                "total_tokens": call.token_total.total_tokens,
                "input_tokens": call.token_total.input_tokens,
                "output_tokens": call.token_total.output_tokens,
                "input_audio_tokens": call.token_total.input_audio_tokens,
                "output_audio_tokens": call.token_total.output_audio_tokens,
                "cost_usd": call.token_total.cost_usd,
                "duration_seconds": call.duration_seconds,
                "answered_at": call.answered_at,
                "ended_at": call.ended_at,
            }
            await repository.save_cdr(cdr_data)
            log.debug("CDR saved", extra={"call_id": self._call_id})
        except Exception as exc:
            log.warning("Failed to save CDR: %s", exc, extra={"call_id": self._call_id})

    async def send_event(self, event: dict) -> None:
        """Send a client event to the OpenAI Realtime WebSocket."""
        # Clear _response_ready synchronously before the send so that any coroutine
        # awaiting wait_for_response_done() will block even if response.created hasn't
        # arrived yet from the server (avoids the race where a fast tool returns before
        # the server-side event is processed).
        if event.get("type") == "response.create":
            self._response_ready.clear()
        if self._ws:
            try:
                await self._ws.send(json.dumps(event))
            except Exception as exc:
                log.warning("Failed to send event: %s", exc, extra={"call_id": self._call_id})
                if event.get("type") == "response.create":
                    self._response_ready.set()  # reset so future calls don't deadlock

    async def close(self) -> None:
        """Close the WebSocket, ending the Realtime session."""
        self._closing_intentionally = True
        if self._ws:
            try:
                await self._ws.close()
            except Exception as exc:
                log.warning("Error closing WebSocket: %s", exc, extra={"call_id": self._call_id})

    async def wait_for_response_done(self, timeout: float = 10.0) -> bool:
        """Wait until no response is in progress. Returns False if timed out."""
        try:
            await asyncio.wait_for(self._response_ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            log.warning("Timed out waiting for active response to finish", extra={"call_id": self._call_id})
            self._response_ready.set()  # reset so we don't deadlock future calls
            return False

    @property
    def tool_lock(self) -> asyncio.Lock:
        """Lock that serialises concurrent tool call handlers for this session."""
        return self._tool_lock

    async def send_session_update(self, config: dict) -> None:
        """Send a session.update event to dynamically reconfigure the session."""
        await self.send_event({"type": "session.update", "session": config})
        log.debug("session.update sent", extra={"call_id": self._call_id})

    async def _save_transcript_turn(self, role: str, text: str) -> None:
        """Persist a transcript turn to the DB and publish it for the live dashboard."""
        from db import repository
        phase = self._fsm.phase.value if self._fsm else None
        turn_index = self._turn_counter
        self._turn_counter += 1
        try:
            scrubbed_text = await repository.save_transcript_turn(
                self._call_id, turn_index, role, text, phase
            )
        except Exception as exc:
            log.warning(
                "Failed to save transcript turn: %s", exc,
                extra={"call_id": self._call_id},
            )
            return
        from datetime import datetime, timezone
        await bus.publish(Topic.TRANSCRIPT_TURN, {
            "call_id": self._call_id,
            "turn_index": turn_index,
            "role": role,
            "text": scrubbed_text,
            "phase": phase,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def _check_frustration(self, text: str) -> None:
        """Scan a caller transcript turn for frustration signals."""
        normalized = text.lower()
        keywords = get_settings().frustration_keywords.split(",")
        for phrase in keywords:
            phrase = phrase.strip()
            if phrase and phrase in normalized:
                log.info(
                    "Frustration signal detected: %r",
                    phrase,
                    extra={"call_id": self._call_id},
                )
                await self._fsm.record_frustration()
                return  # one increment per turn, avoid double-counting
