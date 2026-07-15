from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class FitComponents(BaseModel):
    gap_match: float = Field(..., ge=0, le=100)
    scheme_fit: float = Field(..., ge=0, le=100)
    role_fit: float = Field(..., ge=0, le=100)
    # M6 delta_adjEM normalized to 0-100 (clip ±5 AdjEM → 0-100, 0 delta = 50.0).
    # program_fit excluded — descoped 2026-07-11; DEFAULT_FIT_WEIGHTS uses this instead.
    team_impact_fit: float = Field(..., ge=0, le=100)


class RecommendationItem(BaseModel):
    rank: int
    player_id: str  # str, not int — see player.py's PlayerBase.player_id comment
    player_name: str
    position: str
    overall_fit: float = Field(..., ge=0, le=100)
    components: FitComponents
    reasoning: str  # 1-2 sentence explanation generated from dominant fit factors
    is_portal_candidate: bool  # always true today — CANDIDATE_SQL already filters on it


class RecommendationsResponse(BaseModel):
    program_id: int
    recommendations: list[RecommendationItem]
    total: int
    generated_at: datetime
    model_version: str
