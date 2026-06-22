from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


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
