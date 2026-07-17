from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


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
    model_version: str
