"""Gap Matching — Roster Need Scorer.

Pure fit/score/write functions lifted from notebooks/models/gap_matching.ipynb.
Scores every eligible player-school-season pair and preserves Scheme Fit context
where those rows already exist in player_team_fit_scores.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import StandardScaler

from portalpoint.modeling.db_writers import upsert_with_season_replace

MODEL_VERSION = "gap-cos-v3"
MIN_GAMES = 5
BASELINE_GAP_MATCH = 15.0

GAP_FEATURES = [
    "usage_rate",
    "true_shooting_pct",
    "assist_rate",
    "tov_pct_inverse",
    "off_reb_pct",
    "def_reb_pct",
    "block_pct",
    "steal_pct",
    "free_throw_rate",
    "three_point_rate",
    "rim_rate",
    "mid_range_rate",
    "fg3_pct",
    "rim_pct",
]
POS_COLS = ["pos_confidence_pg", "pos_confidence_sg", "pos_confidence_sf", "pos_confidence_pf", "pos_confidence_c"]
POS_NAMES = ["PG", "SG", "SF", "PF", "C"]
POSITION_SOURCE_COL = "position_source"
POSITION_RELIABILITY_COL = "position_reliability"
SAMPLE_RELIABILITY_COL = "sample_reliability"
FEATURE_RELIABILITY_COL = "feature_reliability"
GAP_RELIABILITY_COL = "gap_reliability"
ROLE_POSITION_PRIORS = {
    "pure pg": [0.90, 0.10, 0.00, 0.00, 0.00],
    "scoring pg": [0.90, 0.10, 0.00, 0.00, 0.00],
    "combo g": [0.15, 0.75, 0.10, 0.00, 0.00],
    "wing g": [0.00, 0.65, 0.30, 0.05, 0.00],
    "wing f": [0.00, 0.05, 0.75, 0.15, 0.05],
    "stretch 4": [0.00, 0.00, 0.15, 0.80, 0.05],
    "pf/c": [0.00, 0.00, 0.05, 0.45, 0.50],
    "c": [0.00, 0.00, 0.00, 0.05, 0.95],
}
POSITION_SOURCE_RELIABILITY = {
    "hoop_explorer": 1.00,
    "barttorvik_role": 0.82,
    "players_position": 0.72,
    "height_prior": 0.55,
    "uniform_prior": 0.40,
}

W_GAP = 0.20
W_SCHEME = 0.30
W_ROLE = 0.25
W_PROG = 0.25

UPSERT_SQL = """
INSERT INTO player_team_fit_scores
    (player_id, school_id, season, gap_match, overall_fit,
     scheme_fit, role_fit, program_fit,
     weight_gap, weight_scheme, weight_role, weight_program,
     breakdown, model_version, computed_at, expires_at)
VALUES %s
ON CONFLICT ON CONSTRAINT uq_fit_score DO UPDATE SET
    gap_match     = EXCLUDED.gap_match,
    overall_fit   = EXCLUDED.overall_fit,
    breakdown     = EXCLUDED.breakdown,
    model_version = EXCLUDED.model_version,
    computed_at   = EXCLUDED.computed_at,
    expires_at    = EXCLUDED.expires_at
"""


def assign_soft_positions(df: pd.DataFrame) -> pd.DataFrame:
    """HE pos_confidence_* where available (~59%); one-hot players.position
    fallback otherwise. If positions are generic/missing, use BartTorvik role,
    then a soft height prior, rather than leaving rows with all-zero weights."""
    df = df.copy()
    mask_no_he = df[POS_COLS].isna().all(axis=1)
    df[POSITION_SOURCE_COL] = np.where(mask_no_he, None, "hoop_explorer")
    df[POSITION_RELIABILITY_COL] = np.where(
        mask_no_he, np.nan, POSITION_SOURCE_RELIABILITY["hoop_explorer"]
    )
    df[POS_COLS] = df[POS_COLS].apply(pd.to_numeric, errors="coerce").fillna(0.0)

    row_sums = df[POS_COLS].sum(axis=1)
    if "barttorvik_role" in df.columns:
        needs_role_fallback = row_sums < 1e-6
        roles = df.loc[needs_role_fallback, "barttorvik_role"].fillna("").astype(str).str.strip().str.lower()
        for idx, role in roles.items():
            prior = ROLE_POSITION_PRIORS.get(role)
            if prior is not None:
                df.loc[idx, POS_COLS] = prior
                df.loc[idx, POSITION_SOURCE_COL] = "barttorvik_role"
                df.loc[idx, POSITION_RELIABILITY_COL] = POSITION_SOURCE_RELIABILITY["barttorvik_role"]
        row_sums = df[POS_COLS].sum(axis=1)

    needs_position_fallback = row_sums < 1e-6
    for pos_name, pos_col in zip(POS_NAMES, POS_COLS):
        exact_mask = needs_position_fallback & (df["position"] == pos_name)
        df.loc[exact_mask, pos_col] = 1.0
        df.loc[exact_mask, POSITION_SOURCE_COL] = "players_position"
        df.loc[exact_mask, POSITION_RELIABILITY_COL] = POSITION_SOURCE_RELIABILITY["players_position"]

    row_sums = df[POS_COLS].sum(axis=1)
    needs_height_fallback = row_sums < 1e-6
    if needs_height_fallback.any() and "height_inches" in df.columns:
        heights = pd.to_numeric(df.loc[needs_height_fallback, "height_inches"], errors="coerce")
        for idx, height in heights.items():
            if pd.isna(height):
                df.loc[idx, POS_COLS] = 1.0 / len(POS_NAMES)
                df.loc[idx, POSITION_SOURCE_COL] = "uniform_prior"
                df.loc[idx, POSITION_RELIABILITY_COL] = POSITION_SOURCE_RELIABILITY["uniform_prior"]
            elif height <= 73:
                df.loc[idx, POS_COLS] = [0.70, 0.25, 0.05, 0.00, 0.00]
                df.loc[idx, POSITION_SOURCE_COL] = "height_prior"
                df.loc[idx, POSITION_RELIABILITY_COL] = POSITION_SOURCE_RELIABILITY["height_prior"]
            elif height <= 76:
                df.loc[idx, POS_COLS] = [0.20, 0.55, 0.20, 0.05, 0.00]
                df.loc[idx, POSITION_SOURCE_COL] = "height_prior"
                df.loc[idx, POSITION_RELIABILITY_COL] = POSITION_SOURCE_RELIABILITY["height_prior"]
            elif height <= 79:
                df.loc[idx, POS_COLS] = [0.05, 0.20, 0.50, 0.20, 0.05]
                df.loc[idx, POSITION_SOURCE_COL] = "height_prior"
                df.loc[idx, POSITION_RELIABILITY_COL] = POSITION_SOURCE_RELIABILITY["height_prior"]
            elif height <= 82:
                df.loc[idx, POS_COLS] = [0.00, 0.05, 0.25, 0.50, 0.20]
                df.loc[idx, POSITION_SOURCE_COL] = "height_prior"
                df.loc[idx, POSITION_RELIABILITY_COL] = POSITION_SOURCE_RELIABILITY["height_prior"]
            else:
                df.loc[idx, POS_COLS] = [0.00, 0.00, 0.05, 0.25, 0.70]
                df.loc[idx, POSITION_SOURCE_COL] = "height_prior"
                df.loc[idx, POSITION_RELIABILITY_COL] = POSITION_SOURCE_RELIABILITY["height_prior"]
    elif needs_height_fallback.any():
        df.loc[needs_height_fallback, POS_COLS] = 1.0 / len(POS_NAMES)
        df.loc[needs_height_fallback, POSITION_SOURCE_COL] = "uniform_prior"
        df.loc[needs_height_fallback, POSITION_RELIABILITY_COL] = POSITION_SOURCE_RELIABILITY["uniform_prior"]

    row_sums = df[POS_COLS].sum(axis=1).clip(lower=1e-9)
    df[POS_COLS] = df[POS_COLS].div(row_sums, axis=0)
    df[POSITION_SOURCE_COL] = df[POSITION_SOURCE_COL].fillna("uniform_prior")
    df[POSITION_RELIABILITY_COL] = (
        pd.to_numeric(df[POSITION_RELIABILITY_COL], errors="coerce")
        .fillna(POSITION_SOURCE_RELIABILITY["uniform_prior"])
        .clip(0.0, 1.0)
    )
    return df


def prepare_gap_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build production gap-vector features from mostly rate/style columns.

    Every feature is oriented so higher means "more of this useful skill/style".
    Turnover percentage is inverted so teams weak at ball security look short on
    `tov_pct_inverse`, not high-turnover players.
    """
    df = df.copy()
    df["tov_pct_inverse"] = 100.0 - pd.to_numeric(df["tov_pct"], errors="coerce")
    for col in GAP_FEATURES:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    missing_count = df[GAP_FEATURES].isna().sum(axis=1)
    df[FEATURE_RELIABILITY_COL] = (1.0 - 0.5 * missing_count / len(GAP_FEATURES)).clip(0.5, 1.0)
    for col in GAP_FEATURES:
        season_medians = df.groupby("season")[col].transform("median")
        global_median = df[col].median()
        fallback = 0.0 if pd.isna(global_median) else float(global_median)
        df[col] = df[col].fillna(season_medians).fillna(fallback)
    return df


def add_gap_reliability(df: pd.DataFrame) -> pd.DataFrame:
    """Reliability used to shrink raw geometric gap scores.

    Position reliability captures source quality. Sample reliability captures
    whether the player's season sample is large enough for the rates to be
    trusted. The combined value is intentionally conservative for low-minute
    fallback-position players.
    """
    df = df.copy()
    games = pd.to_numeric(df["games_played"], errors="coerce").fillna(0.0).clip(lower=0.0)
    if "min_pct" in df.columns:
        min_pct = pd.to_numeric(df["min_pct"], errors="coerce").fillna(0.0).clip(lower=0.0)
    else:
        min_pct = pd.Series(0.0, index=df.index)
    games_rel = (games / 20.0).clip(upper=1.0)
    minutes_rel = np.sqrt((min_pct / 50.0).clip(upper=1.0))
    df[SAMPLE_RELIABILITY_COL] = (games_rel * minutes_rel).clip(0.0, 1.0)
    pos_rel = pd.to_numeric(df[POSITION_RELIABILITY_COL], errors="coerce").fillna(
        POSITION_SOURCE_RELIABILITY["uniform_prior"]
    )
    if FEATURE_RELIABILITY_COL in df.columns:
        feature_rel = pd.to_numeric(df[FEATURE_RELIABILITY_COL], errors="coerce").fillna(1.0)
    else:
        feature_rel = pd.Series(1.0, index=df.index)
    df[GAP_RELIABILITY_COL] = (pos_rel * df[SAMPLE_RELIABILITY_COL] * feature_rel).clip(0.0, 1.0)
    return df


EXISTING_CONTEXT_SQL = """
SELECT player_id, school_id, scheme_fit, breakdown
FROM player_team_fit_scores
WHERE season = %s AND school_id = ANY(%s) AND scheme_fit > 0
"""


def load_existing_scheme_context(engine, season: int, school_ids: list[int]) -> dict[tuple[int, int, int], dict]:
    """Scheme Fit context lookup scoped to (season, school_ids) — one school
    chunk at a time, not the whole table.

    Scheme Fit went all-pairs in scheme-cos-v3 (~9.6M rows). Preloading the
    entire scheme_fit > 0 table into one python dict up front (the original
    approach, fine at the old ~1.3M-row top-50 scope) took ~64 minutes at that
    scale. Querying per chunk instead uses the
    ix_fit_scores_school_season_candidate (school_id, season, ...) index and
    only ever holds one chunk's rows (~ players x len(school_ids)) in memory.
    """
    raw_conn = engine.raw_connection()
    try:
        with raw_conn.cursor() as cur:
            cur.execute(EXISTING_CONTEXT_SQL, (season, list(school_ids)))
            rows = cur.fetchall()
    finally:
        raw_conn.close()
    return {(int(pid), int(sid), season): {"scheme_fit": sfv, "breakdown": bd} for pid, sid, sfv, bd in rows}


def filter_departed(df: pd.DataFrame, departed_pairs: set[tuple[int, int]], current_season: int) -> pd.DataFrame:
    """gap-cos-v2: exclude (player_id, school_id) pairs that have since
    transferred out of school_id, for current_season only — historical
    seasons' rosters are already correct as-is; departure-awareness only
    matters for "who's actually on the roster right now". departed_pairs
    comes from `transfers` (Issue #17 items 3-4 — previously empty, now real)."""
    if not departed_pairs:
        return df
    is_current = df["season"] == current_season
    departed_mask = is_current & df.apply(
        lambda r: (int(r["player_id"]), int(r["school_id"])) in departed_pairs, axis=1
    )
    return df[~departed_mask].reset_index(drop=True)


def build_league_benchmarks(df: pd.DataFrame, seasons: list[int]) -> dict[int, np.ndarray]:
    """benchmark[season] = (5, 8) soft-weighted mean stat vector per position."""
    benchmarks = {}
    for season in seasons:
        s = df[df["season"] == season]
        B = np.zeros((5, len(GAP_FEATURES)))
        stats = s[GAP_FEATURES].values.astype(float)
        for p_i, pos_col in enumerate(POS_COLS):
            w = s[pos_col].values
            w_sum = w.sum()
            B[p_i] = (w[:, None] * stats).sum(axis=0) / w_sum if w_sum > 0 else stats.mean(axis=0)
        benchmarks[season] = B
    return benchmarks


def build_roster_gap_vectors(df: pd.DataFrame, benchmarks: dict[int, np.ndarray], seasons: list[int]) -> dict[int, dict[int, dict]]:
    """gap_data[season][school_id] = {'gap_vecs': (5,8) max(0, benchmark-roster_mean), 'depth': (5,)}."""
    gap_data: dict[int, dict[int, dict]] = {}
    for season in seasons:
        s_df = df[df["season"] == season]
        B = benchmarks[season]
        season_d = {}
        for sid in s_df["school_id"].unique():
            g = s_df[s_df["school_id"] == sid]
            stats = g[GAP_FEATURES].values.astype(float)
            weights = g[POS_COLS].values.astype(float)
            roster_mean = np.zeros((5, len(GAP_FEATURES)))
            depth = np.zeros(5)
            for p_i in range(5):
                w = weights[:, p_i]
                w_sum = w.sum()
                depth[p_i] = w_sum
                roster_mean[p_i] = (w[:, None] * stats).sum(axis=0) / w_sum if w_sum > 0 else np.zeros(len(GAP_FEATURES))
            gap_vecs = np.maximum(0.0, B - roster_mean)
            season_d[int(sid)] = {"gap_vecs": gap_vecs, "depth": depth}
        gap_data[season] = season_d
    return gap_data


def build_archetype_deficits(df: pd.DataFrame, seasons: list[int]) -> dict[int, dict[int, set[int]]]:
    """arch_deficit[season][school_id] = set of archetype_id the school is short on
    (top-2 vs. league distribution), using df['archetype_id'] already merged in."""
    n_clusters = int(df["archetype_id"].dropna().max()) + 1
    arch_deficit: dict[int, dict[int, set[int]]] = {}
    for season in seasons:
        s_df = df[df["season"] == season]
        league_counts = s_df["archetype_id"].value_counts(normalize=True)
        league_dist = np.zeros(n_clusters)
        for c, v in league_counts.items():
            if pd.notna(c):
                league_dist[int(c)] = v

        season_deficit = {}
        for sid in s_df["school_id"].unique():
            g = s_df[s_df["school_id"] == sid]
            school_counts = g["archetype_id"].value_counts(normalize=True)
            school_dist = np.zeros(n_clusters)
            for c, v in school_counts.items():
                if pd.notna(c):
                    school_dist[int(c)] = v
            gap_arr = league_dist - school_dist
            pos_gaps = np.where(gap_arr > 0)[0]
            deficit = set(pos_gaps[np.argsort(gap_arr[pos_gaps])[-2:]]) if len(pos_gaps) > 0 else set()
            season_deficit[int(sid)] = deficit
        arch_deficit[season] = season_deficit
    return arch_deficit


def fit_gap_scaler(df: pd.DataFrame) -> StandardScaler:
    scaler = StandardScaler()
    scaler.fit(df[GAP_FEATURES].values.astype(float))
    return scaler


def prescale_gap_tensors(gap_data: dict[int, dict[int, dict]], scaler: StandardScaler, seasons: list[int]) -> dict[int, dict[int, np.ndarray]]:
    gap_scaled: dict[int, dict[int, np.ndarray]] = {}
    scale = np.where(scaler.scale_ == 0, 1.0, scaler.scale_)
    for season in seasons:
        gap_scaled[season] = {sid: d["gap_vecs"] / scale for sid, d in gap_data[season].items()}
    return gap_scaled


def depth_score(depth_val: float) -> float:
    return float(np.clip(100.0 - depth_val * 20.0, 0.0, 100.0))


def compute_gap_match_ondemand(player_vec: list[float], gap_vec: list[float]) -> dict:
    """Single-pair soft-weighted-cosine gap match, for pairs not in the
    pre-computed cache. Mirrors scheme_fit.compute_scheme_fit_ondemand — used by
    fit_scores.py router. player_vec/gap_vec are 8-dim GAP_FEATURES-ordered."""
    p = np.array(player_vec, dtype=np.float64)
    g = np.array(gap_vec, dtype=np.float64)
    norm_p, norm_g = np.linalg.norm(p), np.linalg.norm(g)
    if norm_p == 0 or norm_g == 0:
        gap_match = 0.0
    else:
        gap_match = float(np.clip(np.dot(p, g) / (norm_p * norm_g) * 100, 0, 100))
    return {"gap_match": round(gap_match, 2)}


def calibrate_gap_match(raw_gap_match: float, reliability: float, baseline: float = BASELINE_GAP_MATCH) -> float:
    reliability = float(np.clip(reliability, 0.0, 1.0))
    return float(np.clip(reliability * raw_gap_match + (1.0 - reliability) * baseline, 0.0, 100.0))


def top_gap_features(gap_vec: np.ndarray, n: int = 3) -> list[dict[str, float]]:
    if gap_vec.size == 0:
        return []
    order = np.argsort(gap_vec)[::-1]
    features = []
    for idx in order:
        val = float(gap_vec[idx])
        if val <= 1e-9:
            continue
        features.append({"feature": GAP_FEATURES[int(idx)], "gap": round(val, 4)})
        if len(features) >= n:
            break
    return features


def score_gap_matches(
    df: pd.DataFrame,
    scaler: StandardScaler,
    gap_scaled: dict[int, dict[int, np.ndarray]],
    gap_data: dict[int, dict[int, dict]],
    arch_deficit: dict[int, dict[int, set[int]]],
    existing_pairs: dict[tuple[int, int, int], dict],
    seasons: list[int],
    school_ids: list[int] | None = None,
) -> list[tuple]:
    """Vectorized per-season cosine similarity between every player and every
    (school, position) gap vector, weighted by the player's soft position
    weights. ``existing_pairs`` is used only to preserve Scheme Fit values and
    breakdowns for rows that were previously written by Scheme Fit."""
    records: list[tuple] = []

    for season in seasons:
        s_df = df[df["season"] == season].reset_index(drop=True)
        sids = sorted(gap_scaled[season].keys())
        if school_ids is not None:
            allowed_sids = {int(sid) for sid in school_ids}
            sids = [sid for sid in sids if sid in allowed_sids]
        if not sids or s_df.empty:
            continue
        n_p, n_s = len(s_df), len(sids)

        P_s = scaler.transform(s_df[GAP_FEATURES].values.astype(float))
        W_s = s_df[POS_COLS].values.astype(float)

        G_s = np.stack([gap_scaled[season][sid] for sid in sids], axis=0)
        G_flat = G_s.reshape(n_s * 5, len(GAP_FEATURES))

        SIM_flat = cosine_similarity(P_s, G_flat)
        SIM = SIM_flat.reshape(n_p, n_s, 5)
        RAW_GAP_MATRIX = np.clip((W_s[:, None, :] * SIM).sum(axis=2) * 100, 0.0, 100.0)

        now_ts = datetime.now(timezone.utc).isoformat()
        player_ids = s_df["player_id"].astype(int).to_numpy()
        for pi, pid in enumerate(player_ids):
            row = s_df.iloc[pi]
            gap_reliability = float(row.get(GAP_RELIABILITY_COL, 1.0))
            arch_id = row["archetype_id"]
            cluster = int(arch_id) if pd.notna(arch_id) else -1
            pos_w = W_s[pi]
            for si, sid in enumerate(sids):
                raw_gap_match = float(RAW_GAP_MATRIX[pi, si])
                gap_match = calibrate_gap_match(raw_gap_match, gap_reliability)
                depth = gap_data[season][sid]["depth"]
                pos_depth = float(np.sum(pos_w * np.array([depth_score(depth[p]) for p in range(5)])))
                archetype_needed = cluster >= 0 and cluster in arch_deficit[season].get(sid, set())
                weighted_gap_vec = (pos_w[:, None] * gap_data[season][sid]["gap_vecs"]).sum(axis=0)

                ex = existing_pairs.get((pid, sid, season), {})
                scheme_fit = float(ex.get("scheme_fit") or 0.0)
                ex_bd = ex.get("breakdown") or {}
                if isinstance(ex_bd, str):
                    ex_bd = json.loads(ex_bd)
                merged_bd = {
                    **ex_bd,
                    "gap": {
                        "raw_gap_match": round(raw_gap_match, 2),
                        "calibrated_gap_match": round(gap_match, 2),
                        "position_source": str(row.get(POSITION_SOURCE_COL, "unknown")),
                        "position_reliability": round(float(row.get(POSITION_RELIABILITY_COL, 0.0)), 3),
                        "sample_reliability": round(float(row.get(SAMPLE_RELIABILITY_COL, 0.0)), 3),
                        "feature_reliability": round(float(row.get(FEATURE_RELIABILITY_COL, 1.0)), 3),
                        "gap_reliability": round(gap_reliability, 3),
                        "position_depth_score": round(pos_depth, 1),
                        "archetype_needed": archetype_needed,
                        "top_gap_features": top_gap_features(weighted_gap_vec),
                    },
                }

                overall_fit = round(W_GAP * gap_match + W_SCHEME * scheme_fit + W_ROLE * 50.0 + W_PROG * 50.0, 2)

                records.append((pid, sid, season, gap_match, overall_fit, json.dumps(merged_bd), now_ts))

    return records


def upsert_gap_scores(engine, records: list[tuple], expires_days: int = 7) -> int:
    """Upsert gap scores without replacing season rows.

    Existing Scheme Fit rows keep their scheme_fit/role_fit/program_fit fields;
    new all-pairs rows are inserted with scheme_fit at zero (no Scheme Fit data
    for that pair) but role_fit/program_fit at the 50.0 stub baseline — matching
    the W_ROLE*50.0 + W_PROG*50.0 already baked into overall_fit by
    score_gap_matches, and the same stub convention scheme_fit_scorer.ipynb
    (M3) uses. Storing 0.0 here instead would silently disagree with the
    overall_fit value computed for the same row.
    """
    now_ts = datetime.now(timezone.utc).isoformat()
    expires = (datetime.now(timezone.utc) + timedelta(days=expires_days)).isoformat()

    upsert_records = [
        (
            int(pid), int(sid), int(season),
            float(gap_match), float(overall_fit),
            0.0, 50.0, 50.0,
            W_GAP, W_SCHEME, W_ROLE, W_PROG,
            breakdown_json,
            MODEL_VERSION,
            now_ts,
            expires,
        )
        for pid, sid, season, gap_match, overall_fit, breakdown_json, _ in records
    ]
    _, upserted = upsert_with_season_replace(engine, UPSERT_SQL, upsert_records, page_size=2000)
    return upserted
