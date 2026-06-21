"""Gap Matching — Roster Need Scorer.

Pure fit/score/write functions lifted from notebooks/models/gap_matching.ipynb.
Scores only pairs Scheme Fit already wrote to player_team_fit_scores — no new
pairs added, no delete-by-season (updates existing rows in place).
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

MODEL_VERSION = "gap-cos-v2"
MIN_GAMES = 5

GAP_FEATURES = [
    "points_per_game", "rebounds_per_game", "assists_per_game",
    "steals_per_game", "blocks_per_game", "true_shooting_pct",
    "usage_rate", "three_point_rate",
]
POS_COLS = ["pos_confidence_pg", "pos_confidence_sg", "pos_confidence_sf", "pos_confidence_pf", "pos_confidence_c"]
POS_NAMES = ["PG", "SG", "SF", "PF", "C"]

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
    fallback otherwise. Renormalized to sum=1 (guards rounding drift / no data)."""
    df = df.copy()
    mask_no_he = df[POS_COLS].isna().all(axis=1)
    for pos_name, pos_col in zip(POS_NAMES, POS_COLS):
        df.loc[mask_no_he & (df["position"] == pos_name), pos_col] = 1.0
    df[POS_COLS] = df[POS_COLS].fillna(0.0)
    row_sums = df[POS_COLS].sum(axis=1).clip(lower=1e-9)
    df[POS_COLS] = df[POS_COLS].div(row_sums, axis=0)
    return df


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
    for season in seasons:
        gap_scaled[season] = {sid: scaler.transform(d["gap_vecs"]) for sid, d in gap_data[season].items()}
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


def score_gap_matches(
    df: pd.DataFrame,
    scaler: StandardScaler,
    gap_scaled: dict[int, dict[int, np.ndarray]],
    gap_data: dict[int, dict[int, dict]],
    arch_deficit: dict[int, dict[int, set[int]]],
    existing_pairs: dict[tuple[int, int, int], dict],
    seasons: list[int],
) -> list[tuple]:
    """Vectorized per-season cosine similarity between every player and every
    (school, position) gap vector, weighted by the player's soft position
    weights. Only scores (player_id, school_id, season) triples already present
    in existing_pairs (Scheme Fit's pair scope — no new pairs added)."""
    records: list[tuple] = []

    for season in seasons:
        s_df = df[df["season"] == season].reset_index(drop=True)
        sids = sorted(gap_scaled[season].keys())
        n_p, n_s = len(s_df), len(sids)
        sid_to_i = {sid: i for i, sid in enumerate(sids)}

        P_s = scaler.transform(s_df[GAP_FEATURES].values.astype(float))
        W_s = s_df[POS_COLS].values.astype(float)

        G_s = np.stack([gap_scaled[season][sid] for sid in sids], axis=0)
        G_flat = G_s.reshape(n_s * 5, len(GAP_FEATURES))

        SIM_flat = cosine_similarity(P_s, G_flat)
        SIM = SIM_flat.reshape(n_p, n_s, 5)
        GAP_MATRIX = np.clip((W_s[:, None, :] * SIM).sum(axis=2) * 100, 0.0, 100.0)

        now_ts = datetime.now(timezone.utc).isoformat()
        pid_to_i = {int(pid): i for i, pid in enumerate(s_df["player_id"])}

        season_pairs = [(pid, sid) for (pid, sid, ssn) in existing_pairs if ssn == season]
        for (pid, sid) in season_pairs:
            pi = pid_to_i.get(pid)
            si = sid_to_i.get(sid)
            if pi is None or si is None:
                continue

            gap_match = float(GAP_MATRIX[pi, si])
            row = s_df.iloc[pi]
            arch_id = row["archetype_id"]
            cluster = int(arch_id) if pd.notna(arch_id) else -1
            pos_w = W_s[pi]
            depth = gap_data[season][sid]["depth"]
            pos_depth = float(np.sum(pos_w * np.array([depth_score(depth[p]) for p in range(5)])))
            archetype_needed = cluster >= 0 and cluster in arch_deficit[season].get(sid, set())

            ex = existing_pairs.get((pid, sid, season), {})
            scheme_fit = float(ex.get("scheme_fit") or 0.0)
            ex_bd = ex.get("breakdown") or {}
            if isinstance(ex_bd, str):
                ex_bd = json.loads(ex_bd)
            merged_bd = {**ex_bd, "gap": {"position_depth_score": round(pos_depth, 1), "archetype_needed": archetype_needed}}

            overall_fit = round(W_GAP * gap_match + W_SCHEME * scheme_fit + W_ROLE * 50.0 + W_PROG * 50.0, 2)

            records.append((pid, sid, season, gap_match, overall_fit, json.dumps(merged_bd), now_ts))

    return records


def upsert_gap_scores(engine, records: list[tuple], expires_days: int = 7) -> int:
    """No delete step (delete_sql=None) — updates gap_match/breakdown onto rows
    Scheme Fit already wrote. scheme_fit/role_fit/program_fit/their weights are
    excluded from the DO UPDATE SET clause so Scheme Fit's values are preserved."""
    now_ts = datetime.now(timezone.utc).isoformat()
    expires = (datetime.now(timezone.utc) + timedelta(days=expires_days)).isoformat()

    upsert_records = [
        (
            pid, sid, season,
            gap_match, overall_fit,
            0.0, 0.0, 0.0,
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
