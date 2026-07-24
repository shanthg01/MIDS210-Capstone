from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from portalpoint.api.schemas.common import ContextStaleness


class FitWeights(BaseModel):
    """User-adjustable Personalized Fit weights.

    Canonical Overall Fit always uses these defaults. User changes produce a
    separate ``personalized_fit`` rather than redefining Overall Fit.
    """

    gap: float = Field(default=0.30, ge=0.0, le=1.0)
    scheme: float = Field(default=0.25, ge=0.0, le=1.0)
    role_fit: float = Field(default=0.25, ge=0.0, le=1.0)
    program_fit: float = Field(default=0.20, ge=0.0, le=1.0)


class CosineFeatureContribution(BaseModel):
    feature: str
    contribution: float
    calibrated_contribution: float | None = None


class SchemeBreakdown(BaseModel):
    three_point_match: float = Field(..., ge=0, le=100)
    pace_match: float = Field(..., ge=0, le=100)
    rim_attack_match: float = Field(..., ge=0, le=100)
    mid_range_match: float = Field(..., ge=0, le=100)
    # Play-type match (HoopExplorer 6-dim cosine) — only present when both
    # player and team have HE coverage; None otherwise, never fabricated.
    he_scheme_fit: float | None = None
    he_breakdown: dict[str, float] | None = None
    cosine_contributions: list[CosineFeatureContribution] | None = None
    cosine_score_adjustment: float | None = None
    he_cosine_contributions: list[CosineFeatureContribution] | None = None
    he_cosine_score_adjustment: float | None = None


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
    top_gap_features: list[GapFeatureGap] = Field(default_factory=list)
    raw_gap_match: float | None = Field(default=None, ge=0, le=100)
    calibrated_gap_match: float | None = Field(default=None, ge=0, le=100)
    cosine_contributions: list[CosineFeatureContribution] | None = None
    raw_score_adjustment: float | None = None
    reliability_baseline_contribution: float | None = None
    calibrated_score_adjustment: float | None = None


class ProgramFitBreakdown(BaseModel):
    nil_score: float = Field(..., ge=0, le=100)
    geographic_score: float = Field(..., ge=0, le=100)
    academic_score: float = Field(..., ge=0, le=100)
    cultural_score: float = Field(..., ge=0, le=100)
    nil_budget_alignment: float


class RawFitComponents(BaseModel):
    gap_match: float = Field(..., ge=0, le=100)
    scheme_fit: float = Field(..., ge=0, le=100)
    role_fit: float = Field(..., ge=0, le=100)
    program_fit: float = Field(..., ge=0, le=100)


class ComponentConfidences(BaseModel):
    gap_match: float = Field(..., ge=0, le=1)
    scheme_fit: float = Field(..., ge=0, le=1)
    role_fit: float = Field(..., ge=0, le=1)
    program_fit: float = Field(..., ge=0, le=1)


class FitBreakdown(BaseModel):
    scheme: SchemeBreakdown
    role_fit: RoleFitBreakdown
    gap: GapMatchBreakdown
    program_fit: ProgramFitBreakdown


class FitScoreResponse(BaseModel):
    player_id: str  # str, not int — see player.py's PlayerBase.player_id comment
    school_id: int
    overall_fit: float = Field(..., ge=0, le=100)
    personalized_fit: float | None = Field(default=None, ge=0, le=100)
    gap_match: float = Field(..., ge=0, le=100)
    scheme_fit: float = Field(..., ge=0, le=100)
    role_fit: float = Field(..., ge=0, le=100)
    program_fit: float = Field(..., ge=0, le=100)
    raw_components: RawFitComponents
    component_confidences: ComponentConfidences
    overall_confidence: float = Field(..., ge=0, le=1)
    data_quality_flags: dict[str, bool | str] = Field(default_factory=dict)
    breakdown: FitBreakdown
    explanation: dict | None = None
    weights_used: FitWeights
    personalized_weights: FitWeights | None = None
    computed_at: datetime
    model_version: str
    calibration_version: str | None = None
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
    # True when the news-monitoring agent detected a coaching change at school_id
    # and M2 team_system_profiles has not yet been re-run for this school/season.
    scheme_fit_stale: bool = False
    scheme_fit_stale_reason: Optional[str] = None
    context_staleness: ContextStaleness = Field(default_factory=ContextStaleness)
