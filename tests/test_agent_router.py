"""Unit tests for api/routers/agent.py's background-task logic.

Pure (no DB, no real Tavily/Gemini calls, no RDS tunnel) — drives the async
_execute_agent_run helper directly via asyncio.run() with a mocked Redis
client and a stubbed news-monitoring cycle, the same way test_news_monitoring.py
mocks the agent's tool layer without hitting live APIs.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from portalpoint.api.routers.agent import _execute_agent_run, _redis_key
from portalpoint.api.schemas.agent import AgentRunRequest


def test_redis_key_is_namespaced():
    key = _redis_key("abc-123")
    assert key == "agent:news_monitoring:run:abc-123"
    assert key != _redis_key("other-run")


def test_execute_agent_run_writes_completed_status_on_success():
    redis = AsyncMock()
    fake_summary = {
        "run_window_start": "2026-07-19T00:00:00",
        "events_detected": 3,
        "portal_updates": 1,
        "errors": [],
        "success": True,
    }
    with patch(
        "portalpoint.api.routers.agent.run_news_monitoring_cycle",
        return_value=fake_summary,
    ) as mock_run:
        asyncio.run(
            _execute_agent_run("run-1", redis, AgentRunRequest(season=2027, dry_run=True))
        )

    mock_run.assert_called_once_with(dry_run=True, use_llm_classifier=True, season=2027)
    redis.set.assert_awaited_once()
    key, payload = redis.set.await_args.args
    assert key == "agent:news_monitoring:run:run-1"
    record = json.loads(payload)
    assert record["status"] == "completed"
    assert record["summary"] == fake_summary
    assert record["error"] is None


def test_execute_agent_run_writes_failed_status_when_summary_has_errors():
    redis = AsyncMock()
    fake_summary = {
        "run_window_start": "2026-07-19T00:00:00",
        "events_detected": 0,
        "portal_updates": 0,
        "errors": ["Missing required environment variables: TAVILY_API_KEY"],
        "success": False,
    }
    with patch(
        "portalpoint.api.routers.agent.run_news_monitoring_cycle",
        return_value=fake_summary,
    ):
        asyncio.run(_execute_agent_run("run-2", redis, AgentRunRequest()))

    record = json.loads(redis.set.await_args.args[1])
    assert record["status"] == "failed"
    assert record["summary"] == fake_summary


def test_execute_agent_run_captures_exception_without_raising():
    redis = AsyncMock()
    with patch(
        "portalpoint.api.routers.agent.run_news_monitoring_cycle",
        side_effect=RuntimeError("Tavily timed out"),
    ):
        # Must not raise — this runs after the HTTP response has already
        # been sent, there's no request left to propagate an error to.
        asyncio.run(_execute_agent_run("run-3", redis, AgentRunRequest()))

    record = json.loads(redis.set.await_args.args[1])
    assert record["status"] == "failed"
    assert record["summary"] is None
    assert "Tavily timed out" in record["error"]


def test_execute_agent_run_passes_optional_overrides_through():
    redis = AsyncMock()
    with patch(
        "portalpoint.api.routers.agent.run_news_monitoring_cycle",
        return_value={"run_window_start": "x", "errors": [], "success": True},
    ) as mock_run:
        asyncio.run(
            _execute_agent_run(
                "run-4", redis,
                AgentRunRequest(season=2026, window_days=3, use_llm=False, dry_run=False),
            )
        )
    mock_run.assert_called_once_with(
        dry_run=False, use_llm_classifier=False, season=2026, window_days=3
    )
