from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from portalpoint.api.schemas.fit_score import FitScoreResponse
from portalpoint.api.schemas.player import PlayerBase
from portalpoint.api.schemas.prediction import PredictionResponse


class CompareRequest(BaseModel):
    program_id: int
    player_ids: list[int] = Field(..., min_length=2, max_length=4)


class ComparisonMatrix(BaseModel):
    """Per-component scores keyed by player name. Structured for table/radar rendering."""
    overall_fit: dict[str, float]
    gap_match: dict[str, float]
    scheme_fit: dict[str, float]
    role_fit: dict[str, float]
    program_fit: dict[str, float]


class TradeOff(BaseModel):
    factor: str
    description: str
    best_player_name: str
    best_player_id: int


class ComparisonPlayerEntry(BaseModel):
    player: PlayerBase
    fit_score: FitScoreResponse
    prediction: PredictionResponse


class CompareResponse(BaseModel):
    program_id: int
    players: list[ComparisonPlayerEntry]
    comparison_matrix: ComparisonMatrix
    trade_offs: list[TradeOff]
    generated_at: datetime
