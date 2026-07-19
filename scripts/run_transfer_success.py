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
  uv run python scripts/run_transfer_success.py --phase inference --target-season 2027
  uv run python scripts/run_transfer_success.py --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys

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
    log.info(
        "Overall success rate: %.1f%%  (n=%d labeled)",
        labeled["success"].mean() * 100, len(labeled),
    )

    brier = float("nan")
    if len(eval_df) > 0:
        from sklearn.metrics import brier_score_loss
        brier = brier_score_loss(
            eval_df["success"].astype(int), eval_df["success_probability"]
        )
        log.info("Brier score (out-of-sample, has_prior_history rows): %.4f", brier)

    log.info("Success tier distribution:")
    for tier, count in result["success_tier"].value_counts().sort_index().items():
        log.info("  %-12s %d", tier, count)

    return brier, result


def run_inference(engine, df_historical, target_season: int, args) -> int:
    """Forward-score active portal candidates and write to transfer_success_scores."""
    log.info("Loading active candidates for season=%d", target_season)
    df_active = ts.load_active_candidates(engine, target_season=target_season)
    log.info(
        "Active candidates: %d rows (%d unique players)",
        len(df_active), df_active["player_id"].nunique(),
    )
    if len(df_active) == 0:
        log.warning("No active candidates found for season=%d — check is_portal_candidate flags", target_season)
        return 0

    scored = ts.score_active_candidates(
        df_active=df_active,
        df_historical=df_historical,
        target_season=target_season,
        shrinkage_k=args.shrinkage_k,
        decay_lambda=args.decay_lambda,
    )
    log.info("Scored %d candidate rows", len(scored))

    if args.dry_run:
        log.info("--dry-run: skipping DB write (%d rows would be upserted)", len(scored))
        return len(scored)

    records = scored.to_dict("records")
    n = ts.upsert_transfer_success_scores(engine, records)
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
    return p.parse_args()


def main() -> None:
    args = parse_args()
    load_env()
    engine = get_sync_engine()

    client = setup_mlflow(MLFLOW_MODEL_NAME)
    df_historical = None

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
        })

        brier = float("nan")

        if args.phase in ("backtest", "both"):
            brier, df_historical = run_backtest(engine, args)
            if not np.isnan(brier):
                mlflow.log_metric("brier_score", brier)

        if args.phase in ("inference", "both"):
            if df_historical is None:
                # inference-only: still need a historical frame for cell rates
                log.info("inference-only: loading historical frame for cell rates")
                df_raw = ts.load_transfer_data(engine, model_version=PROJECTION_MODEL_VERSION)
                df_historical = ts.run_transfer_success_pipeline(
                    df_raw,
                    shrinkage_k=args.shrinkage_k,
                    decay_lambda=args.decay_lambda,
                )
            n_written = run_inference(engine, df_historical, args.target_season, args)
            mlflow.log_metric("n_candidate_rows_written", float(n_written))

        # Always log the marker model so register_model in maybe_promote works.
        mlflow.pyfunc.log_model(
            artifact_path="transfer_success_model",
            python_model=TransferSuccessPyfunc(),
        )
        run_id = run.info.run_id

    log.info("MLflow run_id: %s", run_id)

    # Promote only after backtest (Brier score is the gate metric).
    # lower Brier = better, so higher_is_better=False.
    if args.phase in ("backtest", "both") and not np.isnan(brier) and not args.dry_run:
        result = maybe_promote(
            client, MLFLOW_MODEL_NAME, run_id, "transfer_success_model",
            metric_name="brier_score", new_value=brier, higher_is_better=False,
        )
        log.info("Promotion result: %s", result)
    elif args.dry_run:
        log.info("--dry-run: skipping MLflow promotion")
    elif np.isnan(brier) and args.phase in ("backtest", "both"):
        log.warning("No Brier score computed (no labeled eval rows) — skipping promotion")


if __name__ == "__main__":
    main()
