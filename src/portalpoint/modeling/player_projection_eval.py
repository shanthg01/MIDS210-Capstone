"""Player Projection — Phase 0 Formal Evaluation (rolling-origin CV).

Phase 0 (`player_projection.py`) has been in production with no held-out
validation at all — `off_resid_std`/`def_resid_std` are computed in-sample
(fit and evaluated on the same rows), and `SHRINKAGE_K`/`RIDGE_ALPHA` were
never tuned, just hardcoded. This module provides the pieces to do this
properly: rolling-origin temporal folds, hyperparameter tuning scoped to
each fold's own validation season, real held-out regression metrics,
calibration, baseline comparisons, and cohort slicing — see
docs/models/player_projection_state_space_plan.md §12.

Pure functions only — no DB/notebook coupling, same convention as
`player_projection_kalman.py`/`player_projection_phase2.py`. The notebook
calls these; this module does not call the notebook or write to the DB.

Rolling-origin, not a random split: matches §12's own recommendation and the
actual deployment scenario (production always predicts a season it has no
labels for yet). Three folds, each train season block followed by one
validation season (hyperparameter selection only) and one test season
(final, single-use metric per fold):

    Fold 1: train [2021,2022]           val 2023   test 2024
    Fold 2: train [2021,2022,2023]      val 2024   test 2025
    Fold 3: train [2021,2022,2023,2024] val 2025   test 2026  (headline fold)

Known, accepted limitation: the same player can appear in both a fold's
train and test seasons as different season-observations. Not eliminable
without sacrificing most of the test set's size — standard for this kind of
repeated-measures sports panel. Not a clean player-disjoint holdout.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import r2_score

from portalpoint.modeling import player_projection as pp

FOLD_DEFS: list[dict[str, list[int]]] = [
    {"train": [2021, 2022], "val": [2023], "test": [2024]},
    {"train": [2021, 2022, 2023], "val": [2024], "test": [2025]},
    {"train": [2021, 2022, 2023, 2024], "val": [2025], "test": [2026]},
]

K_CANDIDATES = [2.0, 4.0, 8.0, 12.0, 16.0]
ALPHA_CANDIDATES = [0.1, 1.0, 5.0, 10.0, 20.0]

VALUE_TARGETS = ("off_adj_rapm", "def_adj_rapm")
MIN_LABELED_ROWS = 10


def make_rolling_origin_folds(
    df: pd.DataFrame, fold_defs: list[dict[str, list[int]]] = FOLD_DEFS,
) -> list[dict]:
    """Splits df (must have a 'season' column) into the rolling-origin folds.
    Returns a list of dicts with 'train'/'val'/'test' sub-frames and the
    originating 'fold_def' for reporting."""
    folds = []
    for fold_def in fold_defs:
        folds.append({
            "train": df[df["season"].isin(fold_def["train"])].copy(),
            "val": df[df["season"].isin(fold_def["val"])].copy(),
            "test": df[df["season"].isin(fold_def["test"])].copy(),
            "fold_def": fold_def,
        })
    return folds


def compute_regression_metrics(y_true, y_pred) -> dict:
    """RMSE, R-squared, Spearman rank correlation — the three §12 calls for
    under 'Skill/rate metrics' (RMSE/MAE) and 'Rank correlation'."""
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)
    n = len(y_true)
    resid = y_true - y_pred
    rmse = float(np.sqrt(np.mean(resid**2))) if n > 0 else float("nan")
    r2 = float(r2_score(y_true, y_pred)) if n > 1 else float("nan")
    if n > 1 and np.std(y_true) > 0 and np.std(y_pred) > 0:
        rho, _ = spearmanr(y_true, y_pred)
    else:
        rho = float("nan")
    return {"rmse": rmse, "r2": r2, "spearman": float(rho), "n": n}


def compute_calibration(y_true, ci_lower, ci_upper) -> float:
    """Empirical coverage rate — what fraction of true values fall inside
    the predicted [ci_lower, ci_upper] band. §12's 'Calibration' metric;
    compare against the nominal ~80% project_value's CI_Z=1.2816 targets."""
    y_true = np.asarray(y_true, dtype=np.float64)
    ci_lower = np.asarray(ci_lower, dtype=np.float64)
    ci_upper = np.asarray(ci_upper, dtype=np.float64)
    covered = (y_true >= ci_lower) & (y_true <= ci_upper)
    return float(covered.mean()) if len(covered) > 0 else float("nan")


def _fold_combined_val_rmse(
    train_df: pd.DataFrame, val_df: pd.DataFrame, k: float | None, alpha: float, skip_shrinkage: bool = False,
) -> dict | None:
    if skip_shrinkage:
        # Gap G (Issue #37 reconciliation, 2026-06-24): Phase 2a's state frame
        # already has skill_<x> columns (smoothed Kalman states) and no
        # games_played/min_pct columns at all -- shrink_skills() would
        # KeyError on it (Phase 0's raw-rate-only sample_weight() needs
        # games_played). There's nothing for shrinkage to do here: the
        # season-grain Kalman layer already *is* the shrinkage-equivalent
        # step for these states. Use the frames as-is; k is not meaningful in
        # this mode and is not grid-searched (see tune_hyperparameters).
        shrunk_train, shrunk_val = train_df, val_df
    else:
        shrunk_train = pp.shrink_skills(train_df, k=k)
        shrunk_val = pp.shrink_skills(val_df, k=k)
    try:
        off_model, off_resid_std = pp.fit_value_model(shrunk_train, "off_adj_rapm", alpha=alpha)
        def_model, def_resid_std = pp.fit_value_model(shrunk_train, "def_adj_rapm", alpha=alpha)
    except ValueError:
        return None  # too few HE-labeled rows in this fold's train at this split

    projected_val = pp.project_value(shrunk_val, off_model, def_model, off_resid_std, def_resid_std)
    labeled_val = projected_val.dropna(subset=list(VALUE_TARGETS))
    if len(labeled_val) < MIN_LABELED_ROWS:
        return None

    off_metrics = compute_regression_metrics(labeled_val["off_adj_rapm"], labeled_val["off_value_per_100"])
    def_metrics = compute_regression_metrics(labeled_val["def_adj_rapm"], labeled_val["def_value_per_100"])
    combined_rmse = float(np.sqrt(off_metrics["rmse"] ** 2 + def_metrics["rmse"] ** 2))
    return {
        "k": k, "alpha": alpha, "val_rmse": combined_rmse,
        "off_rmse": off_metrics["rmse"], "def_rmse": def_metrics["rmse"], "n_val_labeled": len(labeled_val),
    }


def tune_hyperparameters(
    train_df: pd.DataFrame, val_df: pd.DataFrame,
    k_candidates: list[float] = K_CANDIDATES, alpha_candidates: list[float] = ALPHA_CANDIDATES,
    skip_shrinkage: bool = False,
) -> tuple[float | None, float, pd.DataFrame]:
    """Grid search over (SHRINKAGE_K, RIDGE_ALPHA) on this fold's validation
    season only — selection criterion is the combined off/def target RMSE
    (sqrt(off_rmse^2 + def_rmse^2), the same two-target summary
    scripts/run_player_projection.py already uses for its MLflow promotion
    metric). Falls back to production's current defaults
    (pp.SHRINKAGE_K, pp.RIDGE_ALPHA) if no grid cell has enough labeled rows
    to fit at all (e.g. a very small early fold).

    `skip_shrinkage` (Gap G, 2026-06-24): set True for Phase 2a's state
    frame, which has no `games_played`/raw-rate columns for `shrink_skills`
    to act on — k is meaningless in this mode and is not grid-searched (only
    `alpha` is), and the returned `k` is `None`."""
    if skip_shrinkage:
        results = [
            result
            for alpha in alpha_candidates
            if (result := _fold_combined_val_rmse(train_df, val_df, None, alpha, skip_shrinkage=True)) is not None
        ]
        grid_df = pd.DataFrame(results)
        if grid_df.empty:
            return None, pp.RIDGE_ALPHA, grid_df
        best = grid_df.loc[grid_df["val_rmse"].idxmin()]
        return None, float(best["alpha"]), grid_df

    results = [
        result
        for k in k_candidates
        for alpha in alpha_candidates
        if (result := _fold_combined_val_rmse(train_df, val_df, k, alpha)) is not None
    ]
    grid_df = pd.DataFrame(results)
    if grid_df.empty:
        return pp.SHRINKAGE_K, pp.RIDGE_ALPHA, grid_df
    best = grid_df.loc[grid_df["val_rmse"].idxmin()]
    return float(best["k"]), float(best["alpha"]), grid_df


def compare_to_baselines(train_df: pd.DataFrame, eval_df: pd.DataFrame, target_col: str) -> dict:
    """Two baselines, both fit on train only and evaluated on eval_df:
    (1) predict the train population's mean target value for everyone;
    (2) predict the train population's mean target value *within position*
    (HE pos_class) — a "prior-only" baseline that uses position but no
    skill features and no Ridge regression at all. If Ridge doesn't beat
    these, it isn't earning its complexity."""
    eval_labeled = eval_df.dropna(subset=[target_col])
    train_labeled = train_df.dropna(subset=[target_col])

    global_mean = float(train_labeled[target_col].mean())
    global_pred = np.full(len(eval_labeled), global_mean)
    global_metrics = compute_regression_metrics(eval_labeled[target_col], global_pred)

    position_means = train_labeled.groupby("position")[target_col].mean()
    position_pred = eval_labeled["position"].map(position_means).fillna(global_mean).to_numpy()
    position_metrics = compute_regression_metrics(eval_labeled[target_col], position_pred)

    return {"predict_train_mean": global_metrics, "predict_position_mean": position_metrics}


def evaluate_cohort_slices(
    df: pd.DataFrame, target_col: str, pred_col: str, slice_defs: dict[str, pd.Series], min_n: int = 5,
) -> pd.DataFrame:
    """One row per named boolean-mask slice in slice_defs, with RMSE/R²/
    Spearman/n. Slices with fewer than min_n labeled rows are skipped, not
    reported with misleadingly small-sample metrics."""
    rows = []
    for name, mask in slice_defs.items():
        sub = df[mask].dropna(subset=[target_col, pred_col])
        if len(sub) < min_n:
            continue
        metrics = compute_regression_metrics(sub[target_col], sub[pred_col])
        rows.append({"slice": name, **metrics})
    return pd.DataFrame(rows)


def join_archetype_metadata(df: pd.DataFrame, archetypes_df: pd.DataFrame) -> pd.DataFrame:
    """Left-joins `player_archetypes` (archetype_id/archetype_label/
    confidence) onto a projection/eval frame by (player_id, season) — Issue
    #37's Gap E (Issue #37 reconciliation): archetypes as evaluation/
    explanation/comparable-player metadata only.

    Deliberately a pure function taking `archetypes_df` as a plain frame, not
    an engine — the caller queries `player_archetypes` (e.g.
    `SELECT player_id, season, archetype_id, archetype_label, confidence
    FROM player_archetypes`), this function never touches the DB itself,
    same convention as the rest of this module.

    Left join, not inner: a player missing an archetype row must not be
    dropped or block evaluation — per Issue #37, "Missing archetype labels
    do not block projections for players with sufficient statistical
    history." Their archetype columns are simply NaN.
    """
    cols = ["player_id", "season", "archetype_id", "archetype_label", "confidence"]
    missing = [c for c in cols if c not in archetypes_df.columns]
    if missing:
        raise ValueError(f"archetypes_df missing expected columns: {missing}")
    return df.merge(archetypes_df[cols], on=["player_id", "season"], how="left")


def find_comparable_players(
    df: pd.DataFrame, player_id: int, season: int, skill_cols: list[str], n: int = 5,
) -> pd.DataFrame:
    """Nearest neighbors by Euclidean distance over `skill_cols` (the shared
    neutral skill-state representation — never archetype, even though
    archetype_label is reported alongside for context if present). Issue
    #37 Gap E: archetype is explanation metadata here, not part of the
    similarity metric itself — two players can be "comparable" by skill
    profile regardless of which archetype cluster they fell into.
    """
    target_row = df[(df["player_id"] == player_id) & (df["season"] == season)]
    if target_row.empty:
        raise ValueError(f"No row for player_id={player_id}, season={season}")
    target_vec = target_row[skill_cols].to_numpy(dtype=np.float64)[0]

    candidates = df[~((df["player_id"] == player_id) & (df["season"] == season))].dropna(subset=skill_cols).copy()
    diffs = candidates[skill_cols].to_numpy(dtype=np.float64) - target_vec
    candidates["_distance"] = np.sqrt((diffs**2).sum(axis=1))
    result_cols = ["player_id", "season", "_distance"] + [c for c in ("archetype_label",) if c in candidates.columns]
    return candidates.nsmallest(n, "_distance")[result_cols].reset_index(drop=True)
