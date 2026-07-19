from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PlayingTimeFeatureContribution(BaseModel):
    feature: str
    feature_value: float
    contribution: float
    feature_kind: Literal["raw", "intermediate_probability"]


class PlayingTimeTargetExplanation(BaseModel):
    base_value: float
    raw_model_output: float
    final_output: float
    unit: Literal["minutes_per_game", "usage_rate"]
    other_contribution: float
    drivers: list[PlayingTimeFeatureContribution]


class PlayingTimeExplanationTargets(BaseModel):
    expected_minutes: PlayingTimeTargetExplanation
    expected_usage: PlayingTimeTargetExplanation


class PlayingTimePostprocessing(BaseModel):
    minutes_clipping_delta: float
    minutes_freshman_adjustment: float
    usage_clipping_delta: float
    usage_freshman_adjustment: float
    usage_roster_compression: float


class PlayingTimeExplanation(BaseModel):
    version: Literal[1]
    method: Literal["tree_shap"]
    model_family: str
    targets: PlayingTimeExplanationTargets
    postprocessing: PlayingTimePostprocessing


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
    explanation: PlayingTimeExplanation | None = None
    role_fit: float = Field(..., ge=0.0, le=100.0)
    model_version: str
    computed_at: datetime
