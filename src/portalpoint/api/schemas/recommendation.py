from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class FitComponents(BaseModel):
    gap_match: float = Field(..., ge=0, le=100)
    scheme_fit: float = Field(..., ge=0, le=100)
    opportunity: float = Field(..., ge=0, le=100)
    personal_fit: float = Field(..., ge=0, le=100)


class RecommendationItem(BaseModel):
    rank: int
    school_id: int
    school_name: str
    conference: str
    overall_fit: float = Field(..., ge=0, le=100)
    components: FitComponents
    reasoning: str  # 1-2 sentence explanation generated from dominant fit factors


class RecommendationsResponse(BaseModel):
    player_id: int
    recommendations: list[RecommendationItem]
    total: int
    generated_at: datetime
    model_version: str
