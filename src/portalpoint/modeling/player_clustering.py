"""Model 1 — Player Clustering (K-Means).

Pure fit/score/write functions lifted from notebooks/models/player_clustering.ipynb.
K-selection sweeps, visualizations, and manual archetype-label review stay in the
notebook; this module holds the logic that's done changing.
"""
from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from psycopg2.extras import Json
from scipy.spatial.distance import cdist
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from sqlalchemy import Engine

from portalpoint.modeling.db_writers import upsert_with_season_replace

RANDOM_STATE = 42
DEFAULT_K = 9

# Style + two-way feature groups. Group weights are applied after standardizing
# each group, so broad offensive style remains the main signal while defense,
# height, creation, and efficiency contribute without dominating by dimension.
BASE_FEATURE_GROUPS = {
    "base_style_size": ["three_point_rate", "rim_rate", "mid_range_rate", "height_inches"],
    "creation_role": ["usage_rate", "assist_rate"],
    "efficiency_impact": ["true_shooting_pct", "bpm"],
    "base_two_way": ["steal_pct", "block_pct", "def_reb_pct", "off_reb_pct"],
}
HE_FEATURE_GROUPS = {
    "he_offensive_style": [
        "off_style_rim_attack_pct", "off_style_attack_kick_pct",
        "off_style_perimeter_sniper_pct", "off_style_dribble_jumper_pct",
        "off_style_mid_range_pct", "off_style_hits_cutter_pct",
        "off_style_perimeter_cut_pct", "off_style_pnr_passer_pct",
        "off_style_big_cut_roll_pct", "off_style_post_up_pct",
        "off_style_post_kick_pct", "off_style_pick_pop_pct",
        "off_style_high_low_pct", "off_style_reb_scramble_pct",
        "off_style_transition_pct",
    ],
    "he_two_way": ["def_adj_rapm", "def_orb", "def_stl", "def_blk"],
}
DEFAULT_FEATURE_GROUP_WEIGHTS = {
    "base_style_size": 0.18,
    "creation_role": 0.14,
    "efficiency_impact": 0.10,
    "base_two_way": 0.16,
    "he_offensive_style": 0.32,
    "he_two_way": 0.10,
}
WEIGHT_BOUNDS = {
    "base_style_size": (0.14, 0.24),
    "creation_role": (0.10, 0.20),
    "efficiency_impact": (0.04, 0.12),
    "base_two_way": (0.14, 0.24),
    "he_offensive_style": (0.26, 0.40),
    "he_two_way": (0.08, 0.18),
}
WEIGHT_SEARCH_N_TRIALS = 120
WEIGHT_SEARCH_SAMPLE = 5000
WEIGHT_SEARCH_N_INIT = 12

BASE_GROUP_NAMES = list(BASE_FEATURE_GROUPS)
HE_GROUP_NAMES = list(HE_FEATURE_GROUPS)
FEATURE_GROUPS = {**BASE_FEATURE_GROUPS, **HE_FEATURE_GROUPS}
BASE_FEATURES = [c for group in BASE_FEATURE_GROUPS.values() for c in group]
HE_PLAYER_STYLE_COLS = HE_FEATURE_GROUPS["he_offensive_style"]
HE_PLAYER_DEFENSE_COLS = HE_FEATURE_GROUPS["he_two_way"]
EXTENDED_FEATURES = [c for group in FEATURE_GROUPS.values() for c in group]
BASE_DIM = len(BASE_FEATURES)

# Legacy 7-dim barttorvik features still exist in parquet with pre-scaled columns.
LEGACY_MODEL_FEATURES = [
    "usage_rate", "true_shooting_pct", "assist_rate", "bpm",
    "three_point_rate", "rim_rate", "mid_range_rate",
]
SCALED_FEATURES = [f + "_scaled" for f in LEGACY_MODEL_FEATURES]

# Confirmed after centroid review (player_clustering.ipynb Section 4) — cluster IDs are
# stable across reruns because of the semantic reorder in fit_player_clusters().
ARCHETYPE_LABELS = {
    0: "Lead Scoring Playmaker",
    1: "High-Usage Frontcourt Creator",
    2: "Skilled Stretch Forward",
    3: "Post Scoring Big",
    4: "Two-Way Perimeter Guard",
    5: "Pressure Connector Guard",
    6: "Active Connector Forward",
    7: "Two-Way Spacing Wing",
    8: "Interior Star Big",
}

UPSERT_SQL = """
INSERT INTO player_archetypes
    (player_id, season, archetype_id, archetype_label, confidence, archetype_memberships, model_version)
VALUES %s
ON CONFLICT ON CONSTRAINT uq_player_archetype_season
DO UPDATE SET
    archetype_id           = EXCLUDED.archetype_id,
    archetype_label        = EXCLUDED.archetype_label,
    confidence             = EXCLUDED.confidence,
    archetype_memberships  = EXCLUDED.archetype_memberships,
    model_version          = EXCLUDED.model_version
"""


def fit_group_scalers(frame: pd.DataFrame, he_mask: pd.Series) -> dict[str, StandardScaler]:
    scalers = {}
    for name, cols in FEATURE_GROUPS.items():
        fit_frame = frame.loc[he_mask, cols] if name in HE_GROUP_NAMES else frame[cols]
        scalers[name] = StandardScaler().fit(fit_frame.values.astype(float))
    return scalers


def transform_grouped(
    frame: pd.DataFrame,
    scalers: dict[str, StandardScaler],
    group_names: list[str],
    weights: dict[str, float],
) -> np.ndarray:
    parts = []
    for name in group_names:
        cols = FEATURE_GROUPS[name]
        scaled = scalers[name].transform(frame[cols].values.astype(float))
        weight = np.sqrt(weights[name] / len(cols))
        parts.append(scaled * weight)
    return np.hstack(parts)


def _normalize_weight_candidate(candidate: dict[str, float]) -> dict[str, float]:
    weights = {
        name: float(np.clip(candidate[name], *WEIGHT_BOUNDS[name]))
        for name in FEATURE_GROUPS
    }
    # Guardrails: style remains primary, defense remains visible, efficiency cannot dominate.
    if weights["base_two_way"] + weights["he_two_way"] < 0.24:
        missing = 0.24 - (weights["base_two_way"] + weights["he_two_way"])
        weights["base_two_way"] += missing * 0.65
        weights["he_two_way"] += missing * 0.35
    weights["he_offensive_style"] = max(weights["he_offensive_style"], 0.28)
    total = sum(weights.values())
    return {name: value / total for name, value in weights.items()}


def search_feature_weights(
    df: pd.DataFrame,
    he_mask: pd.Series,
    scalers: dict[str, StandardScaler],
    k: int = DEFAULT_K,
    n_trials: int = WEIGHT_SEARCH_N_TRIALS,
    sample_size: int = WEIGHT_SEARCH_SAMPLE,
    n_init: int = WEIGHT_SEARCH_N_INIT,
    random_state: int = RANDOM_STATE,
) -> tuple[dict[str, float], pd.DataFrame]:
    """Constrained random search over feature-group weights.

    K-Means cannot learn feature weights directly; rewards compact/separated
    clusters, balanced cluster sizes, and a clearly defense-forward cluster.
    Returns (best_weights, all_trial_results).
    """
    from sklearn.metrics import davies_bouldin_score, silhouette_score

    defense_cols = [
        "steal_pct", "block_pct", "def_reb_pct", "off_reb_pct",
        "def_adj_rapm", "def_orb", "def_stl", "def_blk",
    ]
    he_df = df.loc[he_mask].copy()

    def _candidate_weights(rng):
        keys = list(DEFAULT_FEATURE_GROUP_WEIGHTS)
        yield _normalize_weight_candidate(DEFAULT_FEATURE_GROUP_WEIGHTS)
        for _ in range(n_trials - 1):
            jittered = np.exp(
                np.log([DEFAULT_FEATURE_GROUP_WEIGHTS[k_] for k_ in keys])
                + rng.normal(0, 0.28, len(keys))
            )
            yield _normalize_weight_candidate(dict(zip(keys, jittered)))

    def _defense_focus_score(sample_frame, labels):
        he_frame = df.loc[he_mask, defense_cols]
        z = pd.DataFrame(index=sample_frame.index)
        for col in defense_cols:
            vals = he_frame[col].astype(float)
            denom = vals.std(ddof=0) or 1.0
            z[col] = (sample_frame[col].astype(float) - vals.mean()) / denom
        by_cluster = z.assign(cluster=labels).groupby("cluster").mean()
        rim_anchor = by_cluster[["block_pct", "def_reb_pct", "off_reb_pct", "def_blk", "def_orb"]].mean(axis=1).max()
        disruptor = by_cluster[["steal_pct", "def_stl", "def_adj_rapm"]].mean(axis=1).max()
        return float(np.clip(max(rim_anchor, disruptor) / 1.25, 0, 1))

    rng = np.random.default_rng(random_state)
    search_idx = rng.choice(len(he_df), size=min(sample_size, len(he_df)), replace=False)
    search_df = he_df.iloc[search_idx].copy()

    rows = []
    for trial, weights in enumerate(_candidate_weights(rng)):
        X_trial = transform_grouped(search_df, scalers, BASE_GROUP_NAMES + HE_GROUP_NAMES, weights)
        labels = KMeans(n_clusters=k, random_state=random_state, n_init=n_init, max_iter=400).fit_predict(X_trial)
        cluster_pct = pd.Series(labels).value_counts(normalize=True)
        rows.append({
            "trial": trial,
            "silhouette": silhouette_score(X_trial, labels),
            "davies_bouldin": davies_bouldin_score(X_trial, labels),
            "balance": min(cluster_pct.min() / 0.05, 1.0) * min(0.24 / cluster_pct.max(), 1.0),
            "defense_focus": _defense_focus_score(search_df, labels),
            "min_cluster_pct": cluster_pct.min(),
            "max_cluster_pct": cluster_pct.max(),
            **weights,
        })

    results = pd.DataFrame(rows)

    def _minmax(series, higher_is_better=True):
        lo, hi = series.min(), series.max()
        if np.isclose(lo, hi):
            return pd.Series(1.0, index=series.index)
        raw = (series - lo) / (hi - lo)
        return raw if higher_is_better else 1 - raw

    results["silhouette_score_norm"] = _minmax(results["silhouette"])
    results["davies_bouldin_score_norm"] = _minmax(results["davies_bouldin"], higher_is_better=False)
    results["weight_score"] = (
        0.35 * results["silhouette_score_norm"]
        + 0.20 * results["davies_bouldin_score_norm"]
        + 0.20 * results["balance"]
        + 0.25 * results["defense_focus"]
    )
    best_row = results.sort_values("weight_score", ascending=False).iloc[0]
    best_weights = {name: float(best_row[name]) for name in FEATURE_GROUPS}
    return best_weights, results


def _z(series: pd.Series) -> pd.Series:
    std = series.std(ddof=0)
    return (series - series.mean()) / std if std else series * 0


def _pick(remaining: set[int], scores: pd.Series, mode: str = "max") -> int:
    scores = scores.loc[list(remaining)]
    return int(scores.idxmax() if mode == "max" else scores.idxmin())


def _inverse_centroids_raw(centers: np.ndarray, group_names: list[str], scalers: dict, weights: dict) -> pd.DataFrame:
    parts = []
    start = 0
    for name in group_names:
        cols = FEATURE_GROUPS[name]
        block = centers[:, start:start + len(cols)]
        scale = np.sqrt(weights[name] / len(cols))
        parts.append(pd.DataFrame(scalers[name].inverse_transform(block / scale), columns=cols))
        start += len(cols)
    return pd.concat(parts, axis=1)


def semantic_player_mapping(centroids: pd.DataFrame) -> dict[int, int]:
    """Maps raw K-Means cluster index -> fixed semantic slot (0-8), matching the
    archetype signatures ARCHETYPE_LABELS is written against. Each pick is removed
    from the pool before the next criterion runs, so no two slots claim the same
    cluster. Cluster indices are otherwise arbitrary and NOT stable across reruns."""
    remaining = set(centroids.index.astype(int))
    z = centroids.apply(_z)
    new_to_old = {}

    new_to_old[7] = _pick(remaining, z["three_point_rate"] - z["usage_rate"])
    remaining.remove(new_to_old[7])
    new_to_old[3] = _pick(remaining, z["rim_rate"] + z["block_pct"] - z["three_point_rate"])
    remaining.remove(new_to_old[3])
    new_to_old[0] = _pick(remaining, z["assist_rate"] + z["usage_rate"] - z["height_inches"])
    remaining.remove(new_to_old[0])
    new_to_old[8] = _pick(remaining, z["height_inches"] + z["assist_rate"] + z["usage_rate"])
    remaining.remove(new_to_old[8])
    new_to_old[1] = _pick(remaining, z["usage_rate"] + z["height_inches"])
    remaining.remove(new_to_old[1])
    new_to_old[2] = _pick(remaining, z["three_point_rate"] + z["height_inches"])
    remaining.remove(new_to_old[2])
    new_to_old[5] = _pick(remaining, z["true_shooting_pct"], "min")
    remaining.remove(new_to_old[5])
    new_to_old[4] = _pick(remaining, z["three_point_rate"] + z["def_adj_rapm"])
    remaining.remove(new_to_old[4])
    new_to_old[6] = remaining.pop()

    return {old: new for new, old in new_to_old.items()}


def reorder_kmeans(km: KMeans, old_to_new: dict[int, int]) -> KMeans:
    new_to_old = {new: old for old, new in old_to_new.items()}
    km.cluster_centers_ = km.cluster_centers_[[new_to_old[i] for i in range(len(new_to_old))]]
    return km


def fit_player_clusters(
    df: pd.DataFrame,
    he_mask: pd.Series,
    scalers: dict[str, StandardScaler],
    k: int = DEFAULT_K,
    weights: dict[str, float] | None = None,
    random_state: int = RANDOM_STATE,
) -> dict[str, Any]:
    """Fit K-Means on HE-covered players, semantically reorder clusters, project
    base-only players onto the nearest centroid (25% confidence discount).

    Mutates df in place: adds cluster_id, confidence, feature_coverage columns.
    Returns dict with kmeans, X_base_all, X_ext, dists_ext, dists_base, old_to_new
    — needed by the notebook's centroid/membership/visualization cells.
    """
    weights = weights or DEFAULT_FEATURE_GROUP_WEIGHTS
    group_names = BASE_GROUP_NAMES + HE_GROUP_NAMES

    X_base_all = transform_grouped(df, scalers, BASE_GROUP_NAMES, weights)
    X_ext = transform_grouped(df.loc[he_mask], scalers, group_names, weights)

    kmeans = KMeans(n_clusters=k, random_state=random_state, n_init=50, max_iter=500)
    kmeans.fit(X_ext)

    pre_reorder_centroids = _inverse_centroids_raw(kmeans.cluster_centers_, group_names, scalers, weights)
    old_to_new = semantic_player_mapping(pre_reorder_centroids)
    kmeans = reorder_kmeans(kmeans, old_to_new)

    dists_ext = cdist(X_ext, kmeans.cluster_centers_, metric="euclidean")
    labels_ext = dists_ext.argmin(axis=1)
    d_min_ext, d_mean_ext = dists_ext.min(axis=1), dists_ext.mean(axis=1)
    conf_ext = np.clip(1 - d_min_ext / d_mean_ext, 0, 1)

    X_base_proj = X_base_all[~he_mask.values]
    centroids_base = kmeans.cluster_centers_[:, :BASE_DIM]
    dists_base = cdist(X_base_proj, centroids_base, metric="euclidean")
    labels_proj = dists_base.argmin(axis=1)
    d_min_base, d_mean_base = dists_base.min(axis=1), dists_base.mean(axis=1)
    conf_proj = np.clip(1 - d_min_base / d_mean_base, 0, 1) * 0.75

    df["cluster_id"] = -1
    df["confidence"] = 0.0
    df["feature_coverage"] = "base_fallback"
    df.loc[he_mask, "cluster_id"] = labels_ext
    df.loc[he_mask, "confidence"] = conf_ext
    df.loc[he_mask, "feature_coverage"] = "he_two_way"
    df.loc[~he_mask, "cluster_id"] = labels_proj
    df.loc[~he_mask, "confidence"] = conf_proj
    df["cluster_id"] = df["cluster_id"].astype(int)

    return {
        "kmeans": kmeans,
        "X_base_all": X_base_all,
        "X_ext": X_ext,
        "dists_ext": dists_ext,
        "dists_base": dists_base,
        "old_to_new": old_to_new,
        "weights": weights,
    }


def membership_scores(dists: np.ndarray, label_map: dict[int, str], top_n: int = 3) -> list[list[dict]]:
    """Distance-derived affinities (not calibrated probabilities) — top-n per row."""
    scale = np.median(dists, axis=1, keepdims=True)
    scale = np.where(scale <= 1e-9, 1.0, scale)
    logits = -(dists - dists.min(axis=1, keepdims=True)) / scale
    probs = np.exp(logits)
    probs = probs / probs.sum(axis=1, keepdims=True)
    rows = []
    for prob_row in probs:
        idx = np.argsort(prob_row)[::-1][:top_n]
        rows.append([
            {"cluster_id": int(i), "label": str(label_map[int(i)]), "score": float(round(prob_row[i], 4))}
            for i in idx
        ])
    return rows


def build_archetype_records(df: pd.DataFrame, model_version: str) -> list[tuple]:
    return [
        (
            int(row.player_id),
            int(row.season),
            int(row.cluster_id),
            str(row.archetype_label),
            float(round(row.confidence, 4)),
            Json(row.archetype_memberships),
            model_version,
        )
        for row in df.itertuples(index=False)
    ]


def upsert_player_archetypes(engine: Engine, records: list[tuple], seasons: list[int]) -> tuple[int, int]:
    return upsert_with_season_replace(
        engine,
        UPSERT_SQL,
        records,
        delete_sql="DELETE FROM player_archetypes WHERE season = ANY(%s)",
        delete_params=(list(seasons),),
    )


def save_artifacts(
    models_dir: Path,
    kmeans: KMeans,
    scalers: dict[str, StandardScaler],
    weights: dict[str, float],
    archetype_labels: dict[int, str] = ARCHETYPE_LABELS,
) -> dict[str, Path]:
    models_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "kmeans": models_dir / "player_kmeans.pkl",
        "scaler_base": models_dir / "player_scaler_base.pkl",
        "scalers_grouped": models_dir / "player_scalers_grouped.pkl",
        "labels": models_dir / "player_archetype_labels.pkl",
    }
    with open(paths["kmeans"], "wb") as f:
        pickle.dump(kmeans, f)
    with open(paths["scaler_base"], "wb") as f:
        pickle.dump({name: scalers[name] for name in BASE_GROUP_NAMES}, f)
    with open(paths["scalers_grouped"], "wb") as f:
        pickle.dump({"scalers": scalers, "feature_groups": FEATURE_GROUPS, "weights": weights}, f)
    with open(paths["labels"], "wb") as f:
        pickle.dump(archetype_labels, f)
    return paths
