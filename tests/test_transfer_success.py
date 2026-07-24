"""Unit tests for Transfer Success (Model 5) EB shrinkage and hyperparameter tuning."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from portalpoint.modeling.transfer_success import (
    CELL_MIN_N,
    DECAY_LAMBDA,
    INFERENCE_CHUNK_SIZE,
    K_CELL_CANDIDATES,
    MODEL_VERSION,
    SHRINKAGE_K,
    apply_projection_covariate_adjustment,
    build_explanation,
    compute_expected_calibration_error,
    compute_success_probability,
    compute_tier_calibration,
    fit_projection_beta,
    iter_scored_active_candidate_chunks,
    label_transfer_success,
    standardize_projection_by_season,
    summarize_calibration_metrics,
    summarize_shrinkage_sample_sizes,
    score_active_candidates,
    run_transfer_success_pipeline,
    tune_transfer_success_hyperparameters,
    write_calibration_artifacts,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _transfer_row(
    *,
    season: int,
    player_cluster: int = 1,
    offense: int = 10,
    defense: int = 20,
    actual: float = 5.0,
    projected: float = 4.0,
) -> dict:
    """Minimal labeled transfer row for pipeline tests."""
    return {
        "season": season,
        "player_cluster": player_cluster,
        "team_offense_cluster_id": offense,
        "team_defense_cluster_id": defense,
        "actual_value_per_100": actual,
        "projected_value_per_100": projected,
        "post_per": np.nan,
        "pre_per": np.nan,
    }


def _labeled_frame(rows: list[dict]) -> pd.DataFrame:
    return label_transfer_success(pd.DataFrame(rows))


# ── Shrinkage formula ────────────────────────────────────────────────────────

class TestShrinkageFormula:
    def test_weight_is_n_over_n_plus_k(self):
        cell_n = np.array([0.9, 3.0, 6.3, 15.0])
        k = 15
        expected = cell_n / (cell_n + k)
        assert np.allclose(expected, [0.0566, 0.1667, 0.2958, 0.5], rtol=1e-2)

    def test_compute_success_probability_sets_shrinkage_w(self):
        # Two seasons: season 1 trains, season 2 scores with prior history.
        df = _labeled_frame([
            _transfer_row(season=2020, player_cluster=1, offense=10, defense=20,
                          actual=6.0, projected=4.0),
            _transfer_row(season=2021, player_cluster=1, offense=10, defense=20,
                          actual=3.0, projected=4.0),
        ])
        scored = compute_success_probability(df, shrinkage_k=15)
        row = scored[scored["season"] == 2021].iloc[0]
        expected_w = row["cell_n"] / (row["cell_n"] + 15)
        assert row["shrinkage_w"] == pytest.approx(expected_w)


# ── Tuning: temporal honesty ─────────────────────────────────────────────────

class TestTuneTemporalHonesty:
    def test_changing_future_labels_does_not_change_past_predictions(self):
        """Expanding window: season S must not see labels from season >= S."""
        base_rows = [
            _transfer_row(season=2020, player_cluster=1, offense=10, defense=20,
                            actual=6.0, projected=4.0),
            _transfer_row(season=2020, player_cluster=1, offense=10, defense=20,
                            actual=2.0, projected=4.0),
            _transfer_row(season=2021, player_cluster=1, offense=10, defense=20,
                            actual=5.0, projected=4.0),
            _transfer_row(season=2022, player_cluster=1, offense=10, defense=20,
                            actual=1.0, projected=4.0),
        ]
        df_a = _labeled_frame(base_rows)
        scored_a = compute_success_probability(df_a, shrinkage_k=5, decay_lambda=1.0)
        p_2021_a = scored_a.loc[scored_a["season"] == 2021, "success_probability"].iloc[0]

        # Flip the 2022 label — must not affect 2021 prediction.
        flipped = [dict(r) for r in base_rows]
        flipped[3] = _transfer_row(season=2022, player_cluster=1, offense=10, defense=20,
                                   actual=10.0, projected=4.0)
        df_b = _labeled_frame(flipped)
        scored_b = compute_success_probability(df_b, shrinkage_k=5, decay_lambda=1.0)
        p_2021_b = scored_b.loc[scored_b["season"] == 2021, "success_probability"].iloc[0]

        assert p_2021_a == pytest.approx(p_2021_b)

    def test_tuning_picks_reasonable_params_on_toy_data(self):
        rows = []
        for season in (2020, 2021, 2022):
            for i in range(8):
                rows.append(_transfer_row(
                    season=season,
                    player_cluster=i % 3 + 1,
                    offense=10 + i % 2,
                    defense=20 + i % 2,
                    actual=5.0 if i % 2 == 0 else 2.0,
                    projected=4.0,
                ))
        df = _labeled_frame(rows)
        best_k, best_lam, grid_df = tune_transfer_success_hyperparameters(
            df, k_candidates=[1, 2, 5], lambda_candidates=[0.9, 1.0],
        )
        assert best_k in (1, 2, 5)
        assert best_lam in (0.9, 1.0)
        assert len(grid_df) == 6


# ── Eval mask ────────────────────────────────────────────────────────────────

class TestEvalMask:
    def test_bootstrap_rows_excluded_from_tuning_metric(self):
        """Earliest season has has_prior_history=False and must not enter Brier."""
        df = _labeled_frame([
            _transfer_row(season=2020, actual=6.0, projected=4.0),
            _transfer_row(season=2021, actual=3.0, projected=4.0),
        ])
        scored = compute_success_probability(df)
        bootstrap = scored[scored["season"] == 2020]
        assert not bootstrap["has_prior_history"].iloc[0]

        _, _, grid_df = tune_transfer_success_hyperparameters(
            df, k_candidates=[5], lambda_candidates=[0.9],
        )
        assert grid_df.iloc[0]["n_eval"] == 1  # only season 2021

    def test_summarize_uses_same_eval_mask(self):
        df = _labeled_frame([
            _transfer_row(season=2020, actual=6.0, projected=4.0),
            _transfer_row(season=2021, actual=3.0, projected=4.0),
            _transfer_row(season=2022, actual=5.0, projected=4.0),
        ])
        scored = compute_success_probability(df)
        summary = summarize_shrinkage_sample_sizes(scored)
        assert summary["n_eval_rows"] == 2.0  # 2021 + 2022 only


# ── Grid edge cases ──────────────────────────────────────────────────────────

class TestTuneGridFallback:
    def test_empty_grid_returns_defaults(self):
        df = _labeled_frame([_transfer_row(season=2020, actual=6.0, projected=4.0)])
        k, lam, grid_df = tune_transfer_success_hyperparameters(
            df, k_candidates=[], lambda_candidates=[0.9],
        )
        assert k == SHRINKAGE_K
        assert lam == DECAY_LAMBDA
        assert grid_df.empty

    def test_default_grid_includes_low_k(self):
        assert 1 in K_CELL_CANDIDATES
        assert 2 in K_CELL_CANDIDATES


# ── v2 hierarchy ─────────────────────────────────────────────────────────────

class TestHierarchyV2:
    def test_model_version_is_v2(self):
        assert MODEL_VERSION == "transfer-success-eb-v2"

    def test_three_level_shrinkage_math(self):
        """Hand-check v2 cascade: cluster→global, offense_pair→cluster, cell→offense_pair."""
        k = 10

        def _row(season, cluster, offense, defense, success: bool):
            return _transfer_row(
                season=season,
                player_cluster=cluster,
                offense=offense,
                defense=defense,
                actual=6.0 if success else 2.0,
                projected=4.0,
            )

        train_rows = []
        # Cell (1,10,20): 16/20 success
        train_rows.extend(_row(2020, 1, 10, 20, s) for s in [True] * 16 + [False] * 4)
        # Cell (1,10,21): 8/10 success — same offense pair (1,10)
        train_rows.extend(_row(2020, 1, 10, 21, s) for s in [True] * 8 + [False] * 2)
        # Archetype 1 filler with different offense
        train_rows.extend(_row(2020, 1, 99, 99, False) for _ in range(10))
        # Other archetype filler
        train_rows.extend(_row(2020, 2, 10, 20, False) for _ in range(10))
        train_rows.append(_row(2021, 1, 10, 20, True))

        df = _labeled_frame(train_rows)
        scored = compute_success_probability(df, shrinkage_k=k, decay_lambda=1.0)
        row = scored[scored["season"] == 2021].iloc[0]

        global_rate = 24 / 50
        cluster_raw = 24 / 40
        cluster_n = 40.0
        w_cluster = cluster_n / (cluster_n + k)
        cluster_shrunk = w_cluster * cluster_raw + (1 - w_cluster) * global_rate

        offense_raw = 24 / 30
        offense_n = 30.0
        w_op = offense_n / (offense_n + k)
        offense_shrunk = w_op * offense_raw + (1 - w_op) * cluster_shrunk

        cell_raw = 16 / 20
        cell_n = 20.0
        w_cell = cell_n / (cell_n + k)
        expected_p = w_cell * cell_raw + (1 - w_cell) * offense_shrunk

        assert row["cluster_n"] == pytest.approx(cluster_n)
        assert row["offense_pair_n"] == pytest.approx(offense_n)
        assert row["cell_n"] == pytest.approx(cell_n)
        assert row["cluster_shrinkage_w"] == pytest.approx(w_cluster)
        assert row["offense_pair_shrinkage_w"] == pytest.approx(w_op)
        assert row["shrinkage_w"] == pytest.approx(w_cell)
        assert row["p_base"] == pytest.approx(expected_p)
        assert row["success_probability"] == pytest.approx(expected_p)

    def test_missing_defense_falls_back_to_offense_pair(self):
        """Rows without defense cluster_id use offense-pair level, not full cell."""
        rows = [
            _transfer_row(season=2020, player_cluster=1, offense=10, defense=20,
                          actual=6.0, projected=4.0),
            _transfer_row(season=2020, player_cluster=1, offense=10, defense=21,
                          actual=6.0, projected=4.0),
            _transfer_row(season=2021, player_cluster=1, offense=10, defense=20,
                          actual=3.0, projected=4.0),
        ]
        eval_row = {
            "season": 2022,
            "player_cluster": 1,
            "team_offense_cluster_id": 10,
            "team_defense_cluster_id": np.nan,
            "actual_value_per_100": 5.0,
            "projected_value_per_100": 4.0,
            "post_per": np.nan,
            "pre_per": np.nan,
        }
        rows.append(eval_row)
        df = _labeled_frame(rows)
        scored = compute_success_probability(df, shrinkage_k=5, decay_lambda=1.0)
        row = scored[scored["season"] == 2022].iloc[0]

        assert row["cell_n"] == 0.0
        assert row["offense_pair_n"] > 0.0
        assert row["prediction_level"] in ("offense_pair", "cluster", "global")

    def test_missing_offense_falls_back_to_cluster(self):
        rows = [
            _transfer_row(season=2020, player_cluster=1, offense=10, defense=20,
                          actual=6.0, projected=4.0),
            _transfer_row(season=2021, player_cluster=1, offense=10, defense=20,
                          actual=3.0, projected=4.0),
        ]
        eval_row = {
            "season": 2022,
            "player_cluster": 1,
            "team_offense_cluster_id": np.nan,
            "team_defense_cluster_id": 20,
            "actual_value_per_100": 5.0,
            "projected_value_per_100": 4.0,
            "post_per": np.nan,
            "pre_per": np.nan,
        }
        rows.append(eval_row)
        df = _labeled_frame(rows)
        scored = compute_success_probability(df, shrinkage_k=5, decay_lambda=1.0)
        row = scored[scored["season"] == 2022].iloc[0]

        assert row["cell_n"] == 0.0
        assert row["offense_pair_n"] == 0.0
        assert row["cluster_n"] > 0.0

    def test_v2_outputs_new_columns(self):
        df = _labeled_frame([
            _transfer_row(season=2020, actual=6.0, projected=4.0),
            _transfer_row(season=2021, actual=3.0, projected=4.0),
        ])
        scored = compute_success_probability(df)
        for col in (
            "cluster_shrinkage_w", "offense_pair_n", "offense_pair_shrinkage_w",
            "offense_pair_shrunk_rate", "prediction_level",
        ):
            assert col in scored.columns


class TestBuildExplanation:
    def test_rich_cell_precedent_mentions_exact_pairing(self):
        row = pd.Series({
            "player_cluster": 1,
            "success_probability": 0.72,
            "success_tier": "High",
            "cell_n": 8.0,
            "offense_pair_n": 12.0,
            "prediction_level": "cell",
            "team_cluster_label": "Pace-and-space",
            "similar_transfers": [{
                "player_name": "Test Player",
                "season": 2023,
                "success_label": True,
                "actual_value_per_100": 5.0,
                "projected_value_per_100": 3.0,
                "value_vs_projection": 2.0,
            }],
        })
        text = build_explanation(row)
        assert "exact pairing" in text
        assert "8.0 effective" in text

    def test_sparse_cell_mentions_offense_precedent(self):
        row = pd.Series({
            "player_cluster": 1,
            "success_probability": 0.55,
            "success_tier": "Moderate",
            "cell_n": 1.5,
            "offense_pair_n": 8.0,
            "prediction_level": "offense_pair",
            "team_cluster_label": "Motion",
            "similar_transfers": [{
                "player_name": "Sparse Comp",
                "season": 2022,
                "success_label": False,
                "actual_value_per_100": 2.0,
                "projected_value_per_100": 4.0,
                "value_vs_projection": -2.0,
            }],
        })
        text = build_explanation(row)
        assert "offensive-system precedent" in text or "offense precedent" in text

    def test_no_comps_sparse_uses_archetype_average(self):
        row = pd.Series({
            "player_cluster": 1,
            "success_probability": 0.48,
            "success_tier": "Low",
            "cell_n": 0.0,
            "offense_pair_n": 0.0,
            "prediction_level": "cluster",
            "team_cluster_label": "Unknown",
            "similar_transfers": [],
        })
        text = build_explanation(row)
        assert "broader archetype average" in text


# ── summarize_shrinkage_sample_sizes ─────────────────────────────────────────

class TestSummarizeShrinkageSampleSizes:
    def test_returns_percentiles_and_pct_below_thresholds(self):
        rows = []
        for season in (2020, 2021, 2022, 2023):
            for i in range(5):
                rows.append(_transfer_row(
                    season=season,
                    player_cluster=i % 2 + 1,
                    offense=10,
                    defense=20,
                    actual=5.0,
                    projected=4.0,
                ))
        df = _labeled_frame(rows)
        scored = compute_success_probability(df, shrinkage_k=15)
        summary = summarize_shrinkage_sample_sizes(scored, shrinkage_k=15, cell_min_n=CELL_MIN_N)

        assert "cell_n_median" in summary
        assert "cluster_n_p50" in summary
        assert "pct_cell_n_below_5" in summary
        assert "pct_shrinkage_w_below_0_25" in summary
        assert summary["n_eval_rows"] == 15.0  # 3 eval seasons × 5 rows


# ── Projection covariate (Block 4) ───────────────────────────────────────────

class TestProjectionCovariate:
    def test_higher_projection_lowers_adjusted_probability(self):
        """With fixed p_base and β<0, above-average projection → lower p_final."""
        base = pd.DataFrame({
            "p_base": [0.60, 0.60],
            "projection_z": [-1.0, 1.0],
        })
        adjusted = apply_projection_covariate_adjustment(base, beta=-0.5)
        low_proj = adjusted.iloc[0]["success_probability"]
        high_proj = adjusted.iloc[1]["success_probability"]
        assert high_proj < low_proj
        assert adjusted.iloc[0]["projection_adjustment"] > 0
        assert adjusted.iloc[1]["projection_adjustment"] < 0

    def test_zero_beta_leaves_probability_unchanged(self):
        base = pd.DataFrame({
            "p_base": [0.42, 0.71],
            "projection_z": [-2.0, 3.0],
        })
        adjusted = apply_projection_covariate_adjustment(base, beta=0.0)
        assert adjusted["success_probability"].tolist() == pytest.approx([0.42, 0.71])

    def test_beta_uses_only_prior_seasons(self):
        """Flipping a future-season label must not change an earlier season's probability."""
        rows = []
        for season in (2020, 2021, 2022, 2023):
            for i in range(12):
                rows.append(_transfer_row(
                    season=season,
                    player_cluster=1,
                    offense=10,
                    defense=20,
                    actual=6.0 if i % 2 == 0 else 2.0,
                    projected=2.0 + (i % 5),  # spread projections within season
                ))
        df_a = _labeled_frame(rows)
        scored_a = compute_success_probability(df_a, shrinkage_k=5, decay_lambda=1.0)
        p_2022_a = scored_a.loc[scored_a["season"] == 2022, "success_probability"].mean()

        flipped = [dict(r) for r in rows]
        for r in flipped:
            if r["season"] == 2023:
                r["actual_value_per_100"] = 10.0
        df_b = _labeled_frame(flipped)
        scored_b = compute_success_probability(df_b, shrinkage_k=5, decay_lambda=1.0)
        p_2022_b = scored_b.loc[scored_b["season"] == 2022, "success_probability"].mean()

        assert p_2022_a == pytest.approx(p_2022_b)

    def test_monotonicity_within_season(self):
        """Same cell on holdout: higher projection → lower probability when β<0."""
        rows = []
        for season in range(2018, 2023):
            for i in range(15):
                rows.append(_transfer_row(
                    season=season,
                    player_cluster=1,
                    offense=10,
                    defense=20,
                    actual=5.0 if i % 2 == 0 else 2.0,
                    projected=2.0 + (i % 4) * 0.5,
                ))
        for projected in (1.0, 2.0, 3.0, 4.0, 5.0, 6.0):
            rows.append(_transfer_row(
                season=2023,
                player_cluster=1,
                offense=10,
                defense=20,
                actual=5.0,
                projected=projected,
            ))
        df = _labeled_frame(rows)
        scored = compute_success_probability(df, shrinkage_k=5, decay_lambda=1.0)
        holdout = scored[scored["season"] == 2023].sort_values("projected_value_per_100")
        p_bases = holdout["p_base"].unique()
        assert len(p_bases) == 1, "holdout rows should share identical p_base"
        probs = holdout["success_probability"].to_numpy()
        assert np.all(np.diff(probs) <= 1e-9), "higher projection should not increase success_probability"

    def test_fit_projection_beta_negative_on_synthetic_data(self):
        train = pd.DataFrame({
            "success": [1, 1, 0, 0, 1, 0, 1, 0, 1, 0] * 4,
            "p_base": [0.55] * 40,
            "projection_z": np.linspace(-2, 2, 40),
        })
        # Higher z → lower success in synthetic labels
        train.loc[train["projection_z"] > 0, "success"] = 0
        train.loc[train["projection_z"] < 0, "success"] = 1
        beta = fit_projection_beta(train, min_rows=10)
        assert beta < 0

    def test_standardize_projection_is_within_season(self):
        df = pd.DataFrame({
            "season": [2020, 2020, 2021, 2021],
            "projected_value_per_100": [2.0, 4.0, 10.0, 12.0],
        })
        z = standardize_projection_by_season(df)
        assert z.iloc[0] == pytest.approx(-1.0)
        assert z.iloc[1] == pytest.approx(1.0)

    def test_covariate_outputs_new_columns(self):
        df = _labeled_frame([
            _transfer_row(season=2020, actual=6.0, projected=3.0),
            _transfer_row(season=2021, actual=3.0, projected=5.0),
        ])
        scored = compute_success_probability(df)
        for col in ("p_base", "projection_z", "beta_projection", "projection_adjustment"):
            assert col in scored.columns

    def test_explanation_mentions_projection_adjustment(self):
        row = pd.Series({
            "player_cluster": 1,
            "success_probability": 0.48,
            "success_tier": "Low",
            "cell_n": 8.0,
            "offense_pair_n": 12.0,
            "prediction_level": "cell",
            "team_cluster_label": "Pace",
            "projection_z": 1.5,
            "projection_adjustment": -0.06,
            "similar_transfers": [],
        })
        text = build_explanation(row)
        assert "Projection difficulty adjusted" in text
        assert "down" in text


# ── Calibration metrics (Block 5) ────────────────────────────────────────────

class TestCalibrationMetrics:
    def test_ece_is_zero_for_perfect_calibration(self):
        y_true = np.array([0, 0, 1, 1, 0, 1, 0, 1, 1, 0])
        y_prob = y_true.astype(float)
        assert compute_expected_calibration_error(y_true, y_prob) == pytest.approx(0.0)

    def test_tier_calibration_gap_on_synthetic_frame(self):
        rows = []
        for season in (2020, 2021):
            for i in range(20):
                projected = 2.0 + (i % 5)
                actual = 6.0 if i % 2 == 0 else 1.0
                rows.append(_transfer_row(
                    season=season,
                    player_cluster=1,
                    offense=10,
                    defense=20,
                    actual=actual,
                    projected=projected,
                ))
        scored = compute_success_probability(_labeled_frame(rows))
        tier_df = compute_tier_calibration(scored)
        assert "calibration_gap" in tier_df.columns
        assert tier_df["n"].fillna(0).sum() > 0

        metrics = summarize_calibration_metrics(scored)
        assert "ece" in metrics
        assert metrics["ece"] >= 0.0

    def test_write_calibration_artifacts_creates_files(self, tmp_path):
        rows = []
        for season in (2020, 2021, 2022):
            for i in range(12):
                rows.append(_transfer_row(
                    season=season,
                    player_cluster=1 + (i % 2),
                    offense=10,
                    defense=20,
                    actual=5.0 if i % 2 == 0 else 2.0,
                    projected=3.0,
                ))
        scored = compute_success_probability(_labeled_frame(rows))
        paths = write_calibration_artifacts(
            scored,
            tmp_path,
            shrinkage_k=15,
            decay_lambda=0.9,
            beta_projection=0.0,
            brier_score=0.24,
        )
        assert paths["tier_calibration"].exists()
        assert paths["hyperparameters"].exists()
        assert paths["calibration_curve"].exists()
        assert paths["calibration_curve"].stat().st_size > 0


# ── Inference scoring (Block 6) ─────────────────────────────────────────────

class TestScoreActiveCandidates:
    def test_inference_rows_get_explanation_and_similar_transfers(self):
        """score_active_candidates must mirror backtest post-processing."""
        hist_rows = []
        for season in (2020, 2021, 2022):
            for i in range(6):
                row = _transfer_row(
                    season=season,
                    player_cluster=1,
                    offense=10,
                    defense=20,
                    actual=5.0 if i % 2 == 0 else 2.0,
                    projected=3.0 + (i % 3),
                )
                row.update({
                    "player_id": 1000 + i,
                    "to_school_id": 101,
                    "player_name": f"Hist {i}",
                    "team_cluster_label": "Pace / Rim",
                    "post_minutes_per_game": np.nan,
                    "projected_minutes": np.nan,
                    "post_usage_rate": np.nan,
                    "projected_usage": np.nan,
                })
                hist_rows.append(row)
        df_hist = run_transfer_success_pipeline(_labeled_frame(hist_rows))

        df_active = pd.DataFrame({
            "player_id": [9001],
            "to_school_id": [101],
            "player_name": ["Test Player"],
            "player_cluster": [1],
            "team_offense_cluster_id": [10],
            "team_defense_cluster_id": [20],
            "team_cluster_label": ["Pace / Rim"],
            "projected_value_per_100": [4.0],
        })

        scored = score_active_candidates(
            df_active=df_active,
            df_historical=df_hist,
            target_season=2023,
        )
        assert len(scored) == 1
        assert scored.iloc[0]["explanation"] is not None
        assert isinstance(scored.iloc[0]["explanation"], str)
        assert len(scored.iloc[0]["explanation"]) > 0
        assert "similar_transfers" in scored.columns
        assert isinstance(scored.iloc[0]["similar_transfers"], list)

    def test_projection_z_invariant_to_chunk_size(self):
        """Season-level z-scores must not depend on inference chunk boundaries."""
        hist_rows = []
        for season in (2020, 2021, 2022):
            for i in range(8):
                row = _transfer_row(
                    season=season,
                    player_cluster=1,
                    offense=10,
                    defense=20,
                    actual=5.0 if i % 2 == 0 else 2.0,
                    projected=3.0 + (i % 4) * 0.5,
                )
                row.update({
                    "player_id": 1000 + i,
                    "to_school_id": 101,
                    "player_name": f"Hist {i}",
                    "team_cluster_label": "Pace / Rim",
                    "post_minutes_per_game": np.nan,
                    "projected_minutes": np.nan,
                    "post_usage_rate": np.nan,
                    "projected_usage": np.nan,
                })
                hist_rows.append(row)
        df_hist = run_transfer_success_pipeline(_labeled_frame(hist_rows))

        projections = [1.0, 2.5, 4.0, 5.5, 7.0]
        df_active = pd.DataFrame({
            "player_id": [9001, 9002, 9003, 9004, 9005],
            "to_school_id": [101, 102, 103, 104, 105],
            "player_name": [f"Active {i}" for i in range(5)],
            "player_cluster": [1] * 5,
            "team_offense_cluster_id": [10] * 5,
            "team_defense_cluster_id": [20] * 5,
            "team_cluster_label": ["Pace / Rim"] * 5,
            "projected_value_per_100": projections,
        })

        chunks_small = list(iter_scored_active_candidate_chunks(
            df_historical=df_hist,
            target_season=2023,
            chunk_size=1,
            df_active=df_active,
        ))
        chunks_large = list(iter_scored_active_candidate_chunks(
            df_historical=df_hist,
            target_season=2023,
            chunk_size=100,
            df_active=df_active,
        ))
        scored_small = pd.concat(chunks_small, ignore_index=True).sort_values(
            ["player_id", "to_school_id"],
        ).reset_index(drop=True)
        scored_large = pd.concat(chunks_large, ignore_index=True).sort_values(
            ["player_id", "to_school_id"],
        ).reset_index(drop=True)

        compare_cols = [
            "projection_z",
            "success_probability",
            "success_tier",
            "projection_adjustment",
        ]
        pd.testing.assert_frame_equal(
            scored_small[compare_cols],
            scored_large[compare_cols],
            check_dtype=False,
        )
