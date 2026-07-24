"""One-cycle orchestration for the news-monitoring agent.

Moved out of scripts/run_news_monitoring.py so the API (agent.py router) can
import run() directly — scripts/ isn't a packaged module (see
modeling/recommendations.py's CANDIDATE_SQL/MODEL_VERSION for the same fix
applied to the same class of problem). scripts/run_news_monitoring.py now
just wraps this with argparse for CLI/cron use.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime

from langchain_core.messages import HumanMessage

from portalpoint.agents.news_monitoring.config import GEMINI_MODEL, TAVILY_WINDOW_DAYS
from portalpoint.agents.news_monitoring.extract import (
    RateLimiter,
    build_llm,
    build_llm_classify_tools,
    classify_event,
    classify_events_batch,
)
from portalpoint.agents.news_monitoring.graph import build_graph
from portalpoint.agents.news_monitoring.resolve import build_action_tools
from portalpoint.agents.news_monitoring.sources.tavily import build_search_news_tool
from portalpoint.agents.news_monitoring.state import initial_state
from portalpoint.modeling.io import apply_env_file

log = logging.getLogger(__name__)


def _validate_runtime_keys(*, dry_run: bool) -> list[str]:
    """Return missing required env var names (empty list = ready to run)."""
    missing: list[str] = []
    if not os.environ.get("TAVILY_API_KEY"):
        missing.append("TAVILY_API_KEY")
    if not os.environ.get("GOOGLE_API_KEY"):
        missing.append("GOOGLE_API_KEY")
    if not dry_run:
        from portalpoint.modeling.io import load_env

        if not os.environ.get("DATABASE_URL") and not load_env().get("DATABASE_URL"):
            missing.append("DATABASE_URL")
    return missing


def run(
    *,
    season: int | None = None,
    window_days: int = TAVILY_WINDOW_DAYS,
    use_llm_classifier: bool = True,
    dry_run: bool = False,
    gemini_model: str = GEMINI_MODEL,
) -> dict:
    """Run one monitoring cycle and return a summary dict."""
    apply_env_file()

    _season = season or date.today().year
    run_start = datetime.utcnow()

    missing = _validate_runtime_keys(dry_run=dry_run)
    if missing:
        msg = f"Missing required environment variables: {', '.join(missing)}"
        log.error(msg)
        return {
            "run_window_start": run_start.isoformat(),
            "run_window_end": datetime.utcnow().isoformat(),
            "events_detected": 0,
            "portal_updates": 0,
            "errors": [msg],
            "review_needed": [],
            "dry_run": dry_run,
            "season": _season,
            "success": False,
        }

    rate_limiter = RateLimiter()
    llm = build_llm(model=gemini_model)

    if use_llm_classifier:
        classify_single, classify_batch = build_llm_classify_tools(rate_limiter, llm=llm)
    else:
        classify_single = classify_event
        classify_batch = classify_events_batch

    search_tool = build_search_news_tool(window_days)

    if dry_run:
        log.info("DRY RUN — DB writes disabled")
        tools = [search_tool, classify_single, classify_batch]
    else:
        lookup_tool, transfer_tool, coach_tool = build_action_tools(_season)
        tools = [
            search_tool,
            classify_single,
            classify_batch,
            lookup_tool,
            transfer_tool,
            coach_tool,
        ]

    graph = build_graph(tools, llm=llm)

    state = initial_state(
        run_window_start=run_start,
        run_window_end=None,
    )

    log.info(
        "Starting news monitoring run (season=%d, window=%dd, llm=%s)",
        _season,
        window_days,
        use_llm_classifier,
    )

    final_state = graph.invoke(
        {
            **state,
            "messages": [
                HumanMessage(
                    content=(
                        f"Run the transfer portal and coaching news monitoring cycle. "
                        f"Season context: {_season}. Search window: last {window_days} days."
                    )
                )
            ],
        }
    )

    run_end = datetime.utcnow()

    summary = {
        "run_window_start": run_start.isoformat(),
        "run_window_end": run_end.isoformat(),
        "events_detected": len(final_state.get("detected_events", [])),
        "portal_updates": len(final_state.get("portal_updates", [])),
        "errors": final_state.get("errors", []),
        # Real events found but not confidently matched to a player (needs a
        # human, not a rerun) - kept separate from `errors` so a clean run that
        # surfaces one of these still reports success=True.
        "review_needed": final_state.get("review_needed", []),
        "dry_run": dry_run,
        "season": _season,
        "window_days": window_days,
        "success": not final_state.get("errors"),
    }

    log.info(
        "Run complete: %d events detected, %d portal updates, %d errors, %d needing review",
        summary["events_detected"],
        summary["portal_updates"],
        len(summary["errors"]),
        len(summary["review_needed"]),
    )
    return summary
