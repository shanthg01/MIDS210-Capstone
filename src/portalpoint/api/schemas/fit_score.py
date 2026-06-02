from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class FitWeights(BaseModel):
    """User-adjustable component weights. Default: gap=0.20, scheme=0.30, opp=0.25, personal=0.25."""
    gap: float = Field(default=0.20, ge=0.0, le=1.0)
    scheme: float = Field(default=0.30, ge=0.0, le=1.0)
    opportunity: float = Field(default=0.25, ge=0.0, le=1.0)
    personal: float = Field(default=0.25, ge=0.0, le=1.0)


class SchemeBreakdown(BaseModel):
    three_point_match: float = Field(..., ge=0, le=100)
    pace_match: float = Field(..., ge=0, le=100)
    usage_match: float = Field(..., ge=0, le=100)
    rim_attack_match: float = Field(..., ge=0, le=100)
    ball_movement_match: float = Field(..., ge=0, le=100)


class OpportunityBreakdown(BaseModel):
    projected_minutes: float
    confidence_interval: tuple[float, float]  # [10th-pct, 90th-pct] from Bayesian model
    starter_probability: float = Field(..., ge=0.0, le=1.0)
    depth_chart_position: int  # 1 = projected starter


class GapMatchBreakdown(BaseModel):
    archetype_needed: bool
    position_depth_score: float = Field(..., ge=0, le=100)
    uniqueness_bonus: float  # > 0 when player fills scarce skill the team lacks
    redundancy_penalty: float  # <= 0 when archetype is already stacked on roster


class PersonalFitBreakdown(BaseModel):
    nil_score: float = Field(..., ge=0, le=100)
    geographic_score: float = Field(..., ge=0, le=100)
    academic_score: float = Field(..., ge=0, le=100)
    cultural_score: float = Field(..., ge=0, le=100)
    distance_miles: float


class FitBreakdown(BaseModel):
    scheme: SchemeBreakdown
    opportunity: OpportunityBreakdown
    gap: GapMatchBreakdown
    personal: PersonalFitBreakdown


class FitScoreResponse(BaseModel):
    player_id: int
    school_id: int
    overall_fit: float = Field(..., ge=0, le=100)
    gap_match: float = Field(..., ge=0, le=100)
    scheme_fit: float = Field(..., ge=0, le=100)
    opportunity: float = Field(..., ge=0, le=100)
    personal_fit: float = Field(..., ge=0, le=100)
    breakdown: FitBreakdown
    weights_used: FitWeights
    computed_at: datetime
    model_version: str
    cache_hit: bool = False
