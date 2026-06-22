"""
scripts/run_team_clustering.py

Non-interactive rerun of Model 2 (Team System Clustering). Uses the
last-confirmed K_OFFENSE/K_DEFENSE and feature-group weights from
src/portalpoint/modeling/team_clustering.py — no weight search, no plots.
For retuning those, use notebooks/models/team_clustering.ipynb instead.

Usage:
  uv run python scripts/run_team_clustering.py
  uv run python scripts/run_team_clustering.py --k-offense 7 --k-defense 5
"""
from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd
import pyarrow.parquet as pq
from sklearn.metrics import davies_bouldin_score, silhouette_score

from portalpoint.modeling import team_clustering as tc
from portalpoint.modeling.io import find_repo_root, get_sync_engine
from portalpoint.modeling.mlflow_helpers import maybe_promote, setup_mlflow

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def main(k_offense: int, k_defense: int) -> None:
    root = find_repo_root()
    features_dir = root / "data" / "features"
    models_dir = root / "data" / "models"
    engine = get_sync_engine()

    df = pq.read_table(features_dir / "team_style_vectors.parquet").to_pandas()
    df = df.sort_values(["season", "school_id"]).reset_index(drop=True)
    df = df.drop_duplicates(subset=["school_id", "season"], keep="first").reset_index(drop=True)
    seasons_in_data = sorted(df["season"].dropna().astype(int).unique().tolist())
    current_season = max(seasons_in_data)
    model_version = f"team-v4-{current_season}"

    required_cols = sorted(set(tc.OFFENSE_FEATURES + tc.DEFENSE_FEATURES + ["school_id", "season", "school_name", "he_team_cluster_available"]))
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required team clustering columns: {missing}")

    for col in sorted(set(tc.OFFENSE_FEATURES + tc.DEFENSE_FEATURES + ["adj_o", "adj_d", "adj_em", "def_efg"])):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in tc.BART_FEATURES:
        df[col] = df[col].fillna(df[col].median())

    he_mask = df["he_team_cluster_available"].fillna(False).astype(bool)
    df_he = df[he_mask].copy().reset_index(drop=True)
    df_fallback = df[~he_mask].copy().reset_index(drop=True)
    for col in tc.OFFENSE_FEATURES + tc.DEFENSE_FEATURES:
        if df_he[col].isna().any():
            df_he[col] = df_he[col].fillna(df_he[col].median())
    log.info("Loaded %s team-seasons, HE-covered=%s, fallback=%s", f"{len(df):,}", f"{len(df_he):,}", f"{len(df_fallback):,}")

    offense_scalers = tc.fit_scalers(df, df_he, tc.OFFENSE_FEATURE_GROUPS, tc.OFFENSE_BASE_GROUP_NAMES)
    defense_scalers = tc.fit_scalers(df_he, df_he, tc.DEFENSE_FEATURE_GROUPS, [])
    offense_weights = tc.OFFENSE_DEFAULT_WEIGHTS
    defense_weights = tc.DEFENSE_DEFAULT_WEIGHTS

    fit_result = tc.fit_two_layer_clusters(
        df, df_he, df_fallback, offense_scalers, defense_scalers, offense_weights, defense_weights,
        k_offense=k_offense, k_defense=k_defense,
    )
    offense_kmeans = fit_result["offense_kmeans"]
    defense_kmeans = fit_result["defense_kmeans"]
    df = fit_result["df"]
    df_he = fit_result["df_he"]
    df_fallback = fit_result["df_fallback"]
    X_offense_he = fit_result["X_offense_he"]
    X_defense_he = fit_result["X_defense_he"]

    df = tc.build_system_memberships(
        df, df_he, df_fallback,
        fit_result["offense_dists_he"], fit_result["defense_dists_he"], fit_result["offense_dists_fb"],
    )

    records = tc.build_team_profile_records(df, model_version)
    deleted, upserted = tc.upsert_team_system_profiles(engine, records, seasons_in_data)
    log.info("Deleted %s stale rows, upserted %s rows to team_system_profiles", f"{deleted:,}", f"{upserted:,}")

    paths = tc.save_artifacts(models_dir, offense_kmeans, defense_kmeans, offense_scalers, defense_scalers, offense_weights, defense_weights, model_version)
    for name, path in paths.items():
        log.info("Saved %s: %s", name, path)

    try:
        sys.path.insert(0, str(root / "notebooks" / "utils"))
        from s3_helpers import upload

        for path in paths.values():
            upload(path, f"models/team_clustering/{path.name}")
    except Exception as exc:
        log.warning("S3 upload skipped: %s", exc)

    off_sil = float(silhouette_score(X_offense_he, df_he["offense_cluster_id"]))
    def_sil = float(silhouette_score(X_defense_he, df_he["defense_cluster_id"]))
    off_db = float(davies_bouldin_score(X_offense_he, df_he["offense_cluster_id"]))
    def_db = float(davies_bouldin_score(X_defense_he, df_he["defense_cluster_id"]))

    client = setup_mlflow("team-clustering")
    import mlflow
    import mlflow.sklearn

    with mlflow.start_run(run_name=f"team-clustering-s{current_season}-v4-script") as run:
        mlflow.log_params({
            "model_version": model_version,
            "k_offense": k_offense,
            "k_defense": k_defense,
            "seasons": str(seasons_in_data),
            "offense_weights": str(offense_weights),
            "defense_weights": str(defense_weights),
            "source": "script",
        })
        mlflow.log_metrics({
            "silhouette_score": off_sil,
            "offense_silhouette": off_sil,
            "defense_silhouette": def_sil,
            "offense_davies_bouldin": off_db,
            "defense_davies_bouldin": def_db,
            "n_teams": float(len(df)),
        })
        mlflow.sklearn.log_model(offense_kmeans, "offense_kmeans")
        mlflow.sklearn.log_model(defense_kmeans, "defense_kmeans")
        run_id = run.info.run_id

    result = maybe_promote(client, "team-clustering", run_id, "offense_kmeans", metric_name="silhouette_score", new_value=off_sil, higher_is_better=True)
    log.info("MLflow run %s — %s", run_id, result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Model 2 — Team System Clustering (non-interactive)")
    parser.add_argument("--k-offense", type=int, default=tc.DEFAULT_K_OFFENSE)
    parser.add_argument("--k-defense", type=int, default=tc.DEFAULT_K_DEFENSE)
    args = parser.parse_args()
    main(args.k_offense, args.k_defense)
