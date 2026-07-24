from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from portalpoint.api.schemas.common import ContextStaleness


class RosterImpactItem(BaseModel):
    """Single player row in the ranked roster-impact list."""
    player_id: str              # str, not int — see player.py's PlayerBase.player_id comment
    player_name: str
    position: str
    delta_adjEM: float
    current_adjEM: float
    projected_adjEM: float
    confidence_interval: tuple[float, float]
    expected_minutes_input: float
    candidate_usage_role: Optional[str] = None


class RosterImpactResponse(BaseModel):
    school_id: int
    season: int
    players: list[RosterImpactItem]
    total: int


class TeamRatingOverrideRequest(BaseModel):
    player_id: int
    school_id: int
    season: int = Field(default=2027, description="Target season (must have a scored playing_time_projections row).")
    prior_season: int | None = Field(
        default=None, description="Source/baseline season. Defaults to season - 1."
    )
    minutes_override: float = Field(..., ge=0.0, le=40.0)
    usage_override: float | None = Field(default=None, ge=0.0, le=100.0)


class TeamRatingOverrideResponse(BaseModel):
    player_id: str
    school_id: int
    season: int
    minutes_override: float
    baseline_adj_o: float
    baseline_adj_d: float
    baseline_adj_em: float
    projected_adj_o: float
    projected_adj_d: float
    projected_adj_em: float
    delta_adj_o: float
    delta_adj_d: float
    delta_adj_em: float
    confidence_interval: tuple[float, float]
    explanation: dict[str, Any] | None = None
    context_staleness: ContextStaleness = Field(default_factory=ContextStaleness)


class TeamRatingComparisonRequest(BaseModel):
    player_ids: list[int] = Field(..., min_length=2, max_length=2)
    school_id: int
    season: int = 2027

    @field_validator("player_ids")
    @classmethod
    def require_distinct_players(cls, value: list[int]) -> list[int]:
        if len(set(value)) != 2:
            raise ValueError("player_ids must contain two distinct players")
        return value


class TeamRatingComparisonItem(BaseModel):
    player_id: str
    delta_adj_em: float
    projected_adj_em: float
    confidence_interval: tuple[float, float]
    explanation: dict[str, Any] | None = None


class TeamRatingComparisonResponse(BaseModel):
    school_id: int
    season: int
    scenarios: list[TeamRatingComparisonItem]
    preferred_player_id: str | None
    delta_margin: float
    confidence_intervals_overlap: bool
    reasoning: str
    context_staleness: ContextStaleness = Field(default_factory=ContextStaleness)


class TeamRatingProjectionResponse(BaseModel):
    player_id: str          # str, not int — see player.py's PlayerBase.player_id comment
    school_id: int
    season: int
    current_adjEM: float
    projected_adjEM: float
    delta_adjEM: float
    baseline_adj_o: Optional[float] = None
    baseline_adj_d: Optional[float] = None
    projected_adj_o: Optional[float] = None
    projected_adj_d: Optional[float] = None
    confidence_interval: tuple[float, float]    # 80% CI [lower, upper] on delta_adjEM
    national_percentile: int = Field(..., ge=1, le=100)
    conference_rank: int
    context: str            # human-readable summary, e.g. "Top-40 nationally, up from top-80"
    expected_minutes_input: float
    candidate_usage_role: Optional[str] = None
    explanation: Optional[dict[str, Any]] = None
    context_staleness: ContextStaleness = Field(default_factory=ContextStaleness)
    model_version: str
