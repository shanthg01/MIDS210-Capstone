"""Unit tests for playing_time.compute_role_fit_override.

Deliberately a separate file from test_playing_time.py — that file has a
module-level autouse fixture that opens a real DB connection for every test
in it, even pure ones. compute_role_fit_override itself needs no DB, so it
lives here to stay in the pure-unit set (no RDS tunnel required).
"""
from __future__ import annotations

import pandas as pd
import pytest

from portalpoint.modeling import playing_time as pt


def _stored_projection_row(**overrides) -> dict:
    base = {
        "expected_minutes": 18.0,
        "expected_usage": 20.0,
        "minutes_ci_lower": 14.0,
        "minutes_ci_upper": 22.0,
        "roster_player_count": 12,
        "roster_open_minutes": 30.0,
        "rotation_probability_model": 0.6,
        "starter_probability_model": 0.3,
    }
    role_fit = pt.compute_role_fit_score(pd.Series(base))
    row = {**base, "role_fit": role_fit}
    row.update(overrides)
    return row


def test_role_fit_override_no_change_returns_stored_value():
    row = _stored_projection_row()
    result = pt.compute_role_fit_override(row, minutes_override=row["expected_minutes"])
    assert result == pytest.approx(row["role_fit"])


def test_role_fit_override_more_minutes_increases_score():
    row = _stored_projection_row()
    increased = pt.compute_role_fit_override(row, minutes_override=30.0)
    decreased = pt.compute_role_fit_override(row, minutes_override=6.0)
    assert increased > row["role_fit"] > decreased


def test_role_fit_override_bounded_0_100():
    row = _stored_projection_row(expected_minutes=2.0, role_fit=1.0)
    assert 0.0 <= pt.compute_role_fit_override(row, minutes_override=40.0) <= 100.0
    row_high = _stored_projection_row(expected_minutes=38.0, role_fit=99.0)
    assert 0.0 <= pt.compute_role_fit_override(row_high, minutes_override=0.0) <= 100.0


def test_role_fit_override_usage_override_applied():
    row = _stored_projection_row()
    # usage moving further from the 20.0 "ideal" (per compute_role_fit_scores'
    # usage_score formula) should not increase the score.
    far_usage = pt.compute_role_fit_override(
        row, minutes_override=row["expected_minutes"], usage_override=35.0
    )
    assert far_usage <= row["role_fit"]


def test_role_fit_override_missing_context_cancels_in_delta():
    # roster_player_count/roster_open_minutes are absent entirely (as real
    # stored rows are, since those two fields aren't persisted) — the delta
    # must still be well-defined and bounded, proving the missing-context
    # defaults cancel out rather than corrupting the result.
    row = {
        "expected_minutes": 18.0,
        "expected_usage": 20.0,
        "minutes_ci_lower": 14.0,
        "minutes_ci_upper": 22.0,
        "role_fit": 55.0,
    }
    result = pt.compute_role_fit_override(row, minutes_override=24.0)
    assert 0.0 <= result <= 100.0
    assert result > row["role_fit"]
