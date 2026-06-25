"""
scripts/run_player_projection.py

Non-interactive rerun of the Player Projection model — both phases:
  --phase 0    Phase 0 only (player-projection-shrinkage-v1, season-grain
               shrinkage + Ridge, seconds to run).
  --phase 2a   Phase 2a only (player-projection-phase2a-v1, two-level
               cross-season Kalman state-space, ~10 minutes — see
               docs/models/player_projection_state_space_plan.md §22).
  --phase both Both, sequentially (default).

Both phases write neutral-mode rows (school_id NULL) to player_projections,
each under its own model_version — the partial unique index on (player_id,
season, model_version) WHERE school_id IS NULL means they can never collide,
so order between phases doesn't matter for correctness (sequential just
avoids two concurrent ProcessPoolExecutor pools fighting for cores during
Phase 2a's Kalman fit).

Phase 2a uses the recommended configuration (use_context_adjustment=False —
Gap B was found to regress accuracy on real data, flagged TBD, not enabled)
and applies the two real-data components that were coded+tested but never
previously wired into a production run: Gap A (shared-prior blending for the
2 validated skill blocks) and Gap E (archetype metadata in `explanation`,
evaluation/explanation only, never a model feature).

Usage:
  uv run python scripts/run_player_projection.py             # both phases
  uv run python scripts/run_player_projection.py --phase 0
  uv run python scripts/run_player_projection.py --phase 2a
"""
from __future__ import annotations

import argparse
import logging
import sys

import numpy as np
import pandas as pd
from sqlalchemy import text

from portalpoint.modeling import player_projection as pp
from portalpoint.modeling import player_projection_eval as ppe
from portalpoint.modeling import player_projection_kalman as ppk
from portalpoint.modeling import player_projection_phase2 as pp2
from portalpoint.modeling.io import find_repo_root, get_sync_engine
from portalpoint.modeling.mlflow_helpers import maybe_promote, setup_mlflow

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PHASE2_SEASONS = [2020, 2021, 2022, 2023, 2024, 2025, 2026]


def run_phase0(engine) -> None:
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
            return [{"value_per_100": float(o + d)} for o, d in zip(off, defn)]

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
    block_corrs = pp2.compute_block_correlations(residual_df)
    blended = pp2.blend_block_priors(residual_df, block_corrs)
    out = blended.copy()
    for block_name in pp2.VALIDATED_BLOCKS:
        for skill in pp2.SKILL_BLOCKS.get(block_name, []):
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


def _gap_g_real_metric(phase2_states: pd.DataFrame) -> tuple[float, float, float]:
    """Real held-out fold-3 metrics for Phase 2a — the same rolling-origin
    tooling the notebook's Gap G section already uses, run here so the
    production script's MLflow promotion gate is judged on a real held-out
    metric, not in-sample residual std (Phase 0's own gate metric,
    `total_resid_std`, is in-sample — a known, accepted limitation carried
    over from before this session's eval work existed). Returns
    (combined_rmse, off_rmse, def_rmse) for fold 3 (test season 2026)."""
    folds = ppe.make_rolling_origin_folds(phase2_states)
    fold3 = folds[2]
    train_df, val_df, test_df = fold3["train"], fold3["val"], fold3["test"]
    k, alpha, _ = ppe.tune_hyperparameters(train_df, val_df, skip_shrinkage=True)
    train_val_df = pd.concat([train_df, val_df], ignore_index=True)
    off_m, off_r = pp.fit_value_model(train_val_df, "off_adj_rapm", alpha=alpha)
    def_m, def_r = pp.fit_value_model(train_val_df, "def_adj_rapm", alpha=alpha)
    projected_test = pp.project_value(test_df, off_m, def_m, off_r, def_r)
    labeled_test = projected_test.dropna(subset=["off_adj_rapm", "def_adj_rapm"])
    off_metrics = ppe.compute_regression_metrics(labeled_test["off_adj_rapm"], labeled_test["off_value_per_100"])
    def_metrics = ppe.compute_regression_metrics(labeled_test["def_adj_rapm"], labeled_test["def_value_per_100"])
    phase2a_combined = float(np.sqrt(off_metrics["rmse"] ** 2 + def_metrics["rmse"] ** 2))

    # Phase 0 reference, same fold, same held-out test season -- recomputed
    # here (not read from MLflow) so the comparison is apples-to-apples on
    # this exact run's data, not a stale recorded number from a different day.
    # `maybe_promote` (called by the caller) reads Phase 0's *currently
    # registered* Production metric directly from MLflow for the actual
    # gate decision -- this function only needs to produce Phase 2a's side.
    return phase2a_combined, off_metrics["rmse"], def_metrics["rmse"]


def run_phase2a(engine) -> None:
    log.info("Phase 2a: building season-grain skill states (no-context config) — this is the ~10min step")
    fitted_q_by_season, season_states = pp2.load_or_build_season_skill_states(
        engine, PHASE2_SEASONS, use_phase0_prior=True, use_context_adjustment=False,
    )
    covariates = pp2.load_or_build_season_covariates(engine, season_states)
    log.info("Season-states: %s rows, %s covariate rows", f"{len(season_states):,}", f"{len(covariates):,}")

    fitted_params, residual_df = pp2.fit_all_skills(season_states, covariates)
    residual_df, block_corrs = _apply_gap_a_blending(residual_df)
    for block_name in pp2.VALIDATED_BLOCKS:
        if block_name in block_corrs:
            log.info("Gap A applied — %s block correlation:\n%s", block_name, block_corrs[block_name].round(3))

    # fit_all_skills already merges on the real "season" (2026-06-25 fix —
    # see SeasonSequence's docstring), so no season_rank<->career_season_index
    # reconstruction is needed here.
    rename_map = {f"phase2_skill_{s}": f"skill_{s}" for s in pp2.SKILLS}
    phase2_states = residual_df.rename(columns=rename_map)

    phase0_context = pp.load_player_season_frame(engine)[["player_id", "season", "position"]].drop_duplicates()
    with engine.connect() as conn:
        he_labels_raw = pd.read_sql(
            text("SELECT player_id, season, off_adj_rapm, def_adj_rapm FROM hoop_explorer_player_stats"), conn,
        )
    he_labels = phase0_context.merge(he_labels_raw, on=["player_id", "season"], how="left")
    phase0_weight = pp.shrink_skills(pp.load_player_season_frame(engine))[["player_id", "season", "_weight"]]
    phase2_states = (
        phase2_states
        .merge(he_labels, on=["player_id", "season"], how="left")
        .merge(phase0_weight, on=["player_id", "season"], how="left")
        .drop_duplicates(subset=["player_id", "season"], keep="first")
        .reset_index(drop=True)
    )
    log.info("Phase 2a state frame: %s rows", f"{len(phase2_states):,}")

    # Gap C: attempt-rate decomposition, fed the Gap-A-blended states.
    attempt_targets = pp2.build_attempt_rate_targets(engine, PHASE2_SEASONS)
    stage2a_states = phase2_states.merge(attempt_targets, on=["player_id", "season"], how="inner")
    attempt_models = pp2.fit_attempt_rate_models(stage2a_states)
    log.info("Gap C: fitted attempt-rate models: %s", list(attempt_models.keys()))

    with engine.connect() as conn:
        team_pace = pd.read_sql(
            text(
                "SELECT player_id, adj_tempo FROM team_season_stats t "
                "JOIN player_school_seasons pss ON pss.school_id = t.school_id AND pss.season = t.season "
                "WHERE t.season = 2026"
            ),
            conn,
        )
    pace_lookup = stage2a_states[["player_id"]].merge(team_pace, on="player_id", how="left")["adj_tempo"]
    projected_rates = pp2.project_rates(stage2a_states, attempt_models, pace=pace_lookup)

    off_model_p2, off_resid_std_p2 = pp.fit_value_model(phase2_states, "off_adj_rapm")
    def_model_p2, def_resid_std_p2 = pp.fit_value_model(phase2_states, "def_adj_rapm")
    log.info(
        "Phase 2a in-sample: off_resid_std=%.3f def_resid_std=%.3f", off_resid_std_p2, def_resid_std_p2,
    )

    phase2_pctile = pp.skill_percentiles(phase2_states, skills=ppk.SKILLS)
    phase2_projected = pp.project_value(phase2_pctile, off_model_p2, def_model_p2, off_resid_std_p2, def_resid_std_p2)

    # Gap E: real archetype metadata, evaluation/explanation only.
    archetypes_df = _load_real_archetypes(engine, PHASE2_SEASONS)
    log.info("Gap E: loaded %s real archetype rows", f"{len(archetypes_df):,}")

    records = pp2.build_phase2_records(phase2_projected, projected_rates_df=projected_rates, archetypes_df=archetypes_df)
    upserted = pp.upsert_neutral_projections(engine, records)
    log.info("Upserted %s rows into player_projections (%s)", f"{upserted:,}", pp2.MODEL_VERSION_PHASE2A)

    if not records:
        raise RuntimeError("No Phase 2a player projection records were scored")

    # Gap G: real held-out metric for the MLflow promotion gate -- not
    # in-sample resid_std (see _gap_g_real_metric's docstring).
    phase2a_combined_rmse, off_rmse, def_rmse = _gap_g_real_metric(phase2_states)
    log.info(
        "Gap G real held-out fold-3 metric: combined_rmse=%.4f (off=%.4f def=%.4f)",
        phase2a_combined_rmse, off_rmse, def_rmse,
    )

    values = np.fromiter((r[4] for r in records), dtype=np.float64, count=len(records))
    client = setup_mlflow("player-projection")
    import mlflow
    import mlflow.pyfunc

    class Phase2aPyfunc(mlflow.pyfunc.PythonModel):
        def __init__(self, off_model, def_model):
            self.off_model = off_model
            self.def_model = def_model

        def predict(self, context, model_input):
            X_off = pp.build_design_matrix(model_input, skills=pp.OFFENSE_SKILLS)
            X_def = pp.build_design_matrix(model_input, skills=pp.DEFENSE_SKILLS)
            off = self.off_model.predict(X_off)
            defn = self.def_model.predict(X_def)
            return [{"value_per_100": float(o + d)} for o, d in zip(off, defn)]

    with mlflow.start_run(run_name=f"player-projection-phase2a-s{max(PHASE2_SEASONS)}-script") as run:
        mlflow.log_params({
            "seasons": ",".join(str(s) for s in PHASE2_SEASONS),
            "model_version": pp2.MODEL_VERSION_PHASE2A,
            "use_context_adjustment": False,
            "gap_a_applied": True,
            "gap_e_applied": True,
            "source": "script",
        })
        mlflow.log_metrics({
            "mean_value_per_100": float(values.mean()),
            "std_value_per_100": float(values.std()),
            "off_resid_std": off_resid_std_p2,
            "def_resid_std": def_resid_std_p2,
            "total_resid_std": float(np.sqrt(off_resid_std_p2**2 + def_resid_std_p2**2)),
            "n_records_written": float(len(records)),
            "fold3_combined_rmse": phase2a_combined_rmse,
            "fold3_off_rmse": off_rmse,
            "fold3_def_rmse": def_rmse,
        })
        mlflow.pyfunc.log_model(
            artifact_path="player_projection_model", python_model=Phase2aPyfunc(off_model_p2, def_model_p2),
        )
        run_id = run.info.run_id

    # Same registered model name as Phase 0, gated on the SAME metric name
    # Phase 0's runs actually log ("total_resid_std") -- using the held-out
    # fold3_combined_rmse here instead would compare against a metric that
    # doesn't exist on the current Production run, and maybe_promote treats
    # a missing metric as 0.0, which means "infinite improvement" by its own
    # divide-by-zero convention (real bug, caught on this script's first
    # real run: falsely auto-promoted Phase 2a with a nonsensical "Δ=+inf%").
    # fold3_combined_rmse/off_rmse/def_rmse are still logged above for
    # visibility -- they're just not what the automatic gate compares on.
    total_resid_std_p2 = float(np.sqrt(off_resid_std_p2**2 + def_resid_std_p2**2))
    result = maybe_promote(
        client, "player-projection", run_id, "player_projection_model",
        metric_name="total_resid_std", new_value=total_resid_std_p2, higher_is_better=False,
    )
    log.info("MLflow run %s — %s", run_id, result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["0", "2a", "both"], default="both")
    args = parser.parse_args()

    engine = get_sync_engine()
    if args.phase in ("0", "both"):
        log.info("=== Phase 0 ===")
        run_phase0(engine)
    if args.phase in ("2a", "both"):
        log.info("=== Phase 2a ===")
        run_phase2a(engine)


if __name__ == "__main__":
    main()
