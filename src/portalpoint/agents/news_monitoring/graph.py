"""LangGraph StateGraph for the news-monitoring agent."""
from __future__ import annotations

import json
import logging
from typing import Literal

from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from portalpoint.agents.news_monitoring.config import GEMINI_MODEL
from portalpoint.agents.news_monitoring.extract import RateLimiter, build_llm
from portalpoint.agents.news_monitoring.prompts import SYSTEM_PROMPT
from portalpoint.agents.news_monitoring.resolve import cross_source_dedup
from portalpoint.agents.news_monitoring.state import AgentState

log = logging.getLogger(__name__)

CLASSIFY_TOOL_NAMES = frozenset({
    "classify_event",
    "classify_events_batch",
    "classify_event_llm",
    "classify_events_batch_llm",
})

# Re-export for backward compatibility
__all__ = [
    "SYSTEM_PROMPT",
    "build_graph",
    "should_continue",
    "dedup_node",
    "collect_results_node",
    "build_agent_node",
    "CLASSIFY_TOOL_NAMES",
]


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------

def build_agent_node(llm_with_tools, rate_limiter: RateLimiter):
    """Return an agent_node function closed over the bound LLM and rate limiter."""

    def agent_node(state: AgentState) -> dict:
        rate_limiter.wait_if_needed()
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(state["messages"])
        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}

    return agent_node


def should_continue(state: AgentState) -> Literal["tools", "dedup"]:
    """Route to tools if LLM emitted tool_calls, otherwise to dedup then END."""
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return "dedup"


def _recent_tool_messages(messages: list) -> list[ToolMessage]:
    """Return ToolMessages from the most recent tool-execution batch only."""
    batch: list[ToolMessage] = []
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            batch.append(msg)
            continue
        if batch:
            break
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            break
    return list(reversed(batch))


def _parse_tool_payload(content: str | list) -> dict | list | None:
    if isinstance(content, list):
        return None
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None


def collect_results_node(state: AgentState) -> dict:
    """Parse the latest tool batch into detected_events, portal_updates, and errors."""
    detected_events: list[dict] = []
    portal_updates: list[dict] = []
    errors: list[str] = []

    for msg in _recent_tool_messages(state["messages"]):
        payload = _parse_tool_payload(msg.content)
        if payload is None:
            continue

        tool_name = msg.name or ""

        if tool_name in CLASSIFY_TOOL_NAMES:
            if isinstance(payload, dict) and "results" in payload:
                for result in payload["results"]:
                    if result.get("is_target_event") and result.get("above_threshold"):
                        detected_events.append(result)
            elif isinstance(payload, dict) and payload.get("is_target_event") and payload.get("above_threshold"):
                detected_events.append(payload)
            elif isinstance(payload, dict) and payload.get("error"):
                errors.append(f"{tool_name}: {payload['error']}")

        elif tool_name in {"transfer_player", "transfer_player_for_season"}:
            if isinstance(payload, dict) and payload.get("success"):
                portal_updates.append(payload)
            elif isinstance(payload, dict):
                detail = payload.get("message") or payload.get("error") or "unknown failure"
                errors.append(f"transfer_player: {detail}")

        elif tool_name in {"coach_departure", "coach_departure_for_season"}:
            if isinstance(payload, dict) and payload.get("status") == "log_only_no_db":
                errors.append(f"coach_departure: {payload.get('error', 'db failed')}")
            elif isinstance(payload, dict) and payload.get("event") == "coach_departure":
                detected_events.append({**payload, "event_type": "coach_departed"})

        elif tool_name == "search_news":
            if isinstance(payload, dict) and payload.get("error"):
                errors.append(f"search_news: {payload['error']}")

    out: dict = {}
    if detected_events:
        out["detected_events"] = detected_events
    if portal_updates:
        out["portal_updates"] = list(state.get("portal_updates", [])) + portal_updates
    if errors:
        out["errors"] = errors
    return out


def dedup_node(state: AgentState) -> dict:
    """Post-process portal_updates to collapse cross-source duplicates.

    The DB writes in transfer_player are already idempotent (ON CONFLICT).
    This node provides bookkeeping: which entries are unique vs duplicates, and
    prevents redundant sync_portal_candidate_flags calls for the same player.
    """
    updates = state.get("portal_updates", [])
    if not updates:
        return {}

    deduped, duplicates = cross_source_dedup(updates)

    if duplicates:
        log.info(
            "dedup_node: %d unique portal updates, %d duplicates collapsed",
            len(deduped),
            len(duplicates),
        )

    return {"portal_updates": deduped}


# ---------------------------------------------------------------------------
# Graph factory
# ---------------------------------------------------------------------------

def build_graph(
    tools: list,
    *,
    llm=None,
    gemini_model: str = GEMINI_MODEL,
    calls_per_minute: int = 12,
    memory=None,
):
    """Build and compile the news-monitoring ReAct + dedup graph.

    Graph structure::

        START → agent → should_continue
                            ↓ tools              ↓ dedup
                         ToolNode → collect_results → agent (loop back)
                                               dedup → END

    Args:
        tools: List of LangChain tool functions to bind to the LLM.
        llm: Optional pre-built ChatGoogleGenerativeAI instance.  If None,
            one is built from the env's GOOGLE_API_KEY.
        gemini_model: Gemini model ID (default: GEMINI_MODEL from config).
        calls_per_minute: Rate-limiter ceiling (default 12 — below free 15 RPM).
        memory: Optional LangGraph checkpointer (e.g. MemorySaver).

    Returns:
        Compiled LangGraph ``CompiledGraph``.
    """
    rate_limiter = RateLimiter(calls_per_minute=calls_per_minute)

    _llm = llm or build_llm(model=gemini_model)
    llm_with_tools = _llm.bind_tools(tools)

    agent_node = build_agent_node(llm_with_tools, rate_limiter)

    builder = StateGraph(AgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("collect_results", collect_results_node)
    builder.add_node("dedup", dedup_node)

    builder.set_entry_point("agent")

    builder.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", "dedup": "dedup"},
    )
    builder.add_edge("tools", "collect_results")
    builder.add_edge("collect_results", "agent")
    builder.add_edge("dedup", END)

    return builder.compile(checkpointer=memory)
