"""Integration tests for news-monitoring DB writes (CI Postgres + seed data)."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text as sql_text
from sqlalchemy.exc import OperationalError

from portalpoint.agents.news_monitoring.resolve import (
    _coach_departure_impl,
    _transfer_player_impl,
    build_action_tools,
    lookup_basketball_player_impl,
)
from portalpoint.modeling.io import apply_env_file, get_sync_engine

apply_env_file()

SEASON = 2026
SCHOOL_ID = 9900301
PLAYER_ID = 101


def _invoke(tool_fn, **kwargs):
    if hasattr(tool_fn, "invoke"):
        return tool_fn.invoke(kwargs)
    return tool_fn(**kwargs)


@pytest.fixture(scope="module")
def engine():
    try:
        eng = get_sync_engine()
        with eng.connect() as conn:
            conn.execute(sql_text("SELECT 1"))
            seed = conn.execute(
                sql_text("SELECT 1 FROM schools WHERE id = :sid"),
                {"sid": SCHOOL_ID},
            ).fetchone()
            if seed is None:
                pytest.skip("CI seed schools not present — run scripts/seed_test_data.py")
        return eng
    except OperationalError:
        pytest.skip("Database not reachable — start Postgres or the RDS tunnel")


@pytest.fixture(autouse=True)
def cleanup_news_agent_rows(engine):
    """Remove news-agent rows so tests are idempotent."""
    yield
    with engine.begin() as conn:
        conn.execute(
            sql_text(
                "DELETE FROM program_events "
                "WHERE source = 'news-agent' AND player_id = :pid"
            ),
            {"pid": PLAYER_ID},
        )
        conn.execute(
            sql_text(
                "DELETE FROM transfer_portal_events "
                "WHERE source = 'news-agent' AND player_id = :pid AND season = :season"
            ),
            {"pid": PLAYER_ID, "season": SEASON},
        )
        conn.execute(
            sql_text(
                "DELETE FROM transfers WHERE player_id = :pid AND season = :season"
            ),
            {"pid": PLAYER_ID, "season": SEASON},
        )
        conn.execute(
            sql_text(
                "UPDATE team_system_profiles "
                "SET stale_flag = false, stale_reason = NULL "
                "WHERE school_id = :school_id"
            ),
            {"school_id": SCHOOL_ID},
        )


class TestTransferPlayerIntegration:
    def test_transfer_player_writes_pipeline_rows(self, engine):
        raw = _transfer_player_impl("Marcus Test Player", "Test University", SEASON)
        result = json.loads(raw)

        assert result["success"] is True
        assert result["player_id"] == PLAYER_ID
        assert result["from_school_id"] == SCHOOL_ID
        assert result["season"] == SEASON

        with engine.connect() as conn:
            pe = conn.execute(
                sql_text(
                    "SELECT event_type, player_id, school_id FROM program_events "
                    "WHERE source = 'news-agent' AND player_id = :pid"
                ),
                {"pid": PLAYER_ID},
            ).fetchone()
            assert pe is not None
            assert pe.event_type == "transfer_entry"

            tpe = conn.execute(
                sql_text(
                    "SELECT status, from_school_id FROM transfer_portal_events "
                    "WHERE source = 'news-agent' AND player_id = :pid AND season = :season"
                ),
                {"pid": PLAYER_ID, "season": SEASON},
            ).fetchone()
            assert tpe is not None
            assert tpe.status == "Entered"
            assert tpe.from_school_id == SCHOOL_ID

            xfer = conn.execute(
                sql_text(
                    "SELECT from_school_id FROM transfers "
                    "WHERE player_id = :pid AND season = :season"
                ),
                {"pid": PLAYER_ID, "season": SEASON},
            ).fetchone()
            assert xfer is not None
            assert xfer.from_school_id == SCHOOL_ID

    def test_transfer_player_is_idempotent(self, engine):
        first = json.loads(_transfer_player_impl("Marcus Test Player", "Test University", SEASON))
        second = json.loads(_transfer_player_impl("Marcus Test Player", "Test University", SEASON))

        assert first["success"] is True
        assert second["success"] is True

        with engine.connect() as conn:
            count = conn.execute(
                sql_text(
                    "SELECT COUNT(*) FROM program_events "
                    "WHERE source = 'news-agent' AND player_id = :pid "
                    "AND event_type = 'transfer_entry'"
                ),
                {"pid": PLAYER_ID},
            ).scalar_one()
            assert count == 1

    def test_transfer_player_rejects_non_roster_player(self, engine):
        raw = _transfer_player_impl("Darian Mensah", "Test University", SEASON)
        result = json.loads(raw)
        assert result["success"] is False
        assert result["status"] in {"unmatched", "no_roster", "not_basketball_roster"}

    def test_lookup_basketball_player_matches_seed_player(self, engine):
        result = lookup_basketball_player_impl("Marcus Test Player", "Test University", SEASON)
        assert result["matched"] is True
        assert result["player_id"] == PLAYER_ID
        assert result["from_school_id"] == SCHOOL_ID

    def test_build_action_tools_uses_season(self, engine):
        _, transfer_tool, _ = build_action_tools(SEASON)
        raw = _invoke(transfer_tool, player_name="Marcus Test Player", school_from="Test University")
        result = json.loads(raw)
        assert result["success"] is True
        assert result["season"] == SEASON


class TestCoachDepartureIntegration:
    def test_coach_departure_flags_stale_profile(self, engine):
        raw = _coach_departure_impl("Test Coach", "Test University", SEASON)
        result = json.loads(raw)

        assert result["status"] == "logged_to_program_events"
        assert result["school_id"] == SCHOOL_ID
        assert result["team_system_profiles_stale_flagged"] >= 1

        with engine.connect() as conn:
            stale = conn.execute(
                sql_text(
                    "SELECT stale_flag, stale_reason FROM team_system_profiles "
                    "WHERE school_id = :school_id AND season = :season"
                ),
                {"school_id": SCHOOL_ID, "season": SEASON},
            ).fetchone()
            assert stale is not None
            assert stale.stale_flag is True
            assert stale.stale_reason == "coaching_change"

            count = conn.execute(
                sql_text(
                    "SELECT COUNT(*) FROM program_events "
                    "WHERE source = 'news-agent' AND school_id = :school_id "
                    "AND event_type = 'coach_departed'"
                ),
                {"school_id": SCHOOL_ID},
            ).scalar_one()
            assert count == 1

        # cleanup coach event for other tests
        with engine.begin() as conn:
            conn.execute(
                sql_text(
                    "DELETE FROM program_events "
                    "WHERE source = 'news-agent' AND school_id = :school_id "
                    "AND event_type = 'coach_departed'"
                ),
                {"school_id": SCHOOL_ID},
            )
