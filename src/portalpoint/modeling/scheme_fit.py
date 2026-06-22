"""Model 3 — Scheme Fit Scorer.

Pure fit/score/write functions lifted from notebooks/models/scheme_fit_scorer.ipynb.
compute_scheme_fit_ondemand is consumed directly by the fit_scores.py router for
pairs not in the pre-computed cache — keep its signature stable.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd
from psycopg2.extras import execute_values
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import Engine

from portalpoint.modeling.db_writers import upsert_with_season_replace

MODEL_VERSION = "scheme-cos-v3"
EXPIRES_DAYS = 30

PLAYER_SHOT_FEATS = ["three_point_rate", "rim_rate", "mid_range_rate"]
TEAM_SHOT_FEATS = ["team_three_rate", "team_rim_rate", "team_mid_rate"]

HE_FEATS = [
    "off_style_transition_pct",
    "off_style_post_up_pct",
    "off_style_pick_pop_pct",
    "off_style_big_cut_roll_pct",
    "off_style_attack_kick_pct",
    "off_style_perimeter_cut_pct",
]

W_GAP = 0.20
W_SCHEME = 0.30
W_OPP = 0.25
W_PERS = 0.25

UPSERT_SQL = """
INSERT INTO player_team_fit_scores
    (player_id, school_id, season,
     overall_fit, gap_match, scheme_fit, role_fit, program_fit,
     weight_gap, weight_scheme, weight_role, weight_program,
     breakdown, model_version, computed_at, expires_at)
VALUES %s
ON CONFLICT ON CONSTRAINT uq_fit_score
DO UPDATE SET
    overall_fit   = EXCLUDED.overall_fit,
    scheme_fit    = EXCLUDED.scheme_fit,
    breakdown     = EXCLUDED.breakdown,
    model_version = EXCLUDED.model_version,
    computed_at   = EXCLUDED.computed_at,
    expires_at    = EXCLUDED.expires_at
"""


def scheme_fit_score(p_vec: np.ndarray, t_vec: np.ndarray) -> float:
    """Cosine similarity scaled 0-100. Returns 50.0 for zero-norm edge case."""
    dot = np.dot(p_vec, t_vec)
    norm_p = np.linalg.norm(p_vec)
    norm_t = np.linalg.norm(t_vec)
    if norm_p == 0 or norm_t == 0:
        return 50.0
    return float(np.clip(dot / (norm_p * norm_t) * 100, 0, 100))


def scheme_breakdown(p_vec: np.ndarray, t_vec: np.ndarray, feat_ranges: dict, feat_names: list[str]) -> dict:
    """Per-dimension match scores (0-100) for UI breakdown card."""
    return {
        feat: float(max(0.0, (1.0 - abs(p_vec[i] - t_vec[i]) / feat_ranges[feat]) * 100))
        for i, feat in enumerate(feat_names)
    }


def feature_ranges(player_df: pd.DataFrame, team_df: pd.DataFrame) -> dict[str, float]:
    ranges = {}
    for pf, tf in zip(PLAYER_SHOT_FEATS, TEAM_SHOT_FEATS):
        lo = min(player_df[pf].min(), team_df[tf].min())
        hi = max(player_df[pf].max(), team_df[tf].max())
        ranges[pf] = max(hi - lo, 0.10)
    return ranges


def compute_scheme_fit_ondemand(
    player_three: float,
    player_rim: float,
    player_mid: float,
    team_three: float,
    team_rim: float,
    team_mid: float,
    feat_ranges: dict[str, float],
    tempo_range: float,
    player_tempo: float | None = None,
    team_tempo: float | None = None,
    player_he: list | None = None,
    team_he: list | None = None,
) -> dict:
    """Compute scheme fit for a single player-team pair not in pre-computed cache.

    Used by fit_scores.py router. Base: 3-dim shot distribution cosine (always
    computed). pace_match: tempo delta, computed when both player_tempo and
    team_tempo provided. he_scheme_fit: 6-dim HE play-type cosine, added to
    breakdown when both player_he and team_he provided (6 floats, HE_FEATS order).

    feat_ranges/tempo_range come from feature_ranges()/the team population's
    adj_tempo spread — caller supplies the current population context.
    """
    p = np.array([player_three, player_rim, player_mid], dtype=np.float64)
    t = np.array([team_three, team_rim, team_mid], dtype=np.float64)

    sf = scheme_fit_score(p, t)
    sub = scheme_breakdown(p, t, feat_ranges, PLAYER_SHOT_FEATS)

    if player_tempo is not None and team_tempo is not None:
        pm = float(np.clip((1.0 - abs(player_tempo - team_tempo) / tempo_range) * 100, 0, 100))
    else:
        pm = 50.0

    result = {
        "scheme_fit": round(sf, 2),
        "breakdown": {
            "three_point_match": round(sub["three_point_rate"], 1),
            "rim_attack_match": round(sub["rim_rate"], 1),
            "pace_match": round(pm, 1),
            "usage_match": 50.0,
            "ball_movement_match": round(sub.get("mid_range_rate", 50.0), 1),
        },
    }

    if (player_he is not None and team_he is not None
            and len(player_he) == len(team_he) == len(HE_FEATS)):
        ph = np.array(player_he, dtype=np.float64)
        th = np.array(team_he, dtype=np.float64)
        result["breakdown"]["he_scheme_fit"] = round(scheme_fit_score(ph, th), 1)

    return result


def score_all_seasons(
    player_df: pd.DataFrame,
    team_df: pd.DataFrame,
    seasons: list[int],
    tempo_default: float,
    school_ids: list[int] | None = None,
    model_version: str = MODEL_VERSION,
) -> tuple[list[tuple], dict[int, dict], int]:
    """Score every eligible player x team pair, season-matched (scheme-cos-v3,
    all-pairs — no top-k truncation). Matches Gap Matching's scope so both
    models share the same player x school universe.

    ``school_ids``, if given, restricts scoring to that subset of schools for
    this call — used by scripts/run_scheme_fit.py to chunk the write in
    school-id batches (the same way run_gap_matching.py does), since the full
    all-pairs grid (~4.5K players x ~365 schools x season) is too large to
    hold as one in-memory records list across every season at once.

    Returns (records, per_season_stats, n_he_records). per_season_stats is
    scoped to whatever (season, school_ids) batch was passed in this call,
    not the full season — callers chunking by school accumulate across calls.
    """
    expires_at = datetime.now(timezone.utc) + timedelta(days=EXPIRES_DAYS)
    computed_at = datetime.now(timezone.utc)

    records: list[tuple] = []
    n_he_records = 0
    season_stats: dict[int, dict] = {}

    for season in seasons:
        p_s = player_df[player_df["season"] == season].reset_index(drop=True)
        t_s = team_df[team_df["season"] == season]
        if school_ids is not None:
            allowed_sids = {int(s) for s in school_ids}
            t_s = t_s[t_s["school_id"].isin(allowed_sids)]
        t_s = t_s.reset_index(drop=True)
        if len(p_s) == 0 or len(t_s) == 0:
            continue

        P_s = p_s[PLAYER_SHOT_FEATS].values.astype(np.float64)
        T_s = t_s[TEAM_SHOT_FEATS].values.astype(np.float64)
        SIM_s = np.clip(cosine_similarity(P_s, T_s) * 100, 0, 100)

        T_TEMPO_s = np.where(np.isnan(t_s["adj_tempo"].values), tempo_default, t_s["adj_tempo"].values)
        P_TEMPO_s = np.where(np.isnan(p_s["current_tempo"].values), tempo_default, p_s["current_tempo"].values)
        TEMPO_RANGE_s = max(float(T_TEMPO_s.max() - T_TEMPO_s.min()), 1.0)
        PACE_s = np.clip(
            (1.0 - np.abs(P_TEMPO_s[:, None] - T_TEMPO_s[None, :]) / TEMPO_RANGE_s) * 100,
            0.0, 100.0,
        )

        RANGE_s = {
            pf: max(float(max(p_s[pf].max(), t_s[tf].max()) - min(p_s[pf].min(), t_s[tf].min())), 0.10)
            for pf, tf in zip(PLAYER_SHOT_FEATS, TEAM_SHOT_FEATS)
        }

        he_p_mask_s = p_s[HE_FEATS].notna().all(axis=1).values
        he_t_mask_s = t_s["_he_covered"].values
        n_he_p_s, n_he_t_s = int(he_p_mask_s.sum()), int(he_t_mask_s.sum())
        if n_he_p_s > 0 and n_he_t_s > 0:
            HE_SIM_s = np.clip(
                cosine_similarity(
                    p_s[HE_FEATS].values[he_p_mask_s].astype(np.float64),
                    t_s[HE_FEATS].values[he_t_mask_s].astype(np.float64),
                ) * 100, 0, 100,
            )
            he_p_idx_s = np.full(len(p_s), -1, dtype=int)
            he_p_idx_s[he_p_mask_s] = np.arange(n_he_p_s)
            he_t_idx_s = np.full(len(t_s), -1, dtype=int)
            he_t_idx_s[he_t_mask_s] = np.arange(n_he_t_s)
        else:
            HE_SIM_s = np.empty((0, 0))
            he_p_idx_s = np.full(len(p_s), -1, dtype=int)
            he_t_idx_s = np.full(len(t_s), -1, dtype=int)

        player_ids_s = p_s["player_id"].values
        school_ids_s = t_s["school_id"].values
        n_he_s = 0

        # Vectorize the per-feature breakdown + overall_fit matrices up front —
        # was previously a python-level scheme_breakdown() call per pair
        # (~1.6M calls/season), the dominant cost once top-k was removed.
        RANGE_arr = np.array([RANGE_s[f] for f in PLAYER_SHOT_FEATS])
        diff = np.abs(P_s[:, None, :] - T_s[None, :, :])  # (n_p, n_t, 3)
        MATCH = np.round(np.maximum(0.0, (1.0 - diff / RANGE_arr[None, None, :]) * 100.0), 1)
        SIM_r = np.round(SIM_s, 2)
        PACE_r = np.round(np.where(np.isnan(PACE_s), 50.0, PACE_s), 1)
        CONST_PART = W_GAP * 50.0 + W_OPP * 50.0 + W_PERS * 50.0
        OVERALL_r = np.round(W_SCHEME * SIM_r + CONST_PART, 2)

        for i, pid in enumerate(player_ids_s):
            he_p = he_p_idx_s[i]

            for j in range(len(school_ids_s)):
                sfv = float(SIM_r[i, j])
                sid = int(school_ids_s[j])
                he_t = he_t_idx_s[j]

                bd = {
                    "three_point_match": float(MATCH[i, j, 0]),
                    "rim_attack_match": float(MATCH[i, j, 1]),
                    "pace_match": float(PACE_r[i, j]),
                    "usage_match": 50.0,
                    "ball_movement_match": float(MATCH[i, j, 2]),
                }
                if he_p >= 0 and he_t >= 0 and HE_SIM_s.size > 0:
                    bd["he_scheme_fit"] = float(round(HE_SIM_s[he_p, he_t], 1))
                    n_he_s += 1

                records.append((
                    int(pid), sid, int(season),
                    float(OVERALL_r[i, j]),
                    50.0, sfv, 50.0, 50.0,
                    W_GAP, W_SCHEME, W_OPP, W_PERS,
                    json.dumps({"scheme": bd}),
                    model_version, computed_at, expires_at,
                ))

        n_he_records += n_he_s
        season_stats[season] = {
            "n_players": len(p_s), "n_teams": len(t_s),
            "mean_fit": float(SIM_s.mean()), "n_he": n_he_s,
        }

    # Safety-net dedup: keep highest overall_fit per (player_id, school_id, season).
    seen: dict[tuple, tuple] = {}
    for rec in records:
        key = (rec[0], rec[1], rec[2])
        if key not in seen or rec[3] > seen[key][3]:
            seen[key] = rec
    records_clean = list(seen.values())

    return records_clean, season_stats, n_he_records


def delete_season_rows(engine: Engine, seasons: list[int]) -> int:
    """Full-season wipe before a fresh all-pairs Scheme Fit rebuild.

    Call once per run, before any chunked writes — Gap Matching then layers
    its real gap_match/breakdown onto these fresh rows via its own
    preserve-existing-context upsert (no delete), so Scheme Fit must run
    first, exactly once, not per school-chunk.
    """
    raw_conn = engine.raw_connection()
    try:
        with raw_conn.cursor() as cur:
            cur.execute("DELETE FROM player_team_fit_scores WHERE season = ANY(%s)", (list(seasons),))
            deleted = cur.rowcount
        raw_conn.commit()
    finally:
        raw_conn.close()
    return deleted


def upsert_fit_scores(engine: Engine, records: list[tuple]) -> int:
    """Insert one school-chunk's worth of freshly-scored records.

    No delete here — delete_season_rows already cleared the season once
    up front, and each chunk covers a disjoint set of schools, so no
    (player_id, school_id, season) pair can collide across chunks.
    """
    _, upserted = upsert_with_season_replace(engine, UPSERT_SQL, records, page_size=2000)
    return upserted
