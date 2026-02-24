"""Bearer token authentication dependency for dashboard routes and WebSocket."""
from __future__ import annotations

from fastapi import Depends, HTTPException, WebSocket
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config.settings import get_settings

_bearer = HTTPBearer(auto_error=False)


async def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    s = get_settings()
    if credentials is None or credentials.credentials != s.dashboard_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return credentials.credentials


async def ws_require_auth(websocket: WebSocket) -> bool:
    """Validate auth for WebSocket upgrades. Closes with code 4001 on failure.

    Browsers cannot set custom headers on WebSocket connections, so the token
    is read from the `token` query parameter first, then falls back to the
    Authorization header (for non-browser clients).

    Must accept the WebSocket before closing with a custom code — otherwise
    Starlette rejects the HTTP upgrade with 403 instead of our 4001.
    """
    s = get_settings()

    # Query param (browser clients)
    token = websocket.query_params.get("token", "")

    # Fallback: Authorization header (non-browser / programmatic clients)
    if not token:
        auth_header = websocket.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if token != s.dashboard_api_key:
        await websocket.accept()
        await websocket.close(code=4001, reason="Authentication failed")
        return False
    return True
