"""
scripts/run_gap_matching.py

Non-interactive rerun of Gap Matching. Scores every eligible player against
every school with roster gap vectors, while preserving Scheme Fit context for
rows that already have it.

Usage:
  uv run python scripts/run_gap_matching.py
  uv run python scripts/run_gap_matching.py --include-player-ids 123 456
"""
from __future__ import annotations

import argparse
import logging
import sys

import numpy as np
import pandas as pd

from portalpoint.modeling import gap_matching as gm
from portalpoint.modeling import roster_baseline as rb
from portalpoint.modeling.availability import (
    apply_portal_candidate_override,
    sync_portal_candidate_flags,
)
from portalpoint.modeling.io import get_sync_engine
from portalpoint.modeling.mlflow_helpers import maybe_promote, setup_mlflow

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

SEASONS_IN_DATA = [2021, 2022, 2023, 2024, 2025, 2026]
SCHOOL_CHUNK_SIZE = 50

LOAD_SQL = f"""
SELECT
    pss.player_id,
    pss.school_id,
    pss.season,
    pss.games_played,
    pss.min_pct,
    p.position,
    p.height_inches,
    pss.barttorvik_role,
    pss.true_shooting_pct,
    pss.usage_rate,
    pss.assist_rate,
    pss.tov_pct,
    pss.off_reb_pct,
    pss.def_reb_pct,
    pss.block_pct,
    pss.steal_pct,
    pss.free_throw_rate,
    pss.three_point_rate,
    pss.rim_rate,
    pss.mid_range_rate,
    pss.fg3_pct,
    pss.rim_pct,
    hep.pos_confidence_pg,
    hep.pos_confidence_sg,
    hep.pos_confidence_sf,
    hep.pos_confidence_pf,
    hep.pos_confidence_c
FROM player_season_stats pss
JOIN players p ON p.id = pss.player_id
LEFT JOIN hoop_explorer_player_stats hep
    ON hep.player_id = pss.player_id AND hep.season = pss.season
WHERE pss.games_played           >= {gm.MIN_GAMES}
"""

ARCH_SQL = "SELECT player_id, season, archetype_id, archetype_label FROM player_archetypes"

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Non-interactive rerun of Gap Matching")
    p.add_argument(
        "--include-player-ids",
        type=int,
        nargs="+",
        default=[],
        metavar="PLAYER_ID",
        help=(
            "Force is_portal_candidate=true for these player_ids in the current "
            "season's fit-score rows, regardless of real transfer_portal_events "
            "status — for one-off 'what if X enters the portal' scenario runs. "
            "Does not write to transfer_portal_events."
        ),
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    engine = get_sync_engine()
    conn = engine.raw_connection()

    with conn.cursor() as cur:
        cur.execute(LOAD_SQL)
        cols = [d[0] for d in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=cols)
    df = (
        df.sort_values("games_played", ascending=False)
        .drop_duplicates(subset=["player_id", "season"], keep="first")
        .reset_index(drop=True)
    )
    log.info("Loaded %s player-season rows", f"{len(df):,}")
    df = gm.prepare_gap_features(df)

    current_season = max(SEASONS_IN_DATA)
    roster_df, baseline_summary = rb.build_roster_baseline_frame(
        df, engine, SEASONS_IN_DATA, current_season
    )
    candidate_df = df.copy()
    log.info(
        "Roster baseline rows: %s "
        "(historical_members=%s, latest_snapshot_members=%s across %d schools, "
        "fallback_members=%s across %d schools)",
        f"{baseline_summary.rows:,}",
        f"{baseline_summary.historical_rows:,}",
        f"{baseline_summary.snapshot_rows:,}",
        baseline_summary.snapshot_schools,
        f"{baseline_summary.fallback_rows:,}",
        baseline_summary.fallback_schools,
    )
    log.info("Candidate rows retained for scoring: %s", f"{len(candidate_df):,}")

    roster_df = gm.add_gap_reliability(gm.assign_soft_positions(roster_df))
    candidate_df = gm.add_gap_reliability(gm.assign_soft_positions(candidate_df))

    with conn.cursor() as cur:
        cur.execute(ARCH_SQL)
        arch_df = pd.DataFrame(
            cur.fetchall(),
            columns=["player_id", "season", "archetype_id", "archetype_label"],
        )
    roster_df = roster_df.merge(arch_df, on=["player_id", "season"], how="left")
    candidate_df = candidate_df.merge(arch_df, on=["player_id", "season"], how="left")

    benchmarks = gm.build_league_benchmarks(roster_df, SEASONS_IN_DATA)
    gap_data = gm.build_roster_gap_vectors(roster_df, benchmarks, SEASONS_IN_DATA)
    arch_deficit = gm.build_archetype_deficits(roster_df, SEASONS_IN_DATA)

    scaler = gm.fit_gap_scaler(candidate_df)
    gap_scaled = gm.prescale_gap_tensors(gap_data, scaler, SEASONS_IN_DATA)

    total_records = 0
    total_upserted = 0
    gap_sum = 0.0
    gap_sq_sum = 0.0
    conn.rollback()
    for season in SEASONS_IN_DATA:
        school_ids = sorted(gap_scaled[season].keys())
        season_records = 0
        season_upserted = 0
        for start in range(0, len(school_ids), SCHOOL_CHUNK_SIZE):
            school_batch = school_ids[start:start + SCHOOL_CHUNK_SIZE]
            existing = gm.load_existing_scheme_context(engine, season, school_batch)
            records = gm.score_gap_matches(
                candidate_df,
                scaler,
                gap_scaled,
                gap_data,
                arch_deficit,
                existing,
                [season],
                school_ids=school_batch,
            )
            if not records:
                continue
            gap_values = np.fromiter((r[3] for r in records), dtype=np.float64, count=len(records))
            total_records += len(records)
            season_records += len(records)
            gap_sum += float(gap_values.sum())
            gap_sq_sum += float(np.square(gap_values).sum())
            upserted = gm.upsert_gap_scores(engine, records)
            total_upserted += upserted
            season_upserted += upserted
            log.info(
                "Season %d schools %d-%d/%d: scored=%s upserted=%s",
                season,
                start + 1,
                min(start + len(school_batch), len(school_ids)),
                len(school_ids),
                f"{len(records):,}",
                f"{upserted:,}",
            )
        flagged = sync_portal_candidate_flags(engine, [season])
        log.info(
            "Season %d: is_portal_candidate flagged on %d rows",
            season,
            flagged.get(season, 0),
        )
        log.info(
            "Season %d complete: scored=%s upserted=%s",
            season,
            f"{season_records:,}",
            f"{season_upserted:,}",
        )
    log.info("Total records scored: %s", f"{total_records:,}")
    log.info("Total rows upserted to player_team_fit_scores: %s", f"{total_upserted:,}")
    conn.close()

    if args.include_player_ids:
        overridden = apply_portal_candidate_override(
            engine, args.include_player_ids, current_season
        )
        log.info(
            "Season %d: is_portal_candidate override applied to %d rows for player_ids %s",
            current_season, overridden, args.include_player_ids,
        )

    if total_records == 0:
        raise RuntimeError("No gap matching records were scored")
    mean_gap = gap_sum / total_records
    std_gap = float(np.sqrt(max(gap_sq_sum / total_records - mean_gap * mean_gap, 0.0)))

    client = setup_mlflow("gap-matching")
    import mlflow
    import mlflow.pyfunc

    class GapMatchPyfunc(mlflow.pyfunc.PythonModel):
        """Mirrors the class in gap_matching.ipynb's MLflow Tracking section."""

        def predict(self, context, model_input):
            results = []
            for _, r in model_input.iterrows():
                p = [r[f"player_{f}"] for f in gm.GAP_FEATURES]
                g = [r[f"gap_{f}"] for f in gm.GAP_FEATURES]
                results.append(gm.compute_gap_match_ondemand(p, g))
            return results

    with mlflow.start_run(run_name=f"gap-match-s{max(SEASONS_IN_DATA)}-script") as run:
        mlflow.log_params({
            "seasons": ",".join(str(s) for s in SEASONS_IN_DATA),
            "min_games": gm.MIN_GAMES,
            "model_version": gm.MODEL_VERSION,
            "source": "script",
        })
        mlflow.log_metrics({
            "mean_gap_match": mean_gap,
            "std_gap_match": std_gap,
            "n_records_scored": float(total_records),
            "n_records_written": float(total_upserted),
        })
        mlflow.pyfunc.log_model(artifact_path="gap_match_model", python_model=GapMatchPyfunc())
        run_id = run.info.run_id

    result = maybe_promote(
        client, "gap-matching-scorer", run_id, "gap_match_model",
        metric_name="std_gap_match", new_value=std_gap, higher_is_better=True,
    )
    log.info("MLflow run %s — %s", run_id, result)


if __name__ == "__main__":
    main()
