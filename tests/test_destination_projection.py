"""Unit tests for destination_projection.py.

These tests exercise the pure-function layer (no DB required):
  - Delta computation (role/usage, style/skill, roster context, competition level)
  - Delta cap enforcement (per-delta and total)
  - Tier assignment
  - Training example construction
  - CI propagation (width strictly ≥ neutral CI)
  - Rate translation (per-game values scale with pace and minutes)
  - Explanation payload keys
  - Upsert record shape matches expected column count
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from portalpoint.modeling.destination_projection import (
    DELTA_CAPS,
    MODEL_VERSION,
    NEUTRAL_MODEL_PRIORITY,
    ROSTER_CONTEXT_BREAKPOINTS,
    apply_delta_caps,
    assign_competition_tiers,
    build_competition_tier_matrix,
    build_destination_inference_frame,
    build_destination_projection_records,
    build_destination_training_examples,
    build_explanation_payload,
    calibrate_ci_scale,
    compute_cohort_validation,
    compute_competition_level_delta,
    compute_role_usage_delta,
    compute_roster_context_delta,
    compute_style_skill_fit_delta,
    estimate_usage_value_coef,
    fit_role_usage_model,
    propagate_destination_uncertainty,
    run_rolling_origin_cv,
    translate_neutral_to_destination_value,
    translate_rates_to_destination_stats,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def minimal_frame():
    """Minimal inference frame for delta computation tests."""
    return pd.DataFrame({
        "player_id": [1, 2, 3],
        "school_id": [10, 20, 30],
        "value_per_100": [5.0, -1.0, 0.5],
        "value_ci_lower": [3.0, -3.0, -1.0],
        "value_ci_upper": [7.0, 1.0, 2.0],
        "source_usage_rate": [0.22, 0.18, 0.25],
        "expected_usage": [0.20, 0.28, 0.15],
        "expected_minutes": [25.0, 18.0, 30.0],
        "minutes_ci_lower": [20.0, 12.0, 25.0],
        "minutes_ci_upper": [30.0, 24.0, 35.0],
        "gap_match": [75.0, 25.0, 50.0],
        "source_tier": [2, 1, 3],
        "dest_tier": [1, 3, 2],
        "position": ["SG", "PG", "C"],
        "skill_states": [
            {"shooting_3p": 0.38, "passing_creation": 0.7},
            {"shot_creation_usage": 0.6, "block_rim_protection": 0.5},
            {"defensive_rebounding": 0.8, "offensive_rebounding": 0.6},
        ],
        "skill_percentiles": [
            {"shooting_3p": 80.0, "passing_creation": 70.0},
            {"shot_creation_usage": 75.0, "block_rim_protection": 60.0},
            {"defensive_rebounding": 85.0, "offensive_rebounding": 65.0},
        ],
        "uncertainty": [{"residual_std": 1.2}] * 3,
        "projected_rates": [{}] * 3,
        "projected_box_score": [
            {"pts_per_40": 18.0, "ast_per_40": 4.0},
            {"pts_per_40": 12.0, "ast_per_40": 7.0},
            {"pts_per_40": 14.0, "reb_per_40": 10.0},
        ],
        "adj_tempo": [70.0, 65.0, 68.0],
        "team_off_threepr": [0.40, 0.30, 0.25],
        "team_off_rim_pct": [0.28, 0.35, 0.40],
        "team_def_rim_need": [0.3, 0.5, 0.7],
        "team_usage_crowding": [0.2, 0.8, 0.0],
        "team_frontcourt_need": [0.1, 0.0, 0.8],
        "skill_pctile_shooting_3p": [80.0, 30.0, 50.0],
        "skill_pctile_passing_creation": [70.0, 55.0, 40.0],
        "skill_pctile_shot_creation_usage": [55.0, 75.0, 45.0],
        "skill_pctile_block_rim_protection": [40.0, 60.0, 70.0],
        "skill_pctile_offensive_rebounding": [35.0, 40.0, 65.0],
        "skill_pctile_defensive_rebounding": [30.0, 45.0, 85.0],
        "data_quality_flags": [None, None, None],
    })


@pytest.fixture
def minimal_training_df():
    """Minimal training frame for tier matrix and role model tests."""
    np.random.seed(42)
    n = 80
    source_usage = np.random.uniform(0.15, 0.30, n)
    dest_usage = source_usage + np.random.normal(0, 0.05, n)
    neutral_val = np.random.normal(0, 3, n)
    dest_rapm = neutral_val + (dest_usage - source_usage) * 8 + np.random.normal(0, 1.5, n)
    tiers = np.random.choice([1, 2, 3, 4], size=n)
    dest_tiers = np.random.choice([1, 2, 3, 4], size=n)
    return pd.DataFrame({
        "player_id": range(n),
        "from_school_id": range(100, 100 + n),
        "to_school_id": range(200, 200 + n),
        "dest_season": np.random.choice([2024, 2025, 2026], n),
        "source_season": np.random.choice([2023, 2024, 2025], n),
        "source_usage_rate": source_usage,
        "dest_usage_rate": dest_usage,
        "neutral_value": neutral_val,
        "dest_off_rapm": dest_rapm * 0.6,
        "dest_def_rapm": dest_rapm * 0.4,
        "dest_total_rapm": dest_rapm,
        "source_adj_em": np.random.normal(0, 10, n),
        "dest_adj_em": np.random.normal(0, 10, n),
        "dest_three_point_rate": np.random.uniform(0.25, 0.45, n),
        "position": np.random.choice(["PG", "SG", "SF", "PF", "C"], n),
        "value_delta": dest_rapm - neutral_val,
        "usage_delta": dest_usage - source_usage,
        "source_tier": tiers,
        "dest_tier": dest_tiers,
        "skill_states": [{"shooting_3p": 0.35, "passing_creation": 0.5}] * n,
        "skill_percentiles": [{"shooting_3p": 55.0, "passing_creation": 50.0}] * n,
        "neutral_model_version": ["player-projection-phase2a-v1"] * n,
    })


# ---------------------------------------------------------------------------
# Tier assignment
# ---------------------------------------------------------------------------

class TestCompetitionTiers:
    def test_returns_series_of_int(self):
        em = pd.Series([10.0, 5.0, 0.0, -5.0, -10.0])
        seasons = pd.Series([2026] * 5)
        tiers = assign_competition_tiers(em, seasons)
        assert tiers.dtype in (np.dtype("int64"), pd.Int64Dtype())

    def test_tier_range_1_to_4(self):
        em = pd.Series(np.linspace(-20, 20, 100))
        seasons = pd.Series([2026] * 100)
        tiers = assign_competition_tiers(em, seasons)
        assert tiers.min() >= 1
        assert tiers.max() <= 4

    def test_highest_em_is_tier_1(self):
        em = pd.Series([20.0, 0.0, -5.0, -15.0])
        seasons = pd.Series([2026] * 4)
        tiers = assign_competition_tiers(em, seasons)
        assert int(tiers.iloc[0]) == 1

    def test_lowest_em_is_tier_4(self):
        # Uses percent_rank semantics: the minimum gets percentile 0.0.
        em = pd.Series([20.0, 0.0, -5.0, -15.0])
        seasons = pd.Series([2026] * 4)
        tiers = assign_competition_tiers(em, seasons)
        assert int(tiers.iloc[3]) == 4

    def test_per_season_independence(self):
        # Season A: all high-em; Season B: all low-em → within each season, tiers still span
        em = pd.Series([50.0, 45.0, 40.0, 35.0, 5.0, 0.0, -5.0, -10.0])
        seasons = pd.Series([2025, 2025, 2025, 2025, 2026, 2026, 2026, 2026])
        tiers = assign_competition_tiers(em, seasons)
        # Within 2025: should have tier 1 for index 0
        assert int(tiers.iloc[0]) == 1
        # Within 2026: should have tier 1 for index 4 (highest in that season)
        assert int(tiers.iloc[4]) == 1


# ---------------------------------------------------------------------------
# Role / Usage Delta
# ---------------------------------------------------------------------------

class TestRoleUsageDelta:
    def test_heuristic_sign_matches_usage_direction(self, minimal_frame):
        # Player 2 has dest_usage > source_usage — heuristic should give positive delta
        delta = compute_role_usage_delta(minimal_frame, None, None, [])
        # Player at index 1 goes from 0.18 → 0.28: usage up → delta should be > 0
        assert float(delta.iloc[1]) > 0

    def test_heuristic_bounded_by_cap(self, minimal_frame):
        # Extreme usage swings should be clipped to the cap
        extreme = minimal_frame.copy()
        extreme["expected_usage"] = 0.5   # massive usage increase
        delta = compute_role_usage_delta(extreme, None, None, [])
        assert (delta.abs() <= DELTA_CAPS["role_usage_delta"] + 1e-9).all()

    def test_model_path_fits_and_predicts(self, minimal_training_df):
        model, scaler, features, resid_std = fit_role_usage_model(minimal_training_df)
        assert model is not None
        assert scaler is not None
        assert len(features) > 0
        assert resid_std > 0

    def test_model_prefers_residual_role_target_when_available(self):
        rng = np.random.default_rng(42)
        n = 80
        usage_delta = np.linspace(-0.08, 0.08, n)
        source_usage = np.full(n, 0.22)
        training = pd.DataFrame({
            "source_usage_rate": source_usage,
            "dest_usage_rate": source_usage + usage_delta,
            "neutral_value": rng.normal(0, 1, n),
            "value_delta": -5.0 * usage_delta,
            "role_usage_target": 5.0 * usage_delta,
            "position": ["SG"] * n,
            "skill_states": [{}] * n,
        })
        model, scaler, features, _ = fit_role_usage_model(training)
        frame = pd.DataFrame({
            "source_usage_rate": [0.20, 0.20],
            "expected_usage": [0.16, 0.24],
            "value_per_100": [0.0, 0.0],
            "skill_states": [{}, {}],
            "position": ["SG", "SG"],
        })
        delta = compute_role_usage_delta(frame, model, scaler, features)
        assert delta.iloc[0] < 0
        assert delta.iloc[1] > 0

    def test_returns_none_when_insufficient_data(self):
        tiny_df = pd.DataFrame({
            "source_usage_rate": [0.20],
            "dest_usage_rate": [0.25],
            "neutral_value": [1.0],
            "value_delta": [0.5],
            "position": ["PG"],
            "skill_states": [{"shooting_3p": 0.35}],
        })
        model, scaler, features, resid_std = fit_role_usage_model(tiny_df)
        assert model is None
        assert resid_std == 0.0

    def test_model_prediction_bounded_by_cap(self, minimal_frame, minimal_training_df):
        model, scaler, features, _ = fit_role_usage_model(minimal_training_df)
        delta = compute_role_usage_delta(minimal_frame, model, scaler, features)
        assert (delta.abs() <= DELTA_CAPS["role_usage_delta"] + 1e-9).all()

    def test_output_index_matches_input(self, minimal_frame):
        delta = compute_role_usage_delta(minimal_frame, None, None, [])
        assert list(delta.index) == list(minimal_frame.index)


# ---------------------------------------------------------------------------
# Style / Skill Fit Delta
# ---------------------------------------------------------------------------

class TestStyleSkillFitDelta:
    def test_output_bounded_by_cap(self, minimal_frame):
        delta = compute_style_skill_fit_delta(minimal_frame)
        assert (delta.abs() <= DELTA_CAPS["style_skill_fit_delta"] + 1e-9).all()

    def test_missing_columns_fall_back_to_zero(self):
        sparse_frame = pd.DataFrame({"player_id": [1, 2], "school_id": [10, 20]})
        delta = compute_style_skill_fit_delta(sparse_frame)
        assert (delta.abs() < 1e-9).all()

    def test_shooter_in_high_3pa_team_positive_delta(self, minimal_frame):
        # Row 0: shooting_3p pctile=80 in high-3PA team (0.40) → positive style contribution
        delta = compute_style_skill_fit_delta(minimal_frame)
        # At minimum, the 3P interaction for row 0 should push delta upward vs row 1 (pctile=30)
        assert float(delta.iloc[0]) > float(delta.iloc[1])

    def test_output_index_matches_input(self, minimal_frame):
        delta = compute_style_skill_fit_delta(minimal_frame)
        assert list(delta.index) == list(minimal_frame.index)

    def test_empty_frame_returns_empty(self):
        delta = compute_style_skill_fit_delta(pd.DataFrame())
        assert len(delta) == 0


# ---------------------------------------------------------------------------
# Roster Context Delta
# ---------------------------------------------------------------------------

class TestRosterContextDelta:
    def test_gap_match_50_gives_zero_delta(self):
        frame = pd.DataFrame({"gap_match": [50.0]})
        delta = compute_roster_context_delta(frame)
        assert abs(float(delta.iloc[0])) < 1e-9

    def test_high_gap_match_positive_delta(self):
        frame = pd.DataFrame({"gap_match": [100.0]})
        delta = compute_roster_context_delta(frame)
        assert float(delta.iloc[0]) > 0

    def test_low_gap_match_negative_delta(self):
        frame = pd.DataFrame({"gap_match": [0.0]})
        delta = compute_roster_context_delta(frame)
        assert float(delta.iloc[0]) < 0

    def test_monotone_in_gap_match(self):
        frame = pd.DataFrame({"gap_match": [0.0, 25.0, 50.0, 75.0, 100.0]})
        delta = compute_roster_context_delta(frame)
        assert (delta.diff().dropna() >= 0).all()

    def test_bounded_by_cap(self, minimal_frame):
        delta = compute_roster_context_delta(minimal_frame)
        assert (delta.abs() <= DELTA_CAPS["roster_context_delta"] + 1e-9).all()

    def test_missing_gap_match_falls_back_to_zero(self):
        frame = pd.DataFrame({"player_id": [1, 2]})
        delta = compute_roster_context_delta(frame)
        assert (delta.abs() < 1e-9).all()


# ---------------------------------------------------------------------------
# Competition Level Delta
# ---------------------------------------------------------------------------

class TestCompetitionLevelDelta:
    def test_moving_up_in_competition_negative_delta(self, minimal_training_df):
        mean_mat, std_mat = build_competition_tier_matrix(minimal_training_df)
        # Tier 4→1 (low-major to high-major) = hardest upgrade; expect negative mean delta
        tier41 = float(mean_mat.loc[4, 1])
        assert tier41 < 0

    def test_moving_down_in_competition_positive_delta(self, minimal_training_df):
        mean_mat, std_mat = build_competition_tier_matrix(minimal_training_df)
        tier14 = float(mean_mat.loc[1, 4])
        assert tier14 > 0

    def test_zero_delta_for_same_tier(self, minimal_training_df):
        mean_mat, std_mat = build_competition_tier_matrix(minimal_training_df)
        frame = pd.DataFrame({
            "source_tier": [2, 2], "dest_tier": [2, 2]
        })
        delta = compute_competition_level_delta(frame, mean_mat, std_mat)
        assert (delta.abs() < 1e-9).all()

    def test_delta_bounded_by_cap(self, minimal_frame, minimal_training_df):
        mean_mat, std_mat = build_competition_tier_matrix(minimal_training_df)
        delta = compute_competition_level_delta(minimal_frame, mean_mat, std_mat)
        assert (delta.abs() <= DELTA_CAPS["competition_level_delta"] + 1e-9).all()

    def test_missing_tiers_fall_back(self, minimal_training_df):
        mean_mat, std_mat = build_competition_tier_matrix(minimal_training_df)
        frame = pd.DataFrame({"player_id": [1, 2]})  # no tier columns
        delta = compute_competition_level_delta(frame, mean_mat, std_mat)
        assert len(delta) == 2


# ---------------------------------------------------------------------------
# Delta Caps
# ---------------------------------------------------------------------------

class TestApplyDeltaCaps:
    def test_total_cap_enforced(self):
        df = pd.DataFrame({
            "role_usage_delta": [1.5],
            "style_skill_fit_delta": [0.8],
            "roster_context_delta": [0.6],
            "competition_level_delta": [0.9],
        })
        result = apply_delta_caps(df)
        assert abs(float(result["total_context_delta"].iloc[0])) <= DELTA_CAPS["total_context_delta"] + 1e-9

    def test_per_delta_caps_enforced(self):
        df = pd.DataFrame({
            "role_usage_delta": [5.0],
            "style_skill_fit_delta": [5.0],
            "roster_context_delta": [5.0],
            "competition_level_delta": [5.0],
        })
        result = apply_delta_caps(df)
        assert abs(float(result["role_usage_delta"].iloc[0])) <= DELTA_CAPS["role_usage_delta"] + 1e-9
        assert abs(float(result["style_skill_fit_delta"].iloc[0])) <= DELTA_CAPS["style_skill_fit_delta"] + 1e-9

    def test_small_deltas_not_scaled(self):
        df = pd.DataFrame({
            "role_usage_delta": [0.1],
            "style_skill_fit_delta": [0.1],
            "roster_context_delta": [0.1],
            "competition_level_delta": [0.1],
        })
        result = apply_delta_caps(df)
        assert abs(float(result["total_context_delta"].iloc[0]) - 0.4) < 1e-9

    def test_symmetric_cap_both_directions(self):
        pos_df = pd.DataFrame({
            "role_usage_delta": [2.0], "style_skill_fit_delta": [2.0],
            "roster_context_delta": [2.0], "competition_level_delta": [2.0],
        })
        neg_df = pd.DataFrame({
            "role_usage_delta": [-2.0], "style_skill_fit_delta": [-2.0],
            "roster_context_delta": [-2.0], "competition_level_delta": [-2.0],
        })
        pos_result = apply_delta_caps(pos_df)
        neg_result = apply_delta_caps(neg_df)
        assert abs(float(pos_result["total_context_delta"].iloc[0]) + float(neg_result["total_context_delta"].iloc[0])) < 1e-9


# ---------------------------------------------------------------------------
# Value Translation
# ---------------------------------------------------------------------------

class TestValueTranslation:
    def test_destination_value_equals_neutral_plus_delta(self, minimal_frame):
        df = minimal_frame.copy()
        df["total_context_delta"] = 0.5
        result = translate_neutral_to_destination_value(df)
        expected = df["value_per_100"] + 0.5
        pd.testing.assert_series_equal(result["destination_value_per_100"], expected, check_names=False)

    def test_zero_delta_preserves_neutral_value(self, minimal_frame):
        df = minimal_frame.copy()
        df["total_context_delta"] = 0.0
        result = translate_neutral_to_destination_value(df)
        pd.testing.assert_series_equal(
            result["destination_value_per_100"], df["value_per_100"], check_names=False
        )


# ---------------------------------------------------------------------------
# Uncertainty Propagation
# ---------------------------------------------------------------------------

class TestUncertaintyPropagation:
    def test_destination_ci_wider_than_neutral(self, minimal_frame):
        df = minimal_frame.copy()
        df["destination_value_per_100"] = df["value_per_100"]
        result = propagate_destination_uncertainty(df, role_usage_residual_std=1.5)
        neutral_width = df["value_ci_upper"] - df["value_ci_lower"]
        dest_width = result["value_ci_upper"] - result["value_ci_lower"]
        assert (dest_width >= neutral_width - 1e-9).all()

    def test_ci_symmetric_around_destination_value(self, minimal_frame):
        df = minimal_frame.copy()
        df["destination_value_per_100"] = df["value_per_100"] + 0.3
        result = propagate_destination_uncertainty(df, role_usage_residual_std=1.0)
        upper_gap = result["value_ci_upper"] - result["destination_value_per_100"]
        lower_gap = result["destination_value_per_100"] - result["value_ci_lower"]
        pd.testing.assert_series_equal(upper_gap, lower_gap, check_names=False, rtol=1e-6)

    def test_ci_positive_width_always(self, minimal_frame):
        df = minimal_frame.copy()
        df["destination_value_per_100"] = df["value_per_100"]
        result = propagate_destination_uncertainty(df, role_usage_residual_std=0.0)
        assert (result["value_ci_upper"] > result["value_ci_lower"]).all()

    def test_calibration_scale_above_one_widens_ci(self, minimal_frame):
        df = minimal_frame.copy()
        df["destination_value_per_100"] = df["value_per_100"]
        result_1x = propagate_destination_uncertainty(df, role_usage_residual_std=1.0, ci_calibration_scale=1.0)
        result_2x = propagate_destination_uncertainty(df, role_usage_residual_std=1.0, ci_calibration_scale=2.0)
        width_1x = result_1x["value_ci_upper"] - result_1x["value_ci_lower"]
        width_2x = result_2x["value_ci_upper"] - result_2x["value_ci_lower"]
        assert (width_2x > width_1x).all()


# ---------------------------------------------------------------------------
# CI Calibration
# ---------------------------------------------------------------------------

class TestCalibrateCiScale:
    def test_returns_1_when_coverage_already_met(self):
        df = pd.DataFrame({
            "dest_total_rapm": np.linspace(-5, 5, 50),
            "value_ci_lower": np.linspace(-5, 5, 50) - 3.0,
            "value_ci_upper": np.linspace(-5, 5, 50) + 3.0,
            "destination_value_per_100": np.linspace(-5, 5, 50),
        })
        scale = calibrate_ci_scale(df, actual_col="dest_total_rapm")
        assert scale == 1.0

    def test_returns_1_on_insufficient_rows(self):
        df = pd.DataFrame({
            "dest_total_rapm": [1.0],
            "value_ci_lower": [0.5],
            "value_ci_upper": [1.5],
            "destination_value_per_100": [1.0],
        })
        scale = calibrate_ci_scale(df)
        assert scale == 1.0

    def test_scale_above_one_when_undercoverage(self):
        center = np.linspace(-5, 5, 60)
        df = pd.DataFrame({
            "dest_total_rapm": center + np.random.normal(0, 3, 60),
            "value_ci_lower": center - 0.1,
            "value_ci_upper": center + 0.1,
            "destination_value_per_100": center,
        })
        scale = calibrate_ci_scale(df, nominal_coverage=0.80)
        assert scale > 1.0


# ---------------------------------------------------------------------------
# Rate Translation
# ---------------------------------------------------------------------------

class TestRateTranslation:
    def test_per_game_uses_minutes_not_possessions(self, minimal_frame):
        # projected_box_score fields are per-40-minute rates.
        # pts_per_game = pts_per_40 * (minutes / 40) * usage_factor — pace irrelevant.
        df = minimal_frame.head(1).copy()
        df["adj_tempo"] = 80.0
        df["expected_minutes"] = 20.0
        df["expected_usage"] = df["source_usage_rate"]  # usage_factor = 1.0
        df["destination_value_per_100"] = df["value_per_100"]
        result = translate_rates_to_destination_stats(df, pd.DataFrame())
        box = result["destination_box_score"].iloc[0]
        # pts_per_40=18.0, minutes=20, usage_factor=1.0 → 18.0 * (20/40) * 1.0 = 9.0
        assert box["pts_per_game"] == pytest.approx(9.0)
        # projected_possessions still uses pace: 80 * (20/40) = 40
        assert result["projected_possessions"].iloc[0] == pytest.approx(40.0)

    def test_expected_usage_changes_scoring_projection(self, minimal_frame):
        df = pd.concat([minimal_frame.head(1), minimal_frame.head(1)], ignore_index=True)
        df["adj_tempo"] = 70.0
        df["expected_minutes"] = 20.0
        df["source_usage_rate"] = 0.20
        df["expected_usage"] = [0.16, 0.24]
        df["destination_value_per_100"] = df["value_per_100"]
        result = translate_rates_to_destination_stats(df, pd.DataFrame())
        low_box = result["destination_box_score"].iloc[0]
        high_box = result["destination_box_score"].iloc[1]
        # Same player (pts_per_40=18), same minutes — only usage_factor differs
        # low: 18 * 0.5 * 0.80 = 7.2; high: 18 * 0.5 * 1.20 = 10.8
        assert high_box["pts_per_game"] > low_box["pts_per_game"]
        assert result["box_score_adjustments"].iloc[0]["usage_factor"] == pytest.approx(0.8)
        assert result["box_score_adjustments"].iloc[1]["usage_factor"] == pytest.approx(1.2)

    def test_pace_affects_possessions_not_per_game_stats(self, minimal_frame):
        # Per-40 → per-game conversion only depends on minutes, not pace.
        # Pace drives projected_possessions (and therefore destination_total_value),
        # but not the per-game box-score stats.
        df = minimal_frame.copy()
        df["destination_value_per_100"] = df["value_per_100"]
        df_slow = df.copy()
        df_slow["adj_tempo"] = 60.0
        df_fast = df.copy()
        df_fast["adj_tempo"] = 80.0
        result_slow = translate_rates_to_destination_stats(df_slow, pd.DataFrame())
        result_fast = translate_rates_to_destination_stats(df_fast, pd.DataFrame())
        slow_box = result_slow["destination_box_score"].iloc[0]
        fast_box = result_fast["destination_box_score"].iloc[0]
        if slow_box and fast_box:
            k = list(slow_box.keys())[0]
            assert float(fast_box[k]) == pytest.approx(float(slow_box[k]))
        # But possessions — and therefore total value — DO scale with pace
        assert (
            result_fast["projected_possessions"].iloc[0]
            > result_slow["projected_possessions"].iloc[0]
        )

    def test_expected_minutes_zero_gives_zero_per_game(self, minimal_frame):
        df = minimal_frame.copy()
        df["expected_minutes"] = 0.0
        df["destination_value_per_100"] = df["value_per_100"]
        result = translate_rates_to_destination_stats(df, pd.DataFrame())
        for box in result["destination_box_score"]:
            for v in box.values():
                assert abs(float(v)) < 1e-9

    def test_destination_total_value_proportional_to_value_per_100(self, minimal_frame):
        df = minimal_frame.copy()
        df["adj_tempo"] = 70.0
        df["expected_minutes"] = 20.0
        df["destination_value_per_100"] = df["value_per_100"]
        result = translate_rates_to_destination_stats(df, pd.DataFrame())
        # destination_total_value should be proportional to destination_value_per_100
        ratio = result["destination_total_value"] / result["destination_value_per_100"]
        # All rows have same pace and minutes → same ratio
        assert ratio.std() < 1e-9


# ---------------------------------------------------------------------------
# Explanation Payload
# ---------------------------------------------------------------------------

class TestExplanationPayload:
    def test_required_keys_present(self, minimal_frame):
        df = minimal_frame.copy()
        for col in ["role_usage_delta", "style_skill_fit_delta", "roster_context_delta",
                    "competition_level_delta", "total_context_delta", "destination_value_per_100"]:
            df[col] = 0.0
        explanations = build_explanation_payload(df, source_season=2026, target_season=2027)
        required_keys = {
            "neutral_value_per_100", "role_usage_delta", "style_skill_fit_delta",
            "roster_context_delta", "competition_level_delta", "total_context_delta",
            "destination_adjusted_value_per_100", "gap_match", "scheme_fit",
            "source_usage_rate", "dest_expected_usage", "dest_expected_minutes",
            "projected_possessions", "projected_total_value", "uncertainty_adjustment",
            "source_tier", "dest_tier", "source_season", "target_season",
            "neutral_model_version", "minutes_source_model_version",
            "playing_time_model_version", "team_style_features_used",
            "box_score_adjustments", "uncertainty_components",
        }
        for _, row_payload in explanations.items():
            assert required_keys.issubset(set(row_payload.keys()))

    def test_seasons_correct_in_payload(self, minimal_frame):
        df = minimal_frame.copy()
        for col in ["role_usage_delta", "style_skill_fit_delta", "roster_context_delta",
                    "competition_level_delta", "total_context_delta", "destination_value_per_100"]:
            df[col] = 0.0
        explanations = build_explanation_payload(df, source_season=2026, target_season=2027)
        for _, p in explanations.items():
            assert p["source_season"] == 2026
            assert p["target_season"] == 2027


# ---------------------------------------------------------------------------
# Upsert Record Shape
# ---------------------------------------------------------------------------

class TestBuildDestinationProjectionRecords:
    def test_record_count_matches_frame_rows(self, minimal_frame):
        df = minimal_frame.copy()
        for col in ["role_usage_delta", "style_skill_fit_delta", "roster_context_delta",
                    "competition_level_delta", "total_context_delta"]:
            df[col] = 0.0
        df["destination_value_per_100"] = df["value_per_100"]
        df["destination_box_score"] = [{}] * len(df)
        df["destination_total_value"] = df["value_per_100"]
        explanations = build_explanation_payload(df, source_season=2026, target_season=2027)
        records = build_destination_projection_records(df, explanations, 2026, 2027)
        assert len(records) == len(df)

    def test_record_tuple_has_19_elements(self, minimal_frame):
        df = minimal_frame.copy()
        for col in ["role_usage_delta", "style_skill_fit_delta", "roster_context_delta",
                    "competition_level_delta", "total_context_delta"]:
            df[col] = 0.0
        df["destination_value_per_100"] = df["value_per_100"]
        df["destination_box_score"] = [{}] * len(df)
        df["destination_total_value"] = df["value_per_100"]
        explanations = build_explanation_payload(df, source_season=2026, target_season=2027)
        records = build_destination_projection_records(df, explanations, 2026, 2027)
        # 18 columns in the INSERT statement
        assert len(records[0]) == 18

    def test_school_id_is_in_record(self, minimal_frame):
        df = minimal_frame.copy()
        for col in ["role_usage_delta", "style_skill_fit_delta", "roster_context_delta",
                    "competition_level_delta", "total_context_delta"]:
            df[col] = 0.0
        df["destination_value_per_100"] = df["value_per_100"]
        df["destination_box_score"] = [{}] * len(df)
        df["destination_total_value"] = df["value_per_100"]
        explanations = build_explanation_payload(df, source_season=2026, target_season=2027)
        records = build_destination_projection_records(df, explanations, 2026, 2027)
        # school_id is the 2nd element (index 1) and model_version is the 16th (index 15)
        assert int(records[0][1]) == 10   # first row school_id = 10
        assert records[0][15] == MODEL_VERSION

    def test_projection_mode_is_destination(self, minimal_frame):
        df = minimal_frame.copy()
        for col in ["role_usage_delta", "style_skill_fit_delta", "roster_context_delta",
                    "competition_level_delta", "total_context_delta"]:
            df[col] = 0.0
        df["destination_value_per_100"] = df["value_per_100"]
        df["destination_box_score"] = [{}] * len(df)
        df["destination_total_value"] = df["value_per_100"]
        explanations = build_explanation_payload(df, source_season=2026, target_season=2027)
        records = build_destination_projection_records(df, explanations, 2026, 2027)
        # projection_mode is 4th element (index 3)
        assert records[0][3] == "destination"


# ---------------------------------------------------------------------------
# Training example construction
# ---------------------------------------------------------------------------

class TestBuildDestinationTrainingExamples:
    def test_value_delta_column_added(self):
        df = pd.DataFrame({
            "player_id": [1],
            "dest_off_rapm": [2.0],
            "dest_def_rapm": [-1.0],
            "dest_total_rapm": [1.0],
            "neutral_value": [0.5],
            "source_usage_rate": [0.22],
            "dest_usage_rate": [0.24],
            "source_adj_em": [5.0],
            "dest_adj_em": [10.0],
            "dest_season": [2025],
            "source_season": [2024],
            "position": ["PG"],
        })
        result = build_destination_training_examples(df)
        assert "value_delta" in result.columns
        assert abs(float(result["value_delta"].iloc[0]) - 0.5) < 1e-9

    def test_usage_delta_computed(self):
        df = pd.DataFrame({
            "player_id": [1],
            "dest_off_rapm": [2.0],
            "dest_def_rapm": [-1.0],
            "dest_total_rapm": [1.0],
            "neutral_value": [0.5],
            "source_usage_rate": [0.20],
            "dest_usage_rate": [0.28],
            "source_adj_em": [5.0],
            "dest_adj_em": [10.0],
            "dest_season": [2025],
            "source_season": [2024],
            "position": ["SG"],
        })
        result = build_destination_training_examples(df)
        assert "usage_delta" in result.columns
        assert abs(float(result["usage_delta"].iloc[0]) - 0.08) < 1e-6

    def test_percentage_usage_delta_normalized(self):
        df = pd.DataFrame({
            "player_id": [1],
            "dest_off_rapm": [2.0],
            "dest_def_rapm": [-1.0],
            "dest_total_rapm": [1.0],
            "neutral_value": [0.5],
            "source_usage_rate": [20.0],
            "dest_usage_rate": [28.0],
            "source_adj_em": [5.0],
            "dest_adj_em": [10.0],
            "dest_season": [2025],
            "source_season": [2024],
            "position": ["SG"],
        })
        result = build_destination_training_examples(df)
        assert abs(float(result["usage_delta"].iloc[0]) - 0.08) < 1e-6

    def test_preserves_precomputed_full_distribution_tiers(self):
        df = pd.DataFrame({
            "player_id": [1],
            "dest_off_rapm": [2.0],
            "dest_def_rapm": [-1.0],
            "dest_total_rapm": [1.0],
            "neutral_value": [0.5],
            "source_usage_rate": [0.20],
            "dest_usage_rate": [0.28],
            "source_adj_em": [-30.0],
            "dest_adj_em": [35.0],
            "source_tier": [4],
            "dest_tier": [1],
            "dest_season": [2025],
            "source_season": [2024],
            "position": ["SG"],
        })
        result = build_destination_training_examples(df)
        assert int(result["source_tier"].iloc[0]) == 4
        assert int(result["dest_tier"].iloc[0]) == 1

    def test_empty_input_returns_empty(self):
        result = build_destination_training_examples(pd.DataFrame())
        assert result.empty


class TestBuildDestinationInferenceFrame:
    def test_source_and_destination_tiers_use_team_context_distribution(self):
        neutral_df = pd.DataFrame({
            "player_id": [1],
            "neutral_season": [2027],
            "value_per_100": [2.0],
            "value_ci_lower": [1.0],
            "value_ci_upper": [3.0],
            "projected_rates": [{}],
            "projected_box_score": [{}],
            "skill_states": [{}],
            "skill_percentiles": [{}],
            "uncertainty": [{}],
            "neutral_model_version": ["player-proj-phase2a-fcast-v1"],
        })
        pt_df = pd.DataFrame({
            "player_id": [1],
            "school_id": [10],
            "season": [2027],
            "expected_minutes": [20.0],
            "expected_minutes_share": [0.5],
            "minutes_ci_lower": [15.0],
            "minutes_ci_upper": [25.0],
            "expected_usage": [0.2],
            "usage_role": ["connector"],
            "usage_role_confidence": [0.8],
            "displaced_minutes": [5.0],
            "data_quality_flags": [None],
            "role_fit": [70.0],
        })
        team_context_df = pd.DataFrame({
            "school_id": [99, 20, 30, 10],
            "season": [2026, 2026, 2026, 2026],
            "adj_em": [-20.0, 0.0, 10.0, 30.0],
            "adj_tempo": [65.0, 66.0, 67.0, 68.0],
        })
        source_stats_df = pd.DataFrame({
            "player_id": [1],
            "source_school_id": [99],
            "source_usage_rate": [0.22],
            "source_min_pct": [60.0],
            "source_games_played": [30],
            "position": ["SG"],
        })
        result = build_destination_inference_frame(
            neutral_df=neutral_df,
            pt_df=pt_df,
            team_context_df=team_context_df,
            fit_context_df=pd.DataFrame(),
            roster_df=pd.DataFrame(),
            source_stats_df=source_stats_df,
            target_season=2027,
            source_season=2026,
        )
        assert int(result["source_tier"].iloc[0]) == 4
        assert int(result["dest_tier"].iloc[0]) == 1


# ---------------------------------------------------------------------------
# Integration: end-to-end delta path
# ---------------------------------------------------------------------------

class TestEndToDeltaPath:
    """Verify the full delta → cap → value chain with controlled inputs."""

    def test_full_positive_delta_bounded(self, minimal_frame, minimal_training_df):
        df = minimal_frame.copy()
        # Assign deltas above individual caps → total should still be ≤ 1.50
        df["role_usage_delta"] = 2.0
        df["style_skill_fit_delta"] = 1.5
        df["roster_context_delta"] = 1.0
        df["competition_level_delta"] = 1.5
        result = apply_delta_caps(df)
        assert (result["total_context_delta"] <= DELTA_CAPS["total_context_delta"] + 1e-9).all()

    def test_neutral_value_is_anchor(self, minimal_frame):
        # With zero deltas, destination = neutral
        df = minimal_frame.copy()
        df["total_context_delta"] = 0.0
        translated = translate_neutral_to_destination_value(df)
        pd.testing.assert_series_equal(
            translated["destination_value_per_100"],
            df["value_per_100"],
            check_names=False,
        )

    def test_model_version_constant(self):
        assert MODEL_VERSION == "player-destination-proj-v1"

    def test_neutral_model_priority_nonempty(self):
        assert len(NEUTRAL_MODEL_PRIORITY) >= 2
        assert NEUTRAL_MODEL_PRIORITY[0] == "player-proj-phase2a-fcast-v1"


# ---------------------------------------------------------------------------
# estimate_usage_value_coef
# ---------------------------------------------------------------------------

class TestEstimateUsageValueCoef:
    def _make_frames(self, n: int = 100, coef: float = 10.0):
        np.random.seed(42)
        usage = np.random.uniform(0.10, 0.35, n)
        value = coef * usage + np.random.normal(0, 0.5, n)
        neutral_df = pd.DataFrame({"player_id": range(n), "value_per_100": value})
        source_df = pd.DataFrame({"player_id": range(n), "source_usage_rate": usage})
        return neutral_df, source_df

    def test_returns_fallback_when_neutral_empty(self):
        empty = pd.DataFrame(columns=["player_id", "value_per_100"])
        source = pd.DataFrame({"player_id": [1], "source_usage_rate": [0.20]})
        assert estimate_usage_value_coef(empty, source, fallback=1.5) == 1.5

    def test_returns_fallback_when_source_empty(self):
        neutral = pd.DataFrame({"player_id": [1], "value_per_100": [2.0]})
        empty = pd.DataFrame(columns=["player_id", "source_usage_rate"])
        assert estimate_usage_value_coef(neutral, empty, fallback=1.5) == 1.5

    def test_returns_fallback_when_too_few_rows(self):
        neutral = pd.DataFrame({"player_id": range(5), "value_per_100": [1.0] * 5})
        source = pd.DataFrame({"player_id": range(5), "source_usage_rate": [0.20] * 5})
        assert estimate_usage_value_coef(neutral, source, fallback=2.5) == 2.5

    def test_recovers_approximate_slope(self):
        neutral, source = self._make_frames(n=200, coef=8.0)
        coef = estimate_usage_value_coef(neutral, source, fallback=1.5)
        assert abs(coef - 8.0) < 3.0  # OLS within 3 units of true coef

    def test_recovers_approximate_slope_with_percentage_usage(self):
        neutral, source = self._make_frames(n=200, coef=8.0)
        source["source_usage_rate"] = source["source_usage_rate"] * 100.0
        coef = estimate_usage_value_coef(neutral, source, fallback=1.5)
        assert abs(coef - 8.0) < 3.0

    def test_coef_used_in_heuristic(self):
        df = pd.DataFrame({
            "source_usage_rate": [0.20],
            "expected_usage": [0.25],
            "value_per_100": [2.0],
        })
        delta_default = compute_role_usage_delta(df, None, None, [], linear_coef=1.5)
        delta_custom = compute_role_usage_delta(df, None, None, [], linear_coef=10.0)
        # delta = (0.25 - 0.20) * coef
        assert abs(float(delta_default.iloc[0]) - 0.075) < 1e-9
        assert abs(float(delta_custom.iloc[0]) - 0.50) < 1e-9

    def test_percentage_usage_coef_used_in_heuristic(self):
        df = pd.DataFrame({
            "source_usage_rate": [20.0],
            "expected_usage": [25.0],
            "value_per_100": [2.0],
        })
        delta = compute_role_usage_delta(df, None, None, [], linear_coef=10.0)
        assert abs(float(delta.iloc[0]) - 0.50) < 1e-9


# ---------------------------------------------------------------------------
# run_rolling_origin_cv
# ---------------------------------------------------------------------------

class TestRunRollingOriginCV:
    def _make_training_df(self, n_per_season: int = 10):
        rows = []
        for season in [2022, 2023, 2024, 2025]:
            for i in range(n_per_season):
                rows.append({
                    "dest_season": season,
                    "source_usage_rate": 0.20 + i * 0.01,
                    "dest_usage_rate": 0.22 + i * 0.01,
                    "neutral_value": float(i),
                    "value_delta": float(i) * 0.1,
                    "dest_off_rapm": float(i) * 0.5,
                    "dest_def_rapm": 0.0,
                    "dest_total_rapm": float(i) * 0.5,
                    "source_adj_em": 10.0,
                    "dest_adj_em": 12.0,
                    "position": "PG",
                })
        return pd.DataFrame(rows)

    def test_returns_cv_skipped_on_empty(self):
        result = run_rolling_origin_cv(pd.DataFrame())
        assert result.get("cv_skipped") == 1.0
        assert result["total_resid_std"] == 999.0

    def test_returns_cv_skipped_single_season(self):
        df = pd.DataFrame({
            "dest_season": [2025, 2025],
            "source_usage_rate": [0.20, 0.22],
            "dest_usage_rate": [0.22, 0.24],
            "neutral_value": [1.0, 2.0],
            "value_delta": [0.5, 1.0],
        })
        result = run_rolling_origin_cv(df)
        assert result.get("cv_skipped") == 1.0

    def test_returns_total_resid_std_with_enough_data(self):
        df = self._make_training_df(n_per_season=15)
        result = run_rolling_origin_cv(df)
        assert "total_resid_std" in result
        assert result["total_resid_std"] >= 0.0

    def test_fold_keys_present_when_cv_runs(self):
        df = self._make_training_df(n_per_season=15)
        result = run_rolling_origin_cv(df, min_eval_rows=2)
        # At least some folds should have run
        fold_keys = [k for k in result if k.startswith("fold") and "rmse" in k]
        assert len(fold_keys) >= 1

    def test_cv_skipped_flag_absent_when_cv_ran(self):
        df = self._make_training_df(n_per_season=15)
        result = run_rolling_origin_cv(df, min_eval_rows=2)
        assert "cv_skipped" not in result or result.get("cv_skipped") != 1.0


# ---------------------------------------------------------------------------
# compute_cohort_validation
# ---------------------------------------------------------------------------

class TestCohortValidation:
    def _make_training_df(self, n_per_season: int = 25):
        """Rich training df with position, tier, and usage columns."""
        rng = np.random.default_rng(42)
        rows = []
        for season in [2022, 2023, 2024, 2025]:
            for i in range(n_per_season):
                rows.append({
                    "dest_season": season,
                    "source_usage_rate": 0.20 + (i % 10) * 0.01,
                    "dest_usage_rate": 0.22 + (i % 10) * 0.01,
                    "neutral_value": float(i % 10),
                    "value_delta": float(i % 10) * 0.1 + rng.normal(0, 0.5),
                    "source_tier": int((i % 4) + 1),
                    "dest_tier": int((i % 4) + 1),
                    "position": ["PG", "SG", "SF", "PF", "C"][i % 5],
                    "source_adj_em": 10.0,
                    "dest_adj_em": 12.0,
                    "dest_total_rapm": float(i % 10) * 0.5,
                })
        return pd.DataFrame(rows)

    def test_empty_df_returns_empty_dict(self):
        result = compute_cohort_validation(pd.DataFrame())
        assert result == {}

    def test_single_season_returns_empty_dict(self):
        df = pd.DataFrame({
            "dest_season": [2025] * 10,
            "source_usage_rate": [0.20] * 10,
            "dest_usage_rate": [0.22] * 10,
            "neutral_value": list(range(10)),
            "value_delta": [0.5] * 10,
        })
        result = compute_cohort_validation(df)
        assert result == {}

    def test_returns_dict(self):
        df = self._make_training_df()
        result = compute_cohort_validation(df)
        assert isinstance(result, dict)

    def test_cohort_n_keys_present(self):
        df = self._make_training_df()
        result = compute_cohort_validation(df)
        # At least some cohort_*_n keys should appear
        n_keys = [k for k in result if k.endswith("_n")]
        assert len(n_keys) >= 3

    def test_cohort_n_values_are_nonneg_ints(self):
        df = self._make_training_df()
        result = compute_cohort_validation(df)
        for k, v in result.items():
            if k.endswith("_n"):
                assert isinstance(v, int) and v >= 0, f"{k}={v}"

    def test_spearman_none_when_below_min_cohort_n(self):
        """Small cohort → spearman and rmse are None (not a KeyError)."""
        df = self._make_training_df(n_per_season=25)
        # wing (SF) has only n_per_season/5 per fold = 5 rows per season;
        # with 4 seasons and rolling folds that's well below min_cohort_n=20
        result = compute_cohort_validation(df, min_cohort_n=200)
        # Every cohort should be below 200 → all spearman entries should be None
        spearman_vals = [v for k, v in result.items() if k.endswith("_spearman")]
        assert all(v is None for v in spearman_vals)

    def test_spearman_in_valid_range_when_above_min_n(self):
        df = self._make_training_df(n_per_season=25)
        result = compute_cohort_validation(df, min_cohort_n=5)
        for k, v in result.items():
            if k.endswith("_spearman") and v is not None:
                assert -1.0 <= v <= 1.0, f"{k}={v}"

    def test_rmse_nonneg_when_above_min_n(self):
        df = self._make_training_df(n_per_season=25)
        result = compute_cohort_validation(df, min_cohort_n=5)
        for k, v in result.items():
            if k.endswith("_rmse") and v is not None:
                assert v >= 0.0, f"{k}={v}"

    def test_tier_cohort_keys_present_when_tier_cols_exist(self):
        df = self._make_training_df()
        result = compute_cohort_validation(df, min_cohort_n=5)
        tier_keys = [k for k in result if "tier_" in k]
        assert len(tier_keys) >= 3  # tier_up_n, tier_same_n, tier_down_n at minimum

    def test_position_cohort_keys_present_when_position_col_exists(self):
        df = self._make_training_df()
        result = compute_cohort_validation(df, min_cohort_n=5)
        pos_keys = [k for k in result if any(p in k for p in ("guard", "wing", "big"))]
        assert len(pos_keys) >= 3  # guard/wing/big _n keys at minimum

    def test_usage_cohort_keys_present(self):
        df = self._make_training_df()
        result = compute_cohort_validation(df, min_cohort_n=5)
        usage_keys = [k for k in result if "usage" in k]
        assert len(usage_keys) >= 2
