from fastapi import APIRouter, Depends

from config.settings import get_settings
from dashboard.auth import require_auth
from core.state_store import store

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/channels")
async def channel_health(_: str = Depends(require_auth)) -> dict:
    s = get_settings()
    health = await store.build_channel_health(s.sip_stale_threshold_seconds)
    return health.model_dump(mode="json")
