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


def test_tune_hyperparameters_skip_shrinkage_works_on_a_presmoothed_frame_with_no_games_played():
    # Gap G (Issue #37 reconciliation, 2026-06-24): Phase 2a's state frame has
    # skill_<x> columns (already-smoothed Kalman states) but no
    # games_played/min_pct -- shrink_skills() would KeyError on it. This is
    # the bug a real notebook run actually hit; regression-guard it.
    rng = np.random.default_rng(0)
    n = 300
    df = pd.DataFrame({
        "player_id": np.arange(n),
        "season": rng.choice([2024, 2025, 2026], size=n),
        "position": rng.choice(["WG", "C", "PG"], size=n),
        "off_adj_rapm": rng.normal(0, 1.0, size=n),
        "def_adj_rapm": rng.normal(0, 1.0, size=n),
    })
    import portalpoint.modeling.player_projection as pp
    for skill in pp.SKILLS:
        df[f"skill_{skill}"] = rng.normal(0, 1.0, size=n)
    train_df = df[df["season"].isin([2024, 2025])]
    val_df = df[df["season"] == 2026]

    assert "games_played" not in df.columns  # the actual shape that broke shrink_skills()
    k, alpha, grid_df = ppe.tune_hyperparameters(train_df, val_df, alpha_candidates=[1.0, 10.0], skip_shrinkage=True)
    assert k is None
    assert alpha in [1.0, 10.0]
    assert not grid_df.empty


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


def test_join_archetype_metadata_left_join_does_not_drop_missing_rows():
    # Issue #37: missing archetype labels must not block evaluation for
    # players with sufficient statistical history.
    df = pd.DataFrame({"player_id": [1, 2, 3], "season": [2026, 2026, 2026], "value": [1.0, 2.0, 3.0]})
    archetypes_df = pd.DataFrame({
        "player_id": [1, 3], "season": [2026, 2026],
        "archetype_id": [0, 2], "archetype_label": ["3&D Wing", "Post Scoring Big"], "confidence": [0.9, 0.7],
    })
    joined = ppe.join_archetype_metadata(df, archetypes_df)
    assert len(joined) == 3  # player 2 (no archetype row) is not dropped
    assert joined.loc[joined["player_id"] == 2, "archetype_label"].isna().all()
    assert joined.loc[joined["player_id"] == 1, "archetype_label"].iloc[0] == "3&D Wing"


def test_join_archetype_metadata_raises_on_missing_columns():
    df = pd.DataFrame({"player_id": [1], "season": [2026]})
    bad_archetypes_df = pd.DataFrame({"player_id": [1], "season": [2026]})  # missing archetype_id etc.
    with pytest.raises(ValueError, match="missing expected columns"):
        ppe.join_archetype_metadata(df, bad_archetypes_df)


def test_find_comparable_players_returns_nearest_by_skill_distance_not_archetype():
    df = pd.DataFrame({
        "player_id": [1, 2, 3, 4],
        "season": [2026, 2026, 2026, 2026],
        "skill_a": [0.0, 0.1, 5.0, 0.05],
        "skill_b": [0.0, 0.1, 5.0, 0.05],
        "archetype_label": ["Wing", "Big", "Big", "Wing"],
    })
    result = ppe.find_comparable_players(df, player_id=1, season=2026, skill_cols=["skill_a", "skill_b"], n=2)
    # nearest by skill distance should be players 2 and 4 (close in skill space),
    # not player 3 (far in skill space despite no archetype filter applied)
    assert set(result["player_id"].tolist()) == {2, 4}
    assert 3 not in result["player_id"].tolist()


def test_find_comparable_players_raises_for_unknown_player_season():
    df = pd.DataFrame({"player_id": [1], "season": [2026], "skill_a": [0.0]})
    with pytest.raises(ValueError, match="No row for"):
        ppe.find_comparable_players(df, player_id=99, season=2026, skill_cols=["skill_a"])
