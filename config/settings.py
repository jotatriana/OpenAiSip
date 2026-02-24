from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # OpenAI / SIP Bridge
    openai_api_key: str
    openai_project_id: str
    openai_model: str = "gpt-4o-realtime-preview"
    openai_voice: str = "alloy"
    webhook_secret: str
    webhook_listen_host: str = "0.0.0.0"
    webhook_listen_port: int = 8000

    # Conversation behavior
    default_language: str = "en-US"
    escalation_frustration_limit: int = 3
    escalation_tool_failure_limit: int = 2
    human_agent_sip_uri: str = "sip:queue@avaya.internal"

    # Database
    database_url: str = "sqlite+aiosqlite:///./openaisip.db"

    # Dashboard
    dashboard_api_key: str
    dashboard_listen_port: int = 8001
    log_buffer_size: int = 500
    health_poll_interval_seconds: int = 10
    sip_stale_threshold_seconds: int = 300

    # Frontend reconnect params
    ws_reconnect_base_ms: int = 500
    ws_reconnect_max_ms: int = 30000
    ws_reconnect_max_attempts: int = 15


@lru_cache
def get_settings() -> Settings:
    return Settings()
