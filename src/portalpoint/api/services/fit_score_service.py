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
    GapFeatureGap,
    GapMatchBreakdown,
    ProgramFitBreakdown,
    RoleFitBreakdown,
    SchemeBreakdown,
)
from portalpoint.db.models import PlayerSeasonStats, PlayerTeamFitScore, RosterBaselineMember, TeamSystemProfile
from portalpoint.modeling.gap_matching import GAP_FEATURES


def stub_role_fit_breakdown(rng: random.Random) -> RoleFitBreakdown:
    proj_min = round(rng.uniform(16.0, 28.0), 1)
    return RoleFitBreakdown(
        projected_minutes=proj_min,
        confidence_interval=(
            round(proj_min - rng.uniform(4.0, 7.0), 1),
            round(proj_min + rng.uniform(4.0, 7.0), 1),
        ),
        starter_probability=round(rng.uniform(0.35, 0.85), 2),
        depth_chart_position=rng.randint(1, 3),
    )


def role_fit_breakdown_from_model(raw: dict | None, rng: random.Random) -> RoleFitBreakdown:
    if not raw:
        return stub_role_fit_breakdown(rng)

    projected_minutes = float(raw.get("projected_minutes", raw.get("expected_minutes", 0.0)) or 0.0)
    ci = raw.get("confidence_interval")
    if not ci:
        ci = (
            raw.get("minutes_ci_lower", max(projected_minutes - 6.0, 0.0)),
            raw.get("minutes_ci_upper", min(projected_minutes + 6.0, 40.0)),
        )
    ci_lower = float(ci[0])
    ci_upper = float(ci[1])
    starter_probability = raw.get("starter_probability")
    if starter_probability is None:
        starter_probability = max(0.0, min(1.0, (projected_minutes - 18.0) / 12.0))
    rotation_probability = raw.get("rotation_probability")
    if rotation_probability is None:
        rotation_probability = max(0.0, min(1.0, (projected_minutes - 8.0) / 12.0))
    depth_chart_position = raw.get("depth_chart_position")
    if depth_chart_position is None:
        if projected_minutes >= 24.0:
            depth_chart_position = 1
        elif projected_minutes >= 16.0:
            depth_chart_position = 2
        else:
            depth_chart_position = 3

    return RoleFitBreakdown(
        projected_minutes=round(projected_minutes, 1),
        confidence_interval=(round(ci_lower, 1), round(ci_upper, 1)),
        starter_probability=round(float(starter_probability), 3),
        depth_chart_position=int(depth_chart_position),
        expected_usage=raw.get("expected_usage"),
        usage_role=raw.get("usage_role"),
        usage_role_confidence=raw.get("usage_role_confidence"),
        rotation_probability=round(float(rotation_probability), 3),
        displaced_minutes=raw.get("displaced_minutes"),
        data_quality_flags=raw.get("data_quality_flags"),
    )


def stub_gap_breakdown(rng: random.Random) -> GapMatchBreakdown:
    n = rng.randint(1, 3)
    features = rng.sample(GAP_FEATURES, n)
    return GapMatchBreakdown(
        archetype_needed=rng.random() > 0.3,
        position_depth_score=round(rng.uniform(50.0, 95.0), 1),
        gap_reliability=round(rng.uniform(0.5, 1.0), 2),
        top_gap_features=[
            GapFeatureGap(feature=f, gap=round(rng.uniform(0.1, 0.8), 3)) for f in features
        ],
    )


def stub_program_fit_breakdown(rng: random.Random) -> ProgramFitBreakdown:
    return ProgramFitBreakdown(
        nil_score=round(rng.uniform(40.0, 90.0), 1),
        geographic_score=round(rng.uniform(30.0, 95.0), 1),
        academic_score=round(rng.uniform(55.0, 95.0), 1),
        cultural_score=round(rng.uniform(50.0, 90.0), 1),
        nil_budget_alignment=round(rng.uniform(50.0, 1800.0), 0),
    )


def stub_fit_score(
    player_id: int,
    school_id: int,
    is_current_school: bool = False,
    is_roster_baseline_member: bool = False,
    scheme_fit_stale: bool = False,
    scheme_fit_stale_reason: str | None = None,
) -> FitScoreResponse:
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
        player_id=str(player_id),
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
                rim_attack_match=round(rng.uniform(60.0, 98.0), 1),
                mid_range_match=round(rng.uniform(60.0, 98.0), 1),
                # he_scheme_fit/he_breakdown left None — no real row for this
                # pair at all, don't fabricate a signal that doesn't exist.
            ),
            role_fit=stub_role_fit_breakdown(rng),
            gap=stub_gap_breakdown(rng),
            program_fit=stub_program_fit_breakdown(rng),
        ),
        weights_used=w,
        computed_at=datetime.now(timezone.utc),
        model_version="fit_v1.0-stub",
        cache_hit=False,
        is_portal_candidate=False,  # no real row to check — pair is outside model scope
        is_current_school=is_current_school,
        is_roster_baseline_member=is_roster_baseline_member,
        scheme_fit_stale=scheme_fit_stale,
        scheme_fit_stale_reason=scheme_fit_stale_reason,
    )


def real_fit_score(
    row: PlayerTeamFitScore,
    is_current_school: bool = False,
    is_roster_baseline_member: bool = False,
    scheme_fit_stale: bool = False,
    scheme_fit_stale_reason: str | None = None,
) -> FitScoreResponse:
    # role_fit is model-written where playing-time rows have been synced.
    # program_fit is still the 50.0 placeholder until the Program Fit calculator lands.
    rng = random.Random(row.player_id * 1000 + row.school_id)
    bd = row.breakdown or {}
    scheme_bd = bd.get("scheme", {})
    gap_bd = bd.get("gap", {})
    role_bd = bd.get("role_fit", {})

    return FitScoreResponse(
        player_id=str(row.player_id),
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
                rim_attack_match=scheme_bd.get("rim_attack_match", 50.0),
                mid_range_match=scheme_bd.get("mid_range_match", scheme_bd.get("ball_movement_match", 50.0)),
                he_scheme_fit=scheme_bd.get("he_scheme_fit"),
                he_breakdown=scheme_bd.get("he_breakdown"),
            ),
            role_fit=role_fit_breakdown_from_model(role_bd, rng),
            gap=GapMatchBreakdown(
                archetype_needed=gap_bd.get("archetype_needed", False),
                position_depth_score=gap_bd.get("position_depth_score", 50.0),
                gap_reliability=gap_bd.get("gap_reliability", 0.0),
                top_gap_features=[
                    GapFeatureGap(feature=f["feature"], gap=f["gap"])
                    for f in gap_bd.get("top_gap_features", [])
                ],
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
        is_portal_candidate=row.is_portal_candidate,
        is_current_school=is_current_school,
        is_roster_baseline_member=is_roster_baseline_member,
        scheme_fit_stale=scheme_fit_stale,
        scheme_fit_stale_reason=scheme_fit_stale_reason,
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

    # Player already on school_id's own roster this season — the gap_match
    # row can exist and look unintuitive (player counted in their own
    # school's roster gap calc). Surface it instead of hiding it (PR #33
    # follow-up #3).
    current_school_result = await db.execute(
        select(PlayerSeasonStats.player_id).where(
            PlayerSeasonStats.player_id == player_id,
            PlayerSeasonStats.school_id == school_id,
            PlayerSeasonStats.season == season,
        )
    )
    is_current_school = current_school_result.scalar_one_or_none() is not None
    is_roster_baseline_member = await get_roster_baseline_membership(
        db, player_id, school_id, season
    )

    stale_result = await db.execute(
        select(TeamSystemProfile.stale_flag, TeamSystemProfile.stale_reason).where(
            TeamSystemProfile.school_id == school_id,
            TeamSystemProfile.season == season,
        )
    )
    stale_row = stale_result.first()
    scheme_fit_stale = bool(stale_row and stale_row.stale_flag)
    scheme_fit_stale_reason = stale_row.stale_reason if stale_row else None

    if row is not None:
        return real_fit_score(
            row,
            is_current_school=is_current_school,
            is_roster_baseline_member=is_roster_baseline_member,
            scheme_fit_stale=scheme_fit_stale,
            scheme_fit_stale_reason=scheme_fit_stale_reason,
        )
    return stub_fit_score(
        player_id,
        school_id,
        is_current_school=is_current_school,
        is_roster_baseline_member=is_roster_baseline_member,
        scheme_fit_stale=scheme_fit_stale,
        scheme_fit_stale_reason=scheme_fit_stale_reason,
    )


async def get_roster_baseline_membership(
    db: AsyncSession,
    player_id: int,
    school_id: int,
    season: int,
) -> bool:
    """Whether player_id counts in school_id's shared roster baseline.

    Single lookup against roster_baseline_members — the table
    scripts/run_gap_matching.py and notebooks/models/gap_matching.ipynb both
    write via portalpoint.modeling.roster_baseline.write_roster_baseline_members().
    Reads what Gap Matching actually used, rather than re-deriving the same
    historical/snapshot/fallback rules a second time here — one real
    computation, not two that can drift.
    """
    result = await db.execute(
        select(RosterBaselineMember.id).where(
            RosterBaselineMember.player_id == player_id,
            RosterBaselineMember.school_id == school_id,
            RosterBaselineMember.season == season,
        )
    )
    return result.scalar_one_or_none() is not None
