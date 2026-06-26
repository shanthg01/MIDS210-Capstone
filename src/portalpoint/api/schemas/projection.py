from __future__ import annotations

from pydantic import BaseModel, Field


class TeamRatingProjectionResponse(BaseModel):
    player_id: str  # str, not int — see player.py's PlayerBase.player_id comment
    school_id: int
    current_adjEM: float
    projected_adjEM: float
    delta_adjEM: float
    confidence_interval: tuple[float, float]  # 80% CI [lower_bound, upper_bound]
    national_percentile: int = Field(..., ge=1, le=100)
    conference_rank: int
    context: str  # e.g. "Top-40 nationally, up from top-80 without you"
    expected_minutes_input: float  # minutes fed in from Playing Time model (Model 4)
    model_version: str
