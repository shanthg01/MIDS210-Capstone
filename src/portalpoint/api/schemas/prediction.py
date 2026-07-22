from __future__ import annotations

from pydantic import BaseModel, Field


class SimilarTransfer(BaseModel):
    """Named historical comp from the same archetype × team-system cell."""

    player_name: str
    season: int
    success_label: bool
    actual_value_per_100: float
    projected_value_per_100: float
    value_vs_projection: float
    minutes_drift: float | None = None
    usage_drift: float | None = None
    post_minutes_per_game: float | None = None
    projected_minutes: float | None = None
    post_usage_rate: float | None = None
    projected_usage: float | None = None


class PredictionResponse(BaseModel):
    """Transfer success estimate for a portal player at a destination school."""

    player_id: str  # str, not int — see player.py's PlayerBase.player_id comment
    school_id: int
    season: int
    success_probability: float = Field(..., ge=0.0, le=1.0)
    success_tier: str | None = None
    explanation: str | None = None
    similar_transfers: list[SimilarTransfer] = []
    model_version: str
