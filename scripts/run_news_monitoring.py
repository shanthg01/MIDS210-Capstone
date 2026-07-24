"""
scripts/run_news_monitoring.py

Non-interactive CLI entrypoint for the news-monitoring agent. Thin argparse
wrapper — the actual run() orchestration lives in
portalpoint.agents.news_monitoring.runner so the API (agent.py router) can
import it too (scripts/ isn't a packaged module).

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
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import logging

from portalpoint.agents.news_monitoring.config import GEMINI_MODEL, TAVILY_WINDOW_DAYS
from portalpoint.agents.news_monitoring.runner import run

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
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
        tools = [search_tool, classify_single, classify_batch, lookup_tool, transfer_tool, coach_tool]

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
        "dry_run": dry_run,
        "season": _season,
        "window_days": window_days,
        "success": not final_state.get("errors"),
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
    if not summary.get("success", True):
        sys.exit(1)


if __name__ == "__main__":
    main()
