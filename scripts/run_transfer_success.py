"""
scripts/run_transfer_success.py

Non-interactive run of the Transfer Success evaluation pipeline (Model 5).

Two phases:
  --phase backtest   Score completed historical transfers, compute out-of-sample
                     Brier score, log to MLflow, optionally promote the model.
  --phase inference  Forward-score active portal candidates and write to
                     transfer_success_scores (requires backtest to have run first
                     so the empirical Bayes cell rates exist on a labeled frame).
  --phase both       Run backtest then inference sequentially (default).

The backtest phase is required before inference because inference re-uses the
labeled historical frame to build cell rates; there is no separate "fit" step.

Usage:
  uv run python scripts/run_transfer_success.py
  uv run python scripts/run_transfer_success.py --phase backtest
  uv run python scripts/run_transfer_success.py --phase backtest --tune
  uv run python scripts/run_transfer_success.py --phase inference --target-season 2027
  uv run python scripts/run_transfer_success.py --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path

import numpy as np

import mlflow
import mlflow.pyfunc

from portalpoint.modeling.io import get_sync_engine, load_env
from portalpoint.modeling.mlflow_helpers import maybe_promote, setup_mlflow
from portalpoint.modeling import transfer_success as ts

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

PROJECTION_MODEL_VERSION = "player-destination-proj-v1"
MLFLOW_MODEL_NAME = "transfer-success"


# ---------------------------------------------------------------------------
# MLflow marker model — required by maybe_promote's register_model call.
# Transfer Success has no sklearn/Ridge artifact; we register a trivial
# PythonModel as the registry artifact, same pattern as recommendations.py.
# ---------------------------------------------------------------------------
class TransferSuccessPyfunc(mlflow.pyfunc.PythonModel):
    """Marker model for MLflow registry. Transfer Success is table-scored,
    not a serialized model — this satisfies register_model's artifact requirement."""

    def predict(self, context, model_input):
        return model_input


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------

def run_backtest(engine, args) -> tuple[float, object]:
    """Load + score completed historical transfers. Returns (brier_score, result_df)."""
    log.info("Loading completed transfers (projection_model_version=%s)", PROJECTION_MODEL_VERSION)
    df_raw = ts.load_transfer_data(engine, model_version=PROJECTION_MODEL_VERSION)
    log.info(
        "Loaded %d completed transfers across %d seasons",
        len(df_raw), df_raw["season"].nunique(),
    )
    log.info(
        "  rows with team_cluster_label: %d  |  player_cluster: %d  "
        "|  actual RAPM: %d  |  projection: %d",
        df_raw["team_cluster_label"].notna().sum(),
        df_raw["player_cluster"].notna().sum(),
        df_raw["actual_value_per_100"].notna().sum(),
        df_raw["projected_value_per_100"].notna().sum(),
    )

    if len(df_raw) == 0:
        log.warning("No completed transfers found — check DB connection and model_version")
        sys.exit(0)

    log.info(
        "Running pipeline (shrinkage_k=%d, decay_lambda=%.2f)",
        args.shrinkage_k, args.decay_lambda,
    )
    result = ts.run_transfer_success_pipeline(
        df_raw,
        shrinkage_k=args.shrinkage_k,
        decay_lambda=args.decay_lambda,
    )

    labeled = result[result["success_label"].notna()]
    eval_df  = result[result["success_label"].notna() & result["has_prior_history"]]
    log.info("Pipeline complete — %d rows, %d labeled, %d with prior-season history",
             len(result), len(labeled), len(eval_df))
    base_success_rate = float(labeled["success"].mean()) if len(labeled) > 0 else float("nan")
    log.info(
        "Overall success rate: %.1f%%  (n=%d labeled)",
        base_success_rate * 100, len(labeled),
    )

    brier = float("nan")
    brier_baseline_global = float("nan")
    log_loss = float("nan")
    if len(eval_df) > 0:
        from sklearn.metrics import brier_score_loss, log_loss as sklearn_log_loss
        y_true = eval_df["success"].astype(int)
        y_pred = eval_df["success_probability"]
        brier = brier_score_loss(y_true, y_pred)
        log_loss = sklearn_log_loss(y_true, y_pred, labels=[0, 1])
        # Sanity benchmark: always predict the training global success rate.
        train_global = float(labeled["success"].mean())
        brier_baseline_global = brier_score_loss(
            y_true, np.full(len(y_true), train_global)
        )
        log.info("Brier score (out-of-sample, has_prior_history rows): %.4f", brier)
        log.info("Brier baseline (global rate=%.3f): %.4f", train_global, brier_baseline_global)
        log.info("Log loss: %.4f", log_loss)

    log.info("Success tier distribution:")
    for tier, count in result["success_tier"].value_counts().sort_index().items():
        log.info("  %-12s %d", tier, count)

    # Stash metrics on result for MLflow logging in main().
    beta_summary = ts.summarize_projection_beta(result)
    result.attrs["backtest_metrics"] = {
        "brier_score": brier,
        "brier_baseline_global": brier_baseline_global,
        "log_loss": log_loss,
        "n_eval_rows": float(len(eval_df)),
        "n_labeled_rows": float(len(labeled)),
        "base_success_rate": base_success_rate,
        **beta_summary,
    }
    if beta_summary:
        log.info(
            "Projection covariate beta (median across seasons): %.4f",
            beta_summary.get("beta_projection_median", float("nan")),
        )

    return brier, result


def run_inference(engine, df_historical, target_season: int, args) -> int:
    """Forward-score active portal candidates and write to transfer_success_scores."""
    n_active = ts.count_active_candidates(engine, target_season)
    log.info("Active candidates: %d rows (streaming from RDS)", n_active)
    if n_active == 0:
        log.warning("No active candidates found for season=%d — check is_portal_candidate flags", target_season)
        return 0

    chunk_iter = ts.iter_scored_active_candidate_chunks(
        df_historical=df_historical,
        target_season=target_season,
        shrinkage_k=args.shrinkage_k,
        decay_lambda=args.decay_lambda,
        engine=engine,
        n_active=n_active,
    )

    if args.dry_run:
        n = sum(len(chunk) for chunk in chunk_iter)
        log.info("--dry-run: skipping DB write (%d rows would be upserted)", n)
        return n

    log.info("Scoring and writing %d candidate rows in streamed chunks", n_active)
    n = 0
    for chunk in chunk_iter:
        n += ts.upsert_transfer_success_scores(engine, chunk)
    log.info("Upserted %d rows into transfer_success_scores (model_version=%s)",
             n, ts.MODEL_VERSION)
    return n


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Transfer Success evaluation pipeline")
    p.add_argument(
        "--phase",
        choices=["backtest", "inference", "both"],
        default="both",
        help="Which phase to run (default: both)",
    )
    p.add_argument(
        "--target-season",
        type=int,
        default=2027,
        help="Play season to forward-score active candidates (inference/both, default: 2027)",
    )
    p.add_argument(
        "--shrinkage-k",
        type=int,
        default=ts.SHRINKAGE_K,
        help=f"Empirical Bayes shrinkage constant K (default: {ts.SHRINKAGE_K})",
    )
    p.add_argument(
        "--decay-lambda",
        type=float,
        default=ts.DECAY_LAMBDA,
        help=f"Time-decay per season-of-age, <=1.0 (default: {ts.DECAY_LAMBDA})",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Load + score but skip DB writes and MLflow promotion",
    )
    p.add_argument(
        "--tune",
        action="store_true",
        help="Grid-search shrinkage_k and decay_lambda before backtest (backtest/both only)",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    load_env()
    engine = get_sync_engine()

    client = setup_mlflow(MLFLOW_MODEL_NAME)
    df_historical = None
    grid_df = None
    tune_summary: dict[str, float] = {}

    # Optional hyperparameter grid search (backtest phases only).
    if args.tune and args.phase in ("backtest", "both"):
        log.info("Loading transfers for hyperparameter tuning")
        df_raw = ts.load_transfer_data(engine, model_version=PROJECTION_MODEL_VERSION)
        if len(df_raw) == 0:
            log.warning("No completed transfers found — cannot tune")
            sys.exit(0)
        df_labeled = ts.label_transfer_success(df_raw)
        log.info(
            "Tuning over K=%s, λ=%s",
            ts.K_CELL_CANDIDATES, ts.LAMBDA_CANDIDATES,
        )
        best_k, best_lam, grid_df = ts.tune_transfer_success_hyperparameters(df_labeled)
        args.shrinkage_k = best_k
        args.decay_lambda = best_lam
        log.info("Best hyperparameters: shrinkage_k=%d, decay_lambda=%.2f", best_k, best_lam)
        if not grid_df.empty:
            log.info("Grid search Brier range: %.4f – %.4f",
                     grid_df["brier_score"].min(), grid_df["brier_score"].max())
        # Shrinkage audit on default-K scored frame for MLflow metrics.
        scored_default = ts.compute_success_probability(df_labeled)
        tune_summary = ts.summarize_shrinkage_sample_sizes(scored_default)

    with mlflow.start_run(
        run_name=f"transfer-success-{args.phase}-s{args.target_season}-script"
    ) as run:
        mlflow.log_params({
            "phase": args.phase,
            "model_version": ts.MODEL_VERSION,
            "projection_model_version": PROJECTION_MODEL_VERSION,
            "shrinkage_k": args.shrinkage_k,
            "decay_lambda": args.decay_lambda,
            "target_season": args.target_season,
            "source": "script",
            "dry_run": str(args.dry_run),
            "tune": str(args.tune),
        })

        if args.tune and grid_df is not None:
            mlflow.log_params({
                "best_shrinkage_k": args.shrinkage_k,
                "best_decay_lambda": args.decay_lambda,
            })
            for key, val in tune_summary.items():
                mlflow.log_metric(key, val)
            if not grid_df.empty:
                with tempfile.TemporaryDirectory() as tmpdir:
                    grid_path = Path(tmpdir) / "grid_results.csv"
                    grid_df.to_csv(grid_path, index=False)
                    mlflow.log_artifact(str(grid_path))

        brier = float("nan")

        if args.phase in ("backtest", "both"):
            brier, df_historical = run_backtest(engine, args)
            metrics = getattr(df_historical, "attrs", {}).get("backtest_metrics", {})
            cal_metrics = ts.summarize_calibration_metrics(df_historical)
            metrics.update(cal_metrics)
            for key, val in metrics.items():
                if not np.isnan(val):
                    mlflow.log_metric(key, val)
            beta_median = metrics.get("beta_projection_median")
            if beta_median is not None and not np.isnan(beta_median):
                mlflow.log_param("beta_projection", beta_median)

            with tempfile.TemporaryDirectory() as tmpdir:
                artifact_paths = ts.write_calibration_artifacts(
                    df_historical,
                    Path(tmpdir),
                    shrinkage_k=args.shrinkage_k,
                    decay_lambda=args.decay_lambda,
                    beta_projection=beta_median,
                    brier_score=brier,
                )
                for path in artifact_paths.values():
                    if path.exists():
                        mlflow.log_artifact(str(path))

        if args.phase in ("inference", "both"):
            if df_historical is None:
                # inference-only: labeled historical rows for rate tables + comps
                log.info("inference-only: loading historical frame for cell rates")
                df_raw = ts.load_transfer_data(engine, model_version=PROJECTION_MODEL_VERSION)
                df_historical = ts.compute_drift(ts.label_transfer_success(df_raw))
            n_written = run_inference(engine, df_historical, args.target_season, args)
            mlflow.log_metric("n_candidate_rows_written", float(n_written))

        # Always log the marker model so register_model in maybe_promote works.
        mlflow.pyfunc.log_model(
            artifact_path="transfer_success_model",
            python_model=TransferSuccessPyfunc(),
        )
        run_id = run.info.run_id

        # Promote only after backtest (Brier score is the gate metric).
        # lower Brier = better, so higher_is_better=False.
        if args.phase in ("backtest", "both") and not np.isnan(brier) and not args.dry_run:
            promotion = maybe_promote(
                client, MLFLOW_MODEL_NAME, run_id, "transfer_success_model",
                metric_name="brier_score", new_value=brier, higher_is_better=False,
            )
            log.info("Promotion result: %s", promotion)
            if promotion.delta_pct is not None:
                mlflow.log_metric("promotion_delta_pct", promotion.delta_pct)
        elif args.dry_run:
            log.info("--dry-run: skipping MLflow promotion")
        elif np.isnan(brier) and args.phase in ("backtest", "both"):
            log.warning("No Brier score computed (no labeled eval rows) — skipping promotion")

    log.info("MLflow run_id: %s", run_id)


if __name__ == "__main__":
    main()
