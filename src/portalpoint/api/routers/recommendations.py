from datetime import datetime, timezone

from fastapi import APIRouter, Query
from sqlalchemy import text

from portalpoint.api.deps import CurrentUser, DbSession
from portalpoint.api.schemas.recommendation import FitComponents, RecommendationItem, RecommendationsResponse
from portalpoint.api.services import fit_score_service

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])

# Reads pre-computed rows from the `recommendations` table written by
# scripts/run_recommendations.py (rec-v1.2, scheme/gap/role weights).
# Falls back to a real-time ranking from player_team_fit_scores when the
# table has no non-expired rows for this user (school not yet run).
#
# Component breakdown is joined at query time — the recommendations table
# stores overall_fit only; gap/scheme/role come from player_team_fit_scores
# and team_impact_fit is derived from team_rating_projections.delta_adj_em.

_TARGET_SEASON = 2027  # team_rating_projections target season

_FETCH_SQL = """
WITH user_info AS (
    SELECT school_id FROM users WHERE id = :user_id
),
rec AS (
    SELECT r.rank, r.player_id, r.overall_fit, r.reasoning,
           r.model_version, r.generated_at,
           p.full_name, p.position
    FROM recommendations r
    JOIN players p ON p.id = r.player_id
    WHERE r.user_id = :user_id
      AND r.expires_at > now()
    ORDER BY r.rank ASC
    LIMIT 10
),
fit AS (
    SELECT ptf.player_id, ptf.gap_match, ptf.scheme_fit, ptf.role_fit
    FROM player_team_fit_scores ptf
    JOIN user_info ui ON ptf.school_id = ui.school_id
    WHERE ptf.season = :season
      AND ptf.player_id IN (SELECT player_id FROM rec)
),
trp AS (
    SELECT t.player_id, t.delta_adj_em
    FROM team_rating_projections t
    JOIN user_info ui ON t.school_id = ui.school_id
    WHERE t.season = :trp_season
      AND t.expires_at > now()
      AND t.player_id IN (SELECT player_id FROM rec)
)
SELECT r.rank, r.player_id, r.overall_fit, r.reasoning, r.model_version,
       r.generated_at, r.full_name, r.position,
       COALESCE(f.gap_match,  50.0) AS gap_match,
       COALESCE(f.scheme_fit, 50.0) AS scheme_fit,
       COALESCE(f.role_fit,   50.0) AS role_fit,
       t.delta_adj_em
FROM rec r
LEFT JOIN fit f ON f.player_id = r.player_id
LEFT JOIN trp t ON t.player_id = r.player_id
ORDER BY r.rank ASC
"""

# Used when recommendations table has no non-expired rows for this user.
_FALLBACK_SQL = """
WITH user_info AS (
    SELECT school_id FROM users WHERE id = :user_id
)
SELECT
    ROW_NUMBER() OVER (ORDER BY ptf.overall_fit DESC) AS rank,
    ptf.player_id,
    ptf.overall_fit,
    NULL::text      AS reasoning,
    'rec-v1.2-live' AS model_version,
    now()           AS generated_at,
    p.full_name,
    p.position,
    ptf.gap_match,
    ptf.scheme_fit,
    ptf.role_fit,
    t.delta_adj_em
FROM player_team_fit_scores ptf
JOIN players p ON p.id = ptf.player_id
JOIN user_info ui ON ptf.school_id = ui.school_id
LEFT JOIN team_rating_projections t
    ON  t.player_id  = ptf.player_id
    AND t.school_id  = ui.school_id
    AND t.season     = :trp_season
    AND t.expires_at > now()
WHERE ptf.season             = :season
  AND ptf.is_portal_candidate = true
ORDER BY ptf.overall_fit DESC
LIMIT 10
"""


def _delta_to_team_impact_fit(delta: float | None) -> float:
    """Map delta_adjEM → 0-100 (clip ±5, 0 delta = neutral 50.0)."""
    if delta is None:
        return 50.0
    return 50.0 + max(-5.0, min(5.0, delta)) * 10.0


def _auto_reasoning(gap: float, scheme: float, role: float, impact: float) -> str:
    best = max(
        [("gap match", gap), ("scheme fit", scheme), ("role fit", role), ("team impact", impact)],
        key=lambda x: x[1],
    )
    label, score = best
    templates = {
        "gap match":   f"Top gap match ({score:.0f}) — player skills align with this roster's needs.",
        "scheme fit":  f"Strong scheme fit ({score:.0f}) — shot profile matches offensive system.",
        "role fit":    f"Clear role fit ({score:.0f}) — projected minutes and rotation slot available.",
        "team impact": f"Positive team impact ({score:.0f}) — AdjEM improvement projected.",
    }
    return templates[label]


def _build_item(row, i: int) -> RecommendationItem:
    gap    = float(row["gap_match"])
    scheme = float(row["scheme_fit"])
    role   = float(row["role_fit"])
    impact = _delta_to_team_impact_fit(row["delta_adj_em"])
    reasoning = row["reasoning"] or _auto_reasoning(gap, scheme, role, impact)
    return RecommendationItem(
        rank=i + 1,
        player_id=str(row["player_id"]),
        player_name=row["full_name"],
        position=row["position"] or "",
        overall_fit=float(row["overall_fit"]),
        components=FitComponents(
            gap_match=gap,
            scheme_fit=scheme,
            role_fit=role,
            team_impact_fit=impact,
        ),
        reasoning=reasoning,
    )


@router.get("", response_model=RecommendationsResponse)
async def get_recommendations(
    current_user: CurrentUser,
    db: DbSession,
    user_id: int = Query(...),
) -> RecommendationsResponse:
    season = await fit_score_service.get_current_season(db)

    result = await db.execute(
        text(_FETCH_SQL),
        {"user_id": current_user, "season": season, "trp_season": _TARGET_SEASON},
    )
    rows = result.mappings().all()

    if not rows:
        result = await db.execute(
            text(_FALLBACK_SQL),
            {"user_id": current_user, "season": season, "trp_season": _TARGET_SEASON},
        )
        rows = result.mappings().all()

    items = [_build_item(row, i) for i, row in enumerate(rows)]
    generated_at = rows[0]["generated_at"] if rows else datetime.now(timezone.utc)
    model_version = rows[0]["model_version"] if rows else "rec-v1.2-live"

    return RecommendationsResponse(
        program_id=current_user,
        recommendations=items,
        total=len(items),
        generated_at=generated_at,
        model_version=model_version,
    )
