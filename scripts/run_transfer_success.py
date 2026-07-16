"""
scripts/run_transfer_success.py

Non-interactive run of the Transfer Success evaluation pipeline (Model 5).

Loads completed historical transfers, scores each with empirical Bayes
success probability (expanding window, time-decay weighted), and prints
a summary. Writes nothing to the DB yet — results are returned for
inspection. Wire up a DB write when the table schema is finalised.

Usage:
  uv run python scripts/run_transfer_success.py
  uv run python scripts/run_transfer_success.py --model-version player-destination-proj-v1
  uv run python scripts/run_transfer_success.py --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys

from portalpoint.modeling.io import get_sync_engine, load_env
from portalpoint.modeling.transfer_success import (
    DECAY_LAMBDA,
    SHRINKAGE_K,
    load_transfer_data,
    run_transfer_success_pipeline,
)

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Transfer Success evaluation pipeline")
    parser.add_argument(
        "--model-version",
        default="player-destination-proj-v1",
        help="player_projections.model_version to join as the projection baseline "
             "(default: player-destination-proj-v1)",
    )
    parser.add_argument(
        "--shrinkage-k",
        type=int,
        default=SHRINKAGE_K,
        help=f"Empirical Bayes shrinkage constant K (default: {SHRINKAGE_K})",
    )
    parser.add_argument(
        "--decay-lambda",
        type=float,
        default=DECAY_LAMBDA,
        help=f"Time-decay per season-of-age, <=1.0 (default: {DECAY_LAMBDA})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load + score but skip any DB writes",
    )
    args = parser.parse_args()

    load_env()
    engine = get_sync_engine()

    log.info("Loading completed transfers (model_version=%s)", args.model_version)
    df_raw = load_transfer_data(engine, model_version=args.model_version)
    log.info(
        "Loaded %d completed transfers across %d seasons",
        len(df_raw),
        df_raw["season"].nunique(),
    )
    log.info(
        "  rows with team_cluster_label: %d  |  player_cluster: %d  |  actual RAPM: %d  |  projection: %d",
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
        args.shrinkage_k,
        args.decay_lambda,
    )
    result = run_transfer_success_pipeline(
        df_raw,
        shrinkage_k=args.shrinkage_k,
        decay_lambda=args.decay_lambda,
    )

    # Summary
    labeled = result[result["success_label"].notna()]
    eval_df  = result[result["success_label"].notna() & result["has_prior_history"]]
    log.info("Pipeline complete — %d rows, %d labeled", len(result), len(labeled))
    log.info(
        "Overall success rate: %.1f%%  (n=%d labeled, %d with prior-season history)",
        labeled["success"].mean() * 100,
        len(labeled),
        len(eval_df),
    )

    if len(eval_df) > 0:
        from sklearn.metrics import brier_score_loss
        brier = brier_score_loss(eval_df["success"].astype(int), eval_df["success_probability"])
        log.info("Brier score (out-of-sample, has_prior_history rows): %.4f", brier)

    log.info("Success tier distribution:")
    for tier, count in result["success_tier"].value_counts().sort_index().items():
        log.info("  %-10s %d", tier, count)

    if args.dry_run:
        log.info("--dry-run: skipping DB write")
        return

    # TODO: write result to a transfer_success_scores table once schema is finalised
    log.info("DB write not yet implemented — add upsert here when table schema is ready")


if __name__ == "__main__":
    main()
