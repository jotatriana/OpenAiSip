"""In-memory, async-safe registry for calls, sessions, tokens, and logs."""
from __future__ import annotations

import asyncio
import statistics
import time
from collections import deque
from datetime import datetime, timezone

from core.models import (
    Call,
    CallState,
    ChannelHealth,
    LogEntry,
    Session,
    SIPRegistrationState,
    TokenAggregate,
    TokenUsage,
)


class StateStore:
    def __init__(self, log_buffer_size: int = 500) -> None:
        self._lock = asyncio.Lock()

        # Active and recently ended calls keyed by call_id
        self._calls: dict[str, Call] = {}
        self._sessions: dict[str, Session] = {}  # keyed by call_id

        # Token aggregates
        self._global_tokens = TokenAggregate(scope="global")

        # Log ring buffer
        self._log_buffer: deque[LogEntry] = deque(maxlen=log_buffer_size)
        self._log_seq = 0

        # Metrics for channel health
        self._setup_latencies: deque[float] = deque(maxlen=100)  # ms
        self._call_accept_timestamps: dict[str, float] = {}  # call_id -> epoch
        self._total_calls_today = 0
        self._total_calls_failed = 0
        self._ws_errors_timestamps: deque[float] = deque(maxlen=1000)
        self._last_call_at: datetime | None = None

    # ── Call management ───────────────────────────────────────────────────────

    async def create_call(self, call: Call) -> None:
        async with self._lock:
            self._calls[call.call_id] = call
            self._call_accept_timestamps[call.call_id] = time.monotonic()
            self._total_calls_today += 1
            self._last_call_at = datetime.now(timezone.utc)

    async def update_call(self, call: Call) -> None:
        async with self._lock:
            if call.state == CallState.ACTIVE and call.call_id in self._call_accept_timestamps:
                latency_ms = (time.monotonic() - self._call_accept_timestamps.pop(call.call_id)) * 1000
                self._setup_latencies.append(latency_ms)
            if call.state in (CallState.FAILED,):
                self._total_calls_failed += 1
            self._calls[call.call_id] = call

    async def get_call(self, call_id: str) -> Call | None:
        async with self._lock:
            return self._calls.get(call_id)

    async def get_all_calls(self) -> list[Call]:
        async with self._lock:
            return list(self._calls.values())

    async def get_active_calls(self) -> list[Call]:
        async with self._lock:
            return [c for c in self._calls.values() if c.state in (CallState.RINGING, CallState.ACTIVE, CallState.TRANSFERRING)]

    # ── Session management ────────────────────────────────────────────────────

    async def set_session(self, session: Session) -> None:
        async with self._lock:
            self._sessions[session.call_id] = session

    async def get_session(self, call_id: str) -> Session | None:
        async with self._lock:
            return self._sessions.get(call_id)

    async def get_active_session_count(self) -> int:
        async with self._lock:
            from core.models import WSState
            return sum(1 for s in self._sessions.values() if s.ws_state == WSState.OPEN)

    # ── Token tracking ────────────────────────────────────────────────────────

    async def record_token_usage(self, usage: TokenUsage) -> TokenAggregate:
        async with self._lock:
            call = self._calls.get(usage.call_id)
            if call:
                call.token_total.add(usage)
            self._global_tokens.add(usage)
            return self._global_tokens.model_copy(deep=True)

    async def get_global_tokens(self) -> TokenAggregate:
        async with self._lock:
            return self._global_tokens.model_copy(deep=True)

    async def get_call_tokens(self, call_id: str) -> TokenAggregate | None:
        async with self._lock:
            call = self._calls.get(call_id)
            return call.token_total.model_copy(deep=True) if call else None

    # ── Log buffer ────────────────────────────────────────────────────────────

    async def append_log(self, entry: LogEntry) -> None:
        async with self._lock:
            self._log_buffer.append(entry)

    def next_log_seq(self) -> int:
        self._log_seq += 1
        return self._log_seq

    async def get_logs(self, since_seq: int = 0, limit: int = 100) -> list[LogEntry]:
        async with self._lock:
            entries = [e for e in self._log_buffer if e.sequence_id > since_seq]
            return entries[:limit]

    # ── Channel health ─────────────────────────────────────────────────────────

    async def record_ws_error(self) -> None:
        async with self._lock:
            self._ws_errors_timestamps.append(time.time())

    async def build_channel_health(self, sip_stale_threshold_seconds: int) -> ChannelHealth:
        async with self._lock:
            active = [c for c in self._calls.values() if c.state in (CallState.RINGING, CallState.ACTIVE, CallState.TRANSFERRING)]

            latencies = list(self._setup_latencies)
            avg_lat = statistics.mean(latencies) if latencies else 0.0
            p95_lat = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else (max(latencies) if latencies else 0.0)

            now = time.time()
            ws_errors_1h = sum(1 for t in self._ws_errors_timestamps if now - t <= 3600)

            # Infer SIP registration from last call arrival time
            sip_state = SIPRegistrationState.UNKNOWN
            if self._last_call_at:
                age = (datetime.now(timezone.utc) - self._last_call_at).total_seconds()
                sip_state = SIPRegistrationState.REGISTERED if age <= sip_stale_threshold_seconds else SIPRegistrationState.DEGRADED

            from core.models import WSState
            ws_count = sum(1 for s in self._sessions.values() if s.ws_state == WSState.OPEN)

            return ChannelHealth(
                sip_registration_state=sip_state,
                active_call_count=len(active),
                ws_session_count=ws_count,
                avg_call_setup_latency_ms=avg_lat,
                p95_call_setup_latency_ms=p95_lat,
                total_calls_today=self._total_calls_today,
                total_calls_failed=self._total_calls_failed,
                last_call_at=self._last_call_at,
                openai_ws_errors_1h=ws_errors_1h,
            )

    # ── Snapshot ──────────────────────────────────────────────────────────────

    async def snapshot(self, sip_stale_threshold_seconds: int) -> dict:
        active_calls = await self.get_active_calls()
        global_tokens = await self.get_global_tokens()
        logs = await self.get_logs(since_seq=0, limit=100)
        health = await self.build_channel_health(sip_stale_threshold_seconds)
        return {
            "active_calls": [c.model_dump(mode="json") for c in active_calls],
            "global_tokens": global_tokens.model_dump(mode="json"),
            "recent_logs": [e.model_dump(mode="json") for e in logs],
            "channel_health": health.model_dump(mode="json"),
        }


# Module-level singleton
store = StateStore()
