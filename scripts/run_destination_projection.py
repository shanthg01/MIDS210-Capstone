"""
scripts/run_destination_projection.py

Non-interactive rerun of the Destination-Adjusted Player Projection model.

Translates neutral per-100 talent estimates into school-specific projected
production for portal candidates evaluated against every D1 destination.

Prerequisites (must run first):
  1. run_player_projection.py --phase cross-season   (fills player-proj-phase2a-fcast-v1 rows)
  2. run_playing_time.py --target-season 2027        (fills playing_time_projections for 2027)

This script:
  - Loads portal candidates (is_portal_candidate = true) from player_team_fit_scores
  - Loads best available neutral projection per player (forecast → same-season → Phase 0)
  - Inner-joins on playing_time_projections (hard gate — no PT = no destination row)
  - Computes four context deltas (role/usage, style/skill, roster, competition tier)
  - Applies ±0.75/±1.50 caps, translates to per-game rates via destination pace
  - Upserts to player_projections (projection_mode='destination', school_id set)
  - Logs metrics to MLflow

Usage:
  uv run python scripts/run_destination_projection.py
  uv run python scripts/run_destination_projection.py --target-season 2027 --source-season 2026
  uv run python scripts/run_destination_projection.py --portal-only
  uv run python scripts/run_destination_projection.py --player-ids 123 456 --school-ids 10 20
  uv run python scripts/run_destination_projection.py --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from portalpoint.modeling.destination_projection import (
    MODEL_VERSION,
    NEUTRAL_MODEL_PRIORITY,
    run_destination_projection,
)
from portalpoint.modeling.io import get_sync_engine
from portalpoint.modeling.mlflow_helpers import maybe_promote, setup_mlflow

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Training seasons: look back 5 years of real transfer outcomes.
# The source season's neutral projection is the feature; dest season's realized RAPM is the label.
# Season here = destination season (year player appeared at destination).
DEFAULT_TRAIN_SEASONS = [2022, 2023, 2024, 2025, 2026]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run destination-adjusted player projection")
    parser.add_argument(
        "--target-season",
        type=int,
        default=2027,
        help="Season to project into (default: 2027, the upcoming portal cycle)",
    )
    parser.add_argument(
        "--source-season",
        type=int,
        default=2026,
        help=(
            "Season to source player stats and portal candidacy from "
            "(default: 2026, the last completed season)"
        ),
    )
    parser.add_argument(
        "--train-seasons",
        type=int,
        nargs="+",
        default=DEFAULT_TRAIN_SEASONS,
        help="Destination seasons used to train the delta models (default: 2022-2026)",
    )
    parser.add_argument(
        "--portal-only",
        action="store_true",
        default=True,
        help=(
            "Restrict player pool to is_portal_candidate=true rows (default: True). "
            "Pass --no-portal-only to score all players with neutral projections."
        ),
    )
    parser.add_argument(
        "--no-portal-only",
        dest="portal_only",
        action="store_false",
        help="Score all players with neutral projections, not just portal candidates",
    )
    parser.add_argument(
        "--player-ids",
        type=int,
        nargs="+",
        default=None,
        help="Override player pool with specific player_ids (useful for 'what-if' scenario runs)",
    )
    parser.add_argument(
        "--school-ids",
        type=int,
        nargs="+",
        default=None,
        help=(
            "Restrict destination school set to specific school_ids. "
            "Default: all D1 schools with PT coverage."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Compute all rows but skip the DB upsert (logs what would be written)",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        default=False,
        help=(
            "Historical/population-restricted run (e.g. a scoped backtest season): writes real "
            "rows, but skips run_rolling_origin_cv/compute_cohort_validation entirely and never "
            "touches MLflow (no run, no model registration, no maybe_promote). Real finding "
            "(2026-07-14): a restricted population's CV metric, even when computed correctly "
            "(not a bug), is a much smaller/noisier sample than a real production run and once "
            "produced a real but spurious promotion (Delta=+8.3 pct, manually reverted) — --backfill "
            "makes that structurally impossible instead of relying on catching it after the fact."
        ),
    )
    args = parser.parse_args()

    engine = get_sync_engine()

    log.info(
        "Destination projection — target=%d source=%d train=%s portal_only=%s dry_run=%s backfill=%s",
        args.target_season, args.source_season, args.train_seasons,
        args.portal_only, args.dry_run, args.backfill,
    )

    if args.dry_run:
        metrics = run_destination_projection(
            engine,
            target_season=args.target_season,
            source_season=args.source_season,
            train_seasons=args.train_seasons,
            player_id_subset=args.player_ids,
            school_id_subset=args.school_ids,
            portal_only=args.portal_only,
            dry_run=True,
            validate=not args.backfill,
        )
        log.info("Dry-run destination projection complete. Metrics: %s", metrics)
        if metrics.get("skipped"):
            log.warning("Run skipped: %s", metrics["skipped"])
            sys.exit(1)
        return

    if args.backfill:
        metrics = run_destination_projection(
            engine,
            target_season=args.target_season,
            source_season=args.source_season,
            train_seasons=args.train_seasons,
            player_id_subset=args.player_ids,
            school_id_subset=args.school_ids,
            portal_only=args.portal_only,
            dry_run=False,
            validate=False,
        )
        log.info("Backfill destination projection complete (no MLflow run). Metrics: %s", metrics)

        if metrics.get("skipped"):
            log.warning("Run skipped: %s", metrics["skipped"])
            if metrics["skipped"] == "no_pt_rows":
                log.warning(
                    "Fix: run_playing_time.py --target-season %d first, "
                    "then rerun this script",
                    args.target_season,
                )
            sys.exit(1)

        n_written = metrics.get("n_records_written", 0)
        if n_written == 0:
            log.error("Zero rows written — check logs above for why inference frame was empty")
            sys.exit(1)

        log.info(
            "Wrote %d destination projection rows (model_version=%s, target_season=%d)",
            n_written, MODEL_VERSION, args.target_season,
        )
        return

    import mlflow
    import mlflow.pyfunc
    client = setup_mlflow("destination-projection")

    class DestProjectionPyfunc(mlflow.pyfunc.PythonModel):
        """Marker artifact — destination projection is rule-based with no sklearn model to serialize."""
        def predict(self, context, model_input):
            return model_input

    with mlflow.start_run(run_name=f"dest-proj-{args.target_season}-{datetime.now().strftime('%Y%m%d-%H%M')}") as run:
        mlflow.log_params({
            "target_season": args.target_season,
            "source_season": args.source_season,
            "train_seasons": str(args.train_seasons),
            "portal_only": args.portal_only,
            "model_version": MODEL_VERSION,
            "neutral_model_priority": str(NEUTRAL_MODEL_PRIORITY),
            "dry_run": args.dry_run,
        })

        metrics = run_destination_projection(
            engine,
            target_season=args.target_season,
            source_season=args.source_season,
            train_seasons=args.train_seasons,
            player_id_subset=args.player_ids,
            school_id_subset=args.school_ids,
            portal_only=args.portal_only,
            dry_run=args.dry_run,
        )

        mlflow.log_metrics({k: v for k, v in metrics.items() if isinstance(v, (int, float))})
        mlflow.pyfunc.log_model(artifact_path="dest_proj_model", python_model=DestProjectionPyfunc())

        log.info("Destination projection complete. Metrics: %s", metrics)

        total_resid_std = float(metrics.get("total_resid_std", 999.0))
        result = maybe_promote(
            client, "destination-projection", run.info.run_id, "dest_proj_model",
            metric_name="total_resid_std", new_value=total_resid_std, higher_is_better=False,
        )
        log.info("MLflow run %s — %s", run.info.run_id, result)

        if metrics.get("skipped"):
            log.warning("Run skipped: %s", metrics["skipped"])
            if metrics["skipped"] == "no_pt_rows":
                log.warning(
                    "Fix: run_playing_time.py --target-season %d first, "
                    "then rerun this script",
                    args.target_season,
                )
            sys.exit(1)

        n_written = metrics.get("n_records_written", 0)
        if n_written == 0 and not args.dry_run:
            log.error(
                "Zero rows written — check logs above for why inference frame was empty"
            )
            sys.exit(1)

        log.info(
            "Wrote %d destination projection rows "
            "(model_version=%s, target_season=%d)",
            n_written, MODEL_VERSION, args.target_season,
        )


if __name__ == "__main__":
    main()
