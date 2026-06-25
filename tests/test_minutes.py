from portalpoint.modeling.minutes import (
    minutes_per_game_from_min_pct,
    minutes_share_from_min_pct,
    resolved_minutes_per_game,
)


def test_minutes_share_from_min_pct():
    assert minutes_share_from_min_pct(75.0) == 0.75


def test_minutes_per_game_from_min_pct():
    assert minutes_per_game_from_min_pct(75.0) == 30.0


def test_resolved_minutes_per_game_prefers_min_pct_over_stored_value():
    assert resolved_minutes_per_game(75.0, stored_minutes_per_game=2.5) == 30.0


def test_resolved_minutes_per_game_falls_back_when_min_pct_missing():
    assert resolved_minutes_per_game(None, stored_minutes_per_game=18.0) == 18.0
