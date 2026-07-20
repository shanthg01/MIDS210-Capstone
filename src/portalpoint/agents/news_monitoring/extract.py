"""Classification tools and LLM helpers for the news-monitoring agent."""
from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from langchain_core.tools import tool

from pydantic import BaseModel
from pydantic import Field as PydField

from portalpoint.agents.news_monitoring.config import (
    CONFIDENCE_THRESHOLD,
    EVENT_PATTERNS,
    GEMINI_CALLS_PER_MINUTE,
    GEMINI_MODEL,
    TARGET_EVENT_TYPES,
)


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """Token-bucket rate limiter to stay within Gemini free-tier RPM quota."""

    def __init__(self, calls_per_minute: int = GEMINI_CALLS_PER_MINUTE) -> None:
        self.calls_per_minute = calls_per_minute
        self.min_interval = 60.0 / calls_per_minute
        self.last_call_time: float = 0
        self.call_count: int = 0

    def wait_if_needed(self) -> int:
        now = time.time()
        elapsed = now - self.last_call_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_call_time = time.time()
        self.call_count += 1
        return self.call_count


# ---------------------------------------------------------------------------
# LLM factory
# ---------------------------------------------------------------------------

def build_llm(model: str = GEMINI_MODEL):
    """Create a ChatGoogleGenerativeAI instance.  Requires GOOGLE_API_KEY env var."""
    from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore[import]
    return ChatGoogleGenerativeAI(
        model=model,
        temperature=0,
        max_tokens=4096,
        max_retries=3,
        timeout=60,
    )


# ---------------------------------------------------------------------------
# Structured output schema
# ---------------------------------------------------------------------------

class ArticleClassification(BaseModel):
    """Structured output schema for LLM-based article classification."""
    event_type: str = PydField(
        description="Exactly one of: 'player_enters_portal', 'coach_leaves', or 'unknown'"
    )
    confidence: float = PydField(description="Confidence 0.0 (none) to 1.0 (certain)")
    player_name: Optional[str] = PydField(
        default=None,
        description="Full name of the player if this is a portal entry event",
    )
    coach_name: Optional[str] = PydField(
        default=None,
        description="Full name of the coach if this is a coaching change",
    )
    school_from: Optional[str] = PydField(
        default=None,
        description="School / program the person is departing",
    )
    reasoning: str = PydField(description="1-2 sentence explanation of the classification decision")


# ---------------------------------------------------------------------------
# Deterministic regex classifier (fast path)
# ---------------------------------------------------------------------------

def _classify_event_payload(text: str, source_url: str = "", title: str = "") -> dict:
    """Classify a single article using deterministic regex patterns.

    Title matches receive higher confidence than body-only matches, reflecting
    the editorial signal that a headline being explicit about portal entry is
    stronger evidence than a passing mention in the body.
    """
    title_lower = title.lower()
    body_lower = text.lower()

    best_match = "unknown"
    best_confidence = 0.0
    matched_pattern = ""

    for event_type, patterns in EVENT_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, title_lower):
                confidence = 0.90 if event_type in TARGET_EVENT_TYPES else 0.80
            elif re.search(pattern, body_lower):
                confidence = 0.70 if event_type in TARGET_EVENT_TYPES else 0.60
            else:
                continue
            if confidence > best_confidence:
                best_match = event_type
                best_confidence = confidence
                matched_pattern = pattern

    return {
        "event_type": best_match,
        "confidence": best_confidence,
        "is_target_event": best_match in TARGET_EVENT_TYPES,
        "above_threshold": best_confidence >= CONFIDENCE_THRESHOLD,
        "matched_pattern": matched_pattern,
        "source_url": source_url,
        "title": title,
    }


@tool
def classify_event(article_json: str) -> str:
    """Classify a single news article using deterministic regex patterns.

    Args:
        article_json: JSON string with ``{title, url, content}`` keys.

    Returns:
        JSON classification result with ``event_type``, ``confidence``,
        ``is_target_event``, ``above_threshold``, and ``matched_pattern``.
    """
    try:
        article = json.loads(article_json)
    except (json.JSONDecodeError, TypeError):
        return json.dumps({"error": "invalid JSON input"})
    result = _classify_event_payload(
        text=article.get("content", ""),
        source_url=article.get("url", ""),
        title=article.get("title", ""),
    )
    return json.dumps(result)


@tool
def classify_events_batch(articles_json: str) -> str:
    """Classify a batch of news articles using deterministic regex patterns.

    Prefer this over classify_event for search results — one tool call
    classifies all articles, saving LLM turns.

    Args:
        articles_json: JSON string with a list of ``{title, url, content}`` dicts,
            or a dict ``{results: [...]}`` (Tavily search output format).

    Returns:
        JSON list of classification results, each with ``event_type``,
        ``confidence``, ``is_target_event``, ``above_threshold``, ``title``, and ``url``.
    """
    try:
        data = json.loads(articles_json)
        articles = data if isinstance(data, list) else data.get("results", [])
    except (json.JSONDecodeError, TypeError):
        return json.dumps({"error": "invalid JSON input"})

    def _classify(article: dict) -> dict:
        result = _classify_event_payload(
            text=article.get("content", ""),
            source_url=article.get("url", ""),
            title=article.get("title", ""),
        )
        return result

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_classify, articles))

    return json.dumps({
        "results": results,
        "total": len(results),
        "target_events": sum(1 for r in results if r["is_target_event"] and r["above_threshold"]),
    })


# ---------------------------------------------------------------------------
# LLM classifier (context-aware, used when USE_LLM_CLASSIFIER=True)
# ---------------------------------------------------------------------------

def _classify_llm_single(article: dict, llm_structured) -> dict:
    """Classify one article with the LLM structured-output model."""
    prompt = (
        f"Title: {article.get('title', '')}\n\n"
        f"Content: {article.get('content', '')[:800]}\n\n"
        "Classify this college basketball news article."
    )
    try:
        result: ArticleClassification = llm_structured.invoke(prompt)
        return {
            "event_type": result.event_type,
            "confidence": result.confidence,
            "player_name": result.player_name,
            "coach_name": result.coach_name,
            "school_from": result.school_from,
            "reasoning": result.reasoning,
            "is_target_event": result.event_type in TARGET_EVENT_TYPES,
            "above_threshold": result.confidence >= CONFIDENCE_THRESHOLD,
            "source_url": article.get("url", ""),
            "title": article.get("title", ""),
        }
    except Exception as exc:
        return {
            "event_type": "unknown",
            "confidence": 0.0,
            "is_target_event": False,
            "above_threshold": False,
            "error": str(exc),
            "source_url": article.get("url", ""),
            "title": article.get("title", ""),
        }


def build_llm_classify_tools(rate_limiter: RateLimiter, llm=None):
    """Return (classify_event_llm, classify_events_batch_llm) tool functions
    bound to the given rate_limiter and llm.

    Called by the notebook (Cell 5b) and run_news_monitoring.py after the LLM
    is instantiated.
    """
    _llm = llm or build_llm()
    _llm_structured = _llm.with_structured_output(ArticleClassification)

    @tool
    def classify_event_llm(article_json: str) -> str:
        """Classify a single news article using the Gemini LLM (context-aware).

        Args:
            article_json: JSON string with ``{title, url, content}`` keys.
        """
        rate_limiter.wait_if_needed()
        try:
            article = json.loads(article_json)
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "invalid JSON"})
        return json.dumps(_classify_llm_single(article, _llm_structured))

    @tool
    def classify_events_batch_llm(articles_json: str) -> str:
        """Classify a batch of articles using the Gemini LLM.

        Sequential (not parallel) to respect free-tier RPM limits.

        Args:
            articles_json: JSON list of ``{title, url, content}`` dicts,
                or Tavily ``{results: [...]}`` dict.
        """
        try:
            data = json.loads(articles_json)
            articles = data if isinstance(data, list) else data.get("results", [])
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "invalid JSON"})

        results = []
        for article in articles:
            rate_limiter.wait_if_needed()
            results.append(_classify_llm_single(article, _llm_structured))

        return json.dumps({
            "results": results,
            "total": len(results),
            "target_events": sum(1 for r in results if r.get("is_target_event") and r.get("above_threshold")),
        })

    return classify_event_llm, classify_events_batch_llm
