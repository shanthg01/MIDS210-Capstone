from __future__ import annotations

from pydantic import BaseModel, Field


class ContextStaleness(BaseModel):
    """Shared coaching-context freshness signal for downstream models."""

    is_stale: bool = False
    reason: str | None = None
    affected_models: list[str] = Field(default_factory=list)
