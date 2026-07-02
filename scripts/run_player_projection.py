"""
scripts/run_player_projection.py

Non-interactive rerun of the Player Projection model — both stages:
  --phase baseline       the Shrinkage Baseline only (player-projection-shrinkage-v2,
                         season-grain shrinkage + Ridge, seconds to run).
  --phase cross-season   the Cross-Season model forecast only (player-proj-phase2a-fcast-v1,
                         two-level cross-season Kalman state-space, ~10 minutes — see
                         docs/models/player_projection_state_space_plan.md §22).
  --phase both           Both, sequentially (default).

Both phases write neutral-mode rows (school_id NULL) to player_projections,
each under its own model_version — the partial unique index on (player_id,
season, model_version) WHERE school_id IS NULL means they can never collide,
so order between phases doesn't matter for correctness (sequential just
avoids two concurrent ProcessPoolExecutor pools fighting for cores during
the Cross-Season model's Kalman fit).

the Cross-Season model uses the recommended configuration (use_context_adjustment=False —
Gap B was found to regress accuracy on real data, flagged TBD, not enabled)
and applies the two real-data components that were coded+tested but never
previously wired into a production run: Gap A (shared-prior blending for the
2 validated skill blocks) and Gap E (archetype metadata in `explanation`,
evaluation/explanation only, never a model feature).

Usage:
  uv run python scripts/run_player_projection.py                       # both stages
  uv run python scripts/run_player_projection.py --phase baseline
  uv run python scripts/run_player_projection.py --phase cross-season
"""
from __future__ import annotations

import argparse
import logging
import sys

import numpy as np
import pandas as pd
from sqlalchemy import text

from portalpoint.modeling import player_projection as pp
from portalpoint.modeling.io import find_repo_root, get_sync_engine
from portalpoint.modeling.mlflow_helpers import maybe_promote, setup_mlflow

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CROSS_SEASON_SEASONS = [2020, 2021, 2022, 2023, 2024, 2025, 2026]


def run_baseline(engine) -> None:
    df = pp.load_player_season_frame(engine)
    log.info("Loaded %s player-seasons (games_played >= %d)", f"{len(df):,}", pp.MIN_GAMES)

    df = pp.shrink_skills(df)
    df = pp.skill_percentiles(df)

    off_model, off_resid_std = pp.fit_value_model(df, "off_adj_rapm")
    def_model, def_resid_std = pp.fit_value_model(df, "def_adj_rapm")
    n_labeled = int(df[["off_adj_rapm", "def_adj_rapm"]].dropna().shape[0])
    log.info(
        "Fit value models on %s HE-labeled rows: off_resid_std=%.3f def_resid_std=%.3f",
        f"{n_labeled:,}", off_resid_std, def_resid_std,
    )

    df = pp.project_value(df, off_model, def_model, off_resid_std, def_resid_std)

    # Secondary-label robustness check (plan doc §5/§8): off_adj_rapm_prod /
    # adj_rapm_prod_margin mix per-possession impact with playing-time share,
    # so they're not retraining targets — but our offense-only projection
    # should still track them directionally. Logged, not asserted on; a low
    # correlation here is a real signal worth investigating, not a hard failure.
    prod_check = df[["off_value_per_100", "off_adj_rapm_prod"]].dropna()
    if len(prod_check) > 30:
        prod_corr = prod_check["off_value_per_100"].corr(prod_check["off_adj_rapm_prod"])
        log.info(
            "Robustness check: off_value_per_100 vs off_adj_rapm_prod corr=%.3f (n=%s)",
            prod_corr, f"{len(prod_check):,}",
        )
    margin_check = df[["value_per_100", "adj_rapm_prod_margin"]].dropna()
    if len(margin_check) > 30:
        margin_corr = margin_check["value_per_100"].corr(margin_check["adj_rapm_prod_margin"])
        log.info(
            "Robustness check: value_per_100 vs adj_rapm_prod_margin corr=%.3f (n=%s)",
            margin_corr, f"{len(margin_check):,}",
        )

    records = pp.build_neutral_records(df)
    upserted = pp.upsert_neutral_projections(engine, records)
    log.info("Upserted %s rows into player_projections (neutral mode, %s)", f"{upserted:,}", pp.MODEL_VERSION)

    if not records:
        raise RuntimeError("No player projection records were scored")

    values = np.fromiter((r[4] for r in records), dtype=np.float64, count=len(records))
    mean_value = float(values.mean())
    std_value = float(values.std())

    client = setup_mlflow("player-projection")
    import mlflow
    import mlflow.pyfunc

    class PlayerProjectionPyfunc(mlflow.pyfunc.PythonModel):
        """Wraps the two Ridge value models for the MLflow registry."""

        def __init__(self, off_model, def_model):
            self.off_model = off_model
            self.def_model = def_model

        def predict(self, context, model_input):
            # Offense/defense split (2026-06-24): off_model/def_model expect
            # different feature columns -- two design matrices, not one.
            X_off = pp.build_design_matrix(model_input, skills=pp.OFFENSE_SKILLS)
            X_def = pp.build_design_matrix(model_input, skills=pp.DEFENSE_SKILLS)
            off = self.off_model.predict(X_off)
            defn = self.def_model.predict(X_def)
            value = pp.combine_total_value(off, defn)
            return [{"value_per_100": float(v)} for v in value]

    with mlflow.start_run(run_name=f"player-projection-s{int(df['season'].max())}-script") as run:
        mlflow.log_params({
            "seasons": ",".join(str(s) for s in sorted(df["season"].unique().tolist())),
            "model_version": pp.MODEL_VERSION,
            "min_games": pp.MIN_GAMES,
            "shrinkage_k": pp.SHRINKAGE_K,
            "ridge_alpha": pp.RIDGE_ALPHA,
            "source": "script",
        })
        mlflow.log_metrics({
            "mean_value_per_100": mean_value,
            "std_value_per_100": std_value,
            "off_resid_std": off_resid_std,
            "def_resid_std": def_resid_std,
            "total_resid_std": float(np.sqrt(off_resid_std**2 + def_resid_std**2)),
            "n_records_written": float(len(records)),
            "n_he_labeled_rows": float(n_labeled),
        })
        mlflow.pyfunc.log_model(
            artifact_path="player_projection_model",
            python_model=PlayerProjectionPyfunc(off_model, def_model),
        )
        run_id = run.info.run_id

    total_resid_std = float(np.sqrt(off_resid_std**2 + def_resid_std**2))
    result = maybe_promote(
        client, "player-projection", run_id, "player_projection_model",
        metric_name="total_resid_std", new_value=total_resid_std, higher_is_better=False,
    )
    log.info("MLflow run %s — %s", run_id, result)

    # Save/upload only after scoring, DB write, and MLflow logging complete, so
    # the shared S3 artifacts cannot get ahead of the production table state.
    root = find_repo_root()
    models_dir = root / "data" / "models"
    paths = pp.save_artifacts(models_dir, off_model, def_model, off_resid_std, def_resid_std)
    for name, path in paths.items():
        log.info("Saved %s: %s", name, path)
    try:
        sys.path.insert(0, str(root / "notebooks" / "utils"))
        from s3_helpers import upload

        for name, path in paths.items():
            upload(path, f"models/player_projection/{path.name}")
    except Exception as exc:
        log.warning("S3 upload skipped: %s", exc)


def _apply_gap_a_blending(residual_df: pd.DataFrame) -> pd.DataFrame:
    """Gap A, applied for real (2026-06-25): substitutes the shared-prior-
    blended estimate in place of the raw season-grain estimate for the 5
    skills in the 2 validated blocks (creation, rebounding) — everywhere
    downstream (Gap C's features, both value models, Gap F's write) sees the
    better estimate, not just a diagnostic column nobody reads. Blocks that
    didn't validate (shooting_touch, defensive_playmaking) are untouched."""
    block_corrs = pp.compute_block_correlations(residual_df)
    blended = pp.blend_block_priors(residual_df, block_corrs)
    out = blended.copy()
    for block_name in pp.VALIDATED_BLOCKS:
        for skill in pp.SKILL_BLOCKS.get(block_name, []):
            blended_col = f"phase2_skill_{skill}_blended"
            if blended_col in out.columns:
                out[f"phase2_skill_{skill}"] = out[blended_col]
    return out, block_corrs


def _load_real_archetypes(engine, seasons: list[int]) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(
            text(
                "SELECT player_id, season, archetype_id, archetype_label, confidence "
                "FROM player_archetypes WHERE season = ANY(:seasons)"
            ),
            conn, params={"seasons": seasons},
        )


def _gap_g_real_metric(cross_season_states: pd.DataFrame) -> tuple[float, float, float, float]:
    """Real held-out fold-3 metrics for the Cross-Season model — the same rolling-origin
    tooling the notebook's Gap G section already uses, run here so the
    production script's MLflow promotion gate is judged on a real held-out
    metric, not in-sample residual std (the Shrinkage Baseline's own gate metric,
    `total_resid_std`, is in-sample — a known, accepted limitation carried
    over from before this session's eval work existed). Returns
    (combined_rmse, off_rmse, def_rmse, calibration) for fold 3 (test season
    2026). `calibration` (added 2026-06-25 — the notebook's Gap G section
    already computed this, the script never did) is empirical 80%-CI
    coverage on the total margin value, via `compute_calibration`."""
    folds = pp.make_rolling_origin_folds(cross_season_states)
    fold3 = folds[2]
    train_df, val_df, test_df = fold3["train"], fold3["val"], fold3["test"]
    k, alpha, _ = pp.tune_hyperparameters(train_df, val_df, skip_shrinkage=True)
    train_val_df = pd.concat([train_df, val_df], ignore_index=True)
    off_m, off_r = pp.fit_value_model(train_val_df, "off_adj_rapm", alpha=alpha)
    def_m, def_r = pp.fit_value_model(train_val_df, "def_adj_rapm", alpha=alpha)
    projected_test = pp.project_value(test_df, off_m, def_m, off_r, def_r)
    labeled_test = projected_test.dropna(subset=["off_adj_rapm", "def_adj_rapm"])
    off_metrics = pp.compute_regression_metrics(labeled_test["off_adj_rapm"], labeled_test["off_value_per_100"])
    def_metrics = pp.compute_regression_metrics(labeled_test["def_adj_rapm"], labeled_test["def_value_per_100"])
    cross_season_combined = float(np.sqrt(off_metrics["rmse"] ** 2 + def_metrics["rmse"] ** 2))
    total_actual = labeled_test["off_adj_rapm"] - labeled_test["def_adj_rapm"]
    calibration = pp.compute_calibration(total_actual, labeled_test["value_ci_lower"], labeled_test["value_ci_upper"])

    # the Shrinkage Baseline reference, same fold, same held-out test season -- recomputed
    # here (not read from MLflow) so the comparison is apples-to-apples on
    # this exact run's data, not a stale recorded number from a different day.
    # `maybe_promote` (called by the caller) reads the Shrinkage Baseline's *currently
    # registered* Production metric directly from MLflow for the actual
    # gate decision -- this function only needs to produce the Cross-Season model's side.
    return cross_season_combined, off_metrics["rmse"], def_metrics["rmse"], calibration


def _forecast_rolling_metrics(
    forecast_states: pd.DataFrame,
    off_extra_features: list[str] | None = None,
    def_extra_features: list[str] | None = None,
    ci_scale: float = 1.0,
) -> pd.DataFrame:
    """Rolling-origin validation for next-season forecast rows.

    Rows are keyed by target projected season, so fold 3 means value models
    train on forecast targets through 2025 and evaluate forecasts for 2026.
    The season-transition parameters and source-value priors are still the
    production full-history objects at this stage; this validation primarily
    checks the served forecast frame and value translation under the same
    temporal split used elsewhere in Player Projection. It is intentionally not
    a fully leak-free refit of every upstream state-space component at each
    historical cutoff.
    """
    rows: list[dict] = []
    for fold_idx, fold in enumerate(pp.make_rolling_origin_folds(forecast_states), start=1):
        train_df, val_df, test_df = fold["train"], fold["val"], fold["test"]
        if train_df.empty or val_df.empty or test_df.empty:
            continue
        _, alpha, _ = pp.tune_hyperparameters(train_df, val_df, skip_shrinkage=True)
        train_val_df = pd.concat([train_df, val_df], ignore_index=True)
        try:
            off_m, off_r = pp.fit_value_model(
                train_val_df, "off_adj_rapm", alpha=alpha, extra_features=off_extra_features,
            )
            def_m, def_r = pp.fit_value_model(
                train_val_df, "def_adj_rapm", alpha=alpha, extra_features=def_extra_features,
            )
        except ValueError:
            continue

        projected_test = pp.project_value(
            test_df, off_m, def_m, off_r, def_r,
            off_extra_features=off_extra_features,
            def_extra_features=def_extra_features,
            ci_scale=ci_scale,
        )
        labeled_test = projected_test.dropna(subset=["off_adj_rapm", "def_adj_rapm"])
        if len(labeled_test) < pp.MIN_LABELED_ROWS:
            continue

        off_metrics = pp.compute_regression_metrics(labeled_test["off_adj_rapm"], labeled_test["off_value_per_100"])
        def_metrics = pp.compute_regression_metrics(labeled_test["def_adj_rapm"], labeled_test["def_value_per_100"])
        total_actual = labeled_test["off_adj_rapm"] - labeled_test["def_adj_rapm"]
        total_metrics = pp.compute_regression_metrics(total_actual, labeled_test["value_per_100"])
        calibration = pp.compute_calibration(total_actual, labeled_test["value_ci_lower"], labeled_test["value_ci_upper"])
        half_width = (labeled_test["value_ci_upper"] - labeled_test["value_ci_lower"]) / 2.0
        required_scale_80 = float(np.quantile((total_actual - labeled_test["value_per_100"]).abs() / half_width, 0.80))
        rows.append({
            "fold": fold_idx,
            "test_season": int(fold["fold_def"]["test"][0]),
            "alpha": alpha,
            "n_labeled": len(labeled_test),
            "total_rmse": total_metrics["rmse"],
            "total_r2": total_metrics["r2"],
            "off_rmse": off_metrics["rmse"],
            "def_rmse": def_metrics["rmse"],
            "calibration_80pct_target": calibration,
            "required_ci_scale_80pct": required_scale_80 * ci_scale,
        })
    return pd.DataFrame(rows)


def _load_source_team_pace(engine) -> pd.DataFrame:
    """Per-player source-season pace for projected rate conversions.

    `player_school_seasons` is still empty in the local data stack; the real
    player-team-season linkage lives in `player_season_stats`, so use that for
    production rate payloads instead of falling back to the global default pace
    for every row.
    """
    with engine.connect() as conn:
        return pd.read_sql(
            text(
                "SELECT pss.player_id, pss.season AS source_observed_season, t.adj_tempo "
                "FROM player_season_stats pss "
                "JOIN team_season_stats t ON pss.school_id = t.school_id AND pss.season = t.season"
            ),
            conn,
        ).drop_duplicates(subset=["player_id", "source_observed_season"], keep="first")


def run_cross_season(engine, max_workers: int | None = None) -> None:
    log.info("the Cross-Season model: building season-grain skill states (no-context config) — this is the ~10min step")
    fitted_q_by_season, season_states = pp.load_or_build_season_skill_states(
        engine, CROSS_SEASON_SEASONS, use_baseline_prior=True, use_context_adjustment=False,
        max_workers=max_workers,
    )
    covariates = pp.load_or_build_season_covariates(engine, season_states)
    log.info("Season-states: %s rows, %s covariate rows", f"{len(season_states):,}", f"{len(covariates):,}")

    fitted_params, residual_df = pp.fit_all_skills(season_states, covariates, max_workers=max_workers)
    residual_df, block_corrs = _apply_gap_a_blending(residual_df)
    for block_name in pp.VALIDATED_BLOCKS:
        if block_name in block_corrs:
            log.info("Gap A applied — %s block correlation:\n%s", block_name, block_corrs[block_name].round(3))

    # fit_all_skills already merges on the real "season" (2026-06-25 fix —
    # see SeasonSequence's docstring), so no season_rank<->career_season_index
    # reconstruction is needed here.
    rename_map = {
        **{f"phase2_skill_{s}": f"skill_{s}" for s in pp.SKILLS},
        **{f"phase2_skill_var_{s}": f"skill_var_{s}" for s in pp.SKILLS},
    }
    cross_season_states = residual_df.rename(columns=rename_map)

    baseline_frame = pp.load_player_season_frame(engine)
    baseline_context = baseline_frame[["player_id", "season", "position"]].drop_duplicates()
    with engine.connect() as conn:
        he_labels_raw = pd.read_sql(
            text(
                "SELECT player_id, season, off_adj_rapm, def_adj_rapm, "
                "off_adj_rapm_prod, adj_rapm_prod_margin FROM hoop_explorer_player_stats"
            ),
            conn,
        )
    baseline_weight = pp.shrink_skills(baseline_frame)[["player_id", "season", "_weight"]]
    cross_season_states = (
        cross_season_states
        .merge(baseline_context, on=["player_id", "season"], how="left")
        .drop_duplicates(subset=["player_id", "season"], keep="first")
        .reset_index(drop=True)
    )
    log.info("the Cross-Season model observed state frame: %s rows", f"{len(cross_season_states):,}")

    observed_labeled = (
        cross_season_states
        .merge(he_labels_raw[["player_id", "season", "off_adj_rapm", "def_adj_rapm"]], on=["player_id", "season"], how="left")
        .merge(baseline_weight, on=["player_id", "season"], how="left")
    )
    source_off_model, source_off_resid = pp.fit_value_model(observed_labeled, "off_adj_rapm")
    source_def_model, source_def_resid = pp.fit_value_model(observed_labeled, "def_adj_rapm")
    source_projected = pp.project_value(
        observed_labeled, source_off_model, source_def_model, source_off_resid, source_def_resid,
    )[["player_id", "season", "off_value_per_100", "def_value_per_100", "value_per_100", "_value_std"]].rename(columns={
        "season": "source_observed_season",
        "off_value_per_100": "source_off_value_per_100",
        "def_value_per_100": "source_def_value_per_100",
        "value_per_100": "source_value_per_100",
        "_value_std": "source_value_per_100_var",
    })
    source_projected["source_value_per_100_var"] = source_projected["source_value_per_100_var"] ** 2

    forecast_states = pp.forecast_next_season_states(cross_season_states, covariates, fitted_params)
    forecast_states = (
        forecast_states
        .merge(he_labels_raw, on=["player_id", "season"], how="left")
        .merge(baseline_weight, on=["player_id", "season"], how="left")
        .merge(source_projected, on=["player_id", "source_observed_season"], how="left")
        .drop_duplicates(subset=["player_id", "season", "source_observed_season"], keep="first")
        .reset_index(drop=True)
    )
    log.info(
        "the Cross-Season model forecast frame: %s rows, target seasons %s",
        f"{len(forecast_states):,}",
        sorted(forecast_states["season"].unique().tolist()),
    )

    # Gap C: attempt-rate decomposition, fed the Gap-A-blended states.
    attempt_targets = pp.build_attempt_rate_targets(engine, CROSS_SEASON_SEASONS)
    stage2a_states = cross_season_states.merge(attempt_targets, on=["player_id", "season"], how="inner")
    attempt_models = pp.fit_attempt_rate_models(stage2a_states)
    log.info("Gap C: fitted attempt-rate models: %s", list(attempt_models.keys()))

    source_team_pace = _load_source_team_pace(engine)
    pace_lookup = (
        forecast_states[["player_id", "source_observed_season"]]
        .merge(source_team_pace, on=["player_id", "source_observed_season"], how="left")["adj_tempo"]
    )
    projected_rates = pp.project_rates(forecast_states, attempt_models, pace=pace_lookup)

    off_model_cs, off_resid_std_cs = pp.fit_value_model(
        forecast_states, "off_adj_rapm", extra_features=pp.FORECAST_OFF_EXTRA_FEATURES,
    )
    def_model_cs, def_resid_std_cs = pp.fit_value_model(
        forecast_states, "def_adj_rapm", extra_features=pp.FORECAST_DEF_EXTRA_FEATURES,
    )
    log.info(
        "the Cross-Season model forecast fit: off_resid_std=%.3f def_resid_std=%.3f", off_resid_std_cs, def_resid_std_cs,
    )

    cross_season_pctile = pp.skill_percentiles(forecast_states, skills=pp.SKILLS)
    rolling_metrics_unscaled = _forecast_rolling_metrics(
        cross_season_pctile,
        off_extra_features=pp.FORECAST_OFF_EXTRA_FEATURES,
        def_extra_features=pp.FORECAST_DEF_EXTRA_FEATURES,
        ci_scale=1.0,
    )
    forecast_ci_scale = 1.0
    if not rolling_metrics_unscaled.empty:
        forecast_ci_scale = max(1.0, float(rolling_metrics_unscaled["required_ci_scale_80pct"].max()))
        log.info("Forecast CI conformal scale selected from rolling folds: %.3f", forecast_ci_scale)

    cross_season_projected = pp.project_value(
        cross_season_pctile, off_model_cs, def_model_cs, off_resid_std_cs, def_resid_std_cs,
        off_extra_features=pp.FORECAST_OFF_EXTRA_FEATURES,
        def_extra_features=pp.FORECAST_DEF_EXTRA_FEATURES,
        ci_scale=forecast_ci_scale,
    )
    cross_season_projected = pp.attach_value_drivers(
        cross_season_projected,
        off_model_cs,
        def_model_cs,
        off_extra_features=pp.FORECAST_OFF_EXTRA_FEATURES,
        def_extra_features=pp.FORECAST_DEF_EXTRA_FEATURES,
    )

    # Secondary-label robustness check (Issue #37 item 4 — "validate total
    # against adj_rapm_margin... as robustness only"), same pattern as
    # run_baseline()'s — this was missing from the Cross-Season model until 2026-06-25.
    prod_check = cross_season_projected[["off_value_per_100", "off_adj_rapm_prod"]].dropna()
    if len(prod_check) > 30:
        prod_corr = prod_check["off_value_per_100"].corr(prod_check["off_adj_rapm_prod"])
        log.info(
            "Robustness check: off_value_per_100 vs off_adj_rapm_prod corr=%.3f (n=%s)",
            prod_corr, f"{len(prod_check):,}",
        )
    margin_check = cross_season_projected[["value_per_100", "adj_rapm_prod_margin"]].dropna()
    if len(margin_check) > 30:
        margin_corr = margin_check["value_per_100"].corr(margin_check["adj_rapm_prod_margin"])
        log.info(
            "Robustness check: value_per_100 vs adj_rapm_prod_margin corr=%.3f (n=%s)",
            margin_corr, f"{len(margin_check):,}",
        )

    # Gap E: real archetype metadata, evaluation/explanation only.
    forecast_target_seasons = sorted(cross_season_projected["season"].unique().tolist())
    archetypes_df = _load_real_archetypes(engine, forecast_target_seasons)
    log.info("Gap E: loaded %s real archetype rows", f"{len(archetypes_df):,}")

    records = pp.build_cross_season_records(
        cross_season_projected,
        projected_rates_df=projected_rates,
        archetypes_df=archetypes_df,
        model_version=pp.MODEL_VERSION_CROSS_SEASON_FORECAST,
    )
    upserted = pp.upsert_neutral_projections(engine, records)
    log.info("Upserted %s rows into player_projections (%s)", f"{upserted:,}", pp.MODEL_VERSION_CROSS_SEASON_FORECAST)

    if not records:
        raise RuntimeError("No the Cross-Season model forecast projection records were scored")

    labeled_forecasts = cross_season_projected.dropna(subset=["off_adj_rapm", "def_adj_rapm"])
    off_metrics = pp.compute_regression_metrics(labeled_forecasts["off_adj_rapm"], labeled_forecasts["off_value_per_100"])
    def_metrics = pp.compute_regression_metrics(labeled_forecasts["def_adj_rapm"], labeled_forecasts["def_value_per_100"])
    total_actual = labeled_forecasts["off_adj_rapm"] - labeled_forecasts["def_adj_rapm"]
    total_metrics = pp.compute_regression_metrics(total_actual, labeled_forecasts["value_per_100"])
    calibration = pp.compute_calibration(
        total_actual, labeled_forecasts["value_ci_lower"], labeled_forecasts["value_ci_upper"],
    )
    log.info(
        "Forecast full-fit check: total_rmse=%.4f total_r2=%.4f off_rmse=%.4f def_rmse=%.4f calibration_80pct_target=%.3f (n=%s)",
        total_metrics["rmse"], total_metrics["r2"], off_metrics["rmse"], def_metrics["rmse"],
        calibration, f"{len(labeled_forecasts):,}",
    )
    rolling_metrics = _forecast_rolling_metrics(
        cross_season_pctile,
        off_extra_features=pp.FORECAST_OFF_EXTRA_FEATURES,
        def_extra_features=pp.FORECAST_DEF_EXTRA_FEATURES,
        ci_scale=forecast_ci_scale,
    )
    if not rolling_metrics.empty:
        log.info("Rolling next-season forecast validation:\n%s", rolling_metrics.round(4).to_string(index=False))
        headline = rolling_metrics.iloc[-1]
    else:
        headline = pd.Series(dtype=float)
        log.warning("Rolling next-season forecast validation skipped: no labeled folds")

    values = np.fromiter((r[4] for r in records), dtype=np.float64, count=len(records))
    client = setup_mlflow("player-projection")
    import mlflow
    import mlflow.pyfunc

    class CrossSeasonForecastPyfunc(mlflow.pyfunc.PythonModel):
        def __init__(self, off_model, def_model):
            self.off_model = off_model
            self.def_model = def_model

        def predict(self, context, model_input):
            X_off = pp.build_design_matrix(
                model_input, skills=pp.OFFENSE_SKILLS, extra_features=pp.FORECAST_OFF_EXTRA_FEATURES,
            )
            X_def = pp.build_design_matrix(
                model_input, skills=pp.DEFENSE_SKILLS, extra_features=pp.FORECAST_DEF_EXTRA_FEATURES,
            )
            off = self.off_model.predict(X_off)
            defn = self.def_model.predict(X_def)
            value = pp.combine_total_value(off, defn)
            return [{"value_per_100": float(v)} for v in value]

    with mlflow.start_run(run_name=f"player-projection-cross-season-forecast-s{max(forecast_target_seasons)}-script") as run:
        mlflow.log_params({
            "source_observed_seasons": ",".join(str(s) for s in CROSS_SEASON_SEASONS),
            "target_projected_seasons": ",".join(str(s) for s in forecast_target_seasons),
            "model_version": pp.MODEL_VERSION_CROSS_SEASON_FORECAST,
            "use_context_adjustment": False,
            "gap_a_applied": True,
            "gap_e_applied": True,
            "forecast_horizon_seasons": 1,
            "forecast_value_extra_features_off": ",".join(pp.FORECAST_OFF_EXTRA_FEATURES),
            "forecast_value_extra_features_def": ",".join(pp.FORECAST_DEF_EXTRA_FEATURES),
            "forecast_ci_scale": forecast_ci_scale,
            "source": "script",
        })
        mlflow.log_metrics({
            "mean_value_per_100": float(values.mean()),
            "std_value_per_100": float(values.std()),
            "off_resid_std": off_resid_std_cs,
            "def_resid_std": def_resid_std_cs,
            "total_resid_std": float(np.sqrt(off_resid_std_cs**2 + def_resid_std_cs**2)),
            "n_records_written": float(len(records)),
            "forecast_full_fit_total_rmse": total_metrics["rmse"],
            "forecast_full_fit_total_r2": total_metrics["r2"],
            "forecast_full_fit_off_rmse": off_metrics["rmse"],
            "forecast_full_fit_def_rmse": def_metrics["rmse"],
            "forecast_full_fit_calibration_80pct_target": calibration,
            "n_labeled_forecast_rows": float(len(labeled_forecasts)),
        })
        if not headline.empty:
            mlflow.log_metrics({
                "forecast_fold_headline_test_season": float(headline["test_season"]),
                "forecast_fold_headline_total_rmse": float(headline["total_rmse"]),
                "forecast_fold_headline_total_r2": float(headline["total_r2"]),
                "forecast_fold_headline_off_rmse": float(headline["off_rmse"]),
                "forecast_fold_headline_def_rmse": float(headline["def_rmse"]),
                "forecast_fold_headline_calibration_80pct_target": float(headline["calibration_80pct_target"]),
            })
        mlflow.pyfunc.log_model(
            artifact_path="player_projection_model", python_model=CrossSeasonForecastPyfunc(off_model_cs, def_model_cs),
        )
        run_id = run.info.run_id

    # Same registered model name as the Shrinkage Baseline, gated on the SAME metric name
    # the Shrinkage Baseline's runs actually log ("total_resid_std") -- using the held-out
    # fold3_combined_rmse here instead would compare against a metric that
    # doesn't exist on the current Production run, and maybe_promote treats
    # a missing metric as 0.0, which means "infinite improvement" by its own
    # divide-by-zero convention (real bug, caught on this script's first
    # real run: falsely auto-promoted the Cross-Season model with a nonsensical "Δ=+inf%").
    # fold3_combined_rmse/off_rmse/def_rmse are still logged above for
    # visibility -- they're just not what the automatic gate compares on.
    total_resid_std_cs = float(np.sqrt(off_resid_std_cs**2 + def_resid_std_cs**2))
    result = maybe_promote(
        client, "player-projection", run_id, "player_projection_model",
        metric_name="total_resid_std", new_value=total_resid_std_cs, higher_is_better=False,
    )
    log.info("MLflow run %s — %s", run_id, result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["baseline", "cross-season", "both"], default="both")
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help=(
            "Cap ProcessPoolExecutor worker count for build_season_skill_states and "
            "fit_all_skills. Defaults to all available cores. Reduce to 2-4 on "
            "Windows machines with limited paging file space to avoid DLL OOM errors."
        ),
    )
    args = parser.parse_args()

    engine = get_sync_engine()
    if args.phase in ("baseline", "both"):
        log.info("=== Shrinkage Baseline ===")
        run_baseline(engine)
    if args.phase in ("cross-season", "both"):
        log.info("=== Cross-Season State-Space Model ===")
        run_cross_season(engine, max_workers=args.max_workers)


if __name__ == "__main__":
    main()
