"""
scripts/run_destination_backtest.py

Historical backtest for the Destination-Adjusted Projection model — compares
actual production output (re-scored point-in-time per historical season)
against actual realized per-game stats for real historical transfers.

See docs/models/destination_projection_backtest_plan.md for full design —
this is a diagnostic report, not a new model_version; it does not write to
maybe_promote.

Prerequisites per historical season (checked, not assumed):
  - playing_time_projections for that season (scripts/run_playing_time.py)
  - player_projections destination-mode rows for that season
    (scripts/run_destination_projection.py)
This script checks readiness and reports missing seasons by default. Pass
--backfill to have it invoke those two scripts itself — real DB writes, real
compute (each missing season is a full model rerun, scoped to that season's
backtest population only via --include-school-ids/--include-player-ids, per
§10.3 of the plan doc). Both invocations always pass --backfill through to
run_playing_time.py/run_destination_projection.py — real finding (2026-07-14):
a historical, population-restricted rerun's validation metric is a much
smaller/noisier sample than a real production run, and running CV/promotion
on it produced two real false-or-spurious promotions (playing-time-rotation
Delta=+98.6 pct from a metric-substitution bug; destination-projection
Delta=+8.3 pct from a real-but-tiny-sample metric), both manually reverted.
--backfill on both underlying scripts skips CV/cohort-validation and MLflow
registration/maybe_promote entirely, making this structurally impossible
instead of relying on catching it after the fact.

Usage:
  uv run python scripts/run_destination_backtest.py                      # check + compute on whatever's ready
  uv run python scripts/run_destination_backtest.py --min-season 2023 --max-season 2025
  uv run python scripts/run_destination_backtest.py --backfill           # also backfills missing seasons
  uv run python scripts/run_destination_backtest.py --dry-run            # skip MLflow logging
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from datetime import datetime

import pandas as pd
from sqlalchemy import text

from portalpoint.modeling import destination_backtest as db
from portalpoint.modeling.destination_projection import MODEL_VERSION
from portalpoint.modeling.io import get_sync_engine
from portalpoint.modeling.mlflow_helpers import setup_mlflow

# Mirrors run_destination_projection.py's DEFAULT_TRAIN_SEASONS. Duplicated (not imported) because
# _backfill_season below must filter it per dest_season -- importing the name would invite someone
# to bump one copy and not the other, so keep them next to their own real bug/fix history instead.
_ALL_TRAIN_SEASONS = [2022, 2023, 2024, 2025, 2026]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


_COVERAGE_SQL = """
SELECT pp.player_id, pp.school_id
FROM player_projections pp
JOIN (
    SELECT unnest(:player_ids) AS player_id, unnest(:school_ids) AS school_id
) keys
  ON pp.player_id = keys.player_id
 AND pp.school_id = keys.school_id
WHERE pp.projection_mode = 'destination'
  AND pp.model_version = :model_version
  AND pp.season = :season
"""


def _season_coverage_gaps(engine, population_df: pd.DataFrame, model_version: str) -> dict[int, int]:
    """Real (player_id, school_id) coverage per season vs. the exact backtest population keys.

    Real bug (PR #60 review, ajaypatel-8): the old check only asked "does any destination row
    exist for this season" -- a season interrupted 1 row into a 900-row backfill read as "ready,"
    and load_projected_outcomes' inner join then silently dropped the other 899, biasing the
    sample with no visible warning. Returns {season: n_missing_keys} for every season with less
    than full coverage (0 rows = fully missing, same as before; partial coverage now surfaces too).
    """
    gaps: dict[int, int] = {}
    for season, group in population_df.groupby("dest_season"):
        keys = group[["player_id", "dest_school_id"]].drop_duplicates()
        with engine.connect() as conn:
            covered = pd.read_sql(
                text(_COVERAGE_SQL),
                conn,
                params={
                    "player_ids": keys["player_id"].tolist(),
                    "school_ids": keys["dest_school_id"].tolist(),
                    "model_version": model_version,
                    "season": int(season),
                },
            )
        n_covered = len(covered.drop_duplicates())
        n_expected = len(keys)
        if n_covered < n_expected:
            gaps[int(season)] = n_expected - n_covered
    return gaps


def _season_ids(population_df, season: int) -> tuple[list[int], list[int]]:
    season_pop = population_df[population_df["dest_season"] == season]
    school_ids = sorted(int(s) for s in season_pop["dest_school_id"].unique().tolist())
    player_ids = sorted(int(p) for p in season_pop["player_id"].unique().tolist())
    return school_ids, player_ids


def _backfill_season(dest_season: int, school_ids: list[int], player_ids: list[int]) -> None:
    source_season = dest_season - 1
    # Point-in-time discipline (plan doc §5 step 1): train only on destination seasons strictly
    # before the one being scored. Real bug (found in PR #60 review, ajaypatel-8): this used to
    # omit --train-seasons entirely, so run_destination_projection.py fell back to its own
    # DEFAULT_TRAIN_SEASONS (2022-2026 — the *evaluated* season plus future seasons), training the
    # delta models on the very outcomes being backtested. That's label leakage, not just a noisy
    # sample -- it inflates every reported residual and invalidates the cohort/cluster findings in
    # destination_projection_backtest_plan.md §14. Mirrors run_rolling_origin_cv's own walk-forward
    # fold structure (destination_projection.py) so this backtest's discipline matches the model's
    # own internal validation instead of a different, leakier one.
    train_seasons = [s for s in _ALL_TRAIN_SEASONS if s < dest_season]
    log.info(
        "Backfilling season=%d: %d schools, %d players, train_seasons=%s (real DB writes)",
        dest_season, len(school_ids), len(player_ids), train_seasons,
    )
    pt_cmd = [
        sys.executable, "scripts/run_playing_time.py",
        "--target-season", str(dest_season), "--source-season", str(source_season),
        "--include-school-ids", *[str(s) for s in school_ids],
        "--include-player-ids", *[str(p) for p in player_ids],
        "--backfill",
    ]
    log.info("Running: %s", " ".join(pt_cmd))
    subprocess.run(pt_cmd, check=True)

    dp_cmd = [
        sys.executable, "scripts/run_destination_projection.py",
        "--target-season", str(dest_season), "--source-season", str(source_season),
        "--train-seasons", *[str(s) for s in train_seasons],
        "--no-portal-only",
        "--player-ids", *[str(p) for p in player_ids],
        "--school-ids", *[str(s) for s in school_ids],
        "--backfill",
    ]
    log.info("Running: %s", " ".join(dp_cmd))
    subprocess.run(dp_cmd, check=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Historical backtest for Destination Projection")
    p.add_argument(
        "--min-season", type=int, default=2023,
        help=(
            "Earliest destination season to backtest (default: 2023). 2022 is infeasible — "
            "barttorvik data starts at 2021, so target_season=2022's Playing Time model can't "
            "get the >=2 prior seasons it hard-requires. See plan doc §13."
        ),
    )
    p.add_argument("--max-season", type=int, default=2026)
    p.add_argument(
        "--backfill", action="store_true",
        help=(
            "Invoke run_playing_time.py + run_destination_projection.py for any missing "
            "season (real DB writes, real compute)."
        ),
    )
    p.add_argument("--dry-run", action="store_true", help="Skip MLflow logging")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    engine = get_sync_engine()

    population_df = db.load_backtest_population(engine, args.min_season, args.max_season)
    log.info(
        "Backtest population: %d matched historical transfers (seasons %d-%d)",
        len(population_df), args.min_season, args.max_season,
    )
    if population_df.empty:
        log.warning("No backtest population found — aborting")
        return

    coverage_gaps = _season_coverage_gaps(engine, population_df, MODEL_VERSION)
    missing = sorted(coverage_gaps.keys())
    if missing:
        for season in missing:
            log.info(
                "Season %d: %d/%d population keys missing a destination projection row",
                season, coverage_gaps[season],
                len(population_df.loc[population_df["dest_season"] == season, ["player_id", "dest_school_id"]].drop_duplicates()),
            )

    if missing and args.backfill:
        for season in missing:
            school_ids, player_ids = _season_ids(population_df, season)
            _backfill_season(season, school_ids, player_ids)
    elif missing:
        log.warning(
            "Incomplete destination-mode player_projections coverage for seasons %s — the "
            "affected rows will be silently dropped by the later inner join, biasing the sample. "
            "Re-run with --backfill to fill them, or run manually:", missing,
        )
        for season in missing:
            school_ids, player_ids = _season_ids(population_df, season)
            log.warning(
                "  uv run python scripts/run_playing_time.py --target-season %d --source-season %d "
                "--include-school-ids %s --include-player-ids %s ...",
                season, season - 1,
                " ".join(str(s) for s in school_ids[:5]),
                " ".join(str(p) for p in player_ids[:5]),
            )

    actual_df = db.load_actual_outcomes(engine, population_df)
    projected_df = db.load_projected_outcomes(engine, population_df, MODEL_VERSION)
    residual_df = db.compute_residuals(actual_df, projected_df)
    log.info("Backtest rows with both real actual + projected outcomes: %d", len(residual_df))

    if residual_df.empty:
        log.warning("No overlapping actual/projected rows — nothing to summarize")
        return

    enriched = db.enrich_with_cohorts(engine, population_df)
    residual_df = residual_df.merge(
        enriched[["player_id", "dest_school_id", "dest_season", "archetype_label", "tier_direction", "position"]],
        on=["player_id", "dest_school_id", "dest_season"], how="left",
    )

    overall = db.summarize_residuals(residual_df)
    by_position = db.summarize_residuals(residual_df, group_by="position")
    by_archetype = db.summarize_residuals(residual_df, group_by="archetype_label")
    by_tier_direction = db.summarize_residuals(residual_df, group_by="tier_direction")

    log.info("Overall: %s", overall)
    log.info("By position: %s", by_position)
    log.info("By archetype: %s", by_archetype)
    log.info("By tier direction: %s", by_tier_direction)

    if args.dry_run:
        log.info("Dry run — skipping MLflow logging")
        return

    import mlflow
    setup_mlflow("destination-projection-backtest")
    with mlflow.start_run(run_name=f"dest-backtest-{datetime.now().strftime('%Y%m%d-%H%M')}"):
        mlflow.log_params({
            "min_season": args.min_season,
            "max_season": args.max_season,
            "model_version": MODEL_VERSION,
            "n_population": len(population_df),
            "n_backtest_rows": len(residual_df),
            "missing_seasons": str(missing),
        })
        mlflow.log_metrics({k: v for k, v in overall.items() if isinstance(v, (int, float))})
        mlflow.log_dict(
            {
                "overall": overall,
                "by_position": by_position,
                "by_archetype": by_archetype,
                "by_tier_direction": by_tier_direction,
            },
            "residual_summary.json",
        )
    log.info("Backtest complete — logged to MLflow (diagnostic run, not wired to maybe_promote)")


if __name__ == "__main__":
    main()
