from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from portalpoint.api.schemas.common import ContextStaleness


class Region(str, Enum):
    NORTHEAST = "Northeast"
    SOUTHEAST = "Southeast"
    MID_ATLANTIC = "Mid-Atlantic"
    MIDWEST = "Midwest"
    SOUTHWEST = "Southwest"
    WEST = "West"
    PACIFIC = "Pacific"


class SchoolSetting(str, Enum):
    URBAN = "urban"
    SUBURBAN = "suburban"
    RURAL = "rural"


class TeamStyle(BaseModel):
    pace: float  # possessions per 40 min; national avg ~70
    offensive_rating: float  # points per 100 possessions
    defensive_rating: float
    three_point_rate: float  # share of FGA that are 3s
    assist_rate: float  # assists per made FG
    system_label: str  # e.g. "Fast 3PT Heavy", "Slow Post-Dominant"
    system_cluster_id: int


class SchoolBase(BaseModel):
    school_id: int
    name: str
    conference: str
    city: str
    state: str
    region: Region


class SchoolDetail(SchoolBase):
    enrollment: int | None = None
    setting: SchoolSetting | None = None
    latitude: float | None = None
    longitude: float | None = None
    majors_offered: list[str] = []
    graduation_rate: float | None = None
    nil_tier: str | None = None  # "high" | "medium" | "low"
    nil_estimated_budget_usd: float | None = None
    current_season_style: TeamStyle | None = None
    current_adjEM: float | None = None
    national_rank: int | None = None


class SchoolListItem(BaseModel):
    school_id: int
    name: str
    conference: str


class SchoolListResponse(BaseModel):
    schools: list[SchoolListItem]


class UpdateSchoolRequest(BaseModel):
    school_id: int


class UpdateSchoolResponse(BaseModel):
    school_id: int


class TeamSystemProfileResponse(BaseModel):
    """The user's program's own team-system archetype (Model #2, team_clustering.py)
    — combined offense/defense cluster label, not a player-fit score."""
    school_id: int
    season: int
    system_label: str
    offense_label: str | None = None
    defense_label: str | None = None
    offense_memberships: list[dict] | None = None
    defense_memberships: list[dict] | None = None
    system_memberships: list[dict] | None = None
    explanation: dict | None = None
    context_staleness: ContextStaleness = Field(default_factory=ContextStaleness)
    model_version: str | None = None


class RosterGapResponse(BaseModel):
    """Derived from roster_state_features — facts (open minutes/usage by
    position), not a model output. suggested_position is the simplest real
    read of those facts: whichever position has the most open minutes share,
    not a new score."""
    school_id: int
    season: int
    open_minutes_by_position: dict[str, float]
    open_usage_by_position: dict[str, float] | None = None
    suggested_position: str | None = None
    suggested_open_minutes: float | None = None
