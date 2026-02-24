from fastapi import APIRouter, Depends

from config.settings import get_settings
from dashboard.auth import require_auth

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
async def get_config(_: str = Depends(require_auth)) -> dict:
    """Serve frontend configuration (e.g. WebSocket reconnect parameters)."""
    s = get_settings()
    return {
        "ws_reconnect_base_ms": s.ws_reconnect_base_ms,
        "ws_reconnect_max_ms": s.ws_reconnect_max_ms,
        "ws_reconnect_max_attempts": s.ws_reconnect_max_attempts,
    }
