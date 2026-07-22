from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from portalpoint.api.schemas.common import ContextStaleness


class PlayerProjectionResponse(BaseModel):
    """Player talent/value projection — neutral or destination-adjusted.

    `projection_mode='neutral'` (default): context-independent talent estimate,
    served when no school_id is provided.
    `projection_mode='destination'`: school-specific adjusted projection,
    served when ?school_id=X is passed to the endpoint. Includes projected_minutes
    and projected_usage from the Playing Time model and per-game destination_box_score.

    Not to be confused with TeamRatingProjectionResponse in schemas/projection.py
    (Model 6, school-level delta-AdjEM) — this is player-level talent/value.
    """

    player_id: str  # str, not int — see player.py's PlayerBase.player_id comment
    season: int
    projection_mode: str
    school_id: int | None = None           # set for destination rows, None for neutral
    value_per_100: float
    value_ci_lower: float | None = None
    value_ci_upper: float | None = None
    projected_minutes: float | None = None  # destination mode only — from Playing Time model
    projected_usage: float | None = None    # destination mode only — from Playing Time model
    projected_box_score: dict | None = None
    projected_rates: dict | None = None
    skill_states: dict[str, float] | None = None
    skill_percentiles: dict[str, float] | None = None
    uncertainty: dict | None = None
    explanation: dict | None = None
    context_staleness: ContextStaleness = Field(default_factory=ContextStaleness)
    model_version: str
    computed_at: datetime
