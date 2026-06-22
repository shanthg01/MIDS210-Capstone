from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


class Position(str, Enum):
    PG = "PG"
    SG = "SG"
    SF = "SF"
    PF = "PF"
    C = "C"


class ClassYear(str, Enum):
    FRESHMAN = "freshman"
    SOPHOMORE = "sophomore"
    JUNIOR = "junior"
    SENIOR = "senior"
    GRADUATE = "graduate"


class PlayerStats(BaseModel):
    season: str  # e.g. "2025-26"
    games_played: int
    minutes_per_game: float
    points_per_game: float
    rebounds_per_game: float
    assists_per_game: float
    steals_per_game: float
    blocks_per_game: float
    turnovers_per_game: float
    # Advanced
    per: float
    true_shooting_pct: float
    usage_rate: float
    assist_rate: float
    bpm: float | None = None
    win_shares: float | None = None
    # Shot distribution (from play-by-play; used in scheme fit vector)
    three_point_rate: float
    rim_rate: float
    mid_range_rate: float
    assisted_fg_pct: float


class PlayerArchetype(BaseModel):
    archetype_id: int
    label: str  # "3&D Wing", "Stretch 4", "Primary Creator", etc.
    confidence: float = Field(..., ge=0.0, le=1.0)


class PlayerBase(BaseModel):
    player_id: int
    full_name: str
    position: Position
    height_inches: int | None = None
    class_year: ClassYear
    hometown: str | None = None
    current_school: str
    current_school_id: int


class PlayerProfile(PlayerBase):
    archetype: PlayerArchetype | None = None
    current_season_stats: PlayerStats | None = None
    is_in_portal: bool = False
    portal_entry_date: date | None = None
    twitter_handle: str | None = None
    social_followers: int | None = None


class PlayerSearchResponse(BaseModel):
    results: list[PlayerBase]
    total: int
    query: str


class ClaimPlayerRequest(BaseModel):
    player_id: int
    verification_code: str | None = None


class ClaimPlayerResponse(BaseModel):
    success: bool
    player_id: int
    message: str
