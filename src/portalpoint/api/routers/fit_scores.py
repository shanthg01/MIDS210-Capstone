import random
from datetime import datetime, timezone

from fastapi import APIRouter, Query

from portalpoint.api.deps import CurrentUser
from portalpoint.api.schemas.fit_score import (
    FitBreakdown,
    FitScoreResponse,
    FitWeights,
    GapMatchBreakdown,
    OpportunityBreakdown,
    PersonalFitBreakdown,
    SchemeBreakdown,
)

router = APIRouter(prefix="/api/fit-scores", tags=["fit-scores"])


def _stub_fit_score(player_id: int, school_id: int) -> FitScoreResponse:
    rng = random.Random(player_id * 1000 + school_id)
    gap = round(rng.uniform(55.0, 95.0), 1)
    scheme = round(rng.uniform(55.0, 95.0), 1)
    opp = round(rng.uniform(55.0, 90.0), 1)
    personal = round(rng.uniform(50.0, 85.0), 1)
    w = FitWeights()
    overall = round(
        gap * w.gap + scheme * w.scheme + opp * w.opportunity + personal * w.personal,
        1,
    )
    proj_min = round(rng.uniform(16.0, 28.0), 1)
    return FitScoreResponse(
        player_id=player_id,
        school_id=school_id,
        overall_fit=overall,
        gap_match=gap,
        scheme_fit=scheme,
        opportunity=opp,
        personal_fit=personal,
        breakdown=FitBreakdown(
            scheme=SchemeBreakdown(
                three_point_match=round(rng.uniform(60.0, 98.0), 1),
                pace_match=round(rng.uniform(60.0, 98.0), 1),
                usage_match=round(rng.uniform(60.0, 98.0), 1),
                rim_attack_match=round(rng.uniform(60.0, 98.0), 1),
                ball_movement_match=round(rng.uniform(60.0, 98.0), 1),
            ),
            opportunity=OpportunityBreakdown(
                projected_minutes=proj_min,
                confidence_interval=(round(proj_min - rng.uniform(4.0, 7.0), 1), round(proj_min + rng.uniform(4.0, 7.0), 1)),
                starter_probability=round(rng.uniform(0.35, 0.85), 2),
                depth_chart_position=rng.randint(1, 3),
            ),
            gap=GapMatchBreakdown(
                archetype_needed=rng.random() > 0.3,
                position_depth_score=round(rng.uniform(50.0, 95.0), 1),
                uniqueness_bonus=round(rng.uniform(0.0, 15.0), 1),
                redundancy_penalty=round(rng.uniform(-15.0, 0.0), 1),
            ),
            personal=PersonalFitBreakdown(
                nil_score=round(rng.uniform(40.0, 90.0), 1),
                geographic_score=round(rng.uniform(30.0, 95.0), 1),
                academic_score=round(rng.uniform(55.0, 95.0), 1),
                cultural_score=round(rng.uniform(50.0, 90.0), 1),
                distance_miles=round(rng.uniform(50.0, 1800.0), 0),
            ),
        ),
        weights_used=w,
        computed_at=datetime.now(timezone.utc),
        model_version="fit_v1.0-stub",
        cache_hit=False,
    )


@router.get("", response_model=FitScoreResponse)
async def get_fit_score(
    current_user: CurrentUser,
    player_id: int = Query(...),
    school_id: int = Query(...),
):
    # STUB — replace with Models 3+4 (scheme cosine sim + Bayesian playing time) in Phase 2
    return _stub_fit_score(player_id, school_id)
