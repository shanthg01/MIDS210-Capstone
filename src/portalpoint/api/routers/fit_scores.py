from fastapi import APIRouter, Depends, Query
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from portalpoint.api.deps import CurrentUser
from portalpoint.api.schemas.fit_score import (
    FitScoreResponse,
    ProgramFitUserInputRequest,
    ProgramFitUserInputResponse,
)
from portalpoint.api.services import fit_score_service
from portalpoint.db.models import ProgramFitUserInput
from portalpoint.db.redis_client import get_redis
from portalpoint.db.session import get_db

router = APIRouter(prefix="/api/fit-scores", tags=["fit-scores"])

# Matches the pre-compute cache policy in CLAUDE.md (top-50 portal players,
# cached 30min in Redis).
CACHE_TTL_SECONDS = 1800


def _cache_key(player_id: int, school_id: int, season: int, user_id: int) -> str:
    # Must include user_id — the response carries per-user personalized_fit/
    # personalized_weights now, so caching without it would leak one user's
    # personalization into another user's cached response.
    return f"fitscore:v3:{player_id}:{school_id}:{season}:{user_id}"


@router.get("", response_model=FitScoreResponse)
async def get_fit_score(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    player_id: int = Query(...),
    school_id: int = Query(...),
    season: int | None = Query(
        default=None, description="Defaults to the most recent scored season"
    ),
):
    if season is None:
        season = await fit_score_service.get_current_season(db, redis)

    cache_key = _cache_key(player_id, school_id, season, current_user)

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

    response = await fit_score_service.get_fit_score(
        db, player_id, school_id, season, user_id=current_user
    )

    try:
        await redis.set(cache_key, response.model_dump_json(), ex=CACHE_TTL_SECONDS)
    except Exception:
        pass  # Redis unavailable — request still succeeds, just not cached

    return response


@router.put("/program-fit-input", response_model=ProgramFitUserInputResponse)
async def upsert_program_fit_user_input(
    body: ProgramFitUserInputRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    """Manual qualitative "off the court" grade for one player x school pair.

    Per-user (program_fit_user_inputs), substitutes into personalized_fit only —
    never player_team_fit_scores.program_fit, which stays the shared neutral
    50.0 placeholder until the full Program Fit calculator lands.
    """
    result = await db.execute(
        select(ProgramFitUserInput).where(
            ProgramFitUserInput.user_id == current_user,
            ProgramFitUserInput.player_id == body.player_id,
            ProgramFitUserInput.school_id == body.school_id,
            ProgramFitUserInput.season == body.season,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = ProgramFitUserInput(
            user_id=current_user,
            player_id=body.player_id,
            school_id=body.school_id,
            season=body.season,
        )
        db.add(row)
    row.qualitative_score = body.qualitative_score
    row.notes = body.notes

    await db.commit()
    await db.refresh(row)

    # Bust the cached fit-score response for this exact pair so the new grade
    # is reflected immediately rather than waiting out the 30min TTL.
    try:
        await redis.delete(
            _cache_key(body.player_id, body.school_id, body.season, current_user)
        )
    except Exception:
        pass

    return ProgramFitUserInputResponse(
        player_id=str(row.player_id),
        school_id=row.school_id,
        season=row.season,
        qualitative_score=row.qualitative_score,
        notes=row.notes,
        updated_at=row.updated_at,
    )
