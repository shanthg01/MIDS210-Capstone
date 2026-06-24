from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class PlayerProjectionResponse(BaseModel):
    """Neutral (context-independent) player talent projection — Phase 0 of
    docs/models/player_projection_state_space_plan.md. Destination-adjusted
    projections (school_id set) are Phase 2+, gated on Role Fit existing.

    Not to be confused with TeamRatingProjectionResponse in schemas/projection.py
    (Model 6, school-level delta-AdjEM) — this is player-level talent/value.
    """

    player_id: int
    season: int
    projection_mode: str
    value_per_100: float
    value_ci_lower: float | None = None
    value_ci_upper: float | None = None
    skill_states: dict[str, float] | None = None
    skill_percentiles: dict[str, float] | None = None
    uncertainty: dict | None = None
    explanation: dict | None = None
    model_version: str
    computed_at: datetime
