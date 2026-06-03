from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from portalpoint.api.schemas.fit_score import FitWeights
from portalpoint.api.schemas.school import Region


class ImportanceWeights(BaseModel):
    """Player-stated priorities on 1-10 scale. Used to weight Personal Fit sub-components."""
    playing_time: int = Field(default=7, ge=1, le=10)
    nil: int = Field(default=5, ge=1, le=10)
    academics: int = Field(default=5, ge=1, le=10)
    location: int = Field(default=5, ge=1, le=10)


class UserFilters(BaseModel):
    desired_major: str | None = None
    regions: list[Region] = []
    conferences: list[str] = []
    min_enrollment: int | None = None
    max_enrollment: int | None = None


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
    player_id: int | None = None
    created_at: datetime
    preferences: UserPreferences


class ShortlistItem(BaseModel):
    school_id: int
    school_name: str
    conference: str
    overall_fit: float | None = None
    added_at: datetime


class ShortlistResponse(BaseModel):
    user_id: int
    schools: list[ShortlistItem]
    total: int
