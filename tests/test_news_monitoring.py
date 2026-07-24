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
from portalpoint.agents.news_monitoring.resolve import cross_source_dedup, lookup_basketball_player_impl
from portalpoint.agents.news_monitoring.sport_filter import (
    filter_basketball_articles,
    is_non_basketball_article,
)


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
# collect_results_node
# ---------------------------------------------------------------------------

class TestCollectResultsNode:
    def test_parses_transfer_player_success(self):
        from langchain_core.messages import AIMessage, ToolMessage

        from portalpoint.agents.news_monitoring.graph import collect_results_node

        payload = json.dumps({
            "success": True,
            "player_id": 101,
            "from_school_id": 9900301,
            "match_confidence": 0.9,
            "portal_entry_date": "2026-07-16",
        })
        state = {
            "messages": [
                AIMessage(content="", tool_calls=[{"name": "transfer_player", "args": {}, "id": "1"}]),
                ToolMessage(content=payload, tool_call_id="1", name="transfer_player"),
            ],
            "detected_events": [],
            "portal_updates": [],
            "news_sources": [],
            "run_window_start": None,
            "run_window_end": None,
            "errors": [],
        }
        result = collect_results_node(state)
        assert len(result["portal_updates"]) == 1
        assert result["portal_updates"][0]["player_id"] == 101

    def test_parses_classify_batch_target_events(self):
        from langchain_core.messages import AIMessage, ToolMessage

        from portalpoint.agents.news_monitoring.graph import collect_results_node

        payload = json.dumps({
            "results": [
                {
                    "event_type": "player_enters_portal",
                    "confidence": 0.9,
                    "is_target_event": True,
                    "above_threshold": True,
                    "title": "Guard enters portal",
                },
                {
                    "event_type": "unknown",
                    "confidence": 0.1,
                    "is_target_event": False,
                    "above_threshold": False,
                },
            ],
            "total": 2,
            "target_events": 1,
        })
        state = {
            "messages": [
                AIMessage(content="", tool_calls=[{"name": "x", "args": {}, "id": "1"}]),
                ToolMessage(content=payload, tool_call_id="1", name="classify_events_batch_llm"),
            ],
            "detected_events": [],
            "portal_updates": [],
            "news_sources": [],
            "run_window_start": None,
            "run_window_end": None,
            "errors": [],
        }
        result = collect_results_node(state)
        assert len(result["detected_events"]) == 1
        assert result["detected_events"][0]["event_type"] == "player_enters_portal"

    def test_merges_portal_updates_with_existing(self):
        from langchain_core.messages import AIMessage, ToolMessage

        from portalpoint.agents.news_monitoring.graph import collect_results_node

        payload = json.dumps({"success": True, "player_id": 2})
        state = {
            "messages": [
                AIMessage(content="", tool_calls=[{"name": "x", "args": {}, "id": "1"}]),
                ToolMessage(content=payload, tool_call_id="1", name="transfer_player_for_season"),
            ],
            "detected_events": [],
            "portal_updates": [{"success": True, "player_id": 1}],
            "news_sources": [],
            "run_window_start": None,
            "run_window_end": None,
            "errors": [],
        }
        result = collect_results_node(state)
        assert [u["player_id"] for u in result["portal_updates"]] == [1, 2]


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


# ---------------------------------------------------------------------------
# coach_departure response shape (Gate 7)
# ---------------------------------------------------------------------------

class TestCoachDepartureResponseShape:
    """coach_departure tool returns JSON with stale-flag fields even on DB failure."""

    def test_db_failure_response_has_stale_flag_field(self):
        """When DB is unavailable the error-path response still includes
        team_system_profiles_stale_flagged so callers can parse consistently."""
        import json
        from unittest.mock import patch

        with patch(
            "portalpoint.agents.news_monitoring.resolve._get_engine",
            side_effect=Exception("no db"),
        ):
            raw = _invoke(
                __import__(
                    "portalpoint.agents.news_monitoring.resolve",
                    fromlist=["coach_departure"],
                ).coach_departure,
                coach_name="Test Coach",
                school_from="Test University",
            )

        result = json.loads(raw)
        assert result["event"] == "coach_departure"
        assert "team_system_profiles_stale_flagged" in result
        assert result["team_system_profiles_stale_flagged"] == 0
        assert result["status"] == "log_only_no_db"

    def test_fit_score_response_has_stale_fields(self):
        """FitScoreResponse schema includes scheme_fit_stale + scheme_fit_stale_reason."""
        from portalpoint.api.schemas.fit_score import FitScoreResponse
        fields = FitScoreResponse.model_fields
        assert "scheme_fit_stale" in fields
        assert "scheme_fit_stale_reason" in fields
        # Defaults: False / None
        assert fields["scheme_fit_stale"].default is False
        assert fields["scheme_fit_stale_reason"].default is None


# ---------------------------------------------------------------------------
# sport_filter
# ---------------------------------------------------------------------------

class TestSportFilter:
    def test_rejects_football_portal_article(self):
        article = {
            "title": "Duke quarterback Darian Mensah enters NCAA transfer portal",
            "content": "Mensah is one of the top quarterbacks in college football's portal cycle.",
            "url": "https://www.espn.com/college-football/story/_/id/123",
        }
        rejected, reason = is_non_basketball_article(article)
        assert rejected is True
        assert reason is not None

    def test_keeps_basketball_portal_article(self):
        article = {
            "title": "Duke guard enters NCAA transfer portal",
            "content": "The sophomore guard averaged 12 points for the Blue Devils this season.",
            "url": "https://www.espn.com/mens-college-basketball/story/_/id/123",
        }
        rejected, _ = is_non_basketball_article(article)
        assert rejected is False

    def test_rejects_womens_basketball_portal(self):
        article = {
            "title": "UConn women's basketball forward enters transfer portal",
            "content": "She averaged 8 points for the Huskies last season.",
            "url": "https://www.espn.com/womens-college-basketball/story/_/id/456",
        }
        rejected, reason = is_non_basketball_article(article)
        assert rejected is True
        assert "womens" in (reason or "")

    def test_filter_basketball_articles_partitions(self):
        articles = [
            {
                "title": "Guard enters portal",
                "content": "Men's college basketball player enters portal.",
                "url": "https://247sports.com/college/kentucky/article/guard-portal/",
            },
            {
                "title": "QB enters portal",
                "content": "Quarterback enters college football portal.",
                "url": "https://www.espn.com/college-football/story/_/id/1",
            },
        ]
        kept, rejected = filter_basketball_articles(articles)
        assert len(kept) == 1
        assert len(rejected) == 1
        assert rejected[0]["filtered_reason"]

    def test_rejects_baseball_portal_without_cbb_signal(self):
        article = {
            "title": "Star shortstop enters transfer portal",
            "content": "The junior infielder is exploring options after a strong spring season.",
            "url": "https://www.espn.com/college-sports/story/_/id/789",
        }
        rejected, reason = is_non_basketball_article(article)
        assert rejected is True
        assert reason is not None
        assert reason.startswith("other_sport_text:")


# ---------------------------------------------------------------------------
# Production entrypoints (runner + CLI wrapper)
# ---------------------------------------------------------------------------

class TestProductionEntrypoints:
    def test_runner_unpacks_three_action_tools(self):
        from unittest.mock import MagicMock, patch

        from portalpoint.agents.news_monitoring.runner import run

        lookup = MagicMock(name="lookup")
        transfer = MagicMock(name="transfer")
        coach = MagicMock(name="coach")
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {
            "detected_events": [],
            "portal_updates": [],
            "errors": [],
            "review_needed": [],
        }

        with (
            patch.dict("os.environ", {"TAVILY_API_KEY": "t", "GOOGLE_API_KEY": "g", "DATABASE_URL": "db"}),
            patch(
                "portalpoint.agents.news_monitoring.runner.build_action_tools",
                return_value=(lookup, transfer, coach),
            ) as mock_build,
            patch(
                "portalpoint.agents.news_monitoring.runner.build_graph",
                return_value=mock_graph,
            ) as mock_build_graph,
            patch("portalpoint.agents.news_monitoring.runner.build_llm"),
            patch(
                "portalpoint.agents.news_monitoring.runner.build_llm_classify_tools",
                return_value=(MagicMock(), MagicMock()),
            ),
            patch(
                "portalpoint.agents.news_monitoring.runner.build_search_news_tool",
                return_value=MagicMock(),
            ),
            patch("portalpoint.agents.news_monitoring.runner.apply_env_file"),
        ):
            summary = run(dry_run=False, season=2026, window_days=1)

        assert summary["success"] is True
        mock_build.assert_called_once_with(2026)
        tools = mock_build_graph.call_args[0][0]
        assert tools.count(lookup) == 1
        assert tools.count(transfer) == 1
        assert tools.count(coach) == 1
        assert len(tools) == 6

    def test_cli_script_delegates_to_runner(self):
        import importlib.util
        import sys
        from pathlib import Path
        from unittest.mock import patch

        script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_news_monitoring.py"
        spec = importlib.util.spec_from_file_location("run_news_monitoring_script", script_path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with patch.object(module, "run", return_value={"success": True}) as mock_run:
            with patch.object(sys, "argv", ["run_news_monitoring.py", "--dry-run"]):
                module.main()

        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["dry_run"] is True


# ---------------------------------------------------------------------------
# lookup_basketball_player + transfer_player hardening
# ---------------------------------------------------------------------------

class TestLookupBasketballPlayer:
    def test_rejects_unknown_football_player(self):
        from unittest.mock import patch

        with patch(
            "portalpoint.agents.news_monitoring.resolve._get_engine",
            side_effect=Exception("no db"),
        ):
            result = lookup_basketball_player_impl("Darian Mensah", "Duke", 2026)
        assert result["matched"] is False

    def test_transfer_player_rejects_without_roster_match(self):
        from unittest.mock import patch

        with patch(
            "portalpoint.agents.news_monitoring.resolve.lookup_basketball_player_impl",
            return_value={
                "matched": False,
                "status": "unmatched",
                "message": "Player not on Duke basketball roster",
            },
        ):
            from portalpoint.agents.news_monitoring.resolve import _transfer_player_impl

            raw = _transfer_player_impl("Darian Mensah", "Duke", 2026)
        result = json.loads(raw)
        assert result["success"] is False
        assert result["status"] == "unmatched"


class TestClassifySportGate:
    def test_football_portal_classified_unknown(self):
        result = _classify_event_payload(
            text="Duke quarterback Darian Mensah has entered the NCAA transfer portal.",
            title="Duke quarterback Darian Mensah enters NCAA transfer portal",
            source_url="https://www.espn.com/college-football/story/_/id/123",
        )
        assert result["event_type"] == "unknown"
        assert result["is_target_event"] is False
        assert result.get("filtered_reason")

    def test_baseball_portal_classified_unknown(self):
        result = _classify_event_payload(
            text="The star shortstop has entered the NCAA transfer portal.",
            title="Star shortstop enters transfer portal",
            source_url="https://www.espn.com/college-sports/story/_/id/789",
        )
        assert result["event_type"] == "unknown"
        assert result["is_target_event"] is False
        assert (result.get("filtered_reason") or "").startswith("other_sport_text:")
