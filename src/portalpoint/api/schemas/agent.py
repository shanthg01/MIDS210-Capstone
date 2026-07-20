from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class AgentRunRequest(BaseModel):
    season: int | None = Field(default=None, description="Defaults to the current calendar year.")
    window_days: int | None = Field(default=None, description="Tavily search lookback days; defaults to config.")
    use_llm: bool = Field(default=True, description="Gemini structured-output classifier vs. regex.")
    dry_run: bool = Field(default=False, description="Search + classify only, no DB writes.")


class AgentRunAccepted(BaseModel):
    run_id: str
    status: Literal["running"]


class AgentRunStatus(BaseModel):
    run_id: str
    status: Literal["running", "completed", "failed"]
    started_at: datetime
    finished_at: datetime | None = None
    summary: dict | None = None
    error: str | None = None


class ProgramEventItem(BaseModel):
    id: int
    event_type: str
    school_id: int | None = None
    player_id: int | None = None
    coach_id: int | None = None
    event_date: date | None = None
    source: str
    confidence: float | None = None
    match_status: str
    created_at: datetime


class ProgramEventsResponse(BaseModel):
    events: list[ProgramEventItem]
    total: int
