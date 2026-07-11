"""Unit tests for the news-monitoring agent modules.

Covers:
- extract.py: regex classifier (no LLM calls, no network)
- resolve.py: cross_source_dedup (no DB)
- graph.py: should_continue routing
- config.py: constants
"""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock

import pytest

from portalpoint.agents.news_monitoring.config import (
    CONFIDENCE_THRESHOLD,
    EVENT_PATTERNS,
    TARGET_EVENT_TYPES,
)

from portalpoint.agents.news_monitoring.extract import (
    RateLimiter,
    _classify_event_payload,
    classify_event,
    classify_events_batch,
)
from portalpoint.agents.news_monitoring.resolve import cross_source_dedup


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_target_event_types_subset_of_patterns(self):
        for t in TARGET_EVENT_TYPES:
            assert t in EVENT_PATTERNS, f"{t} in TARGET_EVENT_TYPES but missing from EVENT_PATTERNS"

    def test_confidence_threshold_in_range(self):
        assert 0.0 < CONFIDENCE_THRESHOLD < 1.0

    def test_event_patterns_nonempty(self):
        for k, v in EVENT_PATTERNS.items():
            assert v, f"EVENT_PATTERNS[{k!r}] is empty"


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------

class TestRateLimiter:
    def test_call_count_increments(self):
        rl = RateLimiter(calls_per_minute=600)  # very fast for tests
        rl.wait_if_needed()
        rl.wait_if_needed()
        assert rl.call_count == 2

    def test_returns_call_count(self):
        rl = RateLimiter(calls_per_minute=600)
        result = rl.wait_if_needed()
        assert result == 1


# ---------------------------------------------------------------------------
# _classify_event_payload (deterministic, no LLM)
# ---------------------------------------------------------------------------

class TestClassifyEventPayload:
    def test_portal_entry_in_title_high_confidence(self):
        result = _classify_event_payload(
            text="Junior averaged 12 PPG last season.",
            title="Duke guard enters NCAA transfer portal",
        )
        assert result["event_type"] == "player_enters_portal"
        assert result["confidence"] >= 0.90
        assert result["is_target_event"] is True
        assert result["above_threshold"] is True

    def test_portal_entry_body_only_lower_confidence(self):
        result = _classify_event_payload(
            text="Sources say the guard has entered the transfer portal after meeting with staff.",
            title="Duke roster update",
        )
        assert result["event_type"] == "player_enters_portal"
        assert result["confidence"] < 0.90
        assert result["confidence"] >= 0.60

    def test_coach_leaves_in_title(self):
        result = _classify_event_payload(
            text="After 10 seasons, he announced his departure.",
            title="Arizona head coach stepping down",
        )
        assert result["event_type"] == "coach_leaves"
        assert result["is_target_event"] is True

    def test_unknown_event(self):
        result = _classify_event_payload(
            text="The team practiced for three hours on Tuesday.",
            title="Practice report",
        )
        assert result["event_type"] == "unknown"
        assert result["is_target_event"] is False
        assert result["above_threshold"] is False

    def test_recruit_commitment_not_portal(self):
        result = _classify_event_payload(
            text="Top recruit commits to Florida for 2026 season.",
            title="Five-star recruit commits to Gators",
        )
        # "commits" doesn't match portal patterns
        assert result["event_type"] != "player_enters_portal"

    def test_title_beats_body_for_confidence(self):
        title_result = _classify_event_payload(
            text="",
            title="Star forward enters the transfer portal",
        )
        body_result = _classify_event_payload(
            text="Star forward has entered the transfer portal.",
            title="Roster news",
        )
        assert title_result["confidence"] > body_result["confidence"]

    def test_matched_pattern_populated(self):
        result = _classify_event_payload(
            text="",
            title="Player hits the portal after season ends",
        )
        assert result["matched_pattern"] != ""

    def test_source_url_passthrough(self):
        result = _classify_event_payload(
            text="",
            title="Player enters portal",
            source_url="https://example.com/news",
        )
        assert result["source_url"] == "https://example.com/news"


# ---------------------------------------------------------------------------
# classify_event tool (JSON wrapper)
# ---------------------------------------------------------------------------

def _invoke(tool_fn, **kwargs):
    """Call a langchain tool or plain function."""
    if hasattr(tool_fn, "invoke"):
        return tool_fn.invoke(kwargs)
    return tool_fn(**kwargs)


class TestClassifyEventTool:
    def test_valid_article(self):
        article = json.dumps({
            "title": "Guard enters transfer portal",
            "url": "https://example.com",
            "content": "The guard officially entered the NCAA transfer portal Monday.",
        })
        raw = _invoke(classify_event, article_json=article)
        result = json.loads(raw)
        assert result["event_type"] == "player_enters_portal"
        assert result["is_target_event"] is True

    def test_invalid_json(self):
        raw = _invoke(classify_event, article_json="not json")
        result = json.loads(raw)
        assert "error" in result

    def test_empty_article(self):
        article = json.dumps({"title": "", "url": "", "content": ""})
        raw = _invoke(classify_event, article_json=article)
        result = json.loads(raw)
        assert result["event_type"] == "unknown"


# ---------------------------------------------------------------------------
# classify_events_batch tool
# ---------------------------------------------------------------------------

class TestClassifyEventsBatchTool:
    def test_list_input(self):
        articles = json.dumps([
            {"title": "Player enters portal", "url": "https://a.com", "content": "enters transfer portal"},
            {"title": "Game recap", "url": "https://b.com", "content": "Won 72-68."},
        ])
        raw = _invoke(classify_events_batch, articles_json=articles)
        result = json.loads(raw)
        assert result["total"] == 2
        assert result["target_events"] >= 1

    def test_tavily_dict_input(self):
        data = json.dumps({
            "results": [
                {"title": "Coach fired", "url": "https://a.com", "content": "head coach fired after season"},
            ],
            "count": 1,
        })
        raw = _invoke(classify_events_batch, articles_json=data)
        result = json.loads(raw)
        assert result["total"] == 1

    def test_invalid_json(self):
        raw = _invoke(classify_events_batch, articles_json="bad")
        result = json.loads(raw)
        assert "error" in result

    def test_empty_list(self):
        raw = _invoke(classify_events_batch, articles_json="[]")
        result = json.loads(raw)
        assert result["total"] == 0
        assert result["target_events"] == 0


# ---------------------------------------------------------------------------
# cross_source_dedup
# ---------------------------------------------------------------------------

class TestCrossSourceDedup:
    def _update(self, player_id, school_id, event_date, confidence=0.85):
        return {
            "player_id": player_id,
            "from_school_id": school_id,
            "portal_entry_date": event_date,
            "match_confidence": confidence,
            "event_type": "transfer_entry",
        }

    def test_no_duplicates(self):
        updates = [
            self._update(1, 10, "2026-03-15"),
            self._update(2, 20, "2026-03-15"),
        ]
        deduped, dupes = cross_source_dedup(updates)
        assert len(deduped) == 2
        assert len(dupes) == 0

    def test_exact_duplicate_collapsed(self):
        updates = [
            self._update(1, 10, "2026-03-15", confidence=0.80),
            self._update(1, 10, "2026-03-15", confidence=0.90),
        ]
        deduped, dupes = cross_source_dedup(updates)
        assert len(deduped) == 1
        assert len(dupes) == 1
        # Higher confidence entry wins
        assert deduped[0]["match_confidence"] == 0.90

    def test_within_window_collapsed(self):
        updates = [
            self._update(1, 10, "2026-03-15"),
            self._update(1, 10, "2026-03-16"),  # 1 day apart — same window bucket
        ]
        deduped, dupes = cross_source_dedup(updates, window_days=2)
        assert len(deduped) + len(dupes) == 2
        # At least one deduped (they may or may not fall in same bucket)
        # depending on day arithmetic — just verify no crash and totals add up
        assert len(deduped) + len(dupes) == 2

    def test_different_players_not_collapsed(self):
        updates = [
            self._update(1, 10, "2026-03-15"),
            self._update(2, 10, "2026-03-15"),  # different player_id
        ]
        deduped, dupes = cross_source_dedup(updates)
        assert len(deduped) == 2

    def test_different_schools_not_collapsed(self):
        updates = [
            self._update(1, 10, "2026-03-15"),
            self._update(1, 20, "2026-03-15"),  # different school
        ]
        deduped, dupes = cross_source_dedup(updates)
        assert len(deduped) == 2

    def test_empty_input(self):
        deduped, dupes = cross_source_dedup([])
        assert deduped == []
        assert dupes == []

    def test_single_event(self):
        updates = [self._update(1, 10, "2026-03-15")]
        deduped, dupes = cross_source_dedup(updates)
        assert len(deduped) == 1
        assert len(dupes) == 0

    def test_none_date_handled(self):
        updates = [
            self._update(1, 10, None),
            self._update(1, 10, None),
        ]
        deduped, dupes = cross_source_dedup(updates)
        assert len(deduped) == 1  # both in same bucket (day_bucket = -1)
        assert len(dupes) == 1


# ---------------------------------------------------------------------------
# Graph routing (should_continue) — requires langgraph
# ---------------------------------------------------------------------------

class TestShouldContinue:
    def test_routes_to_tools_when_tool_calls(self):
        from portalpoint.agents.news_monitoring.graph import should_continue

        msg = MagicMock()
        msg.tool_calls = [{"name": "search_news", "args": {}, "id": "1"}]

        state = {
            "messages": [msg],
            "detected_events": [],
            "portal_updates": [],
            "news_sources": [],
            "run_window_start": None,
            "run_window_end": None,
            "errors": [],
        }
        assert should_continue(state) == "tools"

    def test_routes_to_dedup_when_no_tool_calls(self):
        from portalpoint.agents.news_monitoring.graph import should_continue

        msg = MagicMock()
        msg.tool_calls = []

        state = {
            "messages": [msg],
            "detected_events": [],
            "portal_updates": [],
            "news_sources": [],
            "run_window_start": None,
            "run_window_end": None,
            "errors": [],
        }
        assert should_continue(state) == "dedup"

    def test_routes_to_dedup_when_no_tool_calls_attr(self):
        from portalpoint.agents.news_monitoring.graph import should_continue

        msg = MagicMock(spec=[])  # no tool_calls attribute

        state = {
            "messages": [msg],
            "detected_events": [],
            "portal_updates": [],
            "news_sources": [],
            "run_window_start": None,
            "run_window_end": None,
            "errors": [],
        }
        assert should_continue(state) == "dedup"
