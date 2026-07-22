from fastapi import APIRouter, Depends, HTTPException, Query
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from portalpoint.api.deps import CurrentUser
from portalpoint.api.schemas.prediction import PredictionResponse
from portalpoint.api.services import transfer_success_service
from portalpoint.db.redis_client import get_redis
from portalpoint.db.session import get_db

router = APIRouter(prefix="/api/predictions", tags=["predictions"])

CACHE_TTL_SECONDS = 1800


def _cache_key(player_id: int, school_id: int, season: int) -> str:
    return f"prediction:{player_id}:{school_id}:{season}"


@router.get("", response_model=PredictionResponse)
async def get_prediction(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    player_id: int = Query(...),
    school_id: int = Query(...),
    season: int | None = Query(
        default=None,
        description="Defaults to the most recent scored season",
    ),
):
    if season is None:
        season = await transfer_success_service.get_current_season(db)

    cache_key = _cache_key(player_id, school_id, season)

    try:
        cached = await redis.get(cache_key)
    except Exception:
        cached = None

    if cached is not None:
        try:
            return PredictionResponse.model_validate_json(cached)
        except Exception:
            pass

    response = await transfer_success_service.get_prediction(db, player_id, school_id, season)
    if response is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No active transfer success score found for player {player_id} "
                f"at school {school_id} in season {season}."
            ),
        )

    try:
        await redis.set(cache_key, response.model_dump_json(), ex=CACHE_TTL_SECONDS)
    except Exception:
        pass

    return response
