from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from portalpoint.api.schemas.fit_score import FitScoreResponse
from portalpoint.api.schemas.prediction import PredictionResponse
from portalpoint.api.schemas.school import SchoolBase


class CompareRequest(BaseModel):
    player_id: int
    school_ids: list[int] = Field(..., min_length=2, max_length=4)


class ComparisonMatrix(BaseModel):
    """Per-component scores keyed by school name. Structured for table/radar rendering."""
    overall_fit: dict[str, float]
    gap_match: dict[str, float]
    scheme_fit: dict[str, float]
    opportunity: dict[str, float]
    personal_fit: dict[str, float]


class TradeOff(BaseModel):
    factor: str  # e.g. "Playing Time", "NIL Value", "Scheme Fit"
    description: str  # e.g. "School A offers more minutes but weaker NIL"
    best_school_name: str
    best_school_id: int


class ComparisonSchoolEntry(BaseModel):
    school: SchoolBase
    fit_score: FitScoreResponse
    prediction: PredictionResponse


class CompareResponse(BaseModel):
    player_id: int
    schools: list[ComparisonSchoolEntry]
    comparison_matrix: ComparisonMatrix
    trade_offs: list[TradeOff]
    generated_at: datetime
