from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, EmailStr, Field

from portalpoint.api.schemas.fit_score import FitWeights
from portalpoint.api.schemas.school import Region


class ImportanceWeights(BaseModel):
    """Program-stated priorities on a 1-10 scale."""

    scheme_fit: int = Field(default=7, ge=1, le=10)
    role_fit: int = Field(default=5, ge=1, le=10)
    gap_match: int = Field(default=5, ge=1, le=10)
    team_impact_fit: int = Field(default=5, ge=1, le=10)


class StatKey(str, Enum):
    """player_season_stats columns eligible for a hard min-value filter.
    Same 10 columns as player_projection.py's SKILL_COLUMNS, plus min_pct —
    kept manually in sync, same convention as that module's own skill list."""

    USAGE_RATE = "usage_rate"
    THREE_POINT_PCT = "fg3_pct"
    FREE_THROW_PCT = "ft_pct"
    RIM_PCT = "rim_pct"
    ASSIST_RATE = "assist_rate"
    TURNOVER_PCT = "tov_pct"
    OFF_REB_PCT = "off_reb_pct"
    DEF_REB_PCT = "def_reb_pct"
    STEAL_PCT = "steal_pct"
    BLOCK_PCT = "block_pct"
    MIN_PCT = "min_pct"


class StatThreshold(BaseModel):
    stat: StatKey
    min_value: float


class UserFilters(BaseModel):
    recruiting_regions: list[Region] = []
    conferences: list[str] = []
    positions: list[str] = []
    target_archetypes: list[str] = []
    nil_budget_min: float | None = None
    nil_budget_max: float | None = None
    min_stats: list[StatThreshold] | None = None


class UserPreferences(BaseModel):
    importance_weights: ImportanceWeights = Field(default_factory=ImportanceWeights)
    filters: UserFilters = Field(default_factory=UserFilters)
    fit_weights: FitWeights = Field(default_factory=FitWeights)


class UserPreferencesUpdate(BaseModel):
    importance_weights: ImportanceWeights | None = None
    filters: UserFilters | None = None
    fit_weights: FitWeights | None = None


class UserProfile(BaseModel):
    user_id: int
    email: EmailStr
    full_name: str
    school_id: int | None = None
    created_at: datetime
    preferences: UserPreferences


class ShortlistItem(BaseModel):
    player_id: str  # str, not int — see player.py's PlayerBase.player_id comment
    player_name: str
    position: str
    overall_fit: float | None = None
    added_at: datetime


class ShortlistResponse(BaseModel):
    user_id: int
    players: list[ShortlistItem]
    total: int
