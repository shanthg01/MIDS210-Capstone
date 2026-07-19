"""One-off comparison: baseline vs expanded Tavily search params on golden set recall.

Baseline (production):  max_results=10, chunks_per_source=3
Expanded (API ceiling): max_results=20, chunks_per_source=5

Usage:
    uv run python scripts/compare_tavily_recall_configs.py
    uv run python scripts/compare_tavily_recall_configs.py --no-mlflow
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from portalpoint.agents.news_monitoring.config import (
    TAVILY_CHUNKS_PER_SOURCE,
    TAVILY_CHUNKS_PER_SOURCE_EXPANDED,
    TAVILY_MAX_RESULTS,
    TAVILY_MAX_RESULTS_EXPANDED,
)
from portalpoint.agents.news_monitoring.eval import (
    git_commit_hash,
    load_golden_cases,
    log_eval_to_mlflow,
)
from portalpoint.modeling.io import apply_env_file
from tavily_recall_helpers import compute_recall, entity_hit, run_recall_comparison


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-mlflow", action="store_true", help="Skip MLflow logging")
    args = parser.parse_args()

    apply_env_file()
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        print("ERROR: TAVILY_API_KEY not set")
        sys.exit(1)

    from tavily import TavilyClient

    client = TavilyClient(api_key=api_key)
    cases = load_golden_cases()

    configs = [
        ("baseline", TAVILY_MAX_RESULTS, TAVILY_CHUNKS_PER_SOURCE),
        ("expanded", TAVILY_MAX_RESULTS_EXPANDED, TAVILY_CHUNKS_PER_SOURCE_EXPANDED),
    ]

    results = []
    for label, max_results, chunks in configs:
        print(f"Running {label}: max_results={max_results}, chunks_per_source={chunks} ...")
        results.append(
            run_recall_comparison(
                client,
                cases,
                label=label,
                max_results=max_results,
                chunks_per_source=chunks,
            )
        )

    baseline_hits = _baseline_hits(cases, results[0]["outcomes"])
    expanded_hits = _baseline_hits(cases, results[1]["outcomes"])
    newly_found = sorted(expanded_hits - baseline_hits)
    lost = sorted(baseline_hits - expanded_hits)
    results[1]["newly_found"] = newly_found
    delta = results[1]["recall"] - results[0]["recall"]

    print("\n" + "=" * 70)
    print("TAVILY RECALL CONFIG COMPARISON (agent master queries, historical windows)")
    print("=" * 70)
    for r in results:
        hits = len(cases) - len(r["misses"])
        print(f"\n{r['label'].upper()}  max_results={r['max_results']}  chunks_per_source={r['chunks_per_source']}")
        print(f"  Overall recall:     {hits}/{len(cases)} ({r['recall']:.1%})")
        print(f"  Raw index recall:   {r['raw_recall']:.1%}")
        print(f"  Portal recall:      {r['portal_recall']:.1%}")
        print(f"  Coach recall:       {r['coach_recall']:.1%}")
        print(f"  Missed ({len(r['misses'])}): {r['misses']}")

    print("\n" + "-" * 70)
    print(f"DELTA (expanded - baseline): {delta:+.1%}")
    if newly_found:
        print(f"  Newly found with expanded config: {newly_found}")
    else:
        print("  Newly found with expanded config: none")
    if lost:
        print(f"  Lost with expanded config: {lost}")
    print("=" * 70 + "\n")

    if not args.no_mlflow:
        run_id = log_eval_to_mlflow(
            run_name=f"tavily-config-compare-{git_commit_hash()[:8]}",
            metrics={
                "tavily_baseline_recall": results[0]["recall"],
                "tavily_expanded_recall": results[1]["recall"],
                "tavily_recall_delta": delta,
                "tavily_baseline_portal_recall": results[0]["portal_recall"],
                "tavily_expanded_portal_recall": results[1]["portal_recall"],
                "tavily_baseline_coach_recall": results[0]["coach_recall"],
                "tavily_expanded_coach_recall": results[1]["coach_recall"],
            },
            extra_params={
                "eval_type": "tavily_config_comparison",
                "baseline_max_results": TAVILY_MAX_RESULTS,
                "baseline_chunks_per_source": TAVILY_CHUNKS_PER_SOURCE,
                "expanded_max_results": TAVILY_MAX_RESULTS_EXPANDED,
                "expanded_chunks_per_source": TAVILY_CHUNKS_PER_SOURCE_EXPANDED,
            },
            eval_results={
                "comparison": {
                    "baseline": {k: v for k, v in results[0].items() if k != "outcomes"},
                    "expanded": {k: v for k, v in results[1].items() if k != "outcomes"},
                    "newly_found": newly_found,
                    "lost": lost,
                },
            },
        )
        print(f"MLflow run logged: {run_id}")


def _baseline_hits(cases: list[dict], baseline_outcomes) -> set[str]:
    outcome_by_id = {o.case_id: o for o in baseline_outcomes}
    hits = set()
    for case in cases:
        outcome = outcome_by_id[case["id"]]
        if outcome.production_results and entity_hit(case, outcome.production_results):
            hits.add(case["id"])
    return hits


if __name__ == "__main__":
    main()
