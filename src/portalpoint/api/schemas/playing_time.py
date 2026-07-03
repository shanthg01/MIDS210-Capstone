from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PlayingTimeProjectionResponse(BaseModel):
    player_id: str
    school_id: int
    season: int
    roster_snapshot_id: int | None = None
    expected_minutes: float = Field(..., ge=0.0, le=40.0)
    expected_minutes_share: float = Field(..., ge=0.0, le=1.0)
    minutes_ci_lower: float = Field(..., ge=0.0, le=40.0)
    minutes_ci_upper: float = Field(..., ge=0.0, le=40.0)
    expected_usage: float = Field(..., ge=0.0, le=100.0)
    usage_role: str
    usage_role_confidence: float = Field(..., ge=0.0, le=1.0)
    starter_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    rotation_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    displaced_minutes: dict | None = None
    opportunity_drivers: dict | None = None
    data_quality_flags: dict | None = None
    scenario_overrides: dict | None = None
    role_fit: float = Field(..., ge=0.0, le=100.0)
    model_version: str
    computed_at: datetime
