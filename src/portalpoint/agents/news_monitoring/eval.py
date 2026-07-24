"""Golden-set eval metrics and MLflow logging for the news-monitoring agent."""
from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import mlflow

from portalpoint.agents.news_monitoring.config import (
    AGENT_SEARCH_QUERIES,
    CONFIDENCE_THRESHOLD,
    GEMINI_MODEL,
    TARGET_EVENT_TYPES,
    TAVILY_CHUNKS_PER_SOURCE,
    TAVILY_INCLUDE_DOMAINS,
    TAVILY_MAX_RESULTS,
    TAVILY_MIN_SCORE,
    TAVILY_SEARCH_DEPTH,
    TAVILY_WINDOW_DAYS,
)
from portalpoint.agents.news_monitoring.extract import (
    RateLimiter,
    _classify_event_payload,
    build_llm_classify_tools,
)
from portalpoint.agents.news_monitoring.prompts import SYSTEM_PROMPT
from portalpoint.modeling.io import find_repo_root
from portalpoint.modeling.mlflow_helpers import setup_mlflow

MLFLOW_EXPERIMENT = "news-monitoring-eval"
GOLDEN_FIXTURE_REL = Path("tests/fixtures/news_classification/golden_eval_set.json")


def golden_fixture_path() -> Path:
    return find_repo_root() / GOLDEN_FIXTURE_REL


def load_golden_fixture() -> dict[str, Any]:
    with open(golden_fixture_path(), encoding="utf-8") as f:
        return json.load(f)


def load_golden_cases() -> list[dict[str, Any]]:
    return load_golden_fixture()["cases"]


def git_commit_hash() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=find_repo_root(),
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def snapshot_config_params() -> dict[str, str | float | int]:
    """Frozen config snapshot for MLflow params (string values only)."""
    fixture = load_golden_fixture()
    return {
        "gemini_model": GEMINI_MODEL,
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "tavily_search_depth": TAVILY_SEARCH_DEPTH,
        "tavily_min_score": TAVILY_MIN_SCORE,
        "tavily_window_days": TAVILY_WINDOW_DAYS,
        "tavily_max_results": TAVILY_MAX_RESULTS,
        "tavily_chunks_per_source": TAVILY_CHUNKS_PER_SOURCE,
        "tavily_include_domains": ",".join(TAVILY_INCLUDE_DOMAINS),
        "portal_search_query": AGENT_SEARCH_QUERIES["player_enters_portal"],
        "coach_search_query": AGENT_SEARCH_QUERIES["coach_leaves"],
        "golden_set_version": str(fixture.get("version", fixture.get("created", "unknown"))),
        "golden_set_created": str(fixture.get("created", "unknown")),
        "golden_case_count": len(fixture.get("cases", [])),
        "git_commit": git_commit_hash(),
    }


def eval_regex_classifier(cases: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Evaluate deterministic regex classifier against golden cases."""
    cases = cases or load_golden_cases()
    failures: list[dict[str, str]] = []
    by_type: dict[str, list[bool]] = {"player_enters_portal": [], "coach_leaves": []}
    negative_cases = [c for c in cases if c["expected"]["event_type"] == "unknown"]
    positive_cases = [c for c in cases if c["expected"]["event_type"] != "unknown"]
    negative_false_positives: list[dict[str, str]] = []

    for case in cases:
        result = _classify_event_payload(
            text=case["content"],
            title=case["title"],
            source_url=case.get("url", ""),
        )
        expected = case["expected"]["event_type"]
        ok = result["event_type"] == expected
        if expected != "unknown":
            by_type.setdefault(expected, []).append(ok)
        if not ok:
            failures.append({
                "id": case["id"],
                "expected": expected,
                "got": result["event_type"],
                "difficulty": case.get("difficulty", ""),
            })
        if expected == "unknown" and result["event_type"] in TARGET_EVENT_TYPES:
            negative_false_positives.append({
                "id": case["id"],
                "got": result["event_type"],
            })

    correct = len(cases) - len(failures)
    neg_correct = len(negative_cases) - len(negative_false_positives)
    return {
        "accuracy": correct / len(cases) if cases else 0.0,
        "correct": correct,
        "total": len(cases),
        "failures": failures,
        "positive_accuracy": (
            sum(1 for c in positive_cases if _classify_event_payload(
                text=c["content"], title=c["title"], source_url=c.get("url", ""),
            )["event_type"] == c["expected"]["event_type"]) / len(positive_cases)
            if positive_cases else 0.0
        ),
        "negative_accuracy": neg_correct / len(negative_cases) if negative_cases else 0.0,
        "false_positive_rate": (
            len(negative_false_positives) / len(negative_cases) if negative_cases else 0.0
        ),
        "negative_false_positives": negative_false_positives,
        "portal_accuracy": (
            sum(by_type.get("player_enters_portal", [])) / len(by_type["player_enters_portal"])
            if by_type.get("player_enters_portal")
            else 0.0
        ),
        "coach_accuracy": (
            sum(by_type.get("coach_leaves", [])) / len(by_type["coach_leaves"])
            if by_type.get("coach_leaves")
            else 0.0
        ),
    }


def _invoke_tool(tool_fn, **kwargs) -> str:
    if hasattr(tool_fn, "invoke"):
        return tool_fn.invoke(kwargs)
    return tool_fn(**kwargs)


def eval_llm_classifier(cases: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Evaluate LLM classifier against golden cases (live Gemini API calls)."""
    cases = cases or load_golden_cases()
    rate_limiter = RateLimiter()
    classify_single, _ = build_llm_classify_tools(rate_limiter)

    failures: list[dict[str, str]] = []
    extraction_errors: list[dict[str, str]] = []
    negative_cases = [c for c in cases if c["expected"]["event_type"] == "unknown"]
    negative_false_positives: list[dict[str, str]] = []

    for case in cases:
        article_json = json.dumps({
            "title": case["title"],
            "url": case.get("url", ""),
            "content": case["content"],
        })
        raw = _invoke_tool(classify_single, article_json=article_json)
        result = json.loads(raw)

        expected = case["expected"]
        if result.get("event_type") != expected["event_type"]:
            failures.append({
                "id": case["id"],
                "expected": expected["event_type"],
                "got": str(result.get("event_type")),
            })

        if expected["event_type"] == "unknown" and result.get("event_type") in TARGET_EVENT_TYPES:
            negative_false_positives.append({
                "id": case["id"],
                "got": str(result.get("event_type")),
            })

        if case["event_type"] == "player_enters_portal":
            exp_name = (expected.get("player_name") or "").lower()
            got_name = (result.get("player_name") or "").lower()
            if exp_name and got_name and exp_name not in got_name and got_name not in exp_name:
                extraction_errors.append({
                    "id": case["id"],
                    "field": "player_name",
                    "expected": expected.get("player_name"),
                    "got": result.get("player_name"),
                })
        elif case["event_type"] == "coach_leaves":
            exp_name = (expected.get("coach_name") or "").lower()
            got_name = (result.get("coach_name") or "").lower()
            if exp_name and got_name and exp_name not in got_name and got_name not in exp_name:
                extraction_errors.append({
                    "id": case["id"],
                    "field": "coach_name",
                    "expected": expected.get("coach_name"),
                    "got": result.get("coach_name"),
                })

    correct = len(cases) - len(failures)
    neg_correct = len(negative_cases) - len(negative_false_positives)
    return {
        "accuracy": correct / len(cases) if cases else 0.0,
        "correct": correct,
        "total": len(cases),
        "failures": failures,
        "extraction_errors": extraction_errors,
        "entity_extraction_accuracy": (
            1.0 - len(extraction_errors) / len(cases) if cases else 0.0
        ),
        "negative_accuracy": neg_correct / len(negative_cases) if negative_cases else 0.0,
        "false_positive_rate": (
            len(negative_false_positives) / len(negative_cases) if negative_cases else 0.0
        ),
        "negative_false_positives": negative_false_positives,
    }


def eval_tavily_historical_recall(
    tavily_client,
    cases: list[dict[str, Any]] | None = None,
    *,
    max_results: int = TAVILY_MAX_RESULTS,
    chunks_per_source: int = TAVILY_CHUNKS_PER_SOURCE,
) -> dict[str, Any]:
    """Historical Tavily recall using agent master queries (requires client)."""
    # Import from tests helper — shared with pytest eval suite
    import sys

    tests_dir = find_repo_root() / "tests"
    if str(tests_dir) not in sys.path:
        sys.path.insert(0, str(tests_dir))

    from tavily_recall_helpers import (  # noqa: PLC0415
        compute_recall,
        run_recall_comparison,
        search_entity_augmented_for_case,
    )

    cases = cases or load_golden_cases()
    master = run_recall_comparison(
        tavily_client,
        cases,
        label="master_query",
        max_results=max_results,
        chunks_per_source=chunks_per_source,
    )
    entity_outcomes = [
        search_entity_augmented_for_case(
            tavily_client,
            case,
            max_results=max_results,
            chunks_per_source=chunks_per_source,
        )
        for case in cases
    ]
    entity_recall, entity_misses, _ = compute_recall(cases, entity_outcomes)
    raw_recall, _, _ = compute_recall(cases, master["outcomes"], use_raw=True)

    portal_cases = [c for c in cases if c["event_type"] == "player_enters_portal"]
    coach_cases = [c for c in cases if c["event_type"] == "coach_leaves"]

    return {
        "master_query_recall": master["recall"],
        "master_query_portal_recall": master["portal_recall"],
        "master_query_coach_recall": master["coach_recall"],
        "raw_index_recall": raw_recall,
        "entity_augmented_recall": entity_recall,
        "missed_case_ids": master["misses"],
        "entity_augmented_missed_ids": entity_misses,
        "max_results": max_results,
        "chunks_per_source": chunks_per_source,
    }


def log_eval_to_mlflow(
    *,
    run_name: str | None = None,
    metrics: dict[str, float],
    extra_params: dict[str, str | float | int] | None = None,
    eval_results: dict[str, Any] | None = None,
) -> str:
    """Log one eval run to MLflow; return run_id."""
    setup_mlflow(MLFLOW_EXPERIMENT)
    params = snapshot_config_params()
    if extra_params:
        params.update({k: str(v) for k, v in extra_params.items()})

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = run_name or f"news-eval-{ts}"

    with mlflow.start_run(run_name=name) as run:
        mlflow.log_params(params)
        mlflow.log_metrics(metrics)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "system_prompt.txt").write_text(SYSTEM_PROMPT, encoding="utf-8")

            fixture_src = golden_fixture_path()
            (tmp_path / "golden_eval_set.json").write_text(
                fixture_src.read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            if eval_results:
                (tmp_path / "eval_results.json").write_text(
                    json.dumps(eval_results, indent=2),
                    encoding="utf-8",
                )
                missed = eval_results.get("tavily", {}).get("missed_case_ids")
                if missed:
                    (tmp_path / "missed_cases.json").write_text(
                        json.dumps({"missed_case_ids": missed}, indent=2),
                        encoding="utf-8",
                    )

            mlflow.log_artifacts(str(tmp_path))

        return run.info.run_id
