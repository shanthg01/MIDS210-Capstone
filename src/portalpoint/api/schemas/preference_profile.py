from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from portalpoint.api.schemas.fit_score import FitWeights
from portalpoint.api.schemas.user import ImportanceWeights, UserFilters


class PreferenceProfileCreate(BaseModel):
    """A named snapshot of whatever's currently in the Settings form when the
    user clicks Save — not re-derived server-side."""
    name: str = Field(min_length=1, max_length=100)
    fit_weights: FitWeights = Field(default_factory=FitWeights)
    importance_weights: ImportanceWeights = Field(default_factory=ImportanceWeights)
    filters: UserFilters = Field(default_factory=UserFilters)


class PreferenceProfile(PreferenceProfileCreate):
    id: int
    created_at: datetime


class PreferenceProfileListResponse(BaseModel):
    profiles: list[PreferenceProfile]
