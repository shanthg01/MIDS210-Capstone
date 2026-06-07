import random
from datetime import datetime, timezone

from fastapi import APIRouter

from portalpoint.api.deps import CurrentUser
from portalpoint.api.schemas.comparison import (
    CompareRequest,
    CompareResponse,
    ComparisonMatrix,
    ComparisonPlayerEntry,
    TradeOff,
)
from portalpoint.api.schemas.fit_score import (
    FitBreakdown,
    FitScoreResponse,
    FitWeights,
    GapMatchBreakdown,
    ProgramFitBreakdown,
    RoleFitBreakdown,
    SchemeBreakdown,
)
from portalpoint.api.schemas.player import ClassYear, PlayerBase, Position
from portalpoint.api.schemas.prediction import PredictedRole, PredictionResponse, SimilarTransfer

router = APIRouter(prefix="/api/compare", tags=["comparison"])

_KNOWN_PLAYERS: dict[int, tuple] = {
    1001: ("Marcus Johnson",  "SG", "UNC Greensboro",   301, ClassYear.JUNIOR),
    1002: ("Devon Carter",    "PG", "Vermont",           302, ClassYear.SENIOR),
    1003: ("Elijah Williams", "SF", "Fordham",           303, ClassYear.SOPHOMORE),
    1004: ("Jaylen Brooks",   "PF", "Sacred Heart",      304, ClassYear.JUNIOR),
    1005: ("Tremont Davis",   "SG", "Wright State",      305, ClassYear.SENIOR),
    1006: ("Kai Thompson",    "PG", "Eastern Kentucky",  306, ClassYear.GRADUATE),
    1007: ("Andre Mitchell",  "C",  "Longwood",          307, ClassYear.JUNIOR),
    1008: ("Darius Evans",    "SF", "Norfolk State",     308, ClassYear.SOPHOMORE),
    1009: ("Malik Foster",    "PF", "Rider",             309, ClassYear.SENIOR),
    1010: ("Jordan Hayes",    "SG", "UNC Greensboro",    310, ClassYear.JUNIOR),
}


def _player_base(player_id: int) -> PlayerBase:
    if player_id in _KNOWN_PLAYERS:
        name, pos, school, school_id, class_year = _KNOWN_PLAYERS[player_id]
    else:
        name, pos, school, school_id, class_year = f"Player #{player_id}", "SG", "Unknown", 0, ClassYear.JUNIOR
    return PlayerBase(
        player_id=player_id,
        full_name=name,
        position=Position(pos),
        class_year=class_year,
        current_school=school,
        current_school_id=school_id,
    )


def _stub_fit(program_id: int, player_id: int) -> FitScoreResponse:
    rng = random.Random(program_id * 1000 + player_id)
    gap = round(rng.uniform(55.0, 95.0), 1)
    scheme = round(rng.uniform(55.0, 95.0), 1)
    role = round(rng.uniform(55.0, 90.0), 1)
    program = round(rng.uniform(50.0, 85.0), 1)
    w = FitWeights()
    overall = round(gap * w.gap + scheme * w.scheme + role * w.role_fit + program * w.program_fit, 1)
    proj_min = round(rng.uniform(16.0, 28.0), 1)
    return FitScoreResponse(
        player_id=player_id,
        school_id=program_id,
        overall_fit=overall,
        gap_match=gap,
        scheme_fit=scheme,
        role_fit=role,
        program_fit=program,
        breakdown=FitBreakdown(
            scheme=SchemeBreakdown(
                three_point_match=round(rng.uniform(60.0, 98.0), 1),
                pace_match=round(rng.uniform(60.0, 98.0), 1),
                usage_match=round(rng.uniform(60.0, 98.0), 1),
                rim_attack_match=round(rng.uniform(60.0, 98.0), 1),
                ball_movement_match=round(rng.uniform(60.0, 98.0), 1),
            ),
            role_fit=RoleFitBreakdown(
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
            program_fit=ProgramFitBreakdown(
                nil_score=round(rng.uniform(40.0, 90.0), 1),
                geographic_score=round(rng.uniform(30.0, 95.0), 1),
                academic_score=round(rng.uniform(55.0, 95.0), 1),
                cultural_score=round(rng.uniform(50.0, 90.0), 1),
                nil_budget_alignment=round(rng.uniform(50.0, 1800.0), 0),
            ),
        ),
        weights_used=w,
        computed_at=datetime.now(timezone.utc),
        model_version="fit_v1.0-stub",
    )


def _stub_prediction(program_id: int, player_id: int) -> PredictionResponse:
    rng = random.Random(program_id * 1000 + player_id + 999)
    return PredictionResponse(
        player_id=player_id,
        school_id=program_id,
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
async def compare_players(body: CompareRequest, current_user: CurrentUser):
    # STUB — replace with parallel fit score + prediction fetches in Phase 2
    entries = [
        ComparisonPlayerEntry(
            player=_player_base(pid),
            fit_score=_stub_fit(body.program_id, pid),
            prediction=_stub_prediction(body.program_id, pid),
        )
        for pid in body.player_ids
    ]

    matrix = ComparisonMatrix(
        overall_fit={e.player.full_name: e.fit_score.overall_fit for e in entries},
        gap_match={e.player.full_name: e.fit_score.gap_match for e in entries},
        scheme_fit={e.player.full_name: e.fit_score.scheme_fit for e in entries},
        role_fit={e.player.full_name: e.fit_score.role_fit for e in entries},
        program_fit={e.player.full_name: e.fit_score.program_fit for e in entries},
    )

    best_role   = max(entries, key=lambda e: e.fit_score.role_fit)
    best_nil    = max(entries, key=lambda e: e.fit_score.breakdown.program_fit.nil_score)
    best_scheme = max(entries, key=lambda e: e.fit_score.scheme_fit)

    trade_offs = [
        TradeOff(
            factor="Role Fit",
            description=f"{best_role.player.full_name} offers the best projected role and starter probability.",
            best_player_name=best_role.player.full_name,
            best_player_id=best_role.player.player_id,
        ),
        TradeOff(
            factor="NIL Budget Fit",
            description=f"{best_nil.player.full_name} best aligns with the program's NIL budget.",
            best_player_name=best_nil.player.full_name,
            best_player_id=best_nil.player.player_id,
        ),
        TradeOff(
            factor="Scheme Fit",
            description=f"{best_scheme.player.full_name} system profile most closely matches program offensive identity.",
            best_player_name=best_scheme.player.full_name,
            best_player_id=best_scheme.player.player_id,
        ),
    ]

    return CompareResponse(
        program_id=body.program_id,
        players=entries,
        comparison_matrix=matrix,
        trade_offs=trade_offs,
        generated_at=datetime.now(timezone.utc),
    )
