from scripts.ingest_roster_snapshots import rostercast_team_name


def test_rostercast_team_aliases_cover_known_barttorvik_names():
    assert rostercast_team_name("UConn") == "Connecticut"
    assert rostercast_team_name("Ole Miss") == "Mississippi"
    assert rostercast_team_name("St. Mary's") == "Saint Mary's"
    assert rostercast_team_name("Cal State Fullerton") == "Cal St. Fullerton"


def test_rostercast_team_name_defaults_to_school_name():
    assert rostercast_team_name("Duke") == "Duke"
