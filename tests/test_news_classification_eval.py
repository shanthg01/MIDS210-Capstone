"""Fixture-based evals for news classification accuracy.

Loads labeled examples from the golden evaluation set and measures classifier
performance against ground truth. The regex classifier tests run in CI; LLM
and Tavily recall tests are gated behind environment variables.

Run all evals:
    uv run pytest tests/test_news_classification_eval.py -v

Run with LLM evals (requires GOOGLE_API_KEY):
    RUN_LLM_EVALS=1 uv run pytest tests/test_news_classification_eval.py -v

Run with Tavily recall eval (requires TAVILY_API_KEY, makes live API calls):
    RUN_TAVILY_EVALS=1 uv run pytest tests/test_news_classification_eval.py -v

Run all gated evals together:
    RUN_LLM_EVALS=1 RUN_TAVILY_EVALS=1 uv run pytest tests/test_news_classification_eval.py -v
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from portalpoint.agents.news_monitoring.config import (
    CONFIDENCE_THRESHOLD,
    TARGET_EVENT_TYPES,
)
from portalpoint.agents.news_monitoring.extract import (
    _classify_event_payload,
    classify_event,
    classify_events_batch,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "news_classification"


def load_golden_set() -> list[dict[str, Any]]:
    """Load all cases from the golden evaluation set."""
    with open(FIXTURES_DIR / "golden_eval_set.json", encoding="utf-8") as f:
        return json.load(f)["cases"]


def load_cases_by_event_type(event_type: str) -> list[dict[str, Any]]:
    """Load cases filtered by event_type."""
    return [c for c in load_golden_set() if c["event_type"] == event_type]


def _invoke(tool_fn, **kwargs) -> str:
    """Call a langchain tool or plain function."""
    if hasattr(tool_fn, "invoke"):
        return tool_fn.invoke(kwargs)
    return tool_fn(**kwargs)


# ---------------------------------------------------------------------------
# Regex classifier eval (deterministic, always runs in CI)
# ---------------------------------------------------------------------------


class TestRegexClassifierPortalEntries:
    """Eval regex classifier on player_enters_portal cases."""

    @pytest.fixture(scope="class")
    def portal_cases(self) -> list[dict]:
        return load_cases_by_event_type("player_enters_portal")

    def test_all_portal_entries_classified_as_target(self, portal_cases):
        """All known portal entries should be classified as target events."""
        failures = []
        for case in portal_cases:
            result = _classify_event_payload(
                text=case["content"],
                title=case["title"],
                source_url=case["url"],
            )

            expected = case["expected"]
            if result["event_type"] != expected["event_type"]:
                failures.append(
                    f"{case['id']}: got '{result['event_type']}', "
                    f"expected '{expected['event_type']}' (difficulty={case['difficulty']})"
                )

        if failures:
            pytest.fail(
                f"Classification failures ({len(failures)}/{len(portal_cases)}):\n"
                + "\n".join(failures)
            )

    def test_easy_cases_high_confidence(self, portal_cases):
        """Easy portal cases should have confidence >= 0.85."""
        easy_cases = [c for c in portal_cases if c["difficulty"] == "easy"]
        failures = []

        for case in easy_cases:
            result = _classify_event_payload(
                text=case["content"],
                title=case["title"],
            )
            if result["confidence"] < 0.85:
                failures.append(
                    f"{case['id']}: confidence {result['confidence']:.2f} < 0.85"
                )

        if failures:
            pytest.fail(f"Low confidence on easy cases:\n" + "\n".join(failures))

    def test_all_above_action_threshold(self, portal_cases):
        """All portal entries should exceed the action threshold (0.6)."""
        below_threshold = []

        for case in portal_cases:
            result = _classify_event_payload(
                text=case["content"],
                title=case["title"],
            )
            if result["confidence"] < CONFIDENCE_THRESHOLD:
                below_threshold.append(
                    f"{case['id']}: {result['confidence']:.2f} < {CONFIDENCE_THRESHOLD} "
                    f"(difficulty={case['difficulty']})"
                )

        # Hard cases are allowed to fail threshold — they're edge cases
        hard_failures = [f for f in below_threshold if "hard" in f]
        non_hard_failures = [f for f in below_threshold if "hard" not in f]

        if non_hard_failures:
            pytest.fail(
                f"Below threshold (non-hard cases):\n" + "\n".join(non_hard_failures)
            )

        if hard_failures:
            pytest.skip(
                f"Hard cases below threshold (expected): {len(hard_failures)}"
            )


class TestRegexClassifierCoachLeaves:
    """Eval regex classifier on coach_leaves cases."""

    @pytest.fixture(scope="class")
    def coach_cases(self) -> list[dict]:
        return load_cases_by_event_type("coach_leaves")

    def test_all_coach_departures_classified_as_target(self, coach_cases):
        """All known coach departures should be classified as target events."""
        failures = []
        for case in coach_cases:
            result = _classify_event_payload(
                text=case["content"],
                title=case["title"],
                source_url=case["url"],
            )

            expected = case["expected"]
            if result["event_type"] != expected["event_type"]:
                failures.append(
                    f"{case['id']}: got '{result['event_type']}', "
                    f"expected '{expected['event_type']}' (difficulty={case['difficulty']})"
                )

        if failures:
            pytest.fail(
                f"Classification failures ({len(failures)}/{len(coach_cases)}):\n"
                + "\n".join(failures)
            )

    def test_easy_cases_high_confidence(self, coach_cases):
        """Easy coach cases should have confidence >= 0.85."""
        easy_cases = [c for c in coach_cases if c["difficulty"] == "easy"]
        failures = []

        for case in easy_cases:
            result = _classify_event_payload(
                text=case["content"],
                title=case["title"],
            )
            if result["confidence"] < 0.85:
                failures.append(
                    f"{case['id']}: confidence {result['confidence']:.2f} < 0.85"
                )

        if failures:
            pytest.fail(f"Low confidence on easy cases:\n" + "\n".join(failures))


class TestRegexClassifierBatchTool:
    """Eval the batch classification tool against the golden set."""

    def test_batch_tool_processes_all_cases(self):
        """classify_events_batch should process all golden cases."""
        cases = load_golden_set()
        articles = [
            {"title": c["title"], "url": c["url"], "content": c["content"]}
            for c in cases
        ]

        raw = _invoke(classify_events_batch, articles_json=json.dumps(articles))
        result = json.loads(raw)

        assert result["total"] == len(cases)
        assert result["target_events"] > 0

    def test_batch_matches_single_classification(self):
        """Batch and single classification should produce identical results."""
        cases = load_golden_set()[:5]  # Test subset for speed

        for case in cases:
            # Single classification
            single_result = _classify_event_payload(
                text=case["content"],
                title=case["title"],
                source_url=case["url"],
            )

            # Batch classification (single item)
            batch_input = json.dumps([{
                "title": case["title"],
                "url": case["url"],
                "content": case["content"],
            }])
            batch_raw = _invoke(classify_events_batch, articles_json=batch_input)
            batch_result = json.loads(batch_raw)["results"][0]

            assert single_result["event_type"] == batch_result["event_type"], (
                f"{case['id']}: single={single_result['event_type']}, "
                f"batch={batch_result['event_type']}"
            )


class TestRegexClassifierMetrics:
    """Aggregate metrics across the golden set."""

    def test_overall_accuracy_report(self):
        """Report overall classification accuracy (informational, does not fail)."""
        cases = load_golden_set()

        correct = 0
        by_difficulty = {"easy": [0, 0], "medium": [0, 0], "hard": [0, 0]}
        by_event_type = {"player_enters_portal": [0, 0], "coach_leaves": [0, 0], "unknown": [0, 0]}

        for case in cases:
            result = _classify_event_payload(
                text=case["content"],
                title=case["title"],
                source_url=case.get("url", ""),
            )
            expected_type = case["expected"]["event_type"]
            is_correct = result["event_type"] == expected_type

            if is_correct:
                correct += 1

            diff = case["difficulty"]
            by_difficulty[diff][0] += 1 if is_correct else 0
            by_difficulty[diff][1] += 1

            by_event_type.setdefault(expected_type, [0, 0])
            by_event_type[expected_type][0] += 1 if is_correct else 0
            by_event_type[expected_type][1] += 1

        accuracy = correct / len(cases)

        print(f"\n{'='*60}")
        print(f"REGEX CLASSIFIER EVAL REPORT")
        print(f"{'='*60}")
        print(f"Overall accuracy: {correct}/{len(cases)} ({accuracy:.1%})")
        print(f"\nBy difficulty:")
        for diff, (c, t) in by_difficulty.items():
            print(f"  {diff:8s}: {c}/{t} ({c/t:.1%})" if t > 0 else f"  {diff}: N/A")
        print(f"\nBy event type:")
        for etype, (c, t) in by_event_type.items():
            print(f"  {etype:25s}: {c}/{t} ({c/t:.1%})" if t > 0 else f"  {etype}: N/A")
        print(f"{'='*60}\n")

        # This test is informational — always passes
        assert True


class TestNegativeControls:
    """Negative golden-set cases must not be classified as target events."""

    @pytest.fixture(scope="class")
    def negative_cases(self) -> list[dict]:
        return [c for c in load_golden_set() if c["expected"]["event_type"] == "unknown"]

    def test_negative_cases_not_target_events(self, negative_cases):
        failures = []
        for case in negative_cases:
            result = _classify_event_payload(
                text=case["content"],
                title=case["title"],
                source_url=case.get("url", ""),
            )
            if result["event_type"] in TARGET_EVENT_TYPES:
                failures.append(
                    f"{case['id']}: got '{result['event_type']}', expected 'unknown'"
                )
            if result.get("is_target_event"):
                failures.append(f"{case['id']}: is_target_event should be False")

        if failures:
            pytest.fail("Negative control failures:\n" + "\n".join(failures))

    def test_football_portal_filtered_before_classification(self, negative_cases):
        football_cases = [
            c for c in negative_cases if "football" in c["id"] or "mensah" in c["id"]
        ]
        assert football_cases, "expected football negative fixtures"
        for case in football_cases:
            result = _classify_event_payload(
                text=case["content"],
                title=case["title"],
                source_url=case.get("url", ""),
            )
            assert result["event_type"] == "unknown"
            assert result.get("filtered_reason")


# ---------------------------------------------------------------------------
# LLM classifier eval (gated, requires GOOGLE_API_KEY)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("RUN_LLM_EVALS"),
    reason="LLM evals disabled — set RUN_LLM_EVALS=1 to enable",
)
class TestLLMClassifierEval:
    """Eval LLM classifier against labeled fixtures.

    Requires GOOGLE_API_KEY and RUN_LLM_EVALS=1 environment variables.
    Makes real API calls — rate limited to 12 RPM.
    """

    @pytest.fixture(scope="class")
    def llm_tools(self):
        from portalpoint.agents.news_monitoring.extract import (
            RateLimiter,
            build_llm_classify_tools,
        )

        rate_limiter = RateLimiter(calls_per_minute=12)
        classify_single, classify_batch = build_llm_classify_tools(rate_limiter)
        return classify_single, classify_batch

    def test_llm_portal_entries(self, llm_tools):
        """LLM should correctly classify portal entries."""
        classify_single, _ = llm_tools
        cases = load_cases_by_event_type("player_enters_portal")

        failures = []
        for case in cases:
            article_json = json.dumps({
                "title": case["title"],
                "url": case["url"],
                "content": case["content"],
            })
            raw = _invoke(classify_single, article_json=article_json)
            result = json.loads(raw)

            if result.get("event_type") != case["expected"]["event_type"]:
                failures.append(
                    f"{case['id']}: got '{result.get('event_type')}', "
                    f"expected '{case['expected']['event_type']}'"
                )

        if failures:
            pytest.fail(f"LLM portal failures:\n" + "\n".join(failures))

    def test_llm_coach_departures(self, llm_tools):
        """LLM should correctly classify coach departures."""
        classify_single, _ = llm_tools
        cases = load_cases_by_event_type("coach_leaves")

        failures = []
        for case in cases:
            article_json = json.dumps({
                "title": case["title"],
                "url": case["url"],
                "content": case["content"],
            })
            raw = _invoke(classify_single, article_json=article_json)
            result = json.loads(raw)

            if result.get("event_type") != case["expected"]["event_type"]:
                failures.append(
                    f"{case['id']}: got '{result.get('event_type')}', "
                    f"expected '{case['expected']['event_type']}'"
                )

        if failures:
            pytest.fail(f"LLM coach failures:\n" + "\n".join(failures))

    def test_llm_extracts_entity_names(self, llm_tools):
        """LLM should extract player/coach names correctly."""
        classify_single, _ = llm_tools
        cases = load_golden_set()[:5]  # Subset to limit API calls

        extraction_errors = []
        for case in cases:
            article_json = json.dumps({
                "title": case["title"],
                "url": case["url"],
                "content": case["content"],
            })
            raw = _invoke(classify_single, article_json=article_json)
            result = json.loads(raw)

            expected = case["expected"]
            if case["event_type"] == "player_enters_portal":
                if expected.get("player_name") and result.get("player_name"):
                    # Fuzzy match on name (allow partial)
                    exp_name = expected["player_name"].lower()
                    got_name = result["player_name"].lower()
                    if exp_name not in got_name and got_name not in exp_name:
                        extraction_errors.append(
                            f"{case['id']}: player_name mismatch: "
                            f"got '{result['player_name']}', expected '{expected['player_name']}'"
                        )
            elif case["event_type"] == "coach_leaves":
                if expected.get("coach_name") and result.get("coach_name"):
                    exp_name = expected["coach_name"].lower()
                    got_name = result["coach_name"].lower()
                    if exp_name not in got_name and got_name not in exp_name:
                        extraction_errors.append(
                            f"{case['id']}: coach_name mismatch: "
                            f"got '{result['coach_name']}', expected '{expected['coach_name']}'"
                        )

        if extraction_errors:
            pytest.fail(f"Entity extraction errors:\n" + "\n".join(extraction_errors))


# ---------------------------------------------------------------------------
# Tavily search recall eval (gated, requires TAVILY_API_KEY)
# ---------------------------------------------------------------------------

from tests.tavily_recall_helpers import (
    compute_recall,
    entity_hit,
    search_entity_augmented_for_case,
    search_historical_for_case,
    search_production_for_case,
)

# Baselines measured 2026-07-12 with agent master queries + historical date windows.
# These are diagnostic floors — not production quality targets.
_MASTER_QUERY_RECALL_BASELINE = 0.10


@pytest.mark.skipif(
    not os.environ.get("RUN_TAVILY_EVALS"),
    reason="Tavily recall evals disabled — set RUN_TAVILY_EVALS=1 to enable",
)
class TestTavilyHistoricalRecall:
    """Historical recall: agent master queries + production Tavily params,
    scoped to each golden case's event_date ± 7 days.

    Uses the same queries as graph.py SYSTEM_PROMPT and the same
    include_domains / search_depth / topic=news / max_results=10 as
    ``search_news``, plus ``start_date``/``end_date`` for historical windows
    (Tavily supports explicit date bounds per their search API).
    """

    @pytest.fixture(scope="class")
    def tavily_client(self):
        from tavily import TavilyClient

        api_key = os.environ.get("TAVILY_API_KEY")
        if not api_key:
            pytest.skip("TAVILY_API_KEY not set")
        return TavilyClient(api_key=api_key)

    @pytest.fixture(scope="class")
    def historical_outcomes(self, tavily_client):
        return [search_historical_for_case(tavily_client, c) for c in load_golden_set()]

    @pytest.fixture(scope="class")
    def entity_augmented_outcomes(self, tavily_client):
        return [search_entity_augmented_for_case(tavily_client, c) for c in load_golden_set()]

    def test_master_query_historical_recall_report(self, historical_outcomes):
        """Diagnostic: agent master queries with historical date windows."""
        cases = load_golden_set()
        recall, misses, zero_results = compute_recall(cases, historical_outcomes)

        portal_cases = load_cases_by_event_type("player_enters_portal")
        coach_cases = load_cases_by_event_type("coach_leaves")
        portal_recall, _, _ = compute_recall(portal_cases, historical_outcomes)
        coach_recall, _, _ = compute_recall(coach_cases, historical_outcomes)

        print(f"\n{'='*60}")
        print("TAVILY HISTORICAL RECALL — AGENT MASTER QUERIES")
        print(f"  Overall:  {len(cases)-len(misses)}/{len(cases)} ({recall:.1%})")
        print(f"  Portal:   {portal_recall:.1%}")
        print(f"  Coach:    {coach_recall:.1%}")
        if zero_results:
            print(f"  Zero results: {len(zero_results)} cases")
        if misses:
            print(f"  Missed: {misses}")
        print(f"{'='*60}\n")

        # Regression guard only — master-query recall is inherently low (~15%)
        assert recall >= _MASTER_QUERY_RECALL_BASELINE, (
            f"Master-query recall {recall:.1%} below baseline "
            f"{_MASTER_QUERY_RECALL_BASELINE:.0%} — possible Tavily outage"
        )

    def test_entity_augmented_upper_bound_report(self, entity_augmented_outcomes):
        """Diagnostic: entity-named queries (upper bound, not used by agent)."""
        cases = load_golden_set()
        recall, misses, _ = compute_recall(cases, entity_augmented_outcomes)

        print(f"\n{'='*60}")
        print("TAVILY HISTORICAL RECALL — ENTITY-AUGMENTED QUERIES (upper bound)")
        print(f"  Recall: {len(cases)-len(misses)}/{len(cases)} ({recall:.1%})")
        if misses:
            print(f"  Missed: {misses}")
        print(f"{'='*60}\n")

        assert True  # informational

    def test_raw_index_coverage_report(self, historical_outcomes):
        """Informational: recall before TAVILY_MIN_SCORE post-filter."""
        cases = load_golden_set()
        recall, misses, _ = compute_recall(cases, historical_outcomes, use_raw=True)

        print(f"\n{'='*60}")
        print("TAVILY HISTORICAL RAW INDEX COVERAGE (score >= 0.1)")
        print(f"  Recall: {len(cases) - len(misses)}/{len(cases)} ({recall:.1%})")
        if misses:
            print(f"  Still missed at raw threshold: {misses}")
        print(f"{'='*60}\n")

        assert True

    def test_recall_by_difficulty(self, historical_outcomes):
        """Informational: recall broken down by difficulty tier."""
        cases = load_golden_set()
        by_diff: dict[str, list[bool]] = {"easy": [], "medium": [], "hard": []}
        outcome_by_id = {o.case_id: o for o in historical_outcomes}

        for case in cases:
            outcome = outcome_by_id[case["id"]]
            hit = bool(outcome.production_results) and entity_hit(
                case, outcome.production_results
            )
            by_diff[case["difficulty"]].append(hit)

        print(f"\n{'='*60}")
        print("TAVILY HISTORICAL RECALL BY DIFFICULTY")
        for diff, hits in by_diff.items():
            if hits:
                print(f"  {diff:8s}: {sum(hits)}/{len(hits)} ({sum(hits)/len(hits):.1%})")
        print(f"{'='*60}\n")

        assert True  # informational

    def test_recall_by_domain(self, historical_outcomes):
        """Informational: recall broken down by source domain."""
        cases = load_golden_set()
        by_domain: dict[str, list[bool]] = {}
        outcome_by_id = {o.case_id: o for o in historical_outcomes}

        for case in cases:
            domain = case["source_domain"]
            outcome = outcome_by_id[case["id"]]
            hit = bool(outcome.production_results) and entity_hit(
                case, outcome.production_results
            )
            by_domain.setdefault(domain, []).append(hit)

        print(f"\n{'='*60}")
        print("TAVILY HISTORICAL RECALL BY DOMAIN")
        for domain, hits in sorted(by_domain.items()):
            print(f"  {domain:25s}: {sum(hits)}/{len(hits)} ({sum(hits)/len(hits):.1%})")
        print(f"{'='*60}\n")

        assert True  # informational


@pytest.mark.skipif(
    not os.environ.get("RUN_TAVILY_EVALS"),
    reason="Tavily recall evals disabled — set RUN_TAVILY_EVALS=1 to enable",
)
class TestTavilyProductionToolParity:
    """Smoke test: production ``search_news`` with agent master queries.

    Uses rolling 1-day window (no end_date) — same as live agent runs.
    Historical golden events are NOT expected to appear; this confirms the
    tool wiring works and reports current-window recall for context.
    """

    @pytest.fixture(scope="class")
    def production_outcomes(self):
        from portalpoint.agents.news_monitoring.sources.tavily import search_news

        return [search_production_for_case(search_news, c) for c in load_golden_set()]

    def test_production_tool_returns_results(self, production_outcomes):
        """At least one of the two agent queries should return articles."""
        portal_cases = load_cases_by_event_type("player_enters_portal")
        coach_cases = load_cases_by_event_type("coach_leaves")

        portal_outcomes = [o for o in production_outcomes if o.case_id in {c["id"] for c in portal_cases}]
        coach_outcomes = [o for o in production_outcomes if o.case_id in {c["id"] for c in coach_cases}]

        portal_has_results = any(o.production_results for o in portal_outcomes)
        coach_has_results = any(o.production_results for o in coach_outcomes)

        print(f"\n{'='*60}")
        print("TAVILY PRODUCTION TOOL SMOKE (rolling 1-day window)")
        print(f"  Portal query returned results: {portal_has_results}")
        print(f"  Coach query returned results:  {coach_has_results}")
        print("  Note: historical golden cases are not expected to match.")
        print(f"{'='*60}\n")

        assert portal_has_results or coach_has_results, (
            "Neither agent query returned results in the current window — "
            "check TAVILY_API_KEY, credits, or domain filters"
        )

    def test_historical_golden_recall_in_current_window(self, production_outcomes):
        """Informational: how many golden events appear in the live 1-day window."""
        cases = load_golden_set()
        recall, misses, zero_results = compute_recall(cases, production_outcomes)

        print(f"\n{'='*60}")
        print("TAVILY PRODUCTION WINDOW vs GOLDEN SET (informational)")
        print(f"  Recall: {len(cases) - len(misses)}/{len(cases)} ({recall:.1%})")
        if zero_results:
            print(f"  Zero results: {len(zero_results)} cases")
        print(f"{'='*60}\n")

        # Always passes — this is diagnostic, not a gate
        assert True
