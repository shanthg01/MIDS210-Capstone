"""
scripts/run_news_monitoring_eval.py

Run golden-set evals for the news-monitoring agent and log results to MLflow.

Usage:
    # Regex classifier only (fast, no API keys beyond MLflow)
    uv run python scripts/run_news_monitoring_eval.py

    # Include LLM classifier (requires GOOGLE_API_KEY)
    uv run python scripts/run_news_monitoring_eval.py --llm

    # Include Tavily historical recall (requires TAVILY_API_KEY)
    uv run python scripts/run_news_monitoring_eval.py --tavily

    # Full eval suite
    uv run python scripts/run_news_monitoring_eval.py --llm --tavily
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from portalpoint.agents.news_monitoring.eval import (
    MLFLOW_EXPERIMENT,
    eval_llm_classifier,
    eval_regex_classifier,
    eval_tavily_historical_recall,
    load_golden_cases,
    log_eval_to_mlflow,
)
from portalpoint.modeling.io import apply_env_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="News monitoring golden-set eval + MLflow log")
    parser.add_argument("--llm", action="store_true", help="Run LLM classifier eval (GOOGLE_API_KEY)")
    parser.add_argument("--tavily", action="store_true", help="Run Tavily historical recall (TAVILY_API_KEY)")
    parser.add_argument("--run-name", default=None, help="MLflow run name override")
    args = parser.parse_args()

    apply_env_file()
    cases = load_golden_cases()
    log.info("Loaded %d golden cases", len(cases))

    eval_results: dict = {}
    metrics: dict[str, float] = {}

    # Layer 1: regex classifier (always)
    regex = eval_regex_classifier(cases)
    eval_results["regex"] = regex
    metrics["regex_accuracy"] = regex["accuracy"]
    metrics["regex_portal_accuracy"] = regex["portal_accuracy"]
    metrics["regex_coach_accuracy"] = regex["coach_accuracy"]
    log.info(
        "Regex classifier: %d/%d (%.1f%%)",
        regex["correct"],
        regex["total"],
        regex["accuracy"] * 100,
    )
    if regex["failures"]:
        log.warning("Regex failures: %s", [f["id"] for f in regex["failures"]])

    # Layer 2: LLM classifier (optional)
    if args.llm:
        if not os.environ.get("GOOGLE_API_KEY"):
            log.error("GOOGLE_API_KEY not set — skipping LLM eval")
            sys.exit(1)
        log.info("Running LLM classifier eval (%d API calls)...", len(cases))
        llm = eval_llm_classifier(cases)
        eval_results["llm"] = llm
        metrics["llm_accuracy"] = llm["accuracy"]
        metrics["llm_entity_extraction_accuracy"] = llm["entity_extraction_accuracy"]
        log.info(
            "LLM classifier: %d/%d (%.1f%%)",
            llm["correct"],
            llm["total"],
            llm["accuracy"] * 100,
        )
        if llm["failures"]:
            log.warning("LLM failures: %s", [f["id"] for f in llm["failures"]])

    # Layer 3: Tavily historical recall (optional)
    if args.tavily:
        if not os.environ.get("TAVILY_API_KEY"):
            log.error("TAVILY_API_KEY not set — skipping Tavily eval")
            sys.exit(1)
        from tavily import TavilyClient

        log.info("Running Tavily historical recall eval (%d API calls)...", len(cases))
        client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
        tavily = eval_tavily_historical_recall(client, cases)
        eval_results["tavily"] = tavily
        metrics["tavily_master_query_recall"] = tavily["master_query_recall"]
        metrics["tavily_portal_recall"] = tavily["master_query_portal_recall"]
        metrics["tavily_coach_recall"] = tavily["master_query_coach_recall"]
        metrics["tavily_raw_index_recall"] = tavily["raw_index_recall"]
        metrics["tavily_entity_augmented_recall"] = tavily["entity_augmented_recall"]
        log.info(
            "Tavily master-query recall: %.1f%% (portal %.1f%%, coach %.1f%%)",
            tavily["master_query_recall"] * 100,
            tavily["master_query_portal_recall"] * 100,
            tavily["master_query_coach_recall"] * 100,
        )
        if tavily["missed_case_ids"]:
            log.info("Tavily missed: %s", tavily["missed_case_ids"])

    run_id = log_eval_to_mlflow(
        run_name=args.run_name,
        metrics=metrics,
        eval_results=eval_results,
    )

    summary = {
        "mlflow_experiment": MLFLOW_EXPERIMENT,
        "mlflow_run_id": run_id,
        "metrics": metrics,
        "eval_layers": list(eval_results.keys()),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
