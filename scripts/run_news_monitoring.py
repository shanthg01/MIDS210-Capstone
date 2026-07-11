"""
scripts/run_news_monitoring.py

Non-interactive CLI entrypoint for the news-monitoring agent.  Matches the
run_*.py pattern used by every other model in this repo.

Usage:
    uv run python scripts/run_news_monitoring.py
    uv run python scripts/run_news_monitoring.py --season 2026 --window-days 3
    uv run python scripts/run_news_monitoring.py --dry-run         # classify only, no DB writes

Airflow DAG can call this directly:
    hourly_portal_monitoring_dag → BashOperator("uv run python scripts/run_news_monitoring.py")
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from langchain_core.messages import HumanMessage

from portalpoint.agents.news_monitoring.config import (
    CONFIDENCE_THRESHOLD,
    GEMINI_MODEL,
    TAVILY_WINDOW_DAYS,
)
from portalpoint.agents.news_monitoring.extract import (
    RateLimiter,
    build_llm,
    build_llm_classify_tools,
    classify_event,
    classify_events_batch,
)
from portalpoint.agents.news_monitoring.graph import build_graph
from portalpoint.agents.news_monitoring.resolve import coach_departure, transfer_player
from portalpoint.agents.news_monitoring.sources.tavily import search_news
from portalpoint.agents.news_monitoring.state import initial_state
from portalpoint.modeling.io import load_env

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def run(
    *,
    season: int | None = None,
    window_days: int = TAVILY_WINDOW_DAYS,
    use_llm_classifier: bool = True,
    dry_run: bool = False,
    gemini_model: str = GEMINI_MODEL,
) -> dict:
    """Run one monitoring cycle and return a summary dict.

    Args:
        season: Transfer season to record events under.  Defaults to current year.
        window_days: Tavily search lookback window in days.
        use_llm_classifier: Use Gemini for classification (True) or regex (False).
        dry_run: If True, skip all DB writes (search + classify only).
        gemini_model: Gemini model ID to use for LLM classification + agent.

    Returns:
        Summary dict with keys: ``events_detected``, ``portal_updates``,
        ``errors``, ``run_window_start``, ``run_window_end``.
    """
    load_env()

    _season = season or date.today().year
    run_start = datetime.utcnow()

    rate_limiter = RateLimiter()
    llm = build_llm(model=gemini_model)

    if use_llm_classifier:
        classify_single, classify_batch = build_llm_classify_tools(rate_limiter, llm=llm)
    else:
        classify_single = classify_event
        classify_batch = classify_events_batch

    if dry_run:
        log.info("DRY RUN — DB writes disabled")

        @classify_batch.as_tool if hasattr(classify_batch, "as_tool") else lambda f: f
        def _noop_transfer_player(*a, **kw):
            return json.dumps({"success": False, "dry_run": True})

        tools = [search_news, classify_single, classify_batch]
    else:
        tools = [search_news, classify_single, classify_batch, transfer_player, coach_departure]

    graph = build_graph(tools, llm=llm)

    state = initial_state(
        run_window_start=run_start,
        run_window_end=None,
    )

    log.info("Starting news monitoring run (season=%d, window=%dd, llm=%s)", _season, window_days, use_llm_classifier)

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
        "dry_run": dry_run,
        "season": _season,
    }

    log.info(
        "Run complete: %d events detected, %d portal updates, %d errors",
        summary["events_detected"],
        summary["portal_updates"],
        len(summary["errors"]),
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="PortalPoint news-monitoring agent")
    parser.add_argument("--season", type=int, default=None, help="Transfer season (default: current year)")
    parser.add_argument("--window-days", type=int, default=TAVILY_WINDOW_DAYS, help="Tavily search lookback days")
    parser.add_argument("--no-llm", action="store_true", help="Use regex classifier instead of LLM")
    parser.add_argument("--dry-run", action="store_true", help="Search + classify only, no DB writes")
    parser.add_argument("--model", default=GEMINI_MODEL, help="Gemini model ID")
    args = parser.parse_args()

    summary = run(
        season=args.season,
        window_days=args.window_days,
        use_llm_classifier=not args.no_llm,
        dry_run=args.dry_run,
        gemini_model=args.model,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
