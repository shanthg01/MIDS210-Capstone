"""Tavily web-search tool for the news-monitoring agent."""
from __future__ import annotations

import json
import os
from datetime import date, timedelta

from langchain_core.tools import tool

from portalpoint.agents.news_monitoring.config import (
    TAVILY_CHUNKS_PER_SOURCE,
    TAVILY_INCLUDE_DOMAINS,
    TAVILY_MAX_RESULTS,
    TAVILY_MIN_SCORE,
    TAVILY_SEARCH_DEPTH,
    TAVILY_WINDOW_DAYS,
)
from portalpoint.agents.news_monitoring.sport_filter import filter_basketball_articles


def _search_news_impl(
    query: str,
    window_days: int,
    include_domains: list[str] | None = None,
) -> str:
    try:
        from tavily import TavilyClient  # type: ignore[import]
    except ImportError:
        return json.dumps({"error": "tavily-python not installed — run: uv add tavily-python"})

    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return json.dumps({"error": "TAVILY_API_KEY not set in environment"})

    domains = include_domains or TAVILY_INCLUDE_DOMAINS
    start_date = (date.today() - timedelta(days=window_days)).isoformat()

    try:
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=query,
            search_depth=TAVILY_SEARCH_DEPTH,
            topic="news",
            include_domains=domains,
            max_results=TAVILY_MAX_RESULTS,
            chunks_per_source=TAVILY_CHUNKS_PER_SOURCE,
            start_date=start_date,
        )
    except Exception as exc:
        return json.dumps({"error": str(exc), "suggestion": "Check TAVILY_API_KEY and API credits"})

    results = [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", ""),
            "score": r.get("score", 0.0),
            "published_date": r.get("published_date", ""),
        }
        for r in response.get("results", [])
        if r.get("score", 0.0) >= TAVILY_MIN_SCORE
    ]

    kept, rejected = filter_basketball_articles(results)

    return json.dumps({
        "results": kept,
        "query": query,
        "count": len(kept),
        "filtered_count": len(rejected),
        "filtered_articles": rejected,
    })


def build_search_news_tool(window_days: int = TAVILY_WINDOW_DAYS):
    """Return a Tavily search tool with a fixed lookback window for this run."""

    description = (
        f"Search college basketball news via Tavily (last {window_days} days). "
        "Returns JSON with title, url, content, score, and published_date."
    )

    @tool(description=description)
    def search_news(
        query: str,
        include_domains: list[str] | None = None,
    ) -> str:
        return _search_news_impl(query, window_days, include_domains)

    return search_news


@tool
def search_news(
    query: str,
    window_days: int = TAVILY_WINDOW_DAYS,
    include_domains: list[str] | None = None,
) -> str:
    """Search college basketball news via Tavily.

    Args:
        query: Search query string.
        window_days: How many days back to search (default 7).
        include_domains: Domains to restrict results to.  Defaults to
            247sports.com, on3.com, and espn.com.

    Returns:
        JSON string with a list of ``{title, url, content, score, published_date}``
        dicts, filtered to score >= TAVILY_MIN_SCORE.
    """
    return _search_news_impl(query, window_days, include_domains)
