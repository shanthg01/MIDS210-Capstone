import asyncio
from datetime import date

from scripts import ingest_roster_snapshots as irs
from scripts.ingest_roster_snapshots import (
    _name_initials,
    _name_initials_match,
    is_freshman_snapshot_class,
    rostercast_team_name,
)


def _snapshot_row(raw_player_name: str, class_year: str) -> dict:
    return {
        "raw_player_name": raw_player_name,
        "class_year": class_year,
        "height": "6-8",
        "min_pct": 25.0,
        "ortg": None,
        "usage_rate": None,
    }


async def _dry_run_one_row(
    monkeypatch,
    *,
    school_id: int,
    school_name: str,
    row: dict,
    roster_index: dict[int, list[tuple[int, str]]],
    player_to_school: dict[int, int],
) -> dict:
    def fake_fetch_roster(_http_session, _school_name):
        return [row]

    all_players = [player for roster in roster_index.values() for player in roster]
    monkeypatch.setattr(irs, "fetch_roster", fake_fetch_roster)

    result = await irs.ingest_team(
        http_session=None,
        db_session=None,
        school_id=school_id,
        school_name=school_name,
        season=2026,
        snapshot_date=date(2026, 6, 22),
        roster_index=roster_index,
        player_to_school=player_to_school,
        all_players=all_players,
        dry_run=True,
    )
    assert result["players"] == 1
    return result["records"][0]


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


def test_name_initials_ignore_suffixes_and_punctuation():
    assert _name_initials("C.J. Cox") == ("c", "c")
    assert _name_initials("Brandon McCoy Jr.") == ("b", "m")
    assert _name_initials("A.J. Wright, III") == ("a", "w")


def test_name_initials_must_match_before_fuzzy_matching():
    assert _name_initials_match("John Blackwell", "John Blackwell") is True
    assert _name_initials_match("Caden Pierce", "Braden Pierce") is False


def test_freshman_global_match_collision_is_kept_as_new_snapshot_row(monkeypatch):
    record = asyncio.run(_dry_run_one_row(
        monkeypatch,
        school_id=3,
        school_name="Duke",
        row=_snapshot_row("Cameron Williams", "Fr"),
        roster_index={6: [(4386, "Cameron Williams")]},
        player_to_school={4386: 6},
    ))

    assert record["raw_player_name"] == "Cameron Williams"
    assert record["player_id"] is None
    assert record["returning_status"] == "new"


def test_global_match_requires_first_and_last_initials(monkeypatch):
    record = asyncio.run(_dry_run_one_row(
        monkeypatch,
        school_id=8,
        school_name="Purdue",
        row=_snapshot_row("Caden Pierce", "Sr"),
        roster_index={99: [(1524, "Braden Pierce")]},
        player_to_school={1524: 99},
    ))

    assert record["raw_player_name"] == "Caden Pierce"
    assert record["player_id"] is None
    assert record["returning_status"] == "new"


def test_cj_cox_matches_as_purdue_returner(monkeypatch):
    record = asyncio.run(_dry_run_one_row(
        monkeypatch,
        school_id=8,
        school_name="Purdue",
        row=_snapshot_row("C.J. Cox", "Jr"),
        roster_index={8: [(2742, "C.J. Cox")]},
        player_to_school={2742: 8},
    ))

    assert record["raw_player_name"] == "C.J. Cox"
    assert record["player_id"] == 2742
    assert record["returning_status"] == "returning"
    assert record["transfer_source_school_id"] is None


def test_patrick_ngongba_matches_as_duke_returner(monkeypatch):
    record = asyncio.run(_dry_run_one_row(
        monkeypatch,
        school_id=3,
        school_name="Duke",
        row=_snapshot_row("Patrick Ngongba", "Jr"),
        roster_index={3: [(2474, "Patrick Ngongba")]},
        player_to_school={2474: 3},
    ))

    assert record["raw_player_name"] == "Patrick Ngongba"
    assert record["player_id"] == 2474
    assert record["returning_status"] == "returning"
    assert record["transfer_source_school_id"] is None


def test_non_freshman_global_match_still_identifies_transfer_in(monkeypatch):
    record = asyncio.run(_dry_run_one_row(
        monkeypatch,
        school_id=3,
        school_name="Duke",
        row=_snapshot_row("John Blackwell", "Sr"),
        roster_index={22: [(1255, "John Blackwell")]},
        player_to_school={1255: 22},
    ))

    assert record["raw_player_name"] == "John Blackwell"
    assert record["player_id"] == 1255
    assert record["returning_status"] == "transfer_in"
    assert record["transfer_source_school_id"] == 22
