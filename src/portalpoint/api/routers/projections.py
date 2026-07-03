from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from portalpoint.api.deps import CurrentUser
from portalpoint.api.schemas.projection import TeamRatingProjectionResponse
from portalpoint.db.session import AsyncSessionLocal

router = APIRouter(prefix="/api/projections", tags=["projections"])

_FETCH_SQL = """
SELECT
    player_id, school_id, season,
    current_adj_em, projected_adj_em, delta_adj_em,
    baseline_adj_o, baseline_adj_d, projected_adj_o, projected_adj_d,
    ci_lower, ci_upper,
    national_percentile, conference_rank,
    expected_minutes_input, candidate_usage_role,
    explanation, model_version
FROM team_rating_projections
WHERE player_id = :player_id
  AND school_id = :school_id
ORDER BY season DESC, computed_at DESC
LIMIT 1
"""


@router.get("/team-rating", response_model=TeamRatingProjectionResponse)
async def get_team_rating_projection(
    current_user: CurrentUser,
    player_id: int = Query(...),
    school_id: int = Query(...),
    season: int = Query(default=2027),
) -> TeamRatingProjectionResponse:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text(_FETCH_SQL),
            {"player_id": player_id, "school_id": school_id},
        )
        row = result.mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No team rating projection found for player {player_id} at school {school_id}.",
        )

    delta = float(row["delta_adj_em"])
    ci_lower = float(row["ci_lower"])
    ci_upper = float(row["ci_upper"])
    pct = int(row["national_percentile"])
    conf_rank = int(row["conference_rank"])

    context = (
        f"Top-{100 - pct + 1} nationally"
        + (f", projected conference rank {conf_rank}" if conf_rank else "")
    )

    return TeamRatingProjectionResponse(
        player_id=str(player_id),
        school_id=school_id,
        season=int(row["season"]),
        current_adjEM=float(row["current_adj_em"]),
        projected_adjEM=float(row["projected_adj_em"]),
        delta_adjEM=delta,
        baseline_adj_o=row["baseline_adj_o"],
        baseline_adj_d=row["baseline_adj_d"],
        projected_adj_o=row["projected_adj_o"],
        projected_adj_d=row["projected_adj_d"],
        confidence_interval=(ci_lower, ci_upper),
        national_percentile=pct,
        conference_rank=conf_rank,
        context=context,
        expected_minutes_input=float(row["expected_minutes_input"]),
        candidate_usage_role=row.get("candidate_usage_role"),
        explanation=row.get("explanation"),
        model_version=str(row["model_version"]),
    )
