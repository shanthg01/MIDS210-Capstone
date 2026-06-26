from datetime import datetime, timezone

from fastapi import APIRouter, Query

from portalpoint.api.deps import CurrentUser
from portalpoint.api.schemas.recommendation import FitComponents, RecommendationItem, RecommendationsResponse

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])

# (player_id, player_name, position, overall, gap, scheme, role_fit, program_fit, reasoning)
_PLAYER_STUBS = [
    (1001, "Marcus Johnson",   "SG", 92.0, 95.0, 90.0, 88.0, 67.0, "High scheme fit — 3PT-heavy offense matches shooting profile exactly."),
    (1002, "Devon Carter",     "PG", 87.5, 85.0, 92.0, 78.0, 72.0, "Drive-and-kick system maximizes off-ball movement and assist tendencies."),
    (1003, "Elijah Williams",  "SF", 84.0, 88.0, 81.0, 82.0, 70.0, "Two wings departing; clear path to 24+ minutes as starter."),
    (1004, "Jaylen Brooks",    "PF", 82.1, 91.0, 75.0, 80.0, 64.0, "Top gap match — roster needs stretch 4 and archetype fits perfectly."),
    (1005, "Tremont Davis",    "SG", 80.8, 82.0, 84.0, 74.0, 71.0, "Similar tempo to current program — minimal statistical adjustment expected."),
    (1006, "Kai Thompson",     "PG", 79.5, 78.0, 80.0, 76.0, 68.0, "NIL budget alignment and academic fit match program's stated priorities."),
    (1007, "Andre Mitchell",   "C",  77.3, 75.0, 72.0, 79.0, 65.0, "Coaching system emphasizes ball movement matching pass-first tendencies."),
    (1008, "Darius Evans",     "SF", 76.0, 70.0, 78.0, 75.0, 62.0, "High-tempo offense suits usage profile and shot creation volume."),
    (1009, "Malik Foster",     "PF", 74.9, 82.0, 70.0, 72.0, 60.0, "Familiar conference opponent; scheme transition risk is low."),
    (1010, "Jordan Hayes",     "SG", 73.2, 77.0, 68.0, 70.0, 71.0, "Strong academic match and regional fit align with program preference weights."),
]


@router.get("", response_model=RecommendationsResponse)
async def get_recommendations(current_user: CurrentUser, user_id: int = Query(...)):
    # STUB — replace with Model 7 (30% SVD collab filter + 30% content-based + 40% fit scores)
    #
    # Contract once M7 ships (PR #33 follow-ups #1/#2/#5): default to
    # WHERE is_portal_candidate = true on player_team_fit_scores — recommendations
    # are "available players", not every player ever scored. Gap Matching/Scheme
    # Fit stay all-pairs (clustering/projections/one-off scenarios need the full
    # universe); this endpoint is the one that should narrow to it. Add an
    # admin/debug query param to opt into all players rather than defaulting to it.
    items = [
        RecommendationItem(
            rank=i + 1,
            player_id=str(player_id),
            player_name=name,
            position=pos,
            overall_fit=fit,
            components=FitComponents(
                gap_match=gap,
                scheme_fit=scheme,
                role_fit=role,
                program_fit=prog,
            ),
            reasoning=reason,
        )
        for i, (player_id, name, pos, fit, gap, scheme, role, prog, reason) in enumerate(_PLAYER_STUBS)
    ]
    return RecommendationsResponse(
        program_id=user_id,
        recommendations=items,
        total=len(items),
        generated_at=datetime.now(timezone.utc),
        model_version="rec_v1.0-stub",
    )
