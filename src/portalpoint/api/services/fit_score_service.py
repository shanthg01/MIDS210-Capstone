"""Shared player_team_fit_scores row -> FitScoreResponse mapping.

Used by both fit_scores.py (single player x school lookup) and comparison.py
(N players x one program). Keeps the real-row-or-stub fallback and the
stub-generation logic in one place.
"""
import random
from datetime import datetime, timezone

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from portalpoint.api.schemas.fit_score import (
    FitBreakdown,
    FitScoreResponse,
    FitWeights,
    GapMatchBreakdown,
    ProgramFitBreakdown,
    RoleFitBreakdown,
    SchemeBreakdown,
)
from portalpoint.db.models import PlayerTeamFitScore


def stub_role_fit_breakdown(rng: random.Random) -> RoleFitBreakdown:
    proj_min = round(rng.uniform(16.0, 28.0), 1)
    return RoleFitBreakdown(
        projected_minutes=proj_min,
        confidence_interval=(round(proj_min - rng.uniform(4.0, 7.0), 1), round(proj_min + rng.uniform(4.0, 7.0), 1)),
        starter_probability=round(rng.uniform(0.35, 0.85), 2),
        depth_chart_position=rng.randint(1, 3),
    )


def stub_program_fit_breakdown(rng: random.Random) -> ProgramFitBreakdown:
    return ProgramFitBreakdown(
        nil_score=round(rng.uniform(40.0, 90.0), 1),
        geographic_score=round(rng.uniform(30.0, 95.0), 1),
        academic_score=round(rng.uniform(55.0, 95.0), 1),
        cultural_score=round(rng.uniform(50.0, 90.0), 1),
        nil_budget_alignment=round(rng.uniform(50.0, 1800.0), 0),
    )


def stub_fit_score(player_id: int, school_id: int) -> FitScoreResponse:
    rng = random.Random(player_id * 1000 + school_id)
    gap = round(rng.uniform(55.0, 95.0), 1)
    scheme = round(rng.uniform(55.0, 95.0), 1)
    role = round(rng.uniform(55.0, 90.0), 1)
    program = round(rng.uniform(50.0, 85.0), 1)
    w = FitWeights()
    overall = round(
        gap * w.gap + scheme * w.scheme + role * w.role_fit + program * w.program_fit,
        1,
    )
    return FitScoreResponse(
        player_id=player_id,
        school_id=school_id,
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
            role_fit=stub_role_fit_breakdown(rng),
            gap=GapMatchBreakdown(
                archetype_needed=rng.random() > 0.3,
                position_depth_score=round(rng.uniform(50.0, 95.0), 1),
                uniqueness_bonus=round(rng.uniform(0.0, 15.0), 1),
                redundancy_penalty=round(rng.uniform(-15.0, 0.0), 1),
            ),
            program_fit=stub_program_fit_breakdown(rng),
        ),
        weights_used=w,
        computed_at=datetime.now(timezone.utc),
        model_version="fit_v1.0-stub",
        cache_hit=False,
    )


def real_fit_score(row: PlayerTeamFitScore) -> FitScoreResponse:
    # role_fit and program_fit are not yet computed (Models 4 + program calculator
    # pending) — their scalar values are the 50.0 stub written by M3/Gap Matching,
    # and their breakdowns are seeded random for plausible-looking UI fields.
    rng = random.Random(row.player_id * 1000 + row.school_id)
    bd = row.breakdown or {}
    scheme_bd = bd.get("scheme", {})
    gap_bd = bd.get("gap", {})

    return FitScoreResponse(
        player_id=row.player_id,
        school_id=row.school_id,
        overall_fit=row.overall_fit,
        gap_match=row.gap_match,
        scheme_fit=row.scheme_fit,
        role_fit=row.role_fit,
        program_fit=row.program_fit,
        breakdown=FitBreakdown(
            scheme=SchemeBreakdown(
                three_point_match=scheme_bd.get("three_point_match", 50.0),
                pace_match=scheme_bd.get("pace_match", 50.0),
                usage_match=scheme_bd.get("usage_match", 50.0),
                rim_attack_match=scheme_bd.get("rim_attack_match", 50.0),
                ball_movement_match=scheme_bd.get("ball_movement_match", 50.0),
            ),
            role_fit=stub_role_fit_breakdown(rng),
            gap=GapMatchBreakdown(
                archetype_needed=gap_bd.get("archetype_needed", False),
                position_depth_score=gap_bd.get("position_depth_score", 50.0),
                # uniqueness_bonus / redundancy_penalty not yet computed by gap-cos-v1
                uniqueness_bonus=0.0,
                redundancy_penalty=0.0,
            ),
            program_fit=stub_program_fit_breakdown(rng),
        ),
        weights_used=FitWeights(
            gap=row.weight_gap,
            scheme=row.weight_scheme,
            role_fit=row.weight_role,
            program_fit=row.weight_program,
        ),
        computed_at=row.computed_at,
        model_version=row.model_version,
        cache_hit=False,
    )


# Cache key/TTL for the resolved "current season" — only changes when M3 or
# Gap Matching are rerun for a new season, so an hour of staleness is fine.
_CURRENT_SEASON_CACHE_KEY = "current_season"
_CURRENT_SEASON_CACHE_TTL = 3600
_CURRENT_SEASON_FALLBACK = 2026  # used only if player_team_fit_scores is empty


async def get_current_season(db: AsyncSession, redis: Redis | None = None) -> int:
    """Most recent season present in player_team_fit_scores.

    Replaces the old CURRENT_SEASON=2026 hardcode — that constant would go
    stale the moment a new season's data lands without a code change + deploy.
    """
    if redis is not None:
        try:
            cached = await redis.get(_CURRENT_SEASON_CACHE_KEY)
        except Exception:
            cached = None
        if cached is not None:
            return int(cached)

    result = await db.execute(select(func.max(PlayerTeamFitScore.season)))
    season = result.scalar_one_or_none() or _CURRENT_SEASON_FALLBACK

    if redis is not None:
        try:
            await redis.set(_CURRENT_SEASON_CACHE_KEY, str(season), ex=_CURRENT_SEASON_CACHE_TTL)
        except Exception:
            pass

    return season


async def get_fit_score(
    db: AsyncSession, player_id: int, school_id: int, season: int
) -> FitScoreResponse:
    """Real DB row when available; full stub when the pair is outside M3/Gap Matching scope."""
    result = await db.execute(
        select(PlayerTeamFitScore).where(
            PlayerTeamFitScore.player_id == player_id,
            PlayerTeamFitScore.school_id == school_id,
            PlayerTeamFitScore.season == season,
        )
    )
    row = result.scalar_one_or_none()
    return real_fit_score(row) if row is not None else stub_fit_score(player_id, school_id)
