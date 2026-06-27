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
    expected_usage: float | None = Field(default=None, ge=0.0, le=100.0)
    usage_role: str | None = None
    usage_role_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    rotation_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    displaced_minutes: dict | None = None
    data_quality_flags: dict | None = None


class GapFeatureGap(BaseModel):
    feature: str
    gap: float  # how far below the destination's target this player's stat sits, pre-weighting


class GapMatchBreakdown(BaseModel):
    archetype_needed: bool
    position_depth_score: float = Field(..., ge=0, le=100)
    # Confidence in the gap score itself — blends position-source/sample/feature
    # reliability (modeling/gap_matching.py's gap_reliability), not a fit score.
    gap_reliability: float = Field(..., ge=0.0, le=1.0)
    # The specific stats this player most closes the gap on for this school,
    # largest first — real model output (gap_matching.py's top_gap_features),
    # replaces the old uniqueness_bonus/redundancy_penalty fields, which were
    # never actually computed (always hardcoded to 0.0).
    top_gap_features: list[GapFeatureGap] = []


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
    player_id: str  # str, not int — see player.py's PlayerBase.player_id comment
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
    # True if the player counts in the shared roster baseline used by
    # roster-aware models. This can differ from is_current_school when a
    # player has a stale player_season_stats row but is absent from the latest
    # roster outlook.
    is_roster_baseline_member: bool = False
