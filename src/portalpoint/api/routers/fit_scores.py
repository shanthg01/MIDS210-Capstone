from fastapi import APIRouter, Depends, Query
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from portalpoint.api.deps import CurrentUser
from portalpoint.api.schemas.fit_score import FitScoreResponse
from portalpoint.api.services import fit_score_service
from portalpoint.db.redis_client import get_redis
from portalpoint.db.session import get_db

router = APIRouter(prefix="/api/fit-scores", tags=["fit-scores"])

# Matches the pre-compute cache policy in CLAUDE.md (top-50 portal players,
# cached 30min in Redis).
CACHE_TTL_SECONDS = 1800


def _cache_key(player_id: int, school_id: int, season: int) -> str:
    return f"fitscore:{player_id}:{school_id}:{season}"


@router.get("", response_model=FitScoreResponse)
async def get_fit_score(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    player_id: int = Query(...),
    school_id: int = Query(...),
    season: int | None = Query(default=None, description="Defaults to the most recent scored season"),
):
    if season is None:
        season = await fit_score_service.get_current_season(db, redis)

    cache_key = _cache_key(player_id, school_id, season)

    try:
        cached = await redis.get(cache_key)
    except Exception:
        cached = None  # Redis unavailable — fall through to DB, skip caching this request

    if cached is not None:
        try:
            response = FitScoreResponse.model_validate_json(cached)
        except Exception:
            # Stale entry from before a schema change (e.g. player_id's int->str
            # migration) — treat as a cache miss rather than crashing the request.
            response = None
        if response is not None:
            response.cache_hit = True
            return response

    response = await fit_score_service.get_fit_score(db, player_id, school_id, season)

    try:
        await redis.set(cache_key, response.model_dump_json(), ex=CACHE_TTL_SECONDS)
    except Exception:
        pass  # Redis unavailable — request still succeeds, just not cached

    return response
