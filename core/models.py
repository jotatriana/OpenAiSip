from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Enumerations ─────────────────────────────────────────────────────────────

class CallState(str, Enum):
    RINGING = "RINGING"
    ACTIVE = "ACTIVE"
    TRANSFERRING = "TRANSFERRING"
    ENDED = "ENDED"
    FAILED = "FAILED"


class ConvPhase(str, Enum):
    GREETING = "GREETING"
    VERIFY = "VERIFY"
    TRIAGE = "TRIAGE"
    DIAGNOSE = "DIAGNOSE"
    RESOLVE = "RESOLVE"
    WRAP_UP = "WRAP_UP"


class WSState(str, Enum):
    CONNECTING = "CONNECTING"
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"


class SIPRegistrationState(str, Enum):
    REGISTERED = "REGISTERED"
    DEGRADED = "DEGRADED"
    UNKNOWN = "UNKNOWN"


# ── Token models ──────────────────────────────────────────────────────────────

class TokenUsage(BaseModel):
    call_id: str
    session_id: str
    response_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    input_text_tokens: int = 0
    input_audio_tokens: int = 0
    input_cached_tokens: int = 0
    output_text_tokens: int = 0
    output_audio_tokens: int = 0


class TokenAggregate(BaseModel):
    scope: str  # call_id or "global"
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    input_text_tokens: int = 0
    input_audio_tokens: int = 0
    input_cached_tokens: int = 0
    output_text_tokens: int = 0
    output_audio_tokens: int = 0
    response_count: int = 0
    cost_usd: float = 0.0
    last_updated: datetime = Field(default_factory=datetime.utcnow)

    def add(self, usage: TokenUsage, cost_usd: float = 0.0) -> None:
        self.total_tokens += usage.total_tokens
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.input_text_tokens += usage.input_text_tokens
        self.input_audio_tokens += usage.input_audio_tokens
        self.input_cached_tokens += usage.input_cached_tokens
        self.output_text_tokens += usage.output_text_tokens
        self.output_audio_tokens += usage.output_audio_tokens
        self.response_count += 1
        self.cost_usd += cost_usd
        self.last_updated = datetime.utcnow()


# ── Call / Session models ─────────────────────────────────────────────────────

class Call(BaseModel):
    call_id: str
    sip_call_id: str = ""
    from_uri: str = ""
    to_uri: str = ""
    caller_name: str = ""
    caller_number: str = ""
    account_id: str = ""          # set at call start if caller ID matches a known customer
    service_names: list[str] = Field(default_factory=list)  # service types fetched at call start
    state: CallState = CallState.RINGING
    phase: ConvPhase = ConvPhase.GREETING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: float | None = None
    hangup_cause: str | None = None  # normal | transferred | escalated | error
    escalated: bool = False
    frustration_count: int = 0
    tool_failure_count: int = 0
    token_total: TokenAggregate = Field(default_factory=lambda: TokenAggregate(scope=""))

    def model_post_init(self, __context: Any) -> None:
        if not self.token_total.scope:
            self.token_total.scope = self.call_id


class Session(BaseModel):
    session_id: str
    call_id: str
    model: str
    ws_state: WSState = WSState.CONNECTING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_event_at: datetime = Field(default_factory=datetime.utcnow)
    response_count: int = 0


# ── Channel health ────────────────────────────────────────────────────────────

class ChannelHealth(BaseModel):
    measured_at: datetime = Field(default_factory=datetime.utcnow)
    sip_registration_state: SIPRegistrationState = SIPRegistrationState.UNKNOWN
    active_call_count: int = 0
    ws_session_count: int = 0
    avg_call_setup_latency_ms: float = 0.0
    p95_call_setup_latency_ms: float = 0.0
    total_calls_today: int = 0
    total_calls_failed: int = 0
    last_call_at: datetime | None = None
    openai_ws_errors_1h: int = 0
    circuit_breaker_open: bool = False
    reconnect_failures_recent: int = 0


# ── Log entry ─────────────────────────────────────────────────────────────────

class LogEntry(BaseModel):
    sequence_id: int
    timestamp: datetime
    level: str
    logger_name: str
    call_id: str | None = None
    message: str
    extra: dict[str, Any] = Field(default_factory=dict)


# ── Event bus message ─────────────────────────────────────────────────────────

class EventBusMessage(BaseModel):
    type: str
    payload: dict[str, Any]
    ts: float


# ── Event topic constants ─────────────────────────────────────────────────────

class Topic:
    CALL_CREATED = "CALL_CREATED"
    CALL_UPDATED = "CALL_UPDATED"
    CALL_ENDED = "CALL_ENDED"
    TOKEN_USAGE = "TOKEN_USAGE"
    HEALTH_UPDATE = "HEALTH_UPDATE"
    LOG_ENTRY = "LOG_ENTRY"
    SNAPSHOT = "SNAPSHOT"
    TRANSCRIPT_TURN = "TRANSCRIPT_TURN"
    BUDGET_ALERT = "BUDGET_ALERT"
    CALL_EVENT = "CALL_EVENT"
