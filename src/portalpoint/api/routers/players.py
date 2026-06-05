from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from portalpoint.api.deps import CurrentUser, DbSession
from portalpoint.api.schemas.player import (
    ClassYear,
    ClaimPlayerRequest,
    ClaimPlayerResponse,
    PlayerArchetype,
    PlayerBase,
    PlayerProfile,
    PlayerSearchResponse,
    PlayerStats,
    Position,
)
from portalpoint.db.models import (
    Player,
    PlayerArchetype as PlayerArchetypeORM,
    PlayerSeasonStats,
    School,
    Transfer,
)

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


def _build_stats(s: PlayerSeasonStats) -> PlayerStats | None:
    try:
        ts = s.true_shooting_pct or 0.0
        if ts > 1.0:  # barttorvik stores as 0-100; schema expects 0-1
            ts /= 100.0
        return PlayerStats(
            season=_season_str(s.season),
            games_played=s.games_played,
            minutes_per_game=s.minutes_per_game,
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
async def search_players(db: DbSession, name: str = Query(..., min_length=2)):
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

    rows = (await db.execute(stmt)).all()
    results = [
        PlayerBase(
            player_id=p.id,
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
            select(PlayerSeasonStats, School.name.label("school_name"), School.id.label("school_id"))
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
        player_id=player.id,
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


@router.post("/{player_id}/claim", response_model=ClaimPlayerResponse)
async def claim_player(player_id: int, body: ClaimPlayerRequest, current_user: CurrentUser):
    # STUB — replace with identity verification flow in Phase 2
    return ClaimPlayerResponse(
        success=True,
        player_id=player_id,
        message="Player profile linked to your account",
    )
