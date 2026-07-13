"""LangGraph StateGraph for the news-monitoring agent."""
from __future__ import annotations

import json
import logging
from typing import Literal

from langchain_core.messages import SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from portalpoint.agents.news_monitoring.config import (
    AGENT_SEARCH_QUERIES,
    GEMINI_MODEL,
)
from portalpoint.agents.news_monitoring.extract import RateLimiter, build_llm
from portalpoint.agents.news_monitoring.prompts import SYSTEM_PROMPT
from portalpoint.agents.news_monitoring.resolve import cross_source_dedup
from portalpoint.agents.news_monitoring.state import AgentState

log = logging.getLogger(__name__)

# Re-export for backward compatibility
__all__ = ["SYSTEM_PROMPT", "build_graph", "should_continue", "dedup_node", "build_agent_node"]


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
            len(deduped), len(duplicates),
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
                            ↓ tools           ↓ dedup
                         ToolNode ──────▶ agent (loop back)
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
    builder.add_node("dedup", dedup_node)

    builder.set_entry_point("agent")

    builder.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", "dedup": "dedup"},
    )
    builder.add_edge("tools", "agent")
    builder.add_edge("dedup", END)

    return builder.compile(checkpointer=memory)
