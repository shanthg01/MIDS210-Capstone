"""
scripts/run_scheme_fit.py

Non-interactive rerun of Model 3 (Scheme Fit Scorer). For validation plots and
spot-checks, use notebooks/models/scheme_fit_scorer.ipynb instead.

Usage:
  uv run python scripts/run_scheme_fit.py
"""
from __future__ import annotations

import logging
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import text

from portalpoint.modeling import scheme_fit as sf
from portalpoint.modeling.io import find_repo_root, get_sync_engine
from portalpoint.modeling.mlflow_helpers import maybe_promote, setup_mlflow

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PLAYER_SQL = """
SELECT
    pss.player_id,
    pss.season,
    pss.school_id,
    p.full_name,
    pss.three_point_rate,
    pss.rim_rate,
    pss.mid_range_rate,
    pss.usage_rate,
    pss.games_played,
    pss.min_pct
FROM player_season_stats pss
JOIN players p ON p.id = pss.player_id
WHERE pss.season = ANY(:seasons)
  AND pss.three_point_rate IS NOT NULL
  AND pss.rim_rate         IS NOT NULL
  AND pss.mid_range_rate   IS NOT NULL
  AND pss.games_played >= 5
"""

HE_PLAYER_SQL = """
SELECT
    he.player_id,
    he.season,
    he.off_style_transition_pct,
    he.off_style_post_up_pct,
    he.off_style_pick_pop_pct,
    he.off_style_big_cut_roll_pct,
    he.off_style_attack_kick_pct,
    he.off_style_perimeter_cut_pct
FROM hoop_explorer_player_stats he
WHERE he.season = ANY(:seasons)
  AND he.player_id IS NOT NULL
"""


def main() -> None:
    root = find_repo_root()
    features_dir = root / "data" / "features"
    engine = get_sync_engine()

    team_df = pq.read_table(features_dir / "team_style_vectors.parquet").to_pandas()
    seasons_in_data = sorted(team_df["season"].unique().tolist())
    current_season = max(seasons_in_data)
    team_df = team_df.dropna(subset=sf.TEAM_SHOT_FEATS)
    team_df = team_df.drop_duplicates(subset=["school_id", "season"], keep="first").reset_index(drop=True)
    if all(f in team_df.columns for f in sf.HE_FEATS):
        team_df["_he_covered"] = team_df[sf.HE_FEATS].notna().all(axis=1)
    else:
        for f in sf.HE_FEATS:
            team_df[f] = np.nan
        team_df["_he_covered"] = False
    log.info("Loaded %s team-seasons, seasons=%s", f"{len(team_df):,}", seasons_in_data)

    with engine.connect() as conn:
        player_df = pd.read_sql(text(PLAYER_SQL), conn, params={"seasons": seasons_in_data})
    for f in sf.PLAYER_SHOT_FEATS:
        player_df[f] = player_df[f].clip(0, 1)
    player_df = player_df[player_df[sf.PLAYER_SHOT_FEATS].sum(axis=1) != 0]
    player_df = (
        player_df.sort_values("games_played", ascending=False)
        .drop_duplicates(subset=["player_id", "season"], keep="first")
        .reset_index(drop=True)
    )

    tempo_lookup = team_df[["school_id", "season", "adj_tempo"]].rename(columns={"adj_tempo": "current_tempo"})
    player_df = player_df.merge(tempo_lookup, on=["school_id", "season"], how="left")
    tempo_default = float(team_df["adj_tempo"].median())
    player_df["current_tempo"] = player_df["current_tempo"].fillna(tempo_default)

    with engine.connect() as conn:
        he_player_raw = pd.read_sql(text(HE_PLAYER_SQL), conn, params={"seasons": seasons_in_data})
    player_df = player_df.merge(he_player_raw, on=["player_id", "season"], how="left").reset_index(drop=True)
    log.info("Loaded %s player-seasons", f"{len(player_df):,}")

    records, season_stats, n_he_records = sf.score_all_seasons(
        player_df, team_df, seasons_in_data, tempo_default, top_k=sf.TOP_K, model_version=sf.MODEL_VERSION,
    )
    for season, stats in season_stats.items():
        log.info("Season %s: %sp x %st, mean_fit=%.1f, HE=%s", season, f"{stats['n_players']:,}", f"{stats['n_teams']:,}", stats["mean_fit"], f"{stats['n_he']:,}")

    deleted, upserted = sf.upsert_fit_scores(engine, records, seasons_in_data)
    log.info("Deleted %s stale rows, upserted %s rows to player_team_fit_scores", f"{deleted:,}", f"{upserted:,}")

    p_cur = player_df[player_df["season"] == current_season].reset_index(drop=True)
    t_cur = team_df[team_df["season"] == current_season].reset_index(drop=True)
    SIM_SCALED = np.clip(
        cosine_similarity(p_cur[sf.PLAYER_SHOT_FEATS].values.astype(np.float64), t_cur[sf.TEAM_SHOT_FEATS].values.astype(np.float64)) * 100,
        0, 100,
    )

    client = setup_mlflow("scheme-fit-scorer")
    import mlflow
    import mlflow.pyfunc

    class SchemeFitPyfunc(mlflow.pyfunc.PythonModel):
        """Wraps scheme fit cosine for the MLflow registry — mirrors the class in
        scheme_fit_scorer.ipynb's MLflow Tracking section."""

        def predict(self, context, model_input):
            results = []
            for _, r in model_input.iterrows():
                p = np.array([r.player_three, r.player_rim, r.player_mid], dtype=np.float64)
                t = np.array([r.team_three, r.team_rim, r.team_mid], dtype=np.float64)
                results.append({"scheme_fit": round(sf.scheme_fit_score(p, t), 2)})
            return results

    with mlflow.start_run(run_name=f"scheme-fit-s{current_season}-script") as run:
        mlflow.log_params({
            "season": current_season,
            "top_k": sf.TOP_K,
            "model_version": sf.MODEL_VERSION,
            "source": "script",
        })
        mlflow.log_metrics({
            "mean_scheme_fit": float(SIM_SCALED.mean()),
            "std_scheme_fit": float(SIM_SCALED.std()),
            "n_records_written": float(len(records)),
            "n_players": float(len(player_df)),
            "n_teams": float(len(team_df)),
        })
        mlflow.pyfunc.log_model(artifact_path="scheme_fit_model", python_model=SchemeFitPyfunc())
        run_id = run.info.run_id

    result = maybe_promote(
        client, "scheme-fit-scorer", run_id, "scheme_fit_model",
        metric_name="std_scheme_fit", new_value=float(SIM_SCALED.std()), higher_is_better=True,
    )
    log.info("MLflow run %s — %s", run_id, result)


if __name__ == "__main__":
    main()
