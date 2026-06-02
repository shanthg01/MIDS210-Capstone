import random
from datetime import date

from fastapi import APIRouter, Query

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

router = APIRouter(prefix="/api/players", tags=["players"])

_SAMPLE_PLAYERS: list[tuple] = [
    (101, "Marcus Johnson", Position.SG, ClassYear.JUNIOR,    "Atlanta, GA",   "Georgia State", 201),
    (102, "Marcus Williams", Position.PG, ClassYear.SENIOR,   "Chicago, IL",   "DePaul",        202),
    (103, "Marcus Davis",    Position.SF, ClassYear.SOPHOMORE, "Houston, TX",   "Houston",       203),
]


def _stub_stats(seed: int) -> PlayerStats:
    rng = random.Random(seed)
    three = round(rng.uniform(0.20, 0.45), 2)
    rim = round(rng.uniform(0.25, 0.45), 2)
    return PlayerStats(
        season="2025-26",
        games_played=rng.randint(25, 35),
        minutes_per_game=round(rng.uniform(18.0, 32.0), 1),
        points_per_game=round(rng.uniform(8.0, 20.0), 1),
        rebounds_per_game=round(rng.uniform(2.0, 8.0), 1),
        assists_per_game=round(rng.uniform(1.0, 6.0), 1),
        steals_per_game=round(rng.uniform(0.5, 2.0), 1),
        blocks_per_game=round(rng.uniform(0.1, 1.5), 1),
        turnovers_per_game=round(rng.uniform(1.0, 3.0), 1),
        per=round(rng.uniform(12.0, 22.0), 1),
        true_shooting_pct=round(rng.uniform(0.50, 0.62), 3),
        usage_rate=round(rng.uniform(16.0, 28.0), 1),
        assist_rate=round(rng.uniform(10.0, 30.0), 1),
        three_point_rate=three,
        rim_rate=rim,
        mid_range_rate=round(max(0.05, 1.0 - three - rim - 0.15), 2),
        assisted_fg_pct=round(rng.uniform(0.40, 0.75), 2),
        bpm=round(rng.uniform(-2.0, 5.0), 1),
    )


# Static path must be registered before /{player_id}
@router.get("/search", response_model=PlayerSearchResponse)
async def search_players(name: str = Query(..., min_length=2)):
    # STUB — replace with trigram GIN index search in Phase 2
    matches = [
        PlayerBase(
            player_id=pid,
            full_name=full_name,
            position=pos,
            class_year=cy,
            hometown=hometown,
            current_school=school,
            current_school_id=school_id,
        )
        for pid, full_name, pos, cy, hometown, school, school_id in _SAMPLE_PLAYERS
        if name.lower() in full_name.lower()
    ]
    return PlayerSearchResponse(results=matches, total=len(matches), query=name)


@router.get("/{player_id}", response_model=PlayerProfile)
async def get_player(player_id: int):
    # STUB — replace with DB lookup in Phase 2
    return PlayerProfile(
        player_id=player_id,
        full_name="Marcus Johnson",
        position=Position.SG,
        height_inches=76,
        class_year=ClassYear.JUNIOR,
        hometown="Atlanta, GA",
        current_school="Georgia State",
        current_school_id=201,
        archetype=PlayerArchetype(archetype_id=3, label="3&D Wing", confidence=0.82),
        current_season_stats=_stub_stats(player_id),
        is_in_portal=True,
        portal_entry_date=date(2026, 5, 1),
    )


@router.post("/{player_id}/claim", response_model=ClaimPlayerResponse)
async def claim_player(player_id: int, body: ClaimPlayerRequest):
    # STUB — replace with identity verification flow in Step 5
    return ClaimPlayerResponse(
        success=True,
        player_id=player_id,
        message="Player profile linked to your account",
    )
