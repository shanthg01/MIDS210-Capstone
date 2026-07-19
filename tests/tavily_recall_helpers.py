"""Helpers for Tavily search-recall evals against the golden set.

Two eval modes:

1. **Historical recall** — same agent queries + production Tavily params, but
   scoped to ``event_date ± buffer_days``.  Answers: "Would Tavily have surfaced
   this event around when it happened?"

2. **Production tool parity** — calls ``search_news`` exactly as the live agent
   does (rolling ``window_days``, no ``end_date``).  Only useful as a smoke test
   for recent events; historical golden cases are expected to miss.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from portalpoint.agents.news_monitoring.config import (
    AGENT_SEARCH_QUERIES,
    TAVILY_CHUNKS_PER_SOURCE,
    TAVILY_INCLUDE_DOMAINS,
    TAVILY_MAX_RESULTS,
    TAVILY_MIN_SCORE,
    TAVILY_SEARCH_DEPTH,
    TAVILY_WINDOW_DAYS,
)
from portalpoint.modeling.entity_resolution import normalize_name

# Raw-index floor — measures Tavily index coverage independent of score gate.
RAW_INDEX_MIN_SCORE = 0.1


@dataclass
class TavilySearchOutcome:
    """Results from one Tavily search for a golden case."""

    case_id: str
    query: str
    start_date: str | None
    end_date: str | None
    production_results: list[dict[str, Any]]
    raw_results: list[dict[str, Any]]
    mode: str  # "historical" | "production"


def _name_in_text(name: str, text: str) -> bool:
    """True if expected entity name appears in text (accent/suffix tolerant)."""
    norm_text = normalize_name(text)
    parts = normalize_name(name).split()
    if not parts:
        return False
    last_name = parts[-1]
    if last_name not in norm_text:
        return False
    return len(parts) < 2 or parts[0] in norm_text


def _school_in_text(school: str, text: str) -> bool:
    """True if school name (or first token) appears in text."""
    if not school:
        return True
    lowered = text.lower()
    tokens = school.lower().split()
    return school.lower() in lowered or (tokens and tokens[0] in lowered)


def entity_hit(case: dict[str, Any], results: list[dict[str, Any]]) -> bool:
    """Return True if any result mentions the expected entity + school."""
    expected = case["expected"]
    name = expected.get("player_name") or expected.get("coach_name") or ""
    school = expected.get("school_from") or ""

    for result in results:
        combined = f"{result.get('title', '')} {result.get('content', '')}"
        if _name_in_text(name, combined) and _school_in_text(school, combined):
            return True
    return False


def _filter_results(
    results: list[dict[str, Any]],
    min_score: float,
) -> list[dict[str, Any]]:
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", ""),
            "score": r.get("score", 0.0),
            "published_date": r.get("published_date", ""),
        }
        for r in results
        if r.get("score", 0.0) >= min_score
    ]


def search_historical_for_case(
    client,
    case: dict[str, Any],
    *,
    buffer_days: int = 7,
    max_results: int = TAVILY_MAX_RESULTS,
    chunks_per_source: int = TAVILY_CHUNKS_PER_SOURCE,
) -> TavilySearchOutcome:
    """Run agent query scoped to event_date ± buffer_days (historical eval)."""
    query = AGENT_SEARCH_QUERIES[case["event_type"]]
    event_date = datetime.strptime(case["event_date"], "%Y-%m-%d").date()
    start_date = (event_date - timedelta(days=buffer_days)).isoformat()
    end_date = (event_date + timedelta(days=buffer_days)).isoformat()

    response = client.search(
        query=query,
        search_depth=TAVILY_SEARCH_DEPTH,
        topic="news",
        include_domains=TAVILY_INCLUDE_DOMAINS,
        max_results=max_results,
        chunks_per_source=chunks_per_source,
        start_date=start_date,
        end_date=end_date,
    )
    all_results = response.get("results", [])

    return TavilySearchOutcome(
        case_id=case["id"],
        query=query,
        start_date=start_date,
        end_date=end_date,
        production_results=_filter_results(all_results, TAVILY_MIN_SCORE),
        raw_results=_filter_results(all_results, RAW_INDEX_MIN_SCORE),
        mode="historical",
    )


def search_production_for_case(
    search_news_tool,
    case: dict[str, Any],
    *,
    window_days: int = TAVILY_WINDOW_DAYS,
) -> TavilySearchOutcome:
    """Run production ``search_news`` tool with the agent's master query.

    Uses rolling window from today — historical golden events are expected to
    miss unless they occurred within the last ``window_days`` days.
    """
    query = AGENT_SEARCH_QUERIES[case["event_type"]]

    if hasattr(search_news_tool, "invoke"):
        raw = search_news_tool.invoke({"query": query, "window_days": window_days})
    else:
        raw = search_news_tool(query=query, window_days=window_days)

    payload = json.loads(raw)
    if payload.get("error"):
        raise RuntimeError(payload["error"])

    production = payload.get("results", [])

    return TavilySearchOutcome(
        case_id=case["id"],
        query=query,
        start_date=(date.today() - timedelta(days=window_days)).isoformat(),
        end_date=None,
        production_results=production,
        raw_results=production,  # tool already applies TAVILY_MIN_SCORE
        mode="production",
    )


def search_entity_augmented_for_case(
    client,
    case: dict[str, Any],
    *,
    buffer_days: int = 7,
    max_results: int = TAVILY_MAX_RESULTS,
    chunks_per_source: int = TAVILY_CHUNKS_PER_SOURCE,
) -> TavilySearchOutcome:
    """Historical search with entity + school in query (upper-bound diagnostic).

    Not used by the live agent — measures whether Tavily indexes the event at
    all when the query names the player/coach explicitly.
    """
    expected = case["expected"]
    if case["event_type"] == "player_enters_portal":
        query = (
            f"{expected['player_name']} {expected['school_from']} "
            "college basketball transfer portal"
        )
    else:
        query = (
            f"{expected['coach_name']} {expected['school_from']} "
            "college basketball coach fired resigns"
        )

    event_date = datetime.strptime(case["event_date"], "%Y-%m-%d").date()
    start_date = (event_date - timedelta(days=buffer_days)).isoformat()
    end_date = (event_date + timedelta(days=buffer_days)).isoformat()

    response = client.search(
        query=query,
        search_depth=TAVILY_SEARCH_DEPTH,
        topic="news",
        include_domains=TAVILY_INCLUDE_DOMAINS,
        max_results=max_results,
        chunks_per_source=chunks_per_source,
        start_date=start_date,
        end_date=end_date,
    )
    all_results = response.get("results", [])

    return TavilySearchOutcome(
        case_id=case["id"],
        query=query,
        start_date=start_date,
        end_date=end_date,
        production_results=_filter_results(all_results, TAVILY_MIN_SCORE),
        raw_results=_filter_results(all_results, RAW_INDEX_MIN_SCORE),
        mode="entity_augmented",
    )


def run_recall_comparison(
    client,
    cases: list[dict[str, Any]],
    *,
    label: str,
    max_results: int,
    chunks_per_source: int,
) -> dict[str, Any]:
    """Run historical master-query recall for a Tavily config and return metrics."""
    outcomes = [
        search_historical_for_case(
            client,
            case,
            max_results=max_results,
            chunks_per_source=chunks_per_source,
        )
        for case in cases
    ]
    recall, misses, zero_results = compute_recall(cases, outcomes)
    raw_recall, _, _ = compute_recall(cases, outcomes, use_raw=True)

    portal_cases = [c for c in cases if c["event_type"] == "player_enters_portal"]
    coach_cases = [c for c in cases if c["event_type"] == "coach_leaves"]
    portal_recall, _, _ = compute_recall(portal_cases, outcomes)
    coach_recall, _, _ = compute_recall(coach_cases, outcomes)

    newly_found = []
    return {
        "label": label,
        "max_results": max_results,
        "chunks_per_source": chunks_per_source,
        "recall": recall,
        "raw_recall": raw_recall,
        "portal_recall": portal_recall,
        "coach_recall": coach_recall,
        "misses": misses,
        "zero_results": zero_results,
        "outcomes": outcomes,
        "newly_found": newly_found,
    }


def compute_recall(
    cases: list[dict[str, Any]],
    outcomes: list[TavilySearchOutcome],
    *,
    use_raw: bool = False,
) -> tuple[float, list[str], list[str]]:
    """Return (recall, missed_case_ids, zero_result_case_ids)."""
    misses: list[str] = []
    zero_results: list[str] = []
    outcome_by_id = {o.case_id: o for o in outcomes}

    for case in cases:
        outcome = outcome_by_id.get(case["id"])
        if outcome is None:
            misses.append(case["id"])
            continue

        results = outcome.raw_results if use_raw else outcome.production_results
        if not results:
            zero_results.append(case["id"])
            misses.append(case["id"])
        elif not entity_hit(case, results):
            misses.append(case["id"])

    hits = len(cases) - len(misses)
    recall = hits / len(cases) if cases else 0.0
    return recall, misses, zero_results
