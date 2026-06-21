"""
scripts/run_gap_matching.py

Non-interactive rerun of Gap Matching. Scores exactly the (player_id,
school_id, season) pairs Scheme Fit already wrote to player_team_fit_scores —
no new pairs added. Requires scripts/run_scheme_fit.py to have run first.

Usage:
  uv run python scripts/run_gap_matching.py
"""
from __future__ import annotations

import logging
import sys

import numpy as np
import pandas as pd

from portalpoint.modeling import gap_matching as gm
from portalpoint.modeling.io import find_repo_root, get_sync_engine
from portalpoint.modeling.mlflow_helpers import maybe_promote, setup_mlflow

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

SEASONS_IN_DATA = [2021, 2022, 2023, 2024, 2025, 2026]

LOAD_SQL = f"""
SELECT
    pss.player_id,
    pss.school_id,
    pss.season,
    pss.games_played,
    p.position,
    pss.points_per_game,
    pss.rebounds_per_game,
    pss.assists_per_game,
    pss.steals_per_game,
    pss.blocks_per_game,
    pss.true_shooting_pct,
    pss.usage_rate,
    pss.three_point_rate,
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
  AND pss.points_per_game        IS NOT NULL
  AND pss.rebounds_per_game      IS NOT NULL
  AND pss.assists_per_game       IS NOT NULL
  AND pss.steals_per_game        IS NOT NULL
  AND pss.blocks_per_game        IS NOT NULL
  AND pss.true_shooting_pct      IS NOT NULL
  AND pss.usage_rate             IS NOT NULL
  AND pss.three_point_rate       IS NOT NULL
"""

ARCH_SQL = "SELECT player_id, season, archetype_id, archetype_label FROM player_archetypes"

# gap-cos-v2: departure filter for the current season only (see gap_matching.filter_departed).
# Exact query from docs/models/gap_matching_plan.md Cell 2.
DEPARTED_SQL = """
SELECT player_id, from_school_id
FROM transfers
WHERE from_school_id IS NOT NULL
  AND portal_entry_date IS NOT NULL
  AND season = %s
"""

EXISTING_SQL = """
SELECT player_id, school_id, season, scheme_fit, breakdown
FROM player_team_fit_scores
WHERE season = ANY(%s)
"""


def main() -> None:
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

    current_season = max(SEASONS_IN_DATA)
    with conn.cursor() as cur:
        cur.execute(DEPARTED_SQL, (current_season,))
        departed_pairs = {(r[0], r[1]) for r in cur.fetchall()}
    log.info("Departed pairs loaded for season %d: %s", current_season, f"{len(departed_pairs):,}")
    df = gm.filter_departed(df, departed_pairs, current_season)
    log.info("Rows after departure filter: %s", f"{len(df):,}")

    df = gm.assign_soft_positions(df)

    with conn.cursor() as cur:
        cur.execute(ARCH_SQL)
        arch_df = pd.DataFrame(cur.fetchall(), columns=["player_id", "season", "archetype_id", "archetype_label"])
    df = df.merge(arch_df, on=["player_id", "season"], how="left")

    benchmarks = gm.build_league_benchmarks(df, SEASONS_IN_DATA)
    gap_data = gm.build_roster_gap_vectors(df, benchmarks, SEASONS_IN_DATA)
    arch_deficit = gm.build_archetype_deficits(df, SEASONS_IN_DATA)

    scaler = gm.fit_gap_scaler(df)
    gap_scaled = gm.prescale_gap_tensors(gap_data, scaler, SEASONS_IN_DATA)

    with conn.cursor() as cur:
        cur.execute(EXISTING_SQL, (SEASONS_IN_DATA,))
        existing_rows = cur.fetchall()
    existing = {(r[0], r[1], r[2]): {"scheme_fit": r[3], "breakdown": r[4]} for r in existing_rows}
    log.info("Existing M3 pairs loaded: %s", f"{len(existing):,}")

    records = gm.score_gap_matches(df, scaler, gap_scaled, gap_data, arch_deficit, existing, SEASONS_IN_DATA)
    log.info("Total records to upsert: %s", f"{len(records):,}")

    conn.rollback()
    upserted = gm.upsert_gap_scores(engine, records)
    log.info("Upserted %s rows to player_team_fit_scores", f"{upserted:,}")
    conn.close()

    gap_values = np.array([r[3] for r in records], dtype=np.float64)
    mean_gap, std_gap = float(gap_values.mean()), float(gap_values.std())

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
            "n_records_written": float(len(records)),
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
