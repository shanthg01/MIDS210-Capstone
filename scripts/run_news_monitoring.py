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
import logging
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from portalpoint.agents.news_monitoring.config import GEMINI_MODEL, TAVILY_WINDOW_DAYS
from portalpoint.agents.news_monitoring.runner import run

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


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
