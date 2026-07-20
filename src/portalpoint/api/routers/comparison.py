import random
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy import func, select

from portalpoint.api.deps import CurrentUser, DbSession
from portalpoint.api.routers.players import _safe_class_year, _safe_position
from portalpoint.api.schemas.comparison import (
    CompareRequest,
    CompareResponse,
    ComparisonMatrix,
    ComparisonPlayerEntry,
    TradeOff,
)
from portalpoint.api.schemas.player import ClassYear, PlayerBase, Position
from portalpoint.api.schemas.prediction import PredictedRole, PredictionResponse, SimilarTransfer
from portalpoint.api.services import fit_score_service
from portalpoint.db.models import Player, PlayerSeasonStats, School
from portalpoint.db.redis_client import get_redis

router = APIRouter(prefix="/api/compare", tags=["comparison"])


async def _player_info(db: DbSession, player_ids: list[int]) -> dict[int, PlayerBase]:
    """Real player/school data where available; stub fallback for IDs not yet in the DB."""
    latest_season_sq = (
        select(
            PlayerSeasonStats.player_id,
            func.max(PlayerSeasonStats.season).label("max_season"),
        )
        .where(PlayerSeasonStats.player_id.in_(player_ids))
        .group_by(PlayerSeasonStats.player_id)
        .subquery()
    )
    stmt = (
        select(Player, School.name.label("school_name"), School.id.label("school_id"))
        .join(latest_season_sq, latest_season_sq.c.player_id == Player.id)
        .join(
            PlayerSeasonStats,
            (PlayerSeasonStats.player_id == Player.id)
            & (PlayerSeasonStats.season == latest_season_sq.c.max_season),
        )
        .join(School, School.id == PlayerSeasonStats.school_id)
        .where(Player.id.in_(player_ids))
    )
    rows = (await db.execute(stmt)).all()

    found: dict[int, PlayerBase] = {
        p.id: PlayerBase(
            player_id=str(p.id),
            full_name=p.full_name,
            position=_safe_position(p.position),
            class_year=_safe_class_year(p.class_year),
            current_school=school_name,
            current_school_id=school_id,
        )
        for p, school_name, school_id in rows
    }

    for pid in player_ids:
        if pid not in found:
            found[pid] = PlayerBase(
                player_id=str(pid),
                full_name=f"Player #{pid}",
                position=Position.SG,
                class_year=ClassYear.JUNIOR,
                current_school="Unknown",
                current_school_id=0,
            )
    return found


def _stub_prediction(program_id: int, player_id: int) -> PredictionResponse:
    # STUB — replace with Model 5 (transfer success predictor) output
    rng = random.Random(program_id * 1000 + player_id + 999)
    return PredictionResponse(
        player_id=str(player_id),
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
async def compare_players(
    body: CompareRequest,
    current_user: CurrentUser,
    db: DbSession,
    redis: Redis = Depends(get_redis),
):
    season = await fit_score_service.get_current_season(db, redis)
    player_info = await _player_info(db, body.player_ids)

    entries = [
        ComparisonPlayerEntry(
            player=player_info[pid],
            fit_score=await fit_score_service.get_fit_score(db, pid, body.program_id, season),
            prediction=_stub_prediction(body.program_id, pid),
        )
        for pid in body.player_ids
    ]

    matrix = ComparisonMatrix(
        overall_fit={e.player.full_name: e.fit_score.overall_fit for e in entries},
        gap_match={e.player.full_name: e.fit_score.gap_match for e in entries},
        scheme_fit={e.player.full_name: e.fit_score.scheme_fit for e in entries},
        role_fit={e.player.full_name: e.fit_score.role_fit for e in entries},
        team_impact_fit={e.player.full_name: e.fit_score.team_impact_fit for e in entries},
    )

    best_scheme = max(entries, key=lambda e: e.fit_score.scheme_fit)
    best_gap = max(entries, key=lambda e: e.fit_score.gap_match)
    best_role = max(entries, key=lambda e: e.fit_score.role_fit)
    best_impact = max(entries, key=lambda e: e.fit_score.team_impact_fit)

    trade_offs = [
        TradeOff(
            factor="Scheme Fit",
            description=(
                f"{best_scheme.player.full_name} system profile most closely matches "
                "program offensive identity."
            ),
            best_player_name=best_scheme.player.full_name,
            best_player_id=best_scheme.player.player_id,
        ),
        TradeOff(
            factor="Gap Match",
            description=(
                f"{best_gap.player.full_name} best fills the program's current roster needs."
            ),
            best_player_name=best_gap.player.full_name,
            best_player_id=best_gap.player.player_id,
        ),
        TradeOff(
            factor="Role Fit",
            description=(
                f"{best_role.player.full_name} offers the best projected role and "
                "starter probability."
            ),
            best_player_name=best_role.player.full_name,
            best_player_id=best_role.player.player_id,
        ),
        TradeOff(
            factor="Team Rating Impact",
            description=(
                f"{best_impact.player.full_name} produces the largest projected rating gain."
            ),
            best_player_name=best_impact.player.full_name,
            best_player_id=best_impact.player.player_id,
        ),
    ]

    return CompareResponse(
        program_id=body.program_id,
        players=entries,
        comparison_matrix=matrix,
        trade_offs=trade_offs,
        generated_at=datetime.now(timezone.utc),
    )
