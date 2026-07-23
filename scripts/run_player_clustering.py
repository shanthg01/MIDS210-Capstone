"""
scripts/run_player_clustering.py

Non-interactive rerun of Model 1 (Player Clustering). Uses the last-confirmed
K and feature-group weights from src/portalpoint/modeling/player_clustering.py
— no weight search, no K sweep, no plots. For retuning those, use
notebooks/models/player_clustering.ipynb instead.

Usage:
  uv run python scripts/run_player_clustering.py
  uv run python scripts/run_player_clustering.py --seasons 2025 2026
  uv run python scripts/run_player_clustering.py --k 9
"""
from __future__ import annotations

import argparse
import logging
import sys

import numpy as np
import pyarrow.parquet as pq
from sklearn.metrics import davies_bouldin_score, silhouette_score

from portalpoint.modeling import player_clustering as pc
from portalpoint.modeling.io import find_repo_root, get_sync_engine
from portalpoint.modeling.mlflow_helpers import maybe_promote, setup_mlflow

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def main(seasons: list[int] | None, k: int) -> None:
    root = find_repo_root()
    features_dir = root / "data" / "features"
    models_dir = root / "data" / "models"
    engine = get_sync_engine()

    df = pq.read_table(features_dir / "player_features.parquet").to_pandas()
    if seasons:
        df = df[df["season"].isin(seasons)].reset_index(drop=True)
    seasons_in_data = sorted(df["season"].unique().tolist())
    current_season = max(seasons_in_data)
    model_version = f"k{k}-{pc.MODEL_VERSION_REVISION}-{current_season}"
    log.info("Loaded %s player-seasons, seasons=%s", f"{len(df):,}", seasons_in_data)

    required_cols = sorted(set(pc.EXTENDED_FEATURES + pc.SCALED_FEATURES + ["he_player_available"]))
    missing_cols = [c for c in required_cols if c not in df.columns]
    if missing_cols:
        raise ValueError(f"Missing clustering cols in parquet: {missing_cols}")

    for col in pc.BASE_FEATURES:
        if df[col].isna().any():
            df[col] = df[col].fillna(df[col].median())

    if "he_player_two_way_available" in df.columns:
        he_mask = df["he_player_two_way_available"].astype(bool)
    else:
        he_mask = df[pc.HE_PLAYER_STYLE_COLS + pc.HE_PLAYER_DEFENSE_COLS].notna().all(axis=1)
    log.info("HE coverage: %.1f%%", 100 * he_mask.mean())

    scalers = pc.fit_group_scalers(df, he_mask)
    weights = pc.DEFAULT_FEATURE_GROUP_WEIGHTS
    fit_result = pc.fit_player_clusters(df, he_mask, scalers, k=k, weights=weights)
    kmeans = fit_result["kmeans"]

    df["archetype_label"] = df["cluster_id"].map(pc.ARCHETYPE_LABELS)
    memberships_ext = pc.membership_scores(fit_result["dists_ext"], pc.ARCHETYPE_LABELS)
    memberships_base = (
        pc.membership_scores(fit_result["dists_base"], pc.ARCHETYPE_LABELS)
        if len(fit_result["dists_base"]) else []
    )
    df["archetype_memberships"] = None
    for idx, memberships in zip(df.index[he_mask], memberships_ext):
        df.at[idx, "archetype_memberships"] = memberships
    for idx, memberships in zip(df.index[~he_mask], memberships_base):
        df.at[idx, "archetype_memberships"] = memberships

    records = pc.build_archetype_records(df, model_version)
    deleted, upserted = pc.upsert_player_archetypes(engine, records, seasons_in_data)
    log.info("Deleted %s stale rows, upserted %s rows to player_archetypes", f"{deleted:,}", f"{upserted:,}")

    paths = pc.save_artifacts(models_dir, kmeans, scalers, weights, pc.ARCHETYPE_LABELS)
    for name, path in paths.items():
        log.info("Saved %s: %s", name, path)

    try:
        sys.path.insert(0, str(root / "notebooks" / "utils"))
        from s3_helpers import upload

        for key, path in paths.items():
            upload(path, f"models/player_clustering/{path.name}")
    except Exception as exc:
        log.warning("S3 upload skipped: %s", exc)

    rng = np.random.default_rng(pc.RANDOM_STATE)
    all_labels = df["cluster_id"].values
    idx_all = rng.choice(len(fit_result["X_base_all"]), size=min(2000, len(df)), replace=False)
    silhouette_base_all = silhouette_score(fit_result["X_base_all"][idx_all], all_labels[idx_all])
    davies_bouldin_base_all = davies_bouldin_score(fit_result["X_base_all"], all_labels)

    client = setup_mlflow("player-clustering")
    import mlflow
    import mlflow.sklearn

    with mlflow.start_run(run_name=f"player-clustering-s{current_season}-k{k}-script") as run:
        mlflow.log_params({
            "k": k,
            "n_init": 50,
            "max_iter": 500,
            "random_state": pc.RANDOM_STATE,
            "seasons": str(seasons_in_data),
            "current_season": current_season,
            "architecture": "weighted_style_two_way_tuned",
            "feature_group_weights": str(weights),
            "model_version": model_version,
            "source": "script",
        })
        mlflow.log_metrics({
            "silhouette_score": float(silhouette_base_all),
            "davies_bouldin": float(davies_bouldin_base_all),
            "avg_confidence": float(df["confidence"].mean()),
            "n_players": float(len(df)),
        })
        mlflow.sklearn.log_model(kmeans, "kmeans")
        run_id = run.info.run_id

    result = maybe_promote(
        client, "player-clustering", run_id, "kmeans",
        metric_name="silhouette_score", new_value=float(silhouette_base_all), higher_is_better=True,
    )
    log.info("MLflow run %s — %s", run_id, result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Model 1 — Player Clustering (non-interactive)")
    parser.add_argument("--seasons", type=int, nargs="+", default=None, help="Seasons to score (default: all in parquet)")
    parser.add_argument("--k", type=int, default=pc.DEFAULT_K, help="Number of clusters (default: confirmed K)")
    args = parser.parse_args()
    main(args.seasons, args.k)
