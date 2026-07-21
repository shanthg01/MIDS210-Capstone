from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from portalpoint.api.deps import CurrentUser, DbSession
from portalpoint.api.schemas.player import (
    ClaimPlayerRequest,
    ClaimPlayerResponse,
    ClassYear,
    PlayerArchetype,
    PlayerBase,
    PlayerProfile,
    PlayerSearchResponse,
    PlayerStats,
    Position,
)
from portalpoint.api.schemas.player_projection import PlayerProjectionResponse
from portalpoint.api.schemas.playing_time import (
    PlayingTimeOverrideRequest,
    PlayingTimeOverrideResponse,
    PlayingTimeProjectionResponse,
)
from portalpoint.api.schemas.user import StatKey
from portalpoint.db.models import (
    Player,
    PlayerSeasonStats,
    School,
    Transfer,
    TransferPortalEvent,
)
from portalpoint.db.models import (
    PlayerArchetype as PlayerArchetypeORM,
)
from portalpoint.db.models import (
    PlayerProjection as PlayerProjectionORM,
)
from portalpoint.db.models import (
    PlayingTimeProjection as PlayingTimeProjectionORM,
)
from portalpoint.modeling.availability import AVAILABLE_STATUSES
from portalpoint.modeling.destination_projection import recompute_box_score_for_minutes
from portalpoint.modeling.minutes import resolved_minutes_per_game
from portalpoint.modeling.player_projection import (
    MODEL_VERSION_CROSS_SEASON_FORECAST as PLAYER_PROJECTION_MODEL_VERSION,
)
from portalpoint.modeling.playing_time import compute_role_fit_override

router = APIRouter(prefix="/api/players", tags=["players"])


def _season_str(season: int) -> str:
    """2025 → '2024-25'"""
    return f"{season - 1}-{str(season)[2:]}"


_POSITION_MAP: dict[str, Position] = {
    "PG": Position.PG, "SG": Position.SG, "SF": Position.SF,
    "PF": Position.PF, "C": Position.C,
    "G": Position.PG, "F": Position.SF,
}


def _safe_position(raw: str) -> Position:
    return _POSITION_MAP.get((raw or "").upper()[:2].strip(), Position.PG)


_CLASS_MAP: dict[str, ClassYear] = {
    "fr": ClassYear.FRESHMAN, "so": ClassYear.SOPHOMORE,
    "jr": ClassYear.JUNIOR,   "sr": ClassYear.SENIOR,
    "gr": ClassYear.GRADUATE,
}


def _safe_class_year(raw: str) -> ClassYear:
    key = (raw or "").lower()[:2]
    return _CLASS_MAP.get(key, ClassYear.SENIOR)


def _parse_min_stats(raw: list[str] | None) -> list[tuple[StatKey, float]]:
    """Each entry formatted '<stat_key>:<min_value>' (e.g. 'usage_rate:20') —
    a hard filter passed explicitly per-request, not pulled server-side from
    saved preferences, since /search stays public (no CurrentUser)."""
    if not raw:
        return []
    parsed: list[tuple[StatKey, float]] = []
    for entry in raw:
        key, _, value = entry.partition(":")
        try:
            stat = StatKey(key)
            min_value = float(value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid min_stat entry: {entry!r}")
        parsed.append((stat, min_value))
    return parsed


def _build_stats(s: PlayerSeasonStats) -> PlayerStats | None:
    try:
        ts = s.true_shooting_pct or 0.0
        if ts > 1.0:  # barttorvik stores as 0-100; schema expects 0-1
            ts /= 100.0
        return PlayerStats(
            season=_season_str(s.season),
            games_played=s.games_played,
            minutes_per_game=resolved_minutes_per_game(s.min_pct, s.minutes_per_game) or 0.0,
            points_per_game=s.points_per_game,
            rebounds_per_game=s.rebounds_per_game,
            assists_per_game=s.assists_per_game,
            steals_per_game=s.steals_per_game,
            blocks_per_game=s.blocks_per_game,
            turnovers_per_game=s.turnovers_per_game,
            per=s.per or 0.0,
            true_shooting_pct=ts,
            usage_rate=s.usage_rate or 0.0,
            assist_rate=s.assist_rate or 0.0,
            bpm=s.bpm,
            win_shares=None,
            three_point_rate=s.three_point_rate or 0.0,
            rim_rate=s.rim_rate or 0.0,
            mid_range_rate=s.mid_range_rate or 0.0,
            assisted_fg_pct=s.assisted_fg_pct or 0.0,
        )
    except Exception:
        return None


# Static path must be registered before /{player_id}
@router.get("/search", response_model=PlayerSearchResponse)
async def search_players(
    db: DbSession,
    name: str = Query(..., min_length=2),
    available_only: bool = Query(
        default=False,
        description="Restrict to players with a matched Entered/Committed transfer_portal_events "
        "row for their latest season — the 'browse the portal' view, not generic player search.",
    ),
    min_stat: list[str] | None = Query(
        default=None,
        description="Repeatable '<stat_key>:<min_value>' pairs (e.g. usage_rate:20) — "
        "AND'd together as a hard floor on the player's latest-season "
        "player_season_stats row. Valid stat_key values: "
        + ", ".join(k.value for k in StatKey),
    ),
):
    min_stats = _parse_min_stats(min_stat)
    # Latest season subquery — avoids N+1 and multi-row joins
    latest_season_sq = (
        select(
            PlayerSeasonStats.player_id,
            func.max(PlayerSeasonStats.season).label("max_season"),
        )
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
        .where(Player.full_name.ilike(f"%{name}%"))
        .order_by(Player.full_name)
        .limit(20)
    )

    if available_only:
        stmt = stmt.join(
            TransferPortalEvent,
            (TransferPortalEvent.player_id == Player.id)
            & (TransferPortalEvent.season == latest_season_sq.c.max_season)
            & (TransferPortalEvent.match_status == "matched")
            & (TransferPortalEvent.status.in_(AVAILABLE_STATUSES)),
        )

    for stat, min_value in min_stats:
        stmt = stmt.where(getattr(PlayerSeasonStats, stat.value) >= min_value)

    rows = (await db.execute(stmt)).all()
    results = [
        PlayerBase(
            player_id=str(p.id),
            full_name=p.full_name,
            position=_safe_position(p.position),
            class_year=_safe_class_year(p.class_year),
            hometown=p.hometown,
            current_school=school_name,
            current_school_id=school_id,
        )
        for p, school_name, school_id in rows
    ]
    return PlayerSearchResponse(results=results, total=len(results), query=name)


@router.get("/{player_id}", response_model=PlayerProfile)
async def get_player(player_id: int, db: DbSession):
    player = (
        await db.execute(select(Player).where(Player.id == player_id))
    ).scalar_one_or_none()
    if player is None:
        raise HTTPException(status_code=404, detail=f"Player {player_id} not found")

    # Latest season stats + school
    stats_row = (
        await db.execute(
            select(
                PlayerSeasonStats,
                School.name.label("school_name"),
                School.id.label("school_id"),
            )
            .join(School, School.id == PlayerSeasonStats.school_id)
            .where(PlayerSeasonStats.player_id == player_id)
            .order_by(PlayerSeasonStats.season.desc())
            .limit(1)
        )
    ).first()

    stats: PlayerStats | None = None
    school_name = "Unknown"
    school_id = 0
    if stats_row:
        stats = _build_stats(stats_row[0])
        school_name = stats_row[1]
        school_id = stats_row[2]

    # Archetype from Model 1 output (None if player_clustering not yet run)
    arch_row = (
        await db.execute(
            select(PlayerArchetypeORM)
            .where(PlayerArchetypeORM.player_id == player_id)
            .order_by(PlayerArchetypeORM.season.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    archetype: PlayerArchetype | None = None
    if arch_row:
        archetype = PlayerArchetype(
            archetype_id=arch_row.archetype_id,
            label=arch_row.archetype_label,
            confidence=arch_row.confidence,
        )

    # Portal status
    transfer = (
        await db.execute(
            select(Transfer)
            .where(Transfer.player_id == player_id, Transfer.to_school_id.is_(None))
            .order_by(Transfer.season.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    return PlayerProfile(
        player_id=str(player.id),
        full_name=player.full_name,
        position=_safe_position(player.position),
        height_inches=player.height_inches,
        class_year=_safe_class_year(player.class_year),
        hometown=player.hometown,
        current_school=school_name,
        current_school_id=school_id,
        archetype=archetype,
        current_season_stats=stats,
        is_in_portal=transfer is not None,
        portal_entry_date=transfer.portal_entry_date if transfer else None,
        twitter_handle=player.twitter_handle,
        social_followers=player.social_followers,
    )


@router.get("/{player_id}/projection", response_model=PlayerProjectionResponse)
async def get_player_projection(
    player_id: int,
    db: DbSession,
    season: int | None = Query(
        default=None,
        description="Season to fetch. Defaults to the player's latest available projection.",
    ),
    school_id: int | None = Query(
        default=None,
        description=(
            "When provided, returns the destination-adjusted projection for this player "
            "evaluated against the given school (projection_mode='destination'). "
            "Omit for the neutral (context-independent) talent projection."
        ),
    ),
    minutes_override: float | None = Query(
        default=None,
        ge=0,
        le=40,
        description=(
            "Destination mode only. Recomputes projected_box_score_at_minutes for this "
            "hypothetical minutes value, using the same fitted usage/assist/turnover/"
            "rebound/block factors — no model rerun. projected_minutes/projected_box_score "
            "are left as the model's own output for comparison."
        ),
    ),
):
    """Player talent/value projection — neutral or destination-adjusted.

    Without school_id: neutral projection (context-independent talent estimate).
    With school_id: destination-adjusted projection incorporating role/usage,
    style/skill fit, roster context, and competition tier deltas for that school.

    Returns 404 rather than synthesizing a fake row — fabricated skill/value
    breakdowns would be actively misleading on this product surface.

    Neutral model priority: forecast (player-proj-phase2a-fcast-v1) →
    same-season Phase 2a → Phase 0 shrinkage. Not narrowed to one version because
    the forecast model has only ~33K rows (2027 season) while other seasons are
    covered by Phase 2a/Phase 0.
    """
    if school_id is not None:
        # Destination-adjusted row for this (player, school) pair
        stmt = select(PlayerProjectionORM).where(
            PlayerProjectionORM.player_id == player_id,
            PlayerProjectionORM.school_id == school_id,
            PlayerProjectionORM.projection_mode == "destination",
            PlayerProjectionORM.model_version == "player-destination-proj-v1",
            PlayerProjectionORM.expires_at > datetime.now(timezone.utc),
        )
        if season is not None:
            stmt = stmt.where(PlayerProjectionORM.season == season)
        stmt = stmt.order_by(
            PlayerProjectionORM.season.desc(),
            PlayerProjectionORM.computed_at.desc(),
        ).limit(1)
    else:
        # Neutral projection — accepts any populated model version
        stmt = select(PlayerProjectionORM).where(
            PlayerProjectionORM.player_id == player_id,
            PlayerProjectionORM.projection_mode == "neutral",
            PlayerProjectionORM.model_version.in_(
                [
                    PLAYER_PROJECTION_MODEL_VERSION,
                    "player-projection-shrinkage-v1",
                    "player-projection-phase2a-v1",
                ]
            ),
            PlayerProjectionORM.expires_at > datetime.now(timezone.utc),
        )
        if season is not None:
            stmt = stmt.where(PlayerProjectionORM.season == season)
        stmt = stmt.order_by(
            PlayerProjectionORM.season.desc(),
            PlayerProjectionORM.computed_at.desc(),
        ).limit(1)

    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        detail = f"No projection found for player {player_id}"
        if school_id is not None:
            detail += f" at school {school_id}"
            if season is not None:
                detail += f" in season {season}"
            detail += " — run run_destination_projection.py to populate destination rows"
        elif season is not None:
            detail += f" in season {season}"
        raise HTTPException(status_code=404, detail=detail)

    projected_box_score_at_minutes = None
    if minutes_override is not None and school_id is not None:
        adjustments = (row.explanation or {}).get("box_score_adjustments")
        projected_box_score_at_minutes = recompute_box_score_for_minutes(
            row.projected_rates, adjustments, minutes_override
        )

    return PlayerProjectionResponse(
        player_id=str(row.player_id),
        season=row.season,
        projection_mode=row.projection_mode,
        school_id=row.school_id,
        value_per_100=row.value_per_100,
        value_ci_lower=row.value_ci_lower,
        value_ci_upper=row.value_ci_upper,
        projected_minutes=row.projected_minutes,
        projected_usage=row.projected_usage,
        projected_box_score=row.projected_box_score,
        projected_rates=row.projected_rates,
        skill_states=row.skill_states,
        skill_percentiles=row.skill_percentiles,
        uncertainty=row.uncertainty,
        explanation=row.explanation,
        minutes_override=minutes_override if school_id is not None else None,
        projected_box_score_at_minutes=projected_box_score_at_minutes,
        model_version=row.model_version,
        computed_at=row.computed_at,
    )


@router.get("/{player_id}/playing-time", response_model=PlayingTimeProjectionResponse)
async def get_player_playing_time(
    player_id: int,
    db: DbSession,
    current_user: CurrentUser,
    school_id: int = Query(...),
    season: int | None = Query(
        default=None,
        description="Season to fetch. Defaults to the latest unexpired projection for the pair.",
    ),
):
    stmt = select(PlayingTimeProjectionORM).where(
        PlayingTimeProjectionORM.player_id == player_id,
        PlayingTimeProjectionORM.school_id == school_id,
        PlayingTimeProjectionORM.expires_at > datetime.now(timezone.utc),
    )
    if season is not None:
        stmt = stmt.where(PlayingTimeProjectionORM.season == season)
    stmt = stmt.order_by(
        PlayingTimeProjectionORM.season.desc(),
        PlayingTimeProjectionORM.computed_at.desc(),
    ).limit(1)

    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        detail = f"No playing-time projection found for player {player_id} and school {school_id}"
        if season is not None:
            detail += f" in season {season}"
        raise HTTPException(status_code=404, detail=detail)

    return PlayingTimeProjectionResponse(
        player_id=str(row.player_id),
        school_id=row.school_id,
        season=row.season,
        roster_snapshot_id=row.roster_snapshot_id,
        expected_minutes=row.expected_minutes,
        expected_minutes_share=row.expected_minutes_share,
        minutes_ci_lower=row.minutes_ci_lower,
        minutes_ci_upper=row.minutes_ci_upper,
        expected_usage=row.expected_usage,
        usage_role=row.usage_role,
        usage_role_confidence=row.usage_role_confidence,
        starter_probability=row.starter_probability,
        rotation_probability=row.rotation_probability,
        displaced_minutes=row.displaced_minutes,
        opportunity_drivers=row.opportunity_drivers,
        data_quality_flags=row.data_quality_flags,
        scenario_overrides=row.scenario_overrides,
        explanation=row.explanation,
        role_fit=row.role_fit,
        model_version=row.model_version,
        computed_at=row.computed_at,
    )


@router.post("/{player_id}/playing-time/override", response_model=PlayingTimeOverrideResponse)
async def override_player_playing_time(
    player_id: int,
    body: PlayingTimeOverrideRequest,
    db: DbSession,
    current_user: CurrentUser,
):
    """Coach-supplied minutes/usage what-if — the real Role Fit formula run
    twice (stored vs. override) and applied as a delta to the batch-computed
    role_fit. Does not touch playing_time_projections; response-only.
    See modeling.playing_time.compute_role_fit_override for why this is a
    delta rather than a full recompute (roster_player_count/roster_open_minutes
    aren't persisted on the batch row)."""
    stmt = select(PlayingTimeProjectionORM).where(
        PlayingTimeProjectionORM.player_id == player_id,
        PlayingTimeProjectionORM.school_id == body.school_id,
        PlayingTimeProjectionORM.expires_at > datetime.now(timezone.utc),
    )
    if body.season is not None:
        stmt = stmt.where(PlayingTimeProjectionORM.season == body.season)
    stmt = stmt.order_by(
        PlayingTimeProjectionORM.season.desc(),
        PlayingTimeProjectionORM.computed_at.desc(),
    ).limit(1)

    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        detail = f"No playing-time projection found for player {player_id} and school {body.school_id}"
        if body.season is not None:
            detail += f" in season {body.season}"
        raise HTTPException(status_code=404, detail=detail)

    stored = {
        "expected_minutes": row.expected_minutes,
        "expected_usage": row.expected_usage,
        "minutes_ci_lower": row.minutes_ci_lower,
        "minutes_ci_upper": row.minutes_ci_upper,
        "role_fit": row.role_fit,
    }
    if row.rotation_probability is not None:
        stored["rotation_probability_model"] = row.rotation_probability
    if row.starter_probability is not None:
        stored["starter_probability_model"] = row.starter_probability

    override_role_fit = compute_role_fit_override(
        stored, minutes_override=body.minutes_override, usage_override=body.usage_override
    )

    return PlayingTimeOverrideResponse(
        player_id=str(player_id),
        school_id=body.school_id,
        season=row.season,
        stored_expected_minutes=row.expected_minutes,
        stored_role_fit=row.role_fit,
        override_expected_minutes=body.minutes_override,
        override_role_fit=override_role_fit,
        model_version=row.model_version,
    )


@router.post("/{player_id}/claim", response_model=ClaimPlayerResponse)
async def claim_player(player_id: int, body: ClaimPlayerRequest, current_user: CurrentUser):
    # STUB — replace with identity verification flow in Phase 2
    return ClaimPlayerResponse(
        success=True,
        player_id=str(player_id),
        message="Player profile linked to your account",
    )
