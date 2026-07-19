"""State schemas for the news-monitoring agent."""
from __future__ import annotations

import operator
from datetime import datetime
from typing import Annotated, Optional

from langgraph.graph.message import add_messages

from typing_extensions import TypedDict


class AgentState(TypedDict):
    """ReAct loop state — backward-compatible with the v2 notebook."""
    messages: Annotated[list, add_messages]
    detected_events: Annotated[list[dict], operator.add]
    portal_updates: list[dict]  # replaced by collect_results (merge) / dedup_node
    news_sources: list[str]
    run_window_start: Optional[datetime]
    run_window_end: Optional[datetime]
    errors: Annotated[list[str], operator.add]


# Alias used by graph.py + run_news_monitoring.py
MonitoringState = AgentState


def initial_state(
    news_sources: list[str] | None = None,
    run_window_start: datetime | None = None,
    run_window_end: datetime | None = None,
) -> AgentState:
    """Return a clean initial state dict for a new monitoring run."""
    return AgentState(
        messages=[],
        detected_events=[],
        portal_updates=[],
        news_sources=news_sources or [
            "247sports.com",
            "on3.com",
            "espn.com",
        ],
        run_window_start=run_window_start,
        run_window_end=run_window_end,
        errors=[],
    )
