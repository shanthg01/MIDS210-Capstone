import numpy as np
import pandas as pd
import pytest

from portalpoint.modeling import player_projection_eval as ppe


def test_make_rolling_origin_folds_splits_by_season():
    df = pd.DataFrame({"season": [2021, 2022, 2023, 2024, 2025, 2026], "value": range(6)})
    folds = ppe.make_rolling_origin_folds(df)
    assert len(folds) == 3

    fold3 = folds[2]
    assert fold3["train"]["season"].tolist() == [2021, 2022, 2023, 2024]
    assert fold3["val"]["season"].tolist() == [2025]
    assert fold3["test"]["season"].tolist() == [2026]
    # folds don't overlap within themselves
    assert set(fold3["train"]["season"]) & set(fold3["val"]["season"]) == set()
    assert set(fold3["val"]["season"]) & set(fold3["test"]["season"]) == set()


def test_compute_regression_metrics_perfect_prediction():
    y_true = [1.0, 2.0, 3.0, 4.0, 5.0]
    metrics = ppe.compute_regression_metrics(y_true, y_true)
    assert metrics["rmse"] == pytest.approx(0.0)
    assert metrics["r2"] == pytest.approx(1.0)
    assert metrics["spearman"] == pytest.approx(1.0)
    assert metrics["n"] == 5


def test_compute_regression_metrics_handles_constant_predictions():
    # constant y_pred makes spearman undefined (zero variance) — should
    # degrade to nan, not raise.
    y_true = [1.0, 2.0, 3.0]
    y_pred = [2.0, 2.0, 2.0]
    metrics = ppe.compute_regression_metrics(y_true, y_pred)
    assert np.isnan(metrics["spearman"])
    assert np.isfinite(metrics["rmse"])


def test_compute_calibration_full_and_zero_coverage():
    y_true = np.array([1.0, 2.0, 3.0])
    wide_lower, wide_upper = np.array([0.0, 0.0, 0.0]), np.array([10.0, 10.0, 10.0])
    assert ppe.compute_calibration(y_true, wide_lower, wide_upper) == pytest.approx(1.0)

    narrow_lower, narrow_upper = np.array([5.0, 5.0, 5.0]), np.array([6.0, 6.0, 6.0])
    assert ppe.compute_calibration(y_true, narrow_lower, narrow_upper) == pytest.approx(0.0)


def _synthetic_phase0_frame(n=400, seed=0):
    rng = np.random.default_rng(seed)
    seasons = rng.choice([2021, 2022, 2023, 2024, 2025, 2026], size=n)
    positions = rng.choice(["WG", "C", "PG"], size=n)
    fg3_pct = rng.uniform(0.25, 0.45, size=n)
    off_adj_rapm = 10.0 * (fg3_pct - 0.35) + rng.normal(0, 0.5, size=n)
    def_adj_rapm = rng.normal(0, 1.0, size=n)
    return pd.DataFrame({
        "player_id": np.arange(n),
        "season": seasons,
        "position": positions,
        "games_played": rng.integers(10, 32, size=n),
        "min_pct": rng.uniform(20.0, 90.0, size=n),
        "fg3_pct": fg3_pct,
        "rim_pct": rng.uniform(0.45, 0.65, size=n),
        "ft_pct": rng.uniform(0.6, 0.9, size=n),
        "usage_rate": rng.uniform(15.0, 30.0, size=n),
        "assist_rate": rng.uniform(5.0, 25.0, size=n),
        "tov_pct": rng.uniform(8.0, 20.0, size=n),
        "off_reb_pct": rng.uniform(1.0, 12.0, size=n),
        "def_reb_pct": rng.uniform(5.0, 20.0, size=n),
        "steal_pct": rng.uniform(0.5, 3.5, size=n),
        "block_pct": rng.uniform(0.0, 6.0, size=n),
        "off_adj_rapm": off_adj_rapm,
        "def_adj_rapm": def_adj_rapm,
    })


def test_tune_hyperparameters_selects_from_grid_and_falls_back_when_too_few_rows():
    df = _synthetic_phase0_frame(n=400)
    train_df = df[df["season"].isin([2021, 2022, 2023, 2024])]
    val_df = df[df["season"] == 2025]

    k, alpha, grid_df = ppe.tune_hyperparameters(train_df, val_df, k_candidates=[4.0, 8.0], alpha_candidates=[1.0, 10.0])
    assert k in [4.0, 8.0]
    assert alpha in [1.0, 10.0]
    assert not grid_df.empty
    assert set(grid_df.columns) >= {"k", "alpha", "val_rmse"}

    # fallback path: empty train/val should fall back to production defaults, not crash
    empty = df.iloc[0:0]
    import portalpoint.modeling.player_projection as pp
    k_fb, alpha_fb, grid_fb = ppe.tune_hyperparameters(empty, empty, k_candidates=[4.0], alpha_candidates=[1.0])
    assert k_fb == pp.SHRINKAGE_K
    assert alpha_fb == pp.RIDGE_ALPHA
    assert grid_fb.empty


def test_compare_to_baselines_position_mean_beats_or_ties_global_mean_when_positions_differ():
    train_df = pd.DataFrame({
        "position": ["C"] * 50 + ["PG"] * 50,
        "off_adj_rapm": [5.0] * 50 + [-5.0] * 50,  # positions have very different means
    })
    eval_df = pd.DataFrame({
        "position": ["C"] * 10 + ["PG"] * 10,
        "off_adj_rapm": [5.0] * 10 + [-5.0] * 10,
    })
    result = ppe.compare_to_baselines(train_df, eval_df, "off_adj_rapm")
    assert "predict_train_mean" in result
    assert "predict_position_mean" in result
    # position-aware baseline should have near-zero error; global mean baseline should not
    assert result["predict_position_mean"]["rmse"] < result["predict_train_mean"]["rmse"]


def test_evaluate_cohort_slices_skips_small_slices_and_reports_real_ones():
    df = pd.DataFrame({
        "target": list(range(20)),
        "pred": list(range(20)),
        "is_big": [True] * 8 + [False] * 12,
        "is_tiny_slice": [True] * 2 + [False] * 18,
    })
    slice_defs = {
        "bigs": df["is_big"],
        "tiny": df["is_tiny_slice"],
    }
    result = ppe.evaluate_cohort_slices(df, "target", "pred", slice_defs, min_n=5)
    assert "bigs" in result["slice"].tolist()
    assert "tiny" not in result["slice"].tolist()  # below min_n, correctly skipped
