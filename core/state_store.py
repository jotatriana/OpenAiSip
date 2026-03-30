"""In-memory, async-safe registry for calls, sessions, tokens, and logs."""
from __future__ import annotations

import asyncio
import statistics
import time
from collections import deque
from datetime import date, datetime, timezone

from core.models import (
    Call,
    CallState,
    ChannelHealth,
    ConvPhase,
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
        self._reconnect_failure_timestamps: deque[float] = deque(maxlen=200)
        self._last_call_at: datetime | None = None

        # Cost / budget tracking
        self._daily_cost_usd: float = 0.0
        self._daily_cost_date: date = datetime.now(timezone.utc).date()
        self._budget_alert_fired_today: bool = False

        # Operator controls
        self._maintenance_mode: bool = False

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

    @staticmethod
    def _compute_cost(usage: TokenUsage) -> float:
        """Compute USD cost for a single response usage event."""
        from config.settings import get_settings
        s = get_settings()
        cost = (
            usage.input_audio_tokens * s.cost_input_audio_per_1k / 1000
            + usage.output_audio_tokens * s.cost_output_audio_per_1k / 1000
            + usage.input_text_tokens * s.cost_input_text_per_1k / 1000
            + usage.output_text_tokens * s.cost_output_text_per_1k / 1000
            + usage.input_cached_tokens * s.cost_input_cached_per_1k / 1000
        )
        return cost

    async def record_token_usage(self, usage: TokenUsage) -> TokenAggregate:
        cost = self._compute_cost(usage)
        async with self._lock:
            call = self._calls.get(usage.call_id)
            if call:
                call.token_total.add(usage, cost_usd=cost)
            self._global_tokens.add(usage, cost_usd=cost)

            # Reset daily accumulator if date has rolled
            today = datetime.now(timezone.utc).date()
            if today != self._daily_cost_date:
                self._daily_cost_date = today
                self._daily_cost_usd = 0.0
                self._budget_alert_fired_today = False
            self._daily_cost_usd += cost

            should_alert = (
                not self._budget_alert_fired_today
                and self._needs_budget_check(self._daily_cost_usd)
            )
            if should_alert:
                self._budget_alert_fired_today = True
            daily_cost_snapshot = self._daily_cost_usd

            snapshot = self._global_tokens.model_copy(deep=True)

        if should_alert:
            from core.event_bus import bus
            from core.models import Topic
            await bus.publish(Topic.BUDGET_ALERT, {
                "daily_cost_usd": daily_cost_snapshot,
                "budget_usd": self._get_budget_limit(),
            })

        return snapshot

    @staticmethod
    def _needs_budget_check(daily_cost: float) -> bool:
        from config.settings import get_settings
        limit = get_settings().daily_budget_usd
        return limit > 0 and daily_cost >= limit

    @staticmethod
    def _get_budget_limit() -> float:
        from config.settings import get_settings
        return get_settings().daily_budget_usd

    async def get_daily_cost_usd(self) -> float:
        async with self._lock:
            today = datetime.now(timezone.utc).date()
            if today != self._daily_cost_date:
                return 0.0
            return self._daily_cost_usd

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

    async def record_reconnect_failure(self) -> None:
        async with self._lock:
            self._reconnect_failure_timestamps.append(time.time())

    async def is_circuit_open(
        self,
        threshold: int,
        window_seconds: int,
        cooldown_seconds: int,
    ) -> bool:
        """Return True if the circuit breaker should reject new calls.

        Trips when >= threshold reconnect failures occur within window_seconds.
        Auto-resets after cooldown_seconds have passed since the last failure.
        """
        async with self._lock:
            now = time.time()
            recent = [t for t in self._reconnect_failure_timestamps if now - t <= window_seconds]
            if len(recent) < threshold:
                return False
            last_failure = max(recent)
            return (now - last_failure) < cooldown_seconds

    async def build_channel_health(
        self,
        sip_stale_threshold_seconds: int,
        circuit_threshold: int = 5,
        circuit_window: int = 300,
        circuit_cooldown: int = 60,
    ) -> ChannelHealth:
        async with self._lock:
            active = [c for c in self._calls.values() if c.state in (CallState.RINGING, CallState.ACTIVE, CallState.TRANSFERRING)]

            latencies = list(self._setup_latencies)
            avg_lat = statistics.mean(latencies) if latencies else 0.0
            p95_lat = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else (max(latencies) if latencies else 0.0)

            now = time.time()
            ws_errors_1h = sum(1 for t in self._ws_errors_timestamps if now - t <= 3600)
            recent_reconnect_failures = [t for t in self._reconnect_failure_timestamps if now - t <= circuit_window]
            circuit_open = (
                len(recent_reconnect_failures) >= circuit_threshold
                and bool(recent_reconnect_failures)
                and (now - max(recent_reconnect_failures)) < circuit_cooldown
            )

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
                circuit_breaker_open=circuit_open,
                reconnect_failures_recent=len(recent_reconnect_failures),
            )

    # ── Maintenance mode ──────────────────────────────────────────────────────

    async def set_maintenance_mode(self, enabled: bool) -> None:
        async with self._lock:
            self._maintenance_mode = enabled

    async def is_maintenance_mode(self) -> bool:
        async with self._lock:
            return self._maintenance_mode

    # ── Historical CDR pre-load ───────────────────────────────────────────────

    async def load_recent_cdrs(self, limit: int) -> int:
        """Pre-populate _calls from recent CDRs so ended calls survive a restart.

        Only inserts CDRs whose call_id is not already present (live calls take
        precedence). Returns the number of CDRs loaded.
        """
        from db import repository

        cdrs = await repository.get_recent_cdrs(limit)
        loaded = 0
        async with self._lock:
            for cdr in cdrs:
                call_id = cdr["call_id"]
                if call_id in self._calls:
                    continue  # live call already present — don't overwrite

                try:
                    state = CallState(cdr["state"]) if cdr.get("state") else CallState.ENDED
                except ValueError:
                    state = CallState.ENDED

                try:
                    phase = ConvPhase(cdr["phase_at_end"]) if cdr.get("phase_at_end") else None
                except ValueError:
                    phase = None

                def _parse_dt(s: str | None) -> datetime | None:
                    if not s:
                        return None
                    dt = datetime.fromisoformat(s)
                    # Normalize to naive UTC — keeps CDR-loaded calls consistent
                    # with live Call objects that use datetime.utcnow() (naive).
                    # Mixed naive/aware datetimes cause TypeError in sort().
                    return dt.replace(tzinfo=None) if dt.tzinfo else dt

                token_total = TokenAggregate(
                    scope=call_id,
                    total_tokens=cdr.get("total_tokens") or 0,
                    input_tokens=cdr.get("input_tokens") or 0,
                    output_tokens=cdr.get("output_tokens") or 0,
                    input_audio_tokens=cdr.get("input_audio_tokens") or 0,
                    output_audio_tokens=cdr.get("output_audio_tokens") or 0,
                    cost_usd=cdr.get("cost_usd") or 0.0,
                )

                call = Call(
                    call_id=call_id,
                    sip_call_id=cdr.get("sip_call_id") or "",
                    from_uri=cdr.get("from_uri") or "",
                    to_uri=cdr.get("to_uri") or "",
                    caller_number=cdr.get("caller_number") or "",
                    account_id=cdr.get("account_id") or "",
                    state=state,
                    phase=phase or ConvPhase.WRAP_UP,
                    created_at=_parse_dt(cdr.get("created_at")) or datetime.now(timezone.utc),
                    answered_at=_parse_dt(cdr.get("answered_at")),
                    ended_at=_parse_dt(cdr.get("ended_at")),
                    duration_seconds=cdr.get("duration_seconds"),
                    hangup_cause=cdr.get("hangup_cause"),
                    escalated=bool(cdr.get("escalated")),
                    frustration_count=cdr.get("frustration_count") or 0,
                    tool_failure_count=cdr.get("tool_failure_count") or 0,
                    token_total=token_total,
                )
                self._calls[call_id] = call
                loaded += 1
        return loaded

    # ── Snapshot ──────────────────────────────────────────────────────────────

    async def snapshot(self, sip_stale_threshold_seconds: int) -> dict:
        # Include all in-memory calls (active + recently ended) so a browser refresh
        # does not lose calls that have already ended in the current process session.
        all_calls = await self.get_all_calls()
        all_calls.sort(key=lambda c: c.created_at, reverse=True)
        recent_calls = all_calls[:50]  # cap payload at 50 most recent

        global_tokens = await self.get_global_tokens()
        logs = await self.get_logs(since_seq=0, limit=100)
        health = await self.build_channel_health(sip_stale_threshold_seconds)

        # Fetch transcript and events for every call in the snapshot.
        # Active calls fetch everything (full live conversation); ended calls are
        # capped at 50 turns to keep the snapshot fast and the payload small.
        # All queries run in parallel to minimise snapshot build latency.
        from db import repository

        async def _fetch(call: Call) -> tuple[str, list, list]:
            is_active = call.state in (CallState.RINGING, CallState.ACTIVE, CallState.TRANSFERRING)
            t_limit = None if is_active else 50
            try:
                transcript, events = await asyncio.gather(
                    repository.get_transcript(call.call_id, limit=t_limit),
                    repository.get_call_events(call.call_id, limit=t_limit),
                )
                return call.call_id, transcript, events
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "Failed to fetch transcript/events for %s: %s", call.call_id, exc, exc_info=True
                )
                return call.call_id, [], []

        results = await asyncio.gather(*[_fetch(c) for c in recent_calls])
        active_call_transcripts: dict[str, list] = {}
        active_call_events: dict[str, list] = {}
        for call_id, transcript, events in results:
            active_call_transcripts[call_id] = transcript
            active_call_events[call_id] = events

        return {
            "active_calls": [c.model_dump(mode="json") for c in recent_calls],
            "global_tokens": global_tokens.model_dump(mode="json"),
            "recent_logs": [e.model_dump(mode="json") for e in logs],
            "channel_health": health.model_dump(mode="json"),
            "active_call_transcripts": active_call_transcripts,
            "active_call_events": active_call_events,
        }


# Module-level singleton
store = StateStore()
