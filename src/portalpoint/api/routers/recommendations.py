from datetime import datetime, timezone

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from portalpoint.api.deps import CurrentUser, DbSession, RedisClient
from portalpoint.api.schemas.recommendation import FitComponents, RecommendationItem, RecommendationsResponse
from portalpoint.api.services import fit_score_service
from portalpoint.modeling.recommendations import (
    CANDIDATE_SQL,
    DEFAULT_FIT_WEIGHTS,
    MODEL_VERSION,
    fixed_team_impact_preferences,
    generate_top_50_candidates,
    refine_to_top_10,
    team_impact_fit,
)

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])

# Single-user variant of scripts/run_recommendations.py's USERS_SQL — same
# COALESCE defaults (contract with that script's batch job), scoped to one
# user instead of "all active users of a school", and also resolves school_id
# in the same round-trip since the live endpoint needs it for CANDIDATE_SQL.
_USER_SQL = """
SELECT
    u.school_id,
    COALESCE(up.weight_scheme, 0.25) AS weight_scheme,
    COALESCE(up.weight_gap,    0.30) AS weight_gap,
    COALESCE(up.weight_role,   0.25) AS weight_role
FROM users u
LEFT JOIN user_preferences up ON up.user_id = u.id
WHERE u.id = :user_id
"""

_COMPONENT_LABELS = {
    "scheme_fit": "scheme fit",
    "gap_match": "roster gap match",
    "role_fit": "role fit",
    "team_impact_fit": "team-impact projection",
}


def _check_auth(user_id: int, current_user: int) -> None:
    if user_id != current_user:
        raise HTTPException(status_code=403, detail="Not authorized")


def _opt_float(value) -> float | None:
    """None on a LEFT JOIN miss (no destination player_projections row yet) —
    NaN in pandas terms — rather than a fabricated 0.0."""
    return float(value) if pd.notna(value) else None


def _build_reasoning(row: pd.Series) -> str:
    scores = {key: row[key] for key in _COMPONENT_LABELS}
    top_key = max(scores, key=lambda k: scores[k])
    overall = row["personalized_fit"]
    if overall >= 75:
        verdict = "Excellent overall fit"
    elif overall >= 60:
        verdict = "Strong fit"
    elif overall >= 45:
        verdict = "Solid, worth a look"
    else:
        verdict = "Developmental fit"
    return f"{verdict} — stands out most in {_COMPONENT_LABELS[top_key]} ({scores[top_key]:.0f}/100)."


@router.get("", response_model=RecommendationsResponse)
async def get_recommendations(
    current_user: CurrentUser,
    db: DbSession,
    redis: RedisClient,
    user_id: int = Query(...),
    season: int | None = Query(default=None, description="Defaults to the most recent scored season"),
):
    _check_auth(user_id, current_user)

    if season is None:
        season = await fit_score_service.get_current_season(db, redis)

    user_row = (await db.execute(text(_USER_SQL), {"user_id": user_id})).mappings().first()
    if user_row is None:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.now(timezone.utc)

    pool_rows = (
        (
            await db.execute(
                text(CANDIDATE_SQL),
                {"school_id": user_row["school_id"], "season": season},
            )
        )
        .mappings()
        .all()
    )
    if not pool_rows:
        return RecommendationsResponse(
            program_id=user_id, recommendations=[], total=0, generated_at=now, model_version=MODEL_VERSION
        )

    pool = pd.DataFrame(pool_rows)
    pool["team_impact_fit"] = team_impact_fit(pool["delta_adj_em"].fillna(0.0))

    top50 = generate_top_50_candidates(pool, weights=DEFAULT_FIT_WEIGHTS)

    try:
        user_preferences = fixed_team_impact_preferences(
            user_row["weight_scheme"], user_row["weight_gap"], user_row["weight_role"]
        )
        top10 = refine_to_top_10(top50, user_preferences=user_preferences, risk_tolerance="medium")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    items = [
        RecommendationItem(
            rank=int(row["final_rank"]),
            player_id=str(row["player_id"]),
            player_name=row["player_name"],
            position=row["position"],
            overall_fit=row["overall_fit"],
            personalized_fit=row["personalized_fit"],
            components=FitComponents(
                gap_match=row["gap_match"],
                scheme_fit=row["scheme_fit"],
                role_fit=row["role_fit"],
                team_impact_fit=row["team_impact_fit"],
            ),
            reasoning=_build_reasoning(row),
            is_portal_candidate=bool(row["is_portal_candidate"]),
            value_per_100=_opt_float(row.get("value_per_100")),
            projected_minutes=_opt_float(row.get("projected_minutes")),
            projected_usage=_opt_float(row.get("projected_usage")),
        )
        for _, row in top10.iterrows()
    ]
    return RecommendationsResponse(
        program_id=user_id,
        recommendations=items,
        total=len(items),
        generated_at=now,
        model_version=MODEL_VERSION,
    )
