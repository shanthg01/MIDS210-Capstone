from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class SuccessTier(str, Enum):
    VERY_LOW = "Very Low"
    LOW = "Low"
    MODERATE = "Moderate"
    HIGH = "High"
    VERY_HIGH = "Very High"


class SimilarTransfer(BaseModel):
    """One historical comp from the same (player_cluster x team system) cell.

    Field names mirror transfer_success.py's attach_similar_transfers() comp
    records directly — the empirical-Bayes model has no per-player PER
    before/after concept, it compares actual vs. destination-adjusted-
    projected value_per_100.
    """
    player_name: str
    season: int
    value_vs_projection: float
    success_label: bool | None = None
    minutes_drift: float | None = None
    usage_drift: float | None = None
    actual_value_per_100: float | None = None
    projected_value_per_100: float | None = None
    post_minutes_per_game: float | None = None
    projected_minutes: float | None = None
    post_usage_rate: float | None = None
    projected_usage: float | None = None


class PredictionResponse(BaseModel):
    player_id: str  # str, not int — see player.py's PlayerBase.player_id comment
    school_id: int
    success_probability: float = Field(..., ge=0.0, le=1.0)
    success_tier: SuccessTier
    cell_n: float | None = None
    shrinkage_w: float | None = None
    explanation: str
    similar_transfers: list[SimilarTransfer]
    model_version: str
