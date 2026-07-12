"""Team Rating Projection — non-interactive rerun script.

Usage:
    uv run python scripts/run_team_rating_projection.py
    uv run python scripts/run_team_rating_projection.py --target-season 2027 --source-season 2026
    uv run python scripts/run_team_rating_projection.py --train-seasons 2021 2022 2023 2024 2025 2026
    uv run python scripts/run_team_rating_projection.py --skip-cv  # skip CV for a fast rerun

Writes to team_rating_projections (upsert). MLflow tracking: experiment
"team-rating-projection", model "team-rating-scorer".
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import mlflow
import numpy as np
import pandas as pd

# Ensure src/ is importable when run from repo root without `uv run`.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from portalpoint.modeling.io import find_repo_root, load_env, get_sync_engine
from portalpoint.modeling.mlflow_helpers import setup_mlflow, maybe_promote
from portalpoint.modeling.team_rating_projection import (
    MODEL_VERSION,
    NEUTRAL_MODEL_PRIORITY,
    ROSTER_FEATURES,
    build_historical_roster_states,
    build_school_baselines,
    build_candidate_roster,
    build_roster_features,
    build_slot_baselines,
    build_explanation_payload,
    compute_national_percentiles,
    fit_team_translation,
    load_inference_data,
    predict_adj_o_d_batch,
    analytical_ci,
    rolling_origin_cv,
    upsert_team_rating_projections,
    _conference_tier,
    CONFERENCE_TIER_CUTS,
    PLAYING_TIME_MODEL_VERSION,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("run_team_rating_projection")

EXPERIMENT_NAME = "team-rating-projection"
MLFLOW_MODEL_NAME = "team-rating-scorer"
GATE_METRIC = "fold3_em_rmse"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run Team Rating Projection model")
    p.add_argument("--target-season", type=int, default=2027,
                   help="Season to generate projections for (default: 2027)")
    p.add_argument("--source-season", type=int, default=2026,
                   help="Most recent completed season used for baseline roster stats (default: 2026)")
    p.add_argument("--train-seasons", type=int, nargs="+",
                   default=list(range(2021, 2027)),
                   help="Seasons to include in training (default: 2021-2026)")
    p.add_argument("--skip-cv", action="store_true",
                   help="Skip cross-validation (faster rerun when model is already validated)")
    p.add_argument("--portal-only", action="store_true", default=True,
                   help="Only compute counterfactuals for portal candidates (default: True)")
    p.add_argument("--school-chunk-size", type=int, default=0,
                   help="Process schools in chunks of this size (0 = all at once)")
    return p.parse_args()


def run_team_rating_projection(
    target_season: int = 2027,
    source_season: int = 2026,
    train_seasons: list[int] | None = None,
    skip_cv: bool = False,
    portal_only: bool = True,
    school_chunk_size: int = 0,
) -> None:
    if train_seasons is None:
        train_seasons = list(range(2021, 2027))

    load_env()
    engine = get_sync_engine()
    client = setup_mlflow(EXPERIMENT_NAME)

    log.info("=== Team Rating Projection — target_season=%d source_season=%d ===",
             target_season, source_season)
    log.info("Train seasons: %s", train_seasons)

    with mlflow.start_run() as run:
        run_id = run.info.run_id
        mlflow.log_params({
            "model_version":   MODEL_VERSION,
            "target_season":   target_season,
            "source_season":   source_season,
            "train_seasons":   json.dumps(train_seasons),
            "skip_cv":         skip_cv,
            "portal_only":     portal_only,
            "neutral_model":   NEUTRAL_MODEL_PRIORITY[0],
            "playing_time_model": PLAYING_TIME_MODEL_VERSION,
        })

        # ------------------------------------------------------------------
        # Step 1: Build historical training data
        # ------------------------------------------------------------------
        log.info("Step 1: Building historical roster states (%s)...", train_seasons)
        features_df, labels_df, slot_baselines = build_historical_roster_states(
            engine, train_seasons
        )
        log.info("  -> %d school-seasons for training", len(features_df))
        mlflow.log_param("n_train_school_seasons", len(features_df))

        if len(features_df) < 50:
            log.error("Too few training rows (%d) — aborting.", len(features_df))
            raise RuntimeError(f"Insufficient training data: {len(features_df)} rows")

        # ------------------------------------------------------------------
        # Step 2: Cross-validation
        # ------------------------------------------------------------------
        cv_metrics: dict = {}
        if not skip_cv:
            log.info("Step 2: 3-fold rolling-origin CV...")
            cv_metrics = rolling_origin_cv(features_df, labels_df)
            for fm in cv_metrics.get("fold_metrics", []):
                mlflow.log_metrics({
                    f"fold{fm['fold']}_off_rmse": fm["off_rmse"],
                    f"fold{fm['fold']}_def_rmse": fm["def_rmse"],
                    f"fold{fm['fold']}_em_rmse":  fm["em_rmse"],
                    f"fold{fm['fold']}_off_r2":   fm["off_r2"],
                    f"fold{fm['fold']}_def_r2":   fm["def_r2"],
                })
            if "fold3_em_rmse" in cv_metrics:
                mlflow.log_metric(GATE_METRIC, cv_metrics["fold3_em_rmse"])
            if "mean_em_rmse" in cv_metrics:
                mlflow.log_metric("mean_em_rmse", cv_metrics["mean_em_rmse"])
        else:
            log.info("Step 2: CV skipped.")

        # ------------------------------------------------------------------
        # Step 3: Fit final model on all training data
        # ------------------------------------------------------------------
        log.info("Step 3: Fitting final team translation models on all %d rows...", len(features_df))
        models = fit_team_translation(features_df, labels_df)
        models.slot_baselines = slot_baselines
        models.train_seasons = train_seasons
        models.cv_metrics = cv_metrics
        mlflow.log_metric("off_resid_std", models.off_resid_std)
        mlflow.log_metric("def_resid_std", models.def_resid_std)

        # Log slot baselines as artifact (convert tuple keys to str for JSON)
        mlflow.log_dict(
            {str(k): v for k, v in slot_baselines.items()},
            "slot_baselines.json",
        )

        # Log model coefficients
        coef_report = {
            f: {"off_coef": float(models.off_model.coef_[i]),
                "def_coef": float(models.def_model.coef_[i])}
            for i, f in enumerate(ROSTER_FEATURES)
        }
        mlflow.log_dict(coef_report, "model_coefficients.json")
        log.info("  off_resid_std=%.3f  def_resid_std=%.3f", models.off_resid_std, models.def_resid_std)

        # ------------------------------------------------------------------
        # Step 4: Load 2027 inference data
        # ------------------------------------------------------------------
        log.info("Step 4: Loading inference data (target_season=%d)...", target_season)
        data = load_inference_data(engine, target_season, source_season)

        n_portal = len(data["portal_ids"])
        n_pt = len(data["playing_time"]) if not data["playing_time"].empty else 0
        log.info("  portal candidates: %d  playing_time rows: %d", n_portal, n_pt)

        if n_pt == 0:
            log.error(
                "No playing_time_projections for season=%d model=%s — aborting.\n"
                "Run scripts/run_playing_time.py --target-season %d first.",
                target_season, PLAYING_TIME_MODEL_VERSION, target_season,
            )
            raise RuntimeError("Hard gate: playing_time_projections not populated.")

        # School adj_em for tier computation
        sm = data["school_meta"]
        season_adj_ems = sm["adj_em"].dropna().to_numpy(dtype=float)
        school_adj_ems = sm.set_index("school_id")["adj_em"].dropna().to_dict()
        school_adj_ems = {int(k): float(v) for k, v in school_adj_ems.items()}

        # ------------------------------------------------------------------
        # Step 5: Build baseline rosters per school
        # ------------------------------------------------------------------
        log.info("Step 5: Building 2027 baseline rosters...")
        school_baselines, freshman_audit = build_school_baselines(
            data, slot_baselines, school_adj_ems, season_adj_ems, source_season
        )
        log.info("  -> %d schools with baseline rosters", len(school_baselines))
        mlflow.log_param("n_schools_baseline", len(school_baselines))

        # A: Log freshman-prior audit as MLflow artifact; warn for heavy-freshman schools
        heavy_fr = [a for a in freshman_audit if a["n_freshman_priors"] >= 3 or a["total_freshman_min_pct"] >= 20.0]
        if heavy_fr:
            log.warning(
                "Step 5 (freshman audit): %d school(s) with ≥3 priors or ≥20%% freshman min_pct: %s",
                len(heavy_fr),
                ", ".join(f"{a['school_name']}(n={a['n_freshman_priors']}, {a['total_freshman_min_pct']}%%)"
                          for a in heavy_fr[:10]),
            )
        audit_df = pd.DataFrame(freshman_audit)
        mlflow.log_metric("n_schools_with_freshman_priors", int((audit_df["n_freshman_priors"] > 0).sum()))
        mlflow.log_metric("n_schools_heavy_freshman_priors", len(heavy_fr))
        mlflow.log_dict(
            audit_df.to_dict(orient="records"),
            "freshman_prior_audit.json",
        )
        log.info(
            "  -> freshman prior audit: %d schools have ≥1 prior, %d heavy (≥3 or ≥20%%)",
            int((audit_df["n_freshman_priors"] > 0).sum()), len(heavy_fr),
        )

        # ------------------------------------------------------------------
        # Step 6: Candidate counterfactuals (vectorized per player)
        # ------------------------------------------------------------------
        log.info("Step 6: Computing candidate counterfactuals...")
        pt_df = data["playing_time"]
        neutral_df = data["neutral_proj"]

        if neutral_df.empty:
            log.warning("No neutral projections found — candidate value_per_100 will default to 0")
            neutral_index: dict = {}
        else:
            neutral_index = neutral_df.set_index("player_id").to_dict("index")
        candidate_stats_index = (
            data["prior_stats"]
            .drop_duplicates(subset=["player_id"], keep="first")
            .set_index("player_id")[["position", "three_point_rate", "off_reb_pct"]]
            .to_dict("index")
            if not data["prior_stats"].empty else {}
        )

        # Build pt_index without iterrows (10-50x faster on 457K rows)
        pt_records = pt_df.to_dict("records")
        pt_index: dict[tuple[int, int], dict] = {
            (int(r["player_id"]), int(r["school_id"])): r for r in pt_records
        }

        # Pre-compute baseline adj_o/d for all schools in one batch
        all_schools = sorted(school_baselines.keys())
        bl_feature_matrix = np.array([
            [school_baselines[s]["features"][f] for f in ROSTER_FEATURES]
            for s in all_schools
        ])  # (n_schools, 14)
        bl_adj_o_arr, bl_adj_d_arr = predict_adj_o_d_batch(bl_feature_matrix, models)
        bl_adj_o_by_school = dict(zip(all_schools, bl_adj_o_arr.tolist()))
        bl_adj_d_by_school = dict(zip(all_schools, bl_adj_d_arr.tolist()))

        records: list[dict] = []
        n_skipped = 0
        log_every = max(1, len(data["portal_ids"]) // 10)

        for p_idx, player_id in enumerate(data["portal_ids"]):
            if p_idx % log_every == 0:
                log.info("  Player %d/%d  records=%d", p_idx + 1, len(data["portal_ids"]), len(records))

            proj_row = neutral_index.get(int(player_id), {})
            cand_value = float(proj_row.get("value_per_100", 0.0))
            cand_proj = pd.Series({
                "value_per_100": cand_value,
                **candidate_stats_index.get(int(player_id), {}),
            })

            # Build candidate features for all valid schools
            valid_schools: list[int] = []
            ca_features_list: list[dict] = []
            pt_rows_list: list[dict] = []

            for school_id in all_schools:
                pt_key = (int(player_id), int(school_id))
                if pt_key not in pt_index:
                    n_skipped += 1
                    continue
                pt_row_dict = pt_index[pt_key]
                baseline_info = school_baselines[school_id]
                candidate_rows, returning_pct = build_candidate_roster(
                    baseline_info, pd.Series(pt_row_dict), cand_proj, slot_baselines
                )
                ca_features = build_roster_features(
                    candidate_rows, baseline_info["tier"], baseline_info["adj_tempo"],
                    returning_pct, slot_baselines,
                )
                valid_schools.append(school_id)
                ca_features_list.append(ca_features)
                pt_rows_list.append(pt_row_dict)

            if not valid_schools:
                continue

            # Batch predict candidate adj_o/d for all valid schools in one call
            ca_feature_matrix = np.array([
                [f[feat] for feat in ROSTER_FEATURES] for f in ca_features_list
            ])
            ca_adj_o_arr, ca_adj_d_arr = predict_adj_o_d_batch(ca_feature_matrix, models)

            for i, school_id in enumerate(valid_schools):
                bl_adj_o = float(bl_adj_o_by_school[school_id])
                bl_adj_d = float(bl_adj_d_by_school[school_id])
                ca_adj_o = float(ca_adj_o_arr[i])
                ca_adj_d = float(ca_adj_d_arr[i])

                delta_adj_o  = ca_adj_o - bl_adj_o
                delta_adj_d  = ca_adj_d - bl_adj_d
                delta_adj_em = delta_adj_o - delta_adj_d

                n_fr = school_baselines[school_id].get("n_freshman_priors", 0)
                ci_lower, ci_upper = analytical_ci(delta_adj_em, models, n_freshman_priors=n_fr)

                delta = {
                    "baseline_adj_o":  round(bl_adj_o, 3),
                    "baseline_adj_d":  round(bl_adj_d, 3),
                    "baseline_adj_em": round(bl_adj_o - bl_adj_d, 3),
                    "projected_adj_o": round(ca_adj_o, 3),
                    "projected_adj_d": round(ca_adj_d, 3),
                    "projected_adj_em": round(ca_adj_o - ca_adj_d, 3),
                    "delta_adj_o":     round(delta_adj_o, 3),
                    "delta_adj_d":     round(-delta_adj_d, 3),
                    "delta_adj_em":    round(delta_adj_em, 3),
                }

                pt_row_dict = pt_rows_list[i]
                pt_row = pd.Series(pt_row_dict)
                explanation = build_explanation_payload(
                    school_baselines[school_id]["features"], ca_features_list[i],
                    models, pt_row, delta,
                )

                records.append({
                    "player_id":              int(player_id),
                    "school_id":              school_id,
                    "season":                 target_season,
                    "current_adj_em":         delta["baseline_adj_em"],
                    "projected_adj_em":       delta["projected_adj_em"],
                    "delta_adj_em":           delta["delta_adj_em"],
                    "baseline_adj_o":         delta["baseline_adj_o"],
                    "baseline_adj_d":         delta["baseline_adj_d"],
                    "projected_adj_o":        delta["projected_adj_o"],
                    "projected_adj_d":        delta["projected_adj_d"],
                    "ci_lower":               ci_lower,
                    "ci_upper":               ci_upper,
                    "expected_minutes_input": float(pt_row.get("expected_minutes", 0)),
                    "candidate_usage_role":   str(pt_row.get("usage_role", "rotation")),
                    "explanation":            explanation,
                    "minutes_distribution":   pt_row_dict.get("displaced_minutes") or {},
                    "baseline_adj_em":        delta["baseline_adj_em"],
                })

        log.info("  %d counterfactuals computed, %d skipped (no playing_time row)", len(records), n_skipped)

        # ------------------------------------------------------------------
        # Step 7: Percentiles + conference ranks
        # ------------------------------------------------------------------
        log.info("Step 7: Computing national percentiles and conference ranks...")
        records = compute_national_percentiles(records, data["school_meta"])

        # ------------------------------------------------------------------
        # Step 8: DB write
        # ------------------------------------------------------------------
        log.info("Step 8: Writing %d rows to team_rating_projections...", len(records))
        n_written = upsert_team_rating_projections(engine, records, MODEL_VERSION)
        mlflow.log_metric("n_rows_written", n_written)

        # ------------------------------------------------------------------
        # Step 9: MLflow model + maybe_promote
        # ------------------------------------------------------------------
        log.info("Step 9: Logging models to MLflow and checking promotion gate...")
        import pickle, tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            off_path = os.path.join(tmpdir, "off_model.pkl")
            def_path = os.path.join(tmpdir, "def_model.pkl")
            with open(off_path, "wb") as f:
                pickle.dump({"model": models.off_model, "scaler": models.off_scaler}, f)
            with open(def_path, "wb") as f:
                pickle.dump({"model": models.def_model, "scaler": models.def_scaler}, f)
            mlflow.log_artifact(off_path, artifact_path="team_rating_models")
            mlflow.log_artifact(def_path, artifact_path="team_rating_models")

        fresh_metrics = mlflow.get_run(run_id).data.metrics
        if not skip_cv and GATE_METRIC in fresh_metrics:
            gate_value = fresh_metrics[GATE_METRIC]
            outcome = maybe_promote(
                client,
                MLFLOW_MODEL_NAME,
                run_id,
                "team_rating_models",
                GATE_METRIC,
                gate_value,
                higher_is_better=False,   # lower RMSE is better
            )
            log.info("Promotion: %s", outcome)
        else:
            log.info("CV skipped — no promotion gate check.")

        log.info("=== Done. %d rows written. run_id=%s ===", n_written, run_id)


def main() -> None:
    args = parse_args()
    run_team_rating_projection(
        target_season=args.target_season,
        source_season=args.source_season,
        train_seasons=args.train_seasons,
        skip_cv=args.skip_cv,
        portal_only=args.portal_only,
        school_chunk_size=args.school_chunk_size,
    )


if __name__ == "__main__":
    main()
