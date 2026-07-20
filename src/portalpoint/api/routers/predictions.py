from fastapi import APIRouter, Query

from portalpoint.api.deps import CurrentUser, DbSession, RedisClient
from portalpoint.api.schemas.prediction import PredictionResponse
from portalpoint.api.services import fit_score_service, prediction_service

router = APIRouter(prefix="/api/predictions", tags=["predictions"])


@router.get("", response_model=PredictionResponse)
async def get_prediction(
    current_user: CurrentUser,
    db: DbSession,
    redis: RedisClient,
    player_id: int = Query(...),
    school_id: int = Query(...),
    season: int | None = Query(default=None, description="Defaults to the most recent scored season"),
):
    if season is None:
        season = await fit_score_service.get_current_season(db, redis)
    return await prediction_service.get_prediction(db, player_id, school_id, season)
