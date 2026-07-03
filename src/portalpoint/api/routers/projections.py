import random

from fastapi import APIRouter, Query

from portalpoint.api.deps import CurrentUser
from portalpoint.api.schemas.projection import TeamRatingProjectionResponse

router = APIRouter(prefix="/api/projections", tags=["projections"])


@router.get("/team-rating", response_model=TeamRatingProjectionResponse)
async def get_team_rating_projection(
    current_user: CurrentUser,
    player_id: int = Query(...),
    school_id: int = Query(...),
):
    # STUB — replace with Model 6 (XGBoost delta-AdjEM; requires Model 4 output as input) in Phase 2
    rng = random.Random(player_id * 1000 + school_id)
    current = round(rng.uniform(-2.0, 8.0), 1)
    delta = round(rng.uniform(0.8, 4.5), 1)
    projected = round(current + delta, 1)
    ci_spread = round(rng.uniform(1.0, 1.8), 1)
    percentile = rng.randint(45, 85)

    return TeamRatingProjectionResponse(
        player_id=str(player_id),
        school_id=school_id,
        current_adjEM=current,
        projected_adjEM=projected,
        delta_adjEM=delta,
        confidence_interval=(round(delta - ci_spread, 1), round(delta + ci_spread, 1)),
        national_percentile=percentile,
        conference_rank=rng.randint(2, 8),
        context=f"Top-{100 - percentile + 10} nationally, up from top-{100 - percentile + 30} without this player",
        expected_minutes_input=round(rng.uniform(18.0, 26.0), 1),
        model_version="proj_v1.0-stub",
    )
