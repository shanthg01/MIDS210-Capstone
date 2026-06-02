from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class PredictedRole(str, Enum):
    STARTER = "starter"
    ROTATION = "rotation"
    BENCH = "bench"
    RESERVE = "reserve"


class SimilarTransfer(BaseModel):
    player_name: str
    season: str  # e.g. "2023-24"
    from_school: str
    to_school: str
    per_before: float
    per_after: float
    per_change: float
    minutes_before: float
    minutes_after: float
    outcome_score: float = Field(..., ge=0, le=5)  # 0-5 success rating from transfer outcome labels


class SHAPExplanation(BaseModel):
    feature: str
    impact: float  # positive = increases predicted value, negative = decreases
    description: str  # human-readable (e.g. "Higher usage rate than destination team average")


class PredictionResponse(BaseModel):
    player_id: int
    school_id: int
    predicted_per_change: float
    predicted_minutes: float
    predicted_role: PredictedRole
    confidence: float = Field(..., ge=0.0, le=1.0)
    similar_transfers: list[SimilarTransfer]
    shap_explanations: list[SHAPExplanation] = []
    model_version: str
