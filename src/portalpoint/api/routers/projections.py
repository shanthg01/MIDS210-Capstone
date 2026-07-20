from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import text

from portalpoint.api.deps import CurrentUser
from portalpoint.api.schemas.projection import (
    RosterImpactItem,
    RosterImpactResponse,
    TeamRatingOverrideRequest,
    TeamRatingOverrideResponse,
    TeamRatingProjectionResponse,
)
from portalpoint.db.session import AsyncSessionLocal
from portalpoint.modeling.io import get_sync_engine
from portalpoint.modeling.mlflow_helpers import setup_mlflow
from portalpoint.modeling.team_rating_projection import (
    compute_team_rating_override,
    load_champion_models,
)

router = APIRouter(prefix="/api/projections", tags=["projections"])

_MLFLOW_EXPERIMENT_NAME = "team-rating-projection"

_TOP_SQL = """
WITH user_school AS (
    SELECT school_id FROM users WHERE id = :user_id
)
SELECT
    trp.player_id, p.full_name, p.position,
    trp.delta_adj_em, trp.current_adj_em, trp.projected_adj_em,
    trp.ci_lower, trp.ci_upper,
    trp.expected_minutes_input, trp.candidate_usage_role,
    trp.school_id
FROM team_rating_projections trp
JOIN players p          ON p.id = trp.player_id
JOIN user_school us     ON trp.school_id = us.school_id
JOIN player_team_fit_scores ptf
    ON  ptf.player_id  = trp.player_id
    AND ptf.school_id  = us.school_id
    AND ptf.season     = :fit_season
WHERE trp.season       = :trp_season
  AND trp.expires_at   > now()
  AND ptf.is_portal_candidate = true
ORDER BY trp.delta_adj_em DESC
LIMIT :limit
"""

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
  AND season = :season
  AND expires_at > now()
ORDER BY computed_at DESC
LIMIT 1
"""


@router.get("/team-rating/top", response_model=RosterImpactResponse)
async def get_top_roster_impact(
    current_user: CurrentUser,
    season: int = Query(default=2027),
    fit_season: int = Query(default=2027),
    limit: int = Query(default=25, ge=1, le=100),
) -> RosterImpactResponse:
    """Rank all portal candidates for the current user's school by delta AdjEM."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text(_TOP_SQL),
            {
                "user_id": current_user,
                "trp_season": season,
                "fit_season": fit_season,
                "limit": limit,
            },
        )
        rows = result.mappings().all()

    if not rows:
        # Return empty list rather than 404 — school may just not have run yet.
        async with AsyncSessionLocal() as session:
            school_result = await session.execute(
                text("SELECT school_id FROM users WHERE id = :uid"),
                {"uid": current_user},
            )
            school_row = school_result.first()
        school_id = int(school_row[0]) if school_row and school_row[0] else 0
        return RosterImpactResponse(school_id=school_id, season=season, players=[], total=0)

    school_id = int(rows[0]["school_id"])
    players = [
        RosterImpactItem(
            player_id=str(r["player_id"]),
            player_name=r["full_name"],
            position=r["position"] or "",
            delta_adjEM=float(r["delta_adj_em"]),
            current_adjEM=float(r["current_adj_em"]),
            projected_adjEM=float(r["projected_adj_em"]),
            confidence_interval=(float(r["ci_lower"]), float(r["ci_upper"])),
            expected_minutes_input=float(r["expected_minutes_input"]),
            candidate_usage_role=r.get("candidate_usage_role"),
        )
        for r in rows
    ]
    return RosterImpactResponse(school_id=school_id, season=season, players=players, total=len(players))


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
            {"player_id": player_id, "school_id": school_id, "season": season},
        )
        row = result.mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No active team rating projection found for player {player_id} "
                f"at school {school_id} in season {season}."
            ),
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


def _run_team_rating_override(body: TeamRatingOverrideRequest) -> dict | None:
    """Blocking work (sync DB + MLflow artifact download) — call via threadpool."""
    engine = get_sync_engine()
    client = setup_mlflow(_MLFLOW_EXPERIMENT_NAME)
    models = load_champion_models(client)
    prior_season = body.prior_season if body.prior_season is not None else body.season - 1
    return compute_team_rating_override(
        engine,
        models,
        player_id=body.player_id,
        school_id=body.school_id,
        target_season=body.season,
        prior_season=prior_season,
        minutes_override=body.minutes_override,
        usage_override=body.usage_override,
    )


@router.post("/team-rating/override", response_model=TeamRatingOverrideResponse)
async def override_team_rating_projection(
    current_user: CurrentUser,
    body: TeamRatingOverrideRequest,
) -> TeamRatingOverrideResponse:
    """Coach-supplied minutes what-if for one (player, school) pair.

    Rebuilds this school's roster feature vector with the candidate's minutes
    overridden and re-runs the persisted @champion Ridge off/def models — same
    pipeline scripts/run_team_rating_projection.py runs for every pair, scoped
    to one school so it's cheap enough for a live request. Requires a scored
    playing_time_projections row for the pair (same hard gate the batch script
    enforces)."""
    delta = await run_in_threadpool(_run_team_rating_override, body)
    if delta is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No playing-time projection found for player {body.player_id} at school "
                f"{body.school_id} in season {body.season} — run playing-time projection first."
            ),
        )
    return TeamRatingOverrideResponse(
        player_id=str(body.player_id),
        school_id=body.school_id,
        season=body.season,
        minutes_override=body.minutes_override,
        baseline_adj_o=delta["baseline_adj_o"],
        baseline_adj_d=delta["baseline_adj_d"],
        baseline_adj_em=delta["baseline_adj_em"],
        projected_adj_o=delta["projected_adj_o"],
        projected_adj_d=delta["projected_adj_d"],
        projected_adj_em=delta["projected_adj_em"],
        delta_adj_o=delta["delta_adj_o"],
        delta_adj_d=delta["delta_adj_d"],
        delta_adj_em=delta["delta_adj_em"],
        confidence_interval=(delta["ci_lower"], delta["ci_upper"]),
    )
