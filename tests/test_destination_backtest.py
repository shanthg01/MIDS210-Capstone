"""Unit tests for destination_backtest.py — the pure residual/summary layer.

load_backtest_population/load_actual_outcomes/load_projected_outcomes/
enrich_with_cohorts all need a DB connection and aren't unit-tested here,
matching test_destination_projection.py's own convention (only pure
transform functions get unit tests; DB-touching load_* functions don't).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from portalpoint.modeling.destination_backtest import (
    BACKTEST_STATS,
    compute_residuals,
    summarize_residuals,
)


@pytest.fixture
def actual_df():
    return pd.DataFrame({
        "player_id": [1, 2, 3, 4],
        "dest_school_id": [10, 20, 30, 40],
        "dest_season": [2024, 2024, 2025, 2025],
        "games_played": [28, 30, 25, 20],
        "points_per_game": [15.0, 8.0, 0.0, 12.0],
        "rebounds_per_game": [5.0, 3.0, 2.0, 6.0],
        "assists_per_game": [3.0, 6.0, 1.0, 2.0],
        "steals_per_game": [1.0, 1.5, 0.5, 1.0],
        "blocks_per_game": [0.5, 0.2, 0.1, 1.5],
        "turnovers_per_game": [2.0, 2.5, 1.0, 1.8],
    })


@pytest.fixture
def projected_df():
    return pd.DataFrame({
        "player_id": [1, 2, 3, 4],
        "dest_school_id": [10, 20, 30, 40],
        "dest_season": [2024, 2024, 2025, 2025],
        "proj_pts": [12.0, 9.0, 1.0, 10.0],
        "proj_reb": [4.5, 3.5, 2.0, 5.0],
        "proj_ast": [3.5, 5.0, 1.2, 2.5],
        "proj_stl": [0.9, 1.4, 0.6, 0.8],
        "proj_blk": [0.4, 0.3, 0.1, 1.2],
        "proj_tov": [2.2, 2.0, 1.1, 1.5],
    })


class TestComputeResiduals:
    def test_residual_sign_underprojection_positive(self, actual_df, projected_df):
        residual_df = compute_residuals(actual_df, projected_df)
        # Player 1: actual pts 15.0 vs proj 12.0 — model underprojected, residual positive
        assert float(residual_df.loc[residual_df["player_id"] == 1, "residual_pts"].iloc[0]) > 0

    def test_residual_sign_overprojection_negative(self, actual_df, projected_df):
        residual_df = compute_residuals(actual_df, projected_df)
        # Player 2: actual pts 8.0 vs proj 9.0 — model overprojected, residual negative
        assert float(residual_df.loc[residual_df["player_id"] == 2, "residual_pts"].iloc[0]) < 0

    def test_all_stat_columns_present(self, actual_df, projected_df):
        residual_df = compute_residuals(actual_df, projected_df)
        for stat in BACKTEST_STATS:
            assert f"residual_{stat}" in residual_df.columns
            assert f"pct_error_{stat}" in residual_df.columns

    def test_zero_actual_does_not_raise_or_produce_inf(self, actual_df, projected_df):
        # Player 3 has points_per_game=0.0 — division guard should give NaN, not inf/crash.
        residual_df = compute_residuals(actual_df, projected_df)
        pct_error = residual_df.loc[residual_df["player_id"] == 3, "pct_error_pts"].iloc[0]
        assert not np.isinf(pct_error) if pd.notna(pct_error) else True

    def test_inner_join_drops_unmatched_rows(self, actual_df):
        # Projected has no row for player 4 — should be dropped, not raise or fill garbage.
        partial_projected = pd.DataFrame({
            "player_id": [1, 2, 3],
            "dest_school_id": [10, 20, 30],
            "dest_season": [2024, 2024, 2025],
            "proj_pts": [12.0, 9.0, 1.0],
            "proj_reb": [4.5, 3.5, 2.0],
            "proj_ast": [3.5, 5.0, 1.2],
            "proj_stl": [0.9, 1.4, 0.6],
            "proj_blk": [0.4, 0.3, 0.1],
            "proj_tov": [2.2, 2.0, 1.1],
        })
        residual_df = compute_residuals(actual_df, partial_projected)
        assert len(residual_df) == 3
        assert 4 not in residual_df["player_id"].tolist()

    def test_empty_inputs_return_empty(self):
        assert compute_residuals(pd.DataFrame(), pd.DataFrame()).empty
        assert compute_residuals(pd.DataFrame({"a": [1]}), pd.DataFrame()).empty


class TestSummarizeResiduals:
    def test_overall_summary_has_all_stats(self, actual_df, projected_df):
        residual_df = compute_residuals(actual_df, projected_df)
        summary = summarize_residuals(residual_df)
        assert summary["n"] == 4.0
        for stat in BACKTEST_STATS:
            assert f"{stat}_rmse" in summary
            assert f"{stat}_mae" in summary
            assert summary[f"{stat}_rmse"] >= 0
            assert summary[f"{stat}_mae"] >= 0

    def test_rmse_matches_hand_computation_for_pts(self, actual_df, projected_df):
        residual_df = compute_residuals(actual_df, projected_df)
        summary = summarize_residuals(residual_df)
        resid = residual_df["residual_pts"].to_numpy()
        expected_rmse = float(np.sqrt(np.mean(resid ** 2)))
        assert summary["pts_rmse"] == round(expected_rmse, 3)

    def test_grouped_summary_splits_by_column(self, actual_df, projected_df):
        residual_df = compute_residuals(actual_df, projected_df)
        residual_df["season_group"] = residual_df["dest_season"].astype(str)
        summary = summarize_residuals(residual_df, group_by="season_group", min_group_n=1)
        assert set(summary.keys()) == {"2024", "2025"}
        assert summary["2024"]["n"] == 2.0
        assert summary["2025"]["n"] == 2.0

    def test_group_below_min_n_excluded(self, actual_df, projected_df):
        residual_df = compute_residuals(actual_df, projected_df)
        residual_df["season_group"] = residual_df["dest_season"].astype(str)
        summary = summarize_residuals(residual_df, group_by="season_group", min_group_n=3)
        # Each season group only has 2 rows — below the min_group_n=3 floor.
        assert summary == {}

    def test_unknown_group_by_column_returns_empty(self, actual_df, projected_df):
        residual_df = compute_residuals(actual_df, projected_df)
        assert summarize_residuals(residual_df, group_by="not_a_real_column") == {}

    def test_empty_residual_df_returns_empty(self):
        assert summarize_residuals(pd.DataFrame()) == {}
