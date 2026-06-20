"""Model 2 — Team System Clustering (Two-Layer K-Means).

Pure fit/score/write functions lifted from notebooks/models/team_clustering.ipynb.
Weight-search invocation, visual checks, and label review stay in the notebook;
this module holds the logic that's done changing.
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
from sklearn.metrics import davies_bouldin_score, silhouette_score
from sklearn.preprocessing import StandardScaler
from sqlalchemy import Engine

from portalpoint.modeling.db_writers import upsert_with_season_replace

RANDOM_STATE = 42
DEFAULT_K_OFFENSE = 7
DEFAULT_K_DEFENSE = 5
TOP_N_MEMBERSHIPS = 3
WEIGHT_SEARCH_N_TRIALS = 100
WEIGHT_SEARCH_N_INIT = 12

OFFENSE_FEATURE_GROUPS = {
    "style_shape": ["team_three_rate", "team_rim_rate", "team_mid_rate"],
    "pace": ["adj_tempo"],
    "off_play_type": [
        "off_style_transition_pct", "off_style_post_up_pct", "off_style_pick_pop_pct",
        "off_style_big_cut_roll_pct", "off_style_attack_kick_pct", "off_style_perimeter_cut_pct",
    ],
    "off_passing": ["assisted_fg_pct", "off_ast_rim", "off_ast_mid", "off_ast_threep"],
}
DEFENSE_FEATURE_GROUPS = {
    "def_play_type": [
        "def_style_transition_pct", "def_style_rim_attack_pct", "def_style_attack_kick_pct",
        "def_style_dribble_jumper_pct", "def_style_mid_range_pct", "def_style_perimeter_cut_pct",
        "def_style_big_cut_roll_pct", "def_style_post_up_pct", "def_style_pick_pop_pct",
        "def_style_reb_scramble_pct",
    ],
    "def_pressure_shape": ["def_trans_pct", "def_scramble_pct"],
}
OFFENSE_BASE_GROUP_NAMES = ["style_shape", "pace"]
OFFENSE_GROUP_NAMES = ["style_shape", "pace", "off_play_type", "off_passing"]
DEFENSE_GROUP_NAMES = ["def_play_type", "def_pressure_shape"]

OFFENSE_DEFAULT_WEIGHTS = {"style_shape": 0.30, "pace": 0.15, "off_play_type": 0.35, "off_passing": 0.20}
OFFENSE_WEIGHT_BOUNDS = {"style_shape": (0.20, 0.42), "pace": (0.08, 0.25), "off_play_type": (0.25, 0.50), "off_passing": (0.12, 0.30)}
DEFENSE_DEFAULT_WEIGHTS = {"def_play_type": 0.75, "def_pressure_shape": 0.25}
DEFENSE_WEIGHT_BOUNDS = {"def_play_type": (0.62, 0.86), "def_pressure_shape": (0.14, 0.38)}

OFFENSE_LABELS = {
    0: "Perimeter Creation Offense",
    1: "Rim Pressure Offense",
    2: "Transition Attack",
    3: "Balanced Spread Attack",
    4: "Mid-Range Half-Court Offense",
    5: "Deliberate Half-Court Offense",
    6: "3PT Spacing Offense",
}
DEFENSE_LABELS = {
    0: "Scramble-Heavy Set Defense",
    1: "Rim-Exposure Defense",
    2: "Transition-Vulnerable Defense",
    3: "Jump-Shot Funnel Defense",
    4: "Controlled Half-Court Defense",
}
DEFENSE_UNAVAILABLE_LABEL = "Defense Unavailable"

OFFENSE_FEATURES = [c for cols in OFFENSE_FEATURE_GROUPS.values() for c in cols]
DEFENSE_FEATURES = [c for cols in DEFENSE_FEATURE_GROUPS.values() for c in cols]
BART_FEATURES = OFFENSE_FEATURE_GROUPS["style_shape"] + OFFENSE_FEATURE_GROUPS["pace"]

UPSERT_SQL = (
    "INSERT INTO team_system_profiles "
    "(school_id, season, cluster_id, system_label, offense_cluster_id, defense_cluster_id, "
    "offense_memberships, defense_memberships, system_memberships, style_vector, model_version) "
    "VALUES %s "
    "ON CONFLICT ON CONSTRAINT uq_team_system_season DO UPDATE SET "
    "cluster_id = EXCLUDED.cluster_id, system_label = EXCLUDED.system_label, "
    "offense_cluster_id = EXCLUDED.offense_cluster_id, defense_cluster_id = EXCLUDED.defense_cluster_id, "
    "offense_memberships = EXCLUDED.offense_memberships, defense_memberships = EXCLUDED.defense_memberships, "
    "system_memberships = EXCLUDED.system_memberships, style_vector = EXCLUDED.style_vector, model_version = EXCLUDED.model_version"
)


def fit_scalers(df_all: pd.DataFrame, df_he: pd.DataFrame, feature_groups: dict, base_group_names: list[str]) -> dict[str, StandardScaler]:
    scalers = {}
    for name, cols in feature_groups.items():
        fit_df = df_all if name in base_group_names else df_he
        scalers[name] = StandardScaler().fit(fit_df[cols].astype(float).values)
    return scalers


def transform_grouped(df_in: pd.DataFrame, scalers: dict, feature_groups: dict, group_names: list[str], weights: dict) -> np.ndarray:
    parts = []
    for name in group_names:
        cols = feature_groups[name]
        values = df_in[cols].astype(float).copy()
        values = values.fillna(pd.Series(scalers[name].mean_, index=cols))
        block = scalers[name].transform(values.values)
        scale = np.sqrt(weights[name] / len(cols))
        parts.append(block * scale)
    return np.hstack(parts)


def _normalize_weights(candidate: dict, bounds: dict) -> dict:
    clipped = {name: float(np.clip(candidate[name], *bounds[name])) for name in candidate}
    total = sum(clipped.values())
    return {name: value / total for name, value in clipped.items()}


def _sample_weights(defaults: dict, bounds: dict, rng) -> dict:
    return _normalize_weights({name: defaults[name] * float(np.exp(rng.normal(0, 0.28))) for name in defaults}, bounds)


def _cluster_score(X: np.ndarray, labels: np.ndarray) -> dict:
    counts = np.bincount(labels)
    if len(counts) <= 1 or counts.min() == 0:
        return {"score": -np.inf, "silhouette": np.nan, "davies_bouldin": np.nan, "balance": 0.0}
    sil = silhouette_score(X, labels)
    db = davies_bouldin_score(X, labels)
    balance = counts.min() / counts.max()
    score = 0.45 * ((sil + 1) / 2) + 0.25 * (1 / (1 + db)) + 0.30 * balance
    return {"score": float(score), "silhouette": float(sil), "davies_bouldin": float(db), "balance": float(balance)}


def tune_weights(
    df_fit: pd.DataFrame, scalers: dict, feature_groups: dict, group_names: list[str],
    defaults: dict, bounds: dict, k: int, random_state: int, n_trials: int = WEIGHT_SEARCH_N_TRIALS,
    n_init: int = WEIGHT_SEARCH_N_INIT,
) -> tuple[dict, pd.DataFrame]:
    rng = np.random.default_rng(random_state)
    candidates = [_normalize_weights(defaults, bounds)] + [_sample_weights(defaults, bounds, rng) for _ in range(n_trials - 1)]
    rows = []
    for trial, weights in enumerate(candidates):
        X = transform_grouped(df_fit, scalers, feature_groups, group_names, weights)
        labels = KMeans(n_clusters=k, random_state=random_state, n_init=n_init, max_iter=500).fit_predict(X)
        rows.append({"trial": trial, **weights, **_cluster_score(X, labels)})
    results = pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)
    return {name: float(results.loc[0, name]) for name in defaults}, results


def inverse_centroids(kmeans: KMeans, scalers: dict, feature_groups: dict, group_names: list[str], weights: dict) -> pd.DataFrame:
    parts = []
    start = 0
    for name in group_names:
        cols = feature_groups[name]
        block = kmeans.cluster_centers_[:, start:start + len(cols)]
        scale = np.sqrt(weights[name] / len(cols))
        parts.append(pd.DataFrame(scalers[name].inverse_transform(block / scale), columns=cols))
        start += len(cols)
    return pd.concat(parts, axis=1)


def _z(series: pd.Series) -> pd.Series:
    std = series.std(ddof=0)
    return (series - series.mean()) / std if std else series * 0


def _pick(remaining: set[int], scores: pd.Series, mode: str = "max") -> int:
    scores = scores.loc[list(remaining)]
    return int(scores.idxmax() if mode == "max" else scores.idxmin())


def semantic_offense_mapping(centroids: pd.DataFrame) -> dict[int, int]:
    remaining = set(centroids.index.astype(int))
    z = centroids.apply(_z)
    new_to_old = {}
    new_to_old[2] = _pick(remaining, z["adj_tempo"] + z["off_style_transition_pct"]); remaining.remove(new_to_old[2])
    new_to_old[5] = _pick(remaining, z["adj_tempo"], "min"); remaining.remove(new_to_old[5])
    new_to_old[4] = _pick(remaining, z["team_mid_rate"] + z["off_ast_mid"]); remaining.remove(new_to_old[4])
    new_to_old[6] = _pick(remaining, z["team_three_rate"] + z["off_ast_threep"]); remaining.remove(new_to_old[6])
    new_to_old[1] = _pick(remaining, z["team_rim_rate"] + z["off_ast_rim"]); remaining.remove(new_to_old[1])
    new_to_old[3] = _pick(remaining, z["adj_tempo"] + z["team_three_rate"] - z["team_mid_rate"]); remaining.remove(new_to_old[3])
    new_to_old[0] = remaining.pop()
    return {old: new for new, old in new_to_old.items()}


def semantic_defense_mapping(centroids: pd.DataFrame) -> dict[int, int]:
    remaining = set(centroids.index.astype(int))
    z = centroids.apply(_z)
    new_to_old = {}
    new_to_old[2] = _pick(remaining, z["def_trans_pct"] + z["def_style_transition_pct"]); remaining.remove(new_to_old[2])
    new_to_old[4] = _pick(remaining, z["def_trans_pct"] + z["def_scramble_pct"], "min"); remaining.remove(new_to_old[4])
    new_to_old[1] = _pick(remaining, z["def_style_rim_attack_pct"] + z["def_style_perimeter_cut_pct"] + z["def_style_big_cut_roll_pct"]); remaining.remove(new_to_old[1])
    new_to_old[3] = _pick(remaining, z["def_style_dribble_jumper_pct"] + z["def_style_pick_pop_pct"] + z["def_style_attack_kick_pct"]); remaining.remove(new_to_old[3])
    new_to_old[0] = remaining.pop()
    return {old: new for new, old in new_to_old.items()}


def reorder_kmeans(kmeans: KMeans, old_to_new: dict[int, int]) -> KMeans:
    new_to_old = {new: old for old, new in old_to_new.items()}
    kmeans.cluster_centers_ = kmeans.cluster_centers_[[new_to_old[i] for i in range(len(new_to_old))]]
    return kmeans


def confidence_from_dists(dists: np.ndarray) -> np.ndarray:
    ordered = np.sort(dists, axis=1)
    return np.clip(1 - ordered[:, 0] / (ordered[:, 1] + 1e-9), 0, 1)


def membership_scores(dists: np.ndarray, label_map: dict[int, str], top_n: int = TOP_N_MEMBERSHIPS) -> list[list[dict]]:
    nearest = dists.min(axis=1)
    scale = float(np.median(nearest)) if len(nearest) else 1.0
    scale = scale if scale > 1e-9 else 1.0
    sims = np.exp(-dists / scale)
    sims = sims / sims.sum(axis=1, keepdims=True)
    rows = []
    for row in sims:
        idx = np.argsort(row)[::-1][:top_n]
        rows.append([{"cluster_id": int(i), "label": label_map[int(i)], "score": round(float(row[i]), 4)} for i in idx])
    return rows


def combine_memberships(offense_memberships: list, defense_memberships: list, top_n: int = TOP_N_MEMBERSHIPS) -> list:
    rows = []
    for off_row, def_row in zip(offense_memberships, defense_memberships):
        combos = []
        for off in off_row:
            for defense in def_row:
                combos.append({
                    "offense_cluster_id": int(off["cluster_id"]),
                    "defense_cluster_id": int(defense["cluster_id"]),
                    "label": f"{off['label']} / {defense['label']}",
                    "score": round(float(off["score"]) * float(defense["score"]), 4),
                })
        rows.append(sorted(combos, key=lambda x: x["score"], reverse=True)[:top_n])
    return rows


def fit_two_layer_clusters(
    df: pd.DataFrame, df_he: pd.DataFrame, df_fallback: pd.DataFrame,
    offense_scalers: dict, defense_scalers: dict, offense_weights: dict, defense_weights: dict,
    k_offense: int = DEFAULT_K_OFFENSE, k_defense: int = DEFAULT_K_DEFENSE, random_state: int = RANDOM_STATE,
) -> dict[str, Any]:
    """Fit offense (all teams, HE-covered fit) + defense (HE-covered only) K-Means,
    semantically reorder both, assign clusters + confidence to df_he/df_fallback.

    Returns dict with offense_kmeans, defense_kmeans, dists (he/fb), labels, df (merged).
    """
    X_offense_he = transform_grouped(df_he, offense_scalers, OFFENSE_FEATURE_GROUPS, OFFENSE_GROUP_NAMES, offense_weights)
    X_defense_he = transform_grouped(df_he, defense_scalers, DEFENSE_FEATURE_GROUPS, DEFENSE_GROUP_NAMES, defense_weights)

    offense_kmeans = KMeans(n_clusters=k_offense, random_state=random_state, n_init=50, max_iter=500)
    defense_kmeans = KMeans(n_clusters=k_defense, random_state=random_state, n_init=50, max_iter=500)
    offense_kmeans.fit_predict(X_offense_he)
    defense_kmeans.fit_predict(X_defense_he)

    off_pre = inverse_centroids(offense_kmeans, offense_scalers, OFFENSE_FEATURE_GROUPS, OFFENSE_GROUP_NAMES, offense_weights)
    def_pre = inverse_centroids(defense_kmeans, defense_scalers, DEFENSE_FEATURE_GROUPS, DEFENSE_GROUP_NAMES, defense_weights)
    off_old_to_new = semantic_offense_mapping(off_pre)
    def_old_to_new = semantic_defense_mapping(def_pre)
    offense_kmeans = reorder_kmeans(offense_kmeans, off_old_to_new)
    defense_kmeans = reorder_kmeans(defense_kmeans, def_old_to_new)

    offense_dists_he = cdist(X_offense_he, offense_kmeans.cluster_centers_, metric="euclidean")
    defense_dists_he = cdist(X_defense_he, defense_kmeans.cluster_centers_, metric="euclidean")
    df_he = df_he.copy()
    df_he["offense_cluster_id"] = offense_dists_he.argmin(axis=1)
    df_he["defense_cluster_id"] = defense_dists_he.argmin(axis=1)
    df_he["offense_confidence"] = confidence_from_dists(offense_dists_he)
    df_he["defense_confidence"] = confidence_from_dists(defense_dists_he)
    df_he["confidence"] = (df_he["offense_confidence"] + df_he["defense_confidence"]) / 2

    if len(df_fallback):
        df_fallback = df_fallback.copy()
        X_offense_fb = transform_grouped(df_fallback, offense_scalers, OFFENSE_FEATURE_GROUPS, OFFENSE_BASE_GROUP_NAMES, offense_weights)
        offense_dists_fb = cdist(X_offense_fb, offense_kmeans.cluster_centers_[:, :len(BART_FEATURES)], metric="euclidean")
        df_fallback["offense_cluster_id"] = offense_dists_fb.argmin(axis=1)
        df_fallback["defense_cluster_id"] = np.nan
        df_fallback["offense_confidence"] = confidence_from_dists(offense_dists_fb) * 0.75
        df_fallback["defense_confidence"] = np.nan
        df_fallback["confidence"] = df_fallback["offense_confidence"]
    else:
        offense_dists_fb = np.empty((0, k_offense))

    merged = pd.concat([df_he, df_fallback], ignore_index=True).sort_values(["season", "school_id"]).reset_index(drop=True)
    merged["cluster_id"] = merged["offense_cluster_id"].astype(int)
    merged["offense_label"] = merged["offense_cluster_id"].astype(int).map(OFFENSE_LABELS)
    merged["defense_label"] = merged["defense_cluster_id"].map(lambda x: DEFENSE_LABELS[int(x)] if pd.notna(x) else DEFENSE_UNAVAILABLE_LABEL)
    merged["system_label"] = merged["offense_label"] + " / " + merged["defense_label"]

    return {
        "offense_kmeans": offense_kmeans,
        "defense_kmeans": defense_kmeans,
        "df": merged,
        "df_he": df_he,
        "df_fallback": df_fallback,
        "X_offense_he": X_offense_he,
        "X_defense_he": X_defense_he,
        "offense_dists_he": offense_dists_he,
        "defense_dists_he": defense_dists_he,
        "offense_dists_fb": offense_dists_fb,
    }


def build_system_memberships(
    df: pd.DataFrame, df_he: pd.DataFrame, df_fallback: pd.DataFrame,
    offense_dists_he: np.ndarray, defense_dists_he: np.ndarray, offense_dists_fb: np.ndarray,
    top_n: int = TOP_N_MEMBERSHIPS,
) -> pd.DataFrame:
    """Soft top-n offense/defense/system memberships, merged onto df by
    (school_id, season). Required before build_team_profile_records — these are
    DB columns, not just a notebook diagnostic."""
    offense_memberships_he = membership_scores(offense_dists_he, OFFENSE_LABELS, top_n)
    defense_memberships_he = membership_scores(defense_dists_he, DEFENSE_LABELS, top_n)
    system_memberships_he = combine_memberships(offense_memberships_he, defense_memberships_he, top_n)

    he_members = df_he[["school_id", "season"]].copy()
    he_members["offense_memberships"] = offense_memberships_he
    he_members["defense_memberships"] = defense_memberships_he
    he_members["system_memberships"] = system_memberships_he

    if len(df_fallback):
        offense_memberships_fb = membership_scores(offense_dists_fb, OFFENSE_LABELS, top_n)
        fb_members = df_fallback[["school_id", "season"]].copy()
        fb_members["offense_memberships"] = offense_memberships_fb
        fb_members["defense_memberships"] = [
            [{"cluster_id": None, "label": DEFENSE_UNAVAILABLE_LABEL, "score": 1.0}] for _ in offense_memberships_fb
        ]
        fb_members["system_memberships"] = [
            [
                {
                    "offense_cluster_id": int(item["cluster_id"]),
                    "defense_cluster_id": None,
                    "label": f"{item['label']} / {DEFENSE_UNAVAILABLE_LABEL}",
                    "score": item["score"],
                }
                for item in row
            ]
            for row in offense_memberships_fb
        ]
    else:
        fb_members = pd.DataFrame(columns=["school_id", "season", "offense_memberships", "defense_memberships", "system_memberships"])

    members = pd.concat([he_members, fb_members], ignore_index=True)
    return df.merge(members, on=["school_id", "season"], how="left")


def _nullable_int(value):
    return None if pd.isna(value) else int(value)


def build_team_profile_records(df: pd.DataFrame, model_version: str) -> list[tuple]:
    return [
        (
            int(row.school_id), int(row.season), int(row.cluster_id), str(row.system_label),
            int(row.offense_cluster_id), _nullable_int(row.defense_cluster_id),
            Json(row.offense_memberships), Json(row.defense_memberships), Json(row.system_memberships),
            [float(getattr(row, f)) for f in BART_FEATURES], model_version,
        )
        for row in df.itertuples(index=False)
    ]


def upsert_team_system_profiles(engine: Engine, records: list[tuple], seasons: list[int]) -> tuple[int, int]:
    return upsert_with_season_replace(
        engine,
        UPSERT_SQL,
        records,
        delete_sql="DELETE FROM team_system_profiles WHERE season = ANY(%s)",
        delete_params=(list(seasons),),
    )


def save_artifacts(
    models_dir: Path, offense_kmeans: KMeans, defense_kmeans: KMeans,
    offense_scalers: dict, defense_scalers: dict, offense_weights: dict, defense_weights: dict,
    model_version: str,
) -> dict[str, Path]:
    models_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "offense_kmeans": models_dir / "team_offense_kmeans.pkl",
        "defense_kmeans": models_dir / "team_defense_kmeans.pkl",
        "legacy_kmeans": models_dir / "team_kmeans.pkl",
        "scaler_base": models_dir / "team_scaler_base.pkl",
        "scalers_grouped": models_dir / "team_scalers_grouped.pkl",
        "labels": models_dir / "team_system_labels.pkl",
    }
    pickle.dump(offense_kmeans, open(paths["offense_kmeans"], "wb"))
    pickle.dump(defense_kmeans, open(paths["defense_kmeans"], "wb"))
    pickle.dump(offense_kmeans, open(paths["legacy_kmeans"], "wb"))
    pickle.dump({name: offense_scalers[name] for name in OFFENSE_BASE_GROUP_NAMES}, open(paths["scaler_base"], "wb"))
    pickle.dump({
        "offense_scalers": offense_scalers, "defense_scalers": defense_scalers,
        "offense_feature_groups": OFFENSE_FEATURE_GROUPS, "defense_feature_groups": DEFENSE_FEATURE_GROUPS,
        "offense_weights": offense_weights, "defense_weights": defense_weights,
        "offense_labels": OFFENSE_LABELS, "defense_labels": DEFENSE_LABELS,
        "model_version": model_version,
    }, open(paths["scalers_grouped"], "wb"))
    pickle.dump({"offense": OFFENSE_LABELS, "defense": DEFENSE_LABELS, "defense_unavailable": DEFENSE_UNAVAILABLE_LABEL}, open(paths["labels"], "wb"))
    return paths
