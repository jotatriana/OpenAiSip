from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # OpenAI / SIP Bridge
    openai_api_key: str
    openai_project_id: str
    openai_model: str = "gpt-realtime-2.1"
    openai_voice: str = "alloy"
    webhook_secret: str
    webhook_listen_host: str = "0.0.0.0"
    webhook_listen_port: int = 8000
    # Webhook signature verification
    webhook_tolerance_seconds: int = 300   # reject timestamps older/newer than this (replay protection)
    allow_unsigned_webhooks: bool = False  # dev only: accept webhooks when no secret is configured

    # Conversation behavior
    default_language: str = "en-US"
    escalation_frustration_limit: int = 3
    escalation_tool_failure_limit: int = 2
    human_agent_sip_uri: str = "sip:queue@avaya.internal"

    # WebSocket heartbeat
    ws_ping_interval: int = 10   # seconds between pings
    ws_ping_timeout: int = 5     # seconds to wait for pong before treating connection as dead

    # Circuit breaker — trips when N reconnect failures occur in window_seconds
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_window_seconds: int = 300   # 5-minute sliding window
    circuit_breaker_cooldown_seconds: int = 60  # auto-reset after 60s of no failures

    # Transcripts
    transcript_retention_days: int = 90  # CDRs retained independently; only transcript text is purged

    # FSM
    max_turns_per_phase: int = 8  # auto-advance if model forgets to call phase_complete

    # Tool execution
    tool_timeout_seconds: float = 5.0

    # Frustration detection — comma-separated phrases, matched case-insensitively
    frustration_keywords: str = (
        "speak to a person,speak to someone,speak to a manager,speak to a supervisor,"
        "talk to a real person,let me talk to someone,i want a human,transfer me,"
        "this is ridiculous,this is unacceptable,you're not helping,i'm going to cancel,"
        "i already told you,i've been waiting,i've called multiple times,get me a representative"
    )
    # Warm handoff — optional URL to POST escalation context for agent desktop integration
    # Leave empty to skip the webhook; context is always written to the DB.
    handoff_context_url: str = ""

    # Seconds to wait after SIP REFER before sending BYE.
    # Gives the SBC time to complete the new INVITE to the transfer target
    # before the original call leg is torn down.
    transfer_hangup_delay_seconds: int = 10

    # Cost tracking — gpt-realtime-2.1 pricing (USD per 1 000 tokens)
    # Update these when OpenAI revises pricing.
    cost_input_audio_per_1k: float = 0.032
    cost_output_audio_per_1k: float = 0.064
    cost_input_text_per_1k: float = 0.004
    cost_output_text_per_1k: float = 0.024
    cost_input_cached_per_1k: float = 0.0004

    # Daily spend budget in USD; 0.0 means no limit
    daily_budget_usd: float = 0.0

    # Database
    database_url: str = "sqlite+aiosqlite:///./openaisip.db"

    # Dashboard
    dashboard_api_key: str
    dashboard_listen_port: int = 8001
    log_buffer_size: int = 500
    cdr_history_limit: int = 20  # CDRs loaded into snapshot on startup
    health_poll_interval_seconds: int = 10
    sip_stale_threshold_seconds: int = 300

    # Frontend reconnect params
    ws_reconnect_base_ms: int = 500
    ws_reconnect_max_ms: int = 30000
    ws_reconnect_max_attempts: int = 15


@lru_cache
def get_settings() -> Settings:
    return Settings()
