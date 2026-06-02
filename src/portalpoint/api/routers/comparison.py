import random
from datetime import datetime, timezone

from fastapi import APIRouter

from portalpoint.api.schemas.comparison import (
    CompareRequest,
    CompareResponse,
    ComparisonMatrix,
    ComparisonSchoolEntry,
    TradeOff,
)
from portalpoint.api.schemas.fit_score import (
    FitBreakdown,
    FitScoreResponse,
    FitWeights,
    GapMatchBreakdown,
    OpportunityBreakdown,
    PersonalFitBreakdown,
    SchemeBreakdown,
)
from portalpoint.api.schemas.prediction import PredictedRole, PredictionResponse, SimilarTransfer
from portalpoint.api.schemas.school import Region, SchoolBase

router = APIRouter(prefix="/api/compare", tags=["comparison"])

_KNOWN_SCHOOLS: dict[int, tuple] = {
    301: ("Gonzaga",        "WCC",      "Spokane",      "WA", Region.PACIFIC),
    302: ("Duke",           "ACC",      "Durham",       "NC", Region.SOUTHEAST),
    303: ("Villanova",      "Big East", "Villanova",    "PA", Region.MID_ATLANTIC),
    304: ("Xavier",         "Big East", "Cincinnati",   "OH", Region.MIDWEST),
    305: ("St. Mary's",     "WCC",      "Moraga",       "CA", Region.PACIFIC),
    306: ("Davidson",       "A-10",     "Davidson",     "NC", Region.SOUTHEAST),
    307: ("Butler",         "Big East", "Indianapolis", "IN", Region.MIDWEST),
    308: ("VCU",            "A-10",     "Richmond",     "VA", Region.MID_ATLANTIC),
    309: ("Creighton",      "Big East", "Omaha",        "NE", Region.MIDWEST),
    310: ("Loyola Chicago", "MVC",      "Chicago",      "IL", Region.MIDWEST),
}


def _school_base(school_id: int) -> SchoolBase:
    if school_id in _KNOWN_SCHOOLS:
        name, conf, city, state, region = _KNOWN_SCHOOLS[school_id]
    else:
        name, conf, city, state, region = f"School #{school_id}", "Unknown", "Unknown", "XX", Region.MIDWEST
    return SchoolBase(school_id=school_id, name=name, conference=conf, city=city, state=state, region=region)


def _stub_fit(player_id: int, school_id: int) -> FitScoreResponse:
    rng = random.Random(player_id * 1000 + school_id)
    gap = round(rng.uniform(55.0, 95.0), 1)
    scheme = round(rng.uniform(55.0, 95.0), 1)
    opp = round(rng.uniform(55.0, 90.0), 1)
    personal = round(rng.uniform(50.0, 85.0), 1)
    w = FitWeights()
    overall = round(gap * w.gap + scheme * w.scheme + opp * w.opportunity + personal * w.personal, 1)
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
                confidence_interval=(round(proj_min - 5.5, 1), round(proj_min + 5.5, 1)),
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
    )


def _stub_prediction(player_id: int, school_id: int) -> PredictionResponse:
    rng = random.Random(player_id * 1000 + school_id + 999)
    return PredictionResponse(
        player_id=player_id,
        school_id=school_id,
        predicted_per_change=round(rng.uniform(0.5, 5.5), 1),
        predicted_minutes=round(rng.uniform(18.0, 28.0), 1),
        predicted_role=rng.choices(
            [PredictedRole.STARTER, PredictedRole.ROTATION, PredictedRole.BENCH],
            weights=[0.45, 0.40, 0.15],
        )[0],
        confidence=round(rng.uniform(0.55, 0.82), 2),
        similar_transfers=[
            SimilarTransfer(
                player_name="Jordan Hayes",
                season="2023-24",
                from_school="UNC Greensboro",
                to_school="Davidson",
                per_before=14.2,
                per_after=17.8,
                per_change=3.6,
                minutes_before=22.1,
                minutes_after=26.4,
                outcome_score=4.1,
            )
        ],
        model_version="pred_v1.0-stub",
    )


@router.post("", response_model=CompareResponse)
async def compare_schools(body: CompareRequest):
    # STUB — replace with parallel fit score + prediction fetches in Phase 2
    entries = [
        ComparisonSchoolEntry(
            school=_school_base(sid),
            fit_score=_stub_fit(body.player_id, sid),
            prediction=_stub_prediction(body.player_id, sid),
        )
        for sid in body.school_ids
    ]

    matrix = ComparisonMatrix(
        overall_fit={e.school.name: e.fit_score.overall_fit for e in entries},
        gap_match={e.school.name: e.fit_score.gap_match for e in entries},
        scheme_fit={e.school.name: e.fit_score.scheme_fit for e in entries},
        opportunity={e.school.name: e.fit_score.opportunity for e in entries},
        personal_fit={e.school.name: e.fit_score.personal_fit for e in entries},
    )

    best_opp   = max(entries, key=lambda e: e.fit_score.opportunity)
    best_nil   = max(entries, key=lambda e: e.fit_score.breakdown.personal.nil_score)
    best_scheme = max(entries, key=lambda e: e.fit_score.scheme_fit)

    trade_offs = [
        TradeOff(
            factor="Playing Time",
            description=f"{best_opp.school.name} offers the highest projected minutes and starter probability.",
            best_school_name=best_opp.school.name,
            best_school_id=best_opp.school.school_id,
        ),
        TradeOff(
            factor="NIL Value",
            description=f"{best_nil.school.name} is in the strongest NIL market among your finalists.",
            best_school_name=best_nil.school.name,
            best_school_id=best_nil.school.school_id,
        ),
        TradeOff(
            factor="Scheme Fit",
            description=f"{best_scheme.school.name} system most closely matches your shooting and usage profile.",
            best_school_name=best_scheme.school.name,
            best_school_id=best_scheme.school.school_id,
        ),
    ]

    return CompareResponse(
        player_id=body.player_id,
        schools=entries,
        comparison_matrix=matrix,
        trade_offs=trade_offs,
        generated_at=datetime.now(timezone.utc),
    )
