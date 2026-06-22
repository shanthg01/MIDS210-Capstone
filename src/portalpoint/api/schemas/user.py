from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from portalpoint.api.schemas.fit_score import FitWeights
from portalpoint.api.schemas.school import Region


class ImportanceWeights(BaseModel):
    """Program-stated priorities on 1-10 scale. Used to weight Program Fit sub-components."""
    scheme_fit: int = Field(default=7, ge=1, le=10)
    role_fit: int = Field(default=5, ge=1, le=10)
    gap_match: int = Field(default=5, ge=1, le=10)
    program_fit: int = Field(default=5, ge=1, le=10)


class UserFilters(BaseModel):
    recruiting_regions: list[Region] = []
    conferences: list[str] = []
    positions: list[str] = []
    target_archetypes: list[str] = []
    nil_budget_min: float | None = None
    nil_budget_max: float | None = None
    min_stats: dict | None = None


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
    player_id: int
    player_name: str
    position: str
    overall_fit: float | None = None
    added_at: datetime


class ShortlistResponse(BaseModel):
    user_id: int
    players: list[ShortlistItem]
    total: int
