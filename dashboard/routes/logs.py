from fastapi import APIRouter, Depends, Query, WebSocket

from dashboard.auth import require_auth, ws_require_auth
from dashboard.ws_manager import ws_manager
from core.state_store import store

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
async def get_logs(
    since_seq: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    _: str = Depends(require_auth),
) -> dict:
    entries = await store.get_logs(since_seq=since_seq, limit=limit)
    next_seq = entries[-1].sequence_id if entries else since_seq
    return {
        "entries": [e.model_dump(mode="json") for e in entries],
        "next_seq": next_seq,
    }


@router.websocket("/ws")
async def logs_ws(websocket: WebSocket) -> None:
    if not await ws_require_auth(websocket):
        return
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive; actual data pushed by ws_manager broadcast loop
            await websocket.receive_text()
    except Exception:
        pass
    finally:
        await ws_manager.disconnect(websocket)
