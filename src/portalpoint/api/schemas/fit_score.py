from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class FitWeights(BaseModel):
    """User-adjustable component weights. Default: gap=0.20, scheme=0.30, role_fit=0.25, program_fit=0.25."""
    gap: float = Field(default=0.20, ge=0.0, le=1.0)
    scheme: float = Field(default=0.30, ge=0.0, le=1.0)
    role_fit: float = Field(default=0.25, ge=0.0, le=1.0)
    program_fit: float = Field(default=0.25, ge=0.0, le=1.0)


class SchemeBreakdown(BaseModel):
    three_point_match: float = Field(..., ge=0, le=100)
    pace_match: float = Field(..., ge=0, le=100)
    usage_match: float = Field(..., ge=0, le=100)
    rim_attack_match: float = Field(..., ge=0, le=100)
    ball_movement_match: float = Field(..., ge=0, le=100)


class RoleFitBreakdown(BaseModel):
    projected_minutes: float
    confidence_interval: tuple[float, float]  # [10th-pct, 90th-pct] from Bayesian model
    starter_probability: float = Field(..., ge=0.0, le=1.0)
    depth_chart_position: int  # 1 = projected starter


class GapMatchBreakdown(BaseModel):
    archetype_needed: bool
    position_depth_score: float = Field(..., ge=0, le=100)
    uniqueness_bonus: float  # > 0 when player fills scarce skill the team lacks
    redundancy_penalty: float  # <= 0 when archetype is already stacked on roster


class ProgramFitBreakdown(BaseModel):
    nil_score: float = Field(..., ge=0, le=100)
    geographic_score: float = Field(..., ge=0, le=100)
    academic_score: float = Field(..., ge=0, le=100)
    cultural_score: float = Field(..., ge=0, le=100)
    nil_budget_alignment: float


class FitBreakdown(BaseModel):
    scheme: SchemeBreakdown
    role_fit: RoleFitBreakdown
    gap: GapMatchBreakdown
    program_fit: ProgramFitBreakdown


class FitScoreResponse(BaseModel):
    player_id: int
    school_id: int
    overall_fit: float = Field(..., ge=0, le=100)
    gap_match: float = Field(..., ge=0, le=100)
    scheme_fit: float = Field(..., ge=0, le=100)
    role_fit: float = Field(..., ge=0, le=100)
    program_fit: float = Field(..., ge=0, le=100)
    breakdown: FitBreakdown
    weights_used: FitWeights
    computed_at: datetime
    model_version: str
    cache_hit: bool = False
    # True if the player has a matched Entered/Committed transfer_portal_events
    # row for this season — distinguishes "available recruit" from a generic
    # player-school fit score (PR #33 follow-up #5). False for stub-fallback
    # pairs (no real row exists to check).
    is_portal_candidate: bool = False
    # True if the player is already on school_id's own roster for this season
    # (player_season_stats) — flags the current-school-row confusion case
    # (PR #33 follow-up #3) instead of hiding it.
    is_current_school: bool = False
