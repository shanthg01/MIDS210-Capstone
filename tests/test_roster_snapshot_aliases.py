from datetime import date

import pytest

from scripts import ingest_roster_snapshots as irs
from scripts.ingest_roster_snapshots import is_freshman_snapshot_class, rostercast_team_name


def test_rostercast_team_aliases_cover_known_barttorvik_names():
    assert rostercast_team_name("UConn") == "Connecticut"
    assert rostercast_team_name("Ole Miss") == "Mississippi"
    assert rostercast_team_name("St. Mary's") == "Saint Mary's"
    assert rostercast_team_name("Cal State Fullerton") == "Cal St. Fullerton"


def test_rostercast_team_name_defaults_to_school_name():
    assert rostercast_team_name("Duke") == "Duke"


def test_freshman_snapshot_class_detection():
    assert is_freshman_snapshot_class("Fr") is True
    assert is_freshman_snapshot_class("Freshman") is True
    assert is_freshman_snapshot_class("Jr") is False
    assert is_freshman_snapshot_class(None) is False


@pytest.mark.asyncio
async def test_freshman_global_match_collision_is_kept_as_new_snapshot_row(monkeypatch):
    def fake_fetch_roster(_http_session, _school_name):
        return [{
            "raw_player_name": "Cameron Williams",
            "class_year": "Fr",
            "height": "6-8",
            "min_pct": 25.0,
            "ortg": None,
            "usage_rate": None,
        }]

    monkeypatch.setattr(irs, "fetch_roster", fake_fetch_roster)

    result = await irs.ingest_team(
        http_session=None,
        db_session=None,
        school_id=3,
        school_name="Duke",
        season=2026,
        snapshot_date=date(2026, 6, 22),
        roster_index={6: [(4386, "Cameron Williams")]},
        player_to_school={4386: 6},
        all_players=[(4386, "Cameron Williams")],
        dry_run=True,
    )

    assert result["players"] == 1
    assert result["records"][0]["raw_player_name"] == "Cameron Williams"
    assert result["records"][0]["player_id"] is None
    assert result["records"][0]["returning_status"] == "new"
