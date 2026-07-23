"""Team Rating Projection — roster-based counterfactual model.

Answers: "If portal candidate P joins school S, how does S's AdjEM change?"

Architecture:
  - Two Ridge models (offense + defense) trained on roster feature vectors
    vs. BartTorvik adj_o / adj_d labels (consistent scale across all D1).
  - HE player RAPM (off_adj_rapm / def_adj_rapm) feeds the roster features
    as the player-quality signal; it is NOT used as a team-level training label
    (HE off_adj_ppp scale ~1.05 vs. BartTorvik adj_o scale ~105 would break
    a mixed-label Ridge — see docs/models/team_rating_projection_plan.md §16).
  - Counterfactual: build baseline roster (returning players + slot fills),
    insert candidate using playing_time_projections expected minutes and
    displaced_minutes, predict both, delta = difference.

Training seasons: 2021–2026 (all complete, BartTorvik labels available).
CV: 3-fold rolling-origin — folds (2021-23/2024), (2021-24/2025), (2021-25/2026).
Inference season: 2027 (portal candidates × all D1 schools).
"""

from __future__ import annotations

import ast
import json
import logging
import pickle
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import mlflow
import numpy as np
import pandas as pd
from mlflow.tracking import MlflowClient
from psycopg2.extras import execute_values
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from sqlalchemy import Engine, text

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL_VERSION = "team-roster-proj-v1"
EXPIRES_DAYS = 7
STANDARD_TEAM_MINUTES = 200.0
N_BOOTSTRAP = 200
MIN_ROSTER_PLAYERS = 3        # below this, skip school (too many slot-baseline fills)
MIN_TRAIN_GAMES = 5           # player must have played >= 5 games to count in features
RIDGE_ALPHA = 1.0
FRESHMAN_MIN_PCT_PRIOR = 8.0  # kept for backward-compat; superseded by tier-keyed dict below
FRESHMAN_TOTAL_MIN_PCT_CAP = 30.0
FRESHMAN_RAPM_DISCOUNT = 0.65  # kept for backward-compat; superseded by tier-keyed dict below

# B: program-calibrated freshman priors by conference tier.
# Tier 1 (P6/high-major) freshmen historically average ~10% team minutes;
# each lower tier steps down ~1-1.5 pts (derived from player_season_stats class_year averages).
FRESHMAN_MIN_PCT_BY_TIER: dict[int, float] = {1: 10.0, 2: 8.0, 3: 7.0, 4: 6.0}
FRESHMAN_RAPM_DISCOUNT_BY_TIER: dict[int, float] = {1: 0.72, 2: 0.65, 3: 0.60, 4: 0.55}

# C: elite-recruiting-program proxy — ~15 schools historically known for immediate-impact
# freshman classes.  Matched case-insensitively against schools.name.
ELITE_RECRUITING_SCHOOLS: frozenset[str] = frozenset({
    "Duke", "Kentucky", "Kansas", "North Carolina", "Michigan State",
    "Arizona", "UCLA", "Memphis", "Auburn", "Arkansas",
    "Indiana", "Texas", "Ohio State", "Villanova", "Gonzaga",
})
ELITE_RECRUITING_MULTIPLIER: float = 1.5   # applied to min_pct_prior for elite programs
ELITE_RAPM_DISCOUNT_BOOST: float = 0.07   # reduce RAPM discount by this for elite programs

# D: position-aware opportunity weighting.
# Freshman min_pct is scaled by (open_minutes_for_position / SCALE) clamped to [FLOOR, MAX].
FRESHMAN_OPPORTUNITY_SCALE_MINUTES: float = 15.0  # at this open-minutes level, factor = 1.0
FRESHMAN_OPPORTUNITY_FLOOR_FACTOR: float = 1.0 / 3.0  # min factor even with no open minutes
FRESHMAN_OPPORTUNITY_MAX_FACTOR: float = 1.5

# E: CI widening per unmatched freshman prior in baseline roster.
FRESHMAN_VARIANCE_PER_PLAYER: float = 0.4  # extra variance per freshman prior (AdjEM units²)

# Conference tier bucket boundaries: percentile of adj_em within season.
# Tier 1 = high-major, Tier 4 = low-major (same convention as destination_projection.py).
CONFERENCE_TIER_CUTS = [0.75, 0.45, 0.20]

# Neutral model version priority (same as destination_projection.py).
NEUTRAL_MODEL_PRIORITY = [
    "player-proj-phase2a-fcast-v1",
    "player-projection-phase2a-v1",
    "player-projection-shrinkage-v2",
]
PLAYING_TIME_MODEL_VERSION = "playing-time-rotation-v2"

# Ordered feature names — must match the order used in fit + predict everywhere.
ROSTER_FEATURES = [
    "weighted_off_impact",    # Σ(off_adj_rapm * min_share)
    "weighted_def_impact",    # Σ(def_adj_rapm * min_share)
    "top1_off_impact",        # max player off_adj_rapm
    "top2_impact",            # 2nd player off_adj_rapm by minutes
    "bench_depth_impact",     # Σ min_share for players ranked 7+ by minutes
    "three_pt_coverage",      # Σ(three_point_rate * min_share) — spacing floor
    "rim_protection",         # Σ(def_adj_rapm * min_share) for C/PF only
    "pg_creation",            # Σ(off_adj_rapm * min_share) for PG only
    "rebounding_coverage",    # Σ(off_reb_pct * min_share) — offensive boards
    "usage_concentration",    # HHI of usage shares (0-1 scale, higher = star-heavy)
    "returning_minutes_pct",  # fraction of total minutes from returning players
    "n_known_players",        # rotation spots with real projections (data quality)
    "conference_tier",        # 1=high-major, 4=low-major (label encoded)
    "adj_tempo_prior",        # team historical pace (BartTorvik adj_tempo)
]

# Position bands for rim protection + PG creation features.
_FRONTCOURT = {"PF", "C"}
_GUARDS = {"PG"}
_FRESHMAN_CLASS_MARKERS = {"fr", "freshman", "first-year", "first year", "frosh"}


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class TeamRatingModels:
    off_model: Ridge | None = None
    def_model: Ridge | None = None
    off_scaler: StandardScaler | None = None
    def_scaler: StandardScaler | None = None
    off_resid_std: float = 0.0
    def_resid_std: float = 0.0
    slot_baselines: dict = field(default_factory=dict)
    train_seasons: list[int] = field(default_factory=list)
    n_train_rows: int = 0
    cv_metrics: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# SQL queries
# ---------------------------------------------------------------------------

_HISTORICAL_PLAYER_SQL = """
SELECT
    pss.school_id,
    pss.season,
    pss.player_id,
    pss.min_pct,
    pss.usage_rate,
    pss.three_point_rate,
    pss.off_reb_pct,
    pss.games_played,
    p.position,
    hep.off_adj_rapm,
    hep.def_adj_rapm
FROM player_season_stats pss
JOIN players p ON p.id = pss.player_id
LEFT JOIN hoop_explorer_player_stats hep
    ON hep.player_id = pss.player_id AND hep.season = pss.season
WHERE pss.season = ANY(:seasons)
  AND pss.games_played >= :min_games
  AND pss.min_pct IS NOT NULL AND pss.min_pct > 0
ORDER BY pss.school_id, pss.season, pss.min_pct DESC
"""

_TEAM_LABELS_SQL = """
SELECT school_id, season, adj_o, adj_d, adj_em, adj_tempo
FROM team_season_stats
WHERE season = ANY(:seasons)
  AND adj_o IS NOT NULL
  AND adj_d IS NOT NULL
"""

_ROSTER_STATE_SQL = """
SELECT school_id, season,
       returning_minutes_by_position,
       departing_minutes_by_position,
       open_minutes_by_position,
       class_balance,
       returning_player_impact
FROM roster_state_features
WHERE season = ANY(:seasons)
"""

_SCHOOL_SEASON_SQL = """
SELECT s.id AS school_id, s.name AS school_name, s.conference,
       tss.season, tss.adj_em, tss.adj_tempo
FROM schools s
JOIN team_season_stats tss ON tss.school_id = s.id
WHERE tss.season = :target_season
"""

_BASELINE_MEMBERS_SQL = """
SELECT rbm.player_id, rbm.school_id, rbm.baseline_status
FROM roster_baseline_members rbm
WHERE rbm.season = :season
"""

_PRIOR_STATS_SQL = """
SELECT
    pss.player_id, pss.school_id,
    pss.min_pct, pss.usage_rate, pss.three_point_rate, pss.off_reb_pct,
    p.position
FROM player_season_stats pss
JOIN players p ON p.id = pss.player_id
WHERE pss.season = :prior_season
  AND pss.games_played >= :min_games
  AND pss.min_pct IS NOT NULL AND pss.min_pct > 0
"""

_NEUTRAL_PROJ_SQL = """
SELECT DISTINCT ON (player_id)
    player_id,
    value_per_100,
    value_ci_lower,
    value_ci_upper,
    uncertainty,
    model_version
FROM player_projections
WHERE school_id IS NULL
  AND projection_mode = 'neutral'
  AND season = :target_season
  AND model_version = ANY(:model_versions)
ORDER BY player_id,
    CASE WHEN model_version = :preferred_version THEN 0
         ELSE array_position(CAST(:model_versions AS text[]), model_version) END
"""

_PLAYING_TIME_SQL = """
SELECT
    pt.player_id, pt.school_id,
    pt.expected_minutes, pt.expected_minutes_share,
    pt.minutes_ci_lower, pt.minutes_ci_upper,
    pt.expected_usage, pt.usage_role,
    pt.displaced_minutes, pt.role_fit
FROM playing_time_projections pt
WHERE pt.season = :season
  AND pt.model_version = :pt_model_version
  AND pt.player_id = ANY(:player_ids)
"""

_PORTAL_CANDIDATES_SQL = """
SELECT DISTINCT pts.player_id
FROM player_team_fit_scores pts
WHERE pts.season = :season
  AND pts.is_portal_candidate = true
"""

_HE_RAPM_SQL = """
SELECT player_id, off_adj_rapm, def_adj_rapm
FROM hoop_explorer_player_stats
WHERE player_id = ANY(:player_ids)
  AND season = :season
"""

_UPSERT_SQL = """
INSERT INTO team_rating_projections
    (player_id, school_id, season,
     current_adj_em, projected_adj_em, delta_adj_em,
     baseline_adj_o, baseline_adj_d, projected_adj_o, projected_adj_d,
     ci_lower, ci_upper,
     national_percentile, conference_rank,
     expected_minutes_input, candidate_usage_role,
     explanation, minutes_distribution,
     model_version, computed_at, expires_at)
VALUES %s
ON CONFLICT ON CONSTRAINT uq_team_rating_projection DO UPDATE SET
    current_adj_em          = EXCLUDED.current_adj_em,
    projected_adj_em        = EXCLUDED.projected_adj_em,
    delta_adj_em            = EXCLUDED.delta_adj_em,
    baseline_adj_o          = EXCLUDED.baseline_adj_o,
    baseline_adj_d          = EXCLUDED.baseline_adj_d,
    projected_adj_o         = EXCLUDED.projected_adj_o,
    projected_adj_d         = EXCLUDED.projected_adj_d,
    ci_lower                = EXCLUDED.ci_lower,
    ci_upper                = EXCLUDED.ci_upper,
    national_percentile     = EXCLUDED.national_percentile,
    conference_rank         = EXCLUDED.conference_rank,
    expected_minutes_input  = EXCLUDED.expected_minutes_input,
    candidate_usage_role    = EXCLUDED.candidate_usage_role,
    explanation             = EXCLUDED.explanation,
    minutes_distribution    = EXCLUDED.minutes_distribution,
    model_version           = EXCLUDED.model_version,
    computed_at             = EXCLUDED.computed_at,
    expires_at              = EXCLUDED.expires_at
"""


# ---------------------------------------------------------------------------
# Feature helpers
# ---------------------------------------------------------------------------

def _conference_tier(adj_em: float, season_adj_ems: np.ndarray) -> int:
    """1=high-major, 4=low-major, same cut-points as destination_projection.py."""
    if len(season_adj_ems) == 0 or np.isnan(adj_em):
        return 2
    pct = float(np.mean(season_adj_ems <= adj_em))
    if pct >= CONFERENCE_TIER_CUTS[0]:
        return 1
    if pct >= CONFERENCE_TIER_CUTS[1]:
        return 2
    if pct >= CONFERENCE_TIER_CUTS[2]:
        return 3
    return 4


def _usage_hhi(usage_vals: np.ndarray) -> float:
    """Herfindahl–Hirschman Index of usage shares (0-1; higher = more star-heavy)."""
    total = usage_vals.sum()
    if total <= 0:
        return 0.0
    shares = usage_vals / total
    return float((shares ** 2).sum())


def _json_numeric_sum(value: Any) -> float:
    """Sum numeric values from a JSON/JSONB dict-ish value."""
    if value is None:
        return 0.0
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return 0.0
    if isinstance(value, dict):
        vals = value.values()
    elif isinstance(value, list):
        vals = value
    else:
        return 0.0

    total = 0.0
    for item in vals:
        try:
            if item is not None and not pd.isna(item):
                total += float(item)
        except (TypeError, ValueError):
            continue
    return total


def _returning_minutes_pct(roster_state_row: pd.Series | dict | None) -> float:
    """Fraction of known prior roster minutes retained into the roster snapshot."""
    if roster_state_row is None:
        return 1.0
    returning = _json_numeric_sum(roster_state_row.get("returning_minutes_by_position"))
    departing = _json_numeric_sum(roster_state_row.get("departing_minutes_by_position"))
    open_minutes = _json_numeric_sum(roster_state_row.get("open_minutes_by_position"))
    denominator = returning + max(departing, open_minutes)
    if denominator <= 0:
        return 1.0
    return float(np.clip(returning / denominator, 0.0, 1.0))


def _safe_float(value: Any, default: float) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _json_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def _incoming_freshman_count(roster_state_row: pd.Series | dict | None) -> int:
    """Count true incoming freshmen from roster_state_features.class_balance."""
    if roster_state_row is None:
        return 0
    class_balance = _json_dict(roster_state_row.get("class_balance"))
    total = 0
    for key, value in class_balance.items():
        key_norm = str(key).lower().replace("_", " ").replace("-", " ").strip()
        if not key_norm.startswith("incoming "):
            continue
        class_label = key_norm.removeprefix("incoming ").strip()
        if class_label in _FRESHMAN_CLASS_MARKERS:
            total += int(_safe_float(value, 0.0))
    return total


def _freshman_prior_positions(open_minutes_by_position: Any, count: int) -> list[str]:
    """Assign freshman priors to the most open positions; fall back to balanced slots."""
    if count <= 0:
        return []
    open_minutes = {
        str(pos): max(_safe_float(val, 0.0), 0.0)
        for pos, val in _json_dict(open_minutes_by_position).items()
    }
    ranked = [
        pos for pos, val in sorted(open_minutes.items(), key=lambda item: item[1], reverse=True)
        if pos in {"PG", "SG", "SF", "PF", "C"} and val > 0
    ]
    if not ranked:
        ranked = ["PG", "SG", "SF", "PF", "C"]
    return [ranked[i % len(ranked)] for i in range(count)]


def build_freshman_prior_rows(
    roster_state_row: pd.Series | dict | None,
    conference_tier: int,
    slot_baselines: dict,
    school_name: str = "",
) -> list[dict]:
    """Conservative quality priors for incoming freshmen without player IDs/stats.

    True freshmen often appear in roster snapshots as `returning_status='new'`
    before they have player-season history or a matched player_id. Without a
    prior, team baselines treat those roster spots as empty. We add small,
    discounted slot-baseline rows so the team projection acknowledges likely
    depth while preserving uncertainty through `n_known_players`.

    Steps B-D applied here:
      B) tier-keyed base min_pct and RAPM discount
      C) elite-recruiting-program multiplier (~15 schools, hardcoded proxy)
      D) position-aware opportunity weighting via open_minutes_by_position
    """
    count = _incoming_freshman_count(roster_state_row)
    if count <= 0:
        return []

    # B: tier-keyed base priors
    base_min_pct = FRESHMAN_MIN_PCT_BY_TIER.get(conference_tier, FRESHMAN_MIN_PCT_BY_TIER[2])
    rapm_discount = FRESHMAN_RAPM_DISCOUNT_BY_TIER.get(conference_tier, FRESHMAN_RAPM_DISCOUNT_BY_TIER[2])

    # C: elite-recruiting-program multiplier
    school_name_lower = school_name.strip().lower()
    is_elite = any(
        school_name_lower == e.lower() for e in ELITE_RECRUITING_SCHOOLS
    )
    if is_elite:
        base_min_pct = min(base_min_pct * ELITE_RECRUITING_MULTIPLIER, FRESHMAN_TOTAL_MIN_PCT_CAP)
        rapm_discount = min(rapm_discount + ELITE_RAPM_DISCOUNT_BOOST, 0.90)

    total_min_pct = min(count * base_min_pct, FRESHMAN_TOTAL_MIN_PCT_CAP)
    min_pct_per = total_min_pct / count

    open_minutes_raw = roster_state_row.get("open_minutes_by_position") if roster_state_row is not None else None
    open_minutes_dict = _json_dict(open_minutes_raw) if open_minutes_raw is not None else {}
    positions = _freshman_prior_positions(open_minutes_raw, count)

    rows = []
    for idx, position in enumerate(positions):
        fill = _slot_fill(slot_baselines, conference_tier, position)

        # D: position-aware opportunity weighting
        open_min = max(_safe_float(open_minutes_dict.get(position), 0.0), 0.0)
        if FRESHMAN_OPPORTUNITY_SCALE_MINUTES > 0:
            raw_factor = open_min / FRESHMAN_OPPORTUNITY_SCALE_MINUTES
            opportunity_factor = max(
                min(raw_factor, FRESHMAN_OPPORTUNITY_MAX_FACTOR),
                FRESHMAN_OPPORTUNITY_FLOOR_FACTOR,
            )
        else:
            opportunity_factor = 1.0
        adjusted_min_pct = min_pct_per * opportunity_factor

        rows.append({
            "player_id": f"freshman_prior_{idx + 1}",
            "min_pct": adjusted_min_pct,
            "usage_rate": fill["usage_rate"],
            "three_point_rate": fill["three_point_rate"],
            "off_reb_pct": fill["off_reb_pct"],
            "position": position,
            "off_adj_rapm": fill["off_adj_rapm"] * rapm_discount,
            "def_adj_rapm": fill["def_adj_rapm"] * rapm_discount,
            "is_freshman_prior": True,
        })
    return rows


def build_slot_baselines(player_df: pd.DataFrame) -> dict:
    """Compute average RAPM / shooting / reb by (conference_tier, position).

    Used to fill open rotation slots when a player has no real projection.
    Keys: (tier: int, position: str) → dict of mean feature values.
    """
    if player_df.empty:
        return {}

    needed = ["conference_tier", "position", "off_adj_rapm", "def_adj_rapm",
              "three_point_rate", "off_reb_pct", "usage_rate"]
    df = player_df.dropna(subset=["off_adj_rapm", "def_adj_rapm"]).copy()
    if df.empty:
        return {}

    for col in needed:
        if col not in df.columns:
            df[col] = np.nan

    baselines: dict = {}
    for (tier, pos), grp in df.groupby(["conference_tier", "position"]):
        baselines[(int(tier), str(pos))] = {
            "off_adj_rapm":    float(grp["off_adj_rapm"].mean()),
            "def_adj_rapm":    float(grp["def_adj_rapm"].mean()),
            "three_point_rate": float(grp["three_point_rate"].fillna(0.30).mean()),
            "off_reb_pct":     float(grp["off_reb_pct"].fillna(0.25).mean()),
            "usage_rate":      float(grp["usage_rate"].fillna(20.0).mean()),
        }
    return baselines


def _slot_fill(slot_baselines: dict, tier: int, position: str) -> dict:
    """Return baseline values for one open slot, falling back through tier/pos."""
    key = (tier, position)
    if key in slot_baselines:
        return slot_baselines[key]
    # Fall back: any tier for this position, then global average
    for t in range(1, 5):
        if (t, position) in slot_baselines:
            return slot_baselines[(t, position)]
    # Global fallback — average over everything
    if slot_baselines:
        vals = list(slot_baselines.values())
        return {
            k: float(np.mean([v[k] for v in vals if k in v]))
            for k in ("off_adj_rapm", "def_adj_rapm", "three_point_rate",
                      "off_reb_pct", "usage_rate")
        }
    return {"off_adj_rapm": 0.0, "def_adj_rapm": 0.0,
            "three_point_rate": 0.30, "off_reb_pct": 0.25, "usage_rate": 20.0}


def build_roster_features(
    roster_rows: list[dict],
    conference_tier: int,
    adj_tempo: float,
    returning_minutes_pct: float,
    slot_baselines: dict,
) -> dict:
    """Compute the 14-element ROSTER_FEATURES vector for one school-season.

    Each element of roster_rows must have:
        min_pct, position, off_adj_rapm, def_adj_rapm,
        three_point_rate, off_reb_pct, usage_rate
    Missing RAPM fields are filled from slot_baselines.
    """
    if not roster_rows:
        return {f: 0.0 for f in ROSTER_FEATURES}

    df = pd.DataFrame(roster_rows)
    freshman_prior = (
        df["is_freshman_prior"].eq(True)
        if "is_freshman_prior" in df.columns
        else pd.Series(False, index=df.index)
    )
    known_quality = df["off_adj_rapm"].notna() & df["def_adj_rapm"].notna() & ~freshman_prior

    # Fill missing RAPM from slot baselines
    for i, row in df.iterrows():
        if pd.isna(row.get("off_adj_rapm")) or pd.isna(row.get("def_adj_rapm")):
            fill = _slot_fill(slot_baselines, conference_tier, str(row.get("position", "SG")))
            df.at[i, "off_adj_rapm"] = fill["off_adj_rapm"]
            df.at[i, "def_adj_rapm"] = fill["def_adj_rapm"]
        if pd.isna(row.get("three_point_rate")):
            fill = _slot_fill(slot_baselines, conference_tier, str(row.get("position", "SG")))
            df.at[i, "three_point_rate"] = fill["three_point_rate"]
        if pd.isna(row.get("off_reb_pct")):
            fill = _slot_fill(slot_baselines, conference_tier, str(row.get("position", "SG")))
            df.at[i, "off_reb_pct"] = fill["off_reb_pct"]
        if pd.isna(row.get("usage_rate")):
            df.at[i, "usage_rate"] = 20.0

    # min_share from min_pct (0-100 scale); normalize so shares sum to 1
    df["min_pct"] = pd.to_numeric(df["min_pct"], errors="coerce").fillna(0.0).clip(lower=0)
    total_pct = df["min_pct"].sum()
    df["min_share"] = df["min_pct"] / total_pct if total_pct > 0 else df["min_pct"]

    # Sort descending by minutes for rank-based features
    df = df.sort_values("min_pct", ascending=False).reset_index(drop=True)

    off_rapm = df["off_adj_rapm"].to_numpy(dtype=float)
    def_rapm = df["def_adj_rapm"].to_numpy(dtype=float)
    min_share = df["min_share"].to_numpy(dtype=float)
    usage = pd.to_numeric(df["usage_rate"], errors="coerce").fillna(20.0).to_numpy(dtype=float)
    three_pt = pd.to_numeric(df["three_point_rate"], errors="coerce").fillna(0.30).to_numpy(dtype=float)
    off_reb = pd.to_numeric(df["off_reb_pct"], errors="coerce").fillna(0.25).to_numpy(dtype=float)
    position = df["position"].astype(str).to_numpy()

    weighted_off = float(np.dot(off_rapm, min_share))
    weighted_def = float(np.dot(def_rapm, min_share))
    top1_off = float(off_rapm[0]) if len(off_rapm) > 0 else 0.0
    top2_off = float(off_rapm[1]) if len(off_rapm) > 1 else top1_off

    bench_mask = np.zeros(len(df), dtype=bool)
    bench_mask[7:] = True  # players ranked 8+ by minutes
    bench_impact = float(np.dot(off_rapm[bench_mask], min_share[bench_mask])) if bench_mask.any() else 0.0

    three_pt_coverage = float(np.dot(three_pt, min_share))

    fc_mask = np.array([p in _FRONTCOURT for p in position])
    rim_protection = float(np.dot(def_rapm[fc_mask], min_share[fc_mask])) if fc_mask.any() else 0.0

    pg_mask = np.array([p in _GUARDS for p in position])
    pg_creation = float(np.dot(off_rapm[pg_mask], min_share[pg_mask])) if pg_mask.any() else 0.0

    rebounding_coverage = float(np.dot(off_reb, min_share))
    usage_hhi = _usage_hhi(usage * min_share)
    n_known = int(known_quality.sum())

    return {
        "weighted_off_impact":   weighted_off,
        "weighted_def_impact":   weighted_def,
        "top1_off_impact":       top1_off,
        "top2_impact":           top2_off,
        "bench_depth_impact":    bench_impact,
        "three_pt_coverage":     three_pt_coverage,
        "rim_protection":        rim_protection,
        "pg_creation":           pg_creation,
        "rebounding_coverage":   rebounding_coverage,
        "usage_concentration":   usage_hhi,
        "returning_minutes_pct": float(returning_minutes_pct),
        "n_known_players":       float(n_known),
        "conference_tier":       float(conference_tier),
        "adj_tempo_prior":       float(adj_tempo) if not np.isnan(adj_tempo) else 68.0,
    }


# ---------------------------------------------------------------------------
# Training data construction
# ---------------------------------------------------------------------------

def build_historical_roster_states(
    engine: Engine,
    train_seasons: list[int],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build (features_df, labels_df) for historical school-seasons.

    Returns one row per (school_id, season) with the 14-element feature vector
    and BartTorvik adj_o / adj_d labels.
    """
    with engine.connect() as conn:
        players = pd.read_sql_query(
            text(_HISTORICAL_PLAYER_SQL),
            conn,
            params={"seasons": train_seasons, "min_games": MIN_TRAIN_GAMES},
        )
        labels = pd.read_sql_query(
            text(_TEAM_LABELS_SQL),
            conn,
            params={"seasons": train_seasons},
        )
        roster_state = pd.read_sql_query(
            text(_ROSTER_STATE_SQL),
            conn,
            params={"seasons": train_seasons},
        )
        school_meta = pd.read_sql_query(
            text(_SCHOOL_SEASON_SQL.replace("= :target_season", "= ANY(:seasons)")),
            conn,
            params={"seasons": train_seasons},
        )

    # Compute conference tier per school-season
    tier_map: dict[tuple[int, int], int] = {}
    for season in train_seasons:
        season_ems = school_meta.loc[school_meta["season"] == season, "adj_em"].dropna().to_numpy()
        for _, row in school_meta[school_meta["season"] == season].iterrows():
            if pd.notna(row["adj_em"]):
                tier_map[(int(row["school_id"]), season)] = _conference_tier(
                    float(row["adj_em"]), season_ems
                )

    # Add tier + tempo to players frame
    players["conference_tier"] = players.apply(
        lambda r: tier_map.get((int(r["school_id"]), int(r["season"])), 2), axis=1
    )

    # Build slot baselines from full historical player population
    slot_baselines = build_slot_baselines(players)

    # Roster state lookup
    rs_index = roster_state.set_index(["school_id", "season"])

    # Per-(school, season) feature vectors
    feature_rows = []
    for (school_id, season), grp in players.groupby(["school_id", "season"]):
        if len(grp) < MIN_ROSTER_PLAYERS:
            continue

        rs_key = (int(school_id), int(season))
        returning_pct = 1.0  # default: assume fully returning if no roster state data
        if rs_key in rs_index.index:
            rs_row = rs_index.loc[rs_key]
            if isinstance(rs_row, pd.DataFrame):
                rs_row = rs_row.iloc[0]
            returning_pct = _returning_minutes_pct(rs_row)

        tempo_row = school_meta[
            (school_meta["school_id"] == school_id) & (school_meta["season"] == season)
        ]
        adj_tempo = float(tempo_row["adj_tempo"].iloc[0]) if not tempo_row.empty and pd.notna(tempo_row["adj_tempo"].iloc[0]) else 68.0
        tier = tier_map.get((int(school_id), int(season)), 2)

        roster_list = grp[["min_pct", "position", "off_adj_rapm", "def_adj_rapm",
                            "three_point_rate", "off_reb_pct", "usage_rate"]].to_dict("records")
        feats = build_roster_features(
            roster_list, tier, adj_tempo, returning_pct, slot_baselines
        )
        feats["school_id"] = int(school_id)
        feats["season"] = int(season)
        feature_rows.append(feats)

    features_df = pd.DataFrame(feature_rows)
    # Join labels
    features_df = features_df.merge(
        labels[["school_id", "season", "adj_o", "adj_d", "adj_em"]],
        on=["school_id", "season"],
        how="inner",
    )
    labels_df = features_df[["school_id", "season", "adj_o", "adj_d", "adj_em"]].copy()
    features_df = features_df[["school_id", "season"] + ROSTER_FEATURES].copy()

    log.info("Historical roster states: %d school-seasons", len(features_df))
    return features_df, labels_df, slot_baselines


# ---------------------------------------------------------------------------
# Model fitting
# ---------------------------------------------------------------------------

def _feature_matrix(features_df: pd.DataFrame) -> np.ndarray:
    return features_df[ROSTER_FEATURES].to_numpy(dtype=float)


def fit_team_translation(
    features_df: pd.DataFrame,
    labels_df: pd.DataFrame,
) -> TeamRatingModels:
    """Fit Ridge offense + defense models on roster features vs. BartTorvik labels."""
    X = _feature_matrix(features_df)
    y_off = labels_df["adj_o"].to_numpy(dtype=float)
    y_def = labels_df["adj_d"].to_numpy(dtype=float)

    scaler_off = StandardScaler()
    scaler_def = StandardScaler()
    Xs_off = scaler_off.fit_transform(X)
    Xs_def = scaler_def.fit_transform(X)

    off_model = Ridge(alpha=RIDGE_ALPHA)
    def_model = Ridge(alpha=RIDGE_ALPHA)
    off_model.fit(Xs_off, y_off)
    def_model.fit(Xs_def, y_def)

    off_resid = y_off - off_model.predict(Xs_off)
    def_resid = y_def - def_model.predict(Xs_def)

    return TeamRatingModels(
        off_model=off_model,
        def_model=def_model,
        off_scaler=scaler_off,
        def_scaler=scaler_def,
        off_resid_std=float(np.std(off_resid)),
        def_resid_std=float(np.std(def_resid)),
        n_train_rows=len(features_df),
    )


def rolling_origin_cv(
    features_df: pd.DataFrame,
    labels_df: pd.DataFrame,
    folds: list[tuple[list[int], int]] | None = None,
) -> dict[str, Any]:
    """3-fold rolling-origin cross-validation.

    Default folds:
      Fold 1: train 2021-2023, val 2024
      Fold 2: train 2021-2024, val 2025
      Fold 3: train 2021-2025, val 2026
    """
    if folds is None:
        folds = [
            (list(range(2021, 2024)), 2024),
            (list(range(2021, 2025)), 2025),
            (list(range(2021, 2026)), 2026),
        ]

    all_df = features_df.merge(labels_df, on=["school_id", "season"])
    results: dict[str, Any] = {"fold_metrics": []}

    for fold_idx, (train_seasons, val_season) in enumerate(folds, 1):
        tr = all_df[all_df["season"].isin(train_seasons)]
        va = all_df[all_df["season"] == val_season]
        if len(tr) < 10 or len(va) < 5:
            log.warning("Fold %d: insufficient data (train=%d, val=%d)", fold_idx, len(tr), len(va))
            continue

        m = fit_team_translation(tr[["school_id", "season"] + ROSTER_FEATURES], tr[["school_id", "season", "adj_o", "adj_d", "adj_em"]])

        X_va = _feature_matrix(va)
        Xs_va_off = m.off_scaler.transform(X_va)
        Xs_va_def = m.def_scaler.transform(X_va)
        pred_off = m.off_model.predict(Xs_va_off)
        pred_def = m.def_model.predict(Xs_va_def)
        pred_em = pred_off - pred_def

        y_off = va["adj_o"].to_numpy(dtype=float)
        y_def = va["adj_d"].to_numpy(dtype=float)
        y_em = va["adj_em"].to_numpy(dtype=float)

        off_rmse = float(np.sqrt(np.mean((pred_off - y_off) ** 2)))
        def_rmse = float(np.sqrt(np.mean((pred_def - y_def) ** 2)))
        em_rmse  = float(np.sqrt(np.mean((pred_em - y_em) ** 2)))
        off_r2   = float(1 - np.var(pred_off - y_off) / np.var(y_off)) if np.var(y_off) > 0 else 0.0
        def_r2   = float(1 - np.var(pred_def - y_def) / np.var(y_def)) if np.var(y_def) > 0 else 0.0

        fold_result = {
            "fold": fold_idx,
            "val_season": val_season,
            "n_train": len(tr),
            "n_val": len(va),
            "off_rmse": off_rmse,
            "def_rmse": def_rmse,
            "em_rmse": em_rmse,
            "off_r2": off_r2,
            "def_r2": def_r2,
        }
        results["fold_metrics"].append(fold_result)
        log.info(
            "Fold %d (val=%d): off_rmse=%.3f  def_rmse=%.3f  em_rmse=%.3f  off_r2=%.3f  def_r2=%.3f",
            fold_idx, val_season, off_rmse, def_rmse, em_rmse, off_r2, def_r2,
        )

    if results["fold_metrics"]:
        results["fold3_em_rmse"] = results["fold_metrics"][-1]["em_rmse"]
        results["mean_em_rmse"] = float(np.mean([f["em_rmse"] for f in results["fold_metrics"]]))
    return results


# ---------------------------------------------------------------------------
# Inference: 2027 baseline and candidate rosters
# ---------------------------------------------------------------------------

def load_inference_data(
    engine: Engine,
    target_season: int,
    prior_season: int,
) -> dict[str, pd.DataFrame]:
    """Load target-season projections plus observed source-season team context."""
    with engine.connect() as conn:
        school_meta = pd.read_sql_query(
            text(_SCHOOL_SEASON_SQL),
            conn,
            params={"target_season": prior_season},
        )
        baseline_members = pd.read_sql_query(
            text(_BASELINE_MEMBERS_SQL),
            conn,
            params={"season": prior_season},
        )
        prior_stats = pd.read_sql_query(
            text(_PRIOR_STATS_SQL),
            conn,
            params={"prior_season": prior_season, "min_games": MIN_TRAIN_GAMES},
        )
        roster_state = pd.read_sql_query(
            text(_ROSTER_STATE_SQL.replace("= ANY(:seasons)", "= ANY(:seasons_list)")),
            conn,
            params={"seasons_list": [prior_season]},
        )
        portal_ids_raw = conn.execute(
            text(_PORTAL_CANDIDATES_SQL),
            {"season": prior_season},
        ).fetchall()
        portal_ids = [r[0] for r in portal_ids_raw]

        neutral_proj = pd.read_sql_query(
            text(_NEUTRAL_PROJ_SQL),
            conn,
            params={
                "target_season": target_season,
                "model_versions": NEUTRAL_MODEL_PRIORITY,
                "preferred_version": NEUTRAL_MODEL_PRIORITY[0],
            },
        ) if portal_ids else pd.DataFrame()

        playing_time = pd.read_sql_query(
            text(_PLAYING_TIME_SQL),
            conn,
            params={
                "season": target_season,
                "pt_model_version": PLAYING_TIME_MODEL_VERSION,
                "player_ids": portal_ids,
            },
        ) if portal_ids else pd.DataFrame()

        # HE RAPM for returning baseline players (prior season)
        returner_ids = baseline_members["player_id"].unique().tolist()
        he_rapm = pd.read_sql_query(
            text(_HE_RAPM_SQL),
            conn,
            params={"player_ids": returner_ids, "season": prior_season},
        ) if returner_ids else pd.DataFrame()

    return {
        "school_meta": school_meta,
        "baseline_members": baseline_members,
        "prior_stats": prior_stats,
        "roster_state": roster_state,
        "portal_ids": portal_ids,
        "neutral_proj": neutral_proj,
        "playing_time": playing_time,
        "he_rapm": he_rapm,
    }


def build_school_baselines(
    data: dict[str, pd.DataFrame],
    slot_baselines: dict,
    school_adj_ems: dict[int, float],
    season_adj_ems: np.ndarray,
    context_season: int,
) -> tuple[dict[int, dict], list[dict]]:
    """Build one baseline roster feature vector per school.

    Baseline = returning players (from roster_baseline_members) + prior-season
    stats + HE RAPM. Open slots filled with slot baselines.

    Returns (result, freshman_audit):
      result:  school_id → {"features", "adj_em", "tier", "adj_tempo",
                            "roster_rows", "returning_pct", "n_freshman_priors"}
      freshman_audit: list of per-school dicts for Step-A MLflow logging.
    """
    bm = data["baseline_members"]
    ps = data["prior_stats"].set_index(["player_id", "school_id"])
    he = data["he_rapm"].set_index("player_id") if not data["he_rapm"].empty else pd.DataFrame()
    sm = data["school_meta"].set_index("school_id")
    rs = data["roster_state"].set_index(["school_id", "season"])

    result: dict[int, dict] = {}
    freshman_audit: list[dict] = []

    for school_id, grp in bm.groupby("school_id"):
        school_id = int(school_id)
        adj_em = school_adj_ems.get(school_id, 0.0)
        tier = _conference_tier(adj_em, season_adj_ems)

        school_name = ""
        if school_id in sm.index:
            sm_row = sm.loc[school_id]
            adj_tempo = float(sm_row["adj_tempo"]) if pd.notna(sm_row.get("adj_tempo")) else 68.0
            school_name = str(sm_row.get("school_name", "") or "")
        else:
            adj_tempo = 68.0

        returning_pct = 1.0
        rs_row_for_school: pd.Series | dict | None = None
        rs_key = (school_id, context_season)
        if rs_key in rs.index:
            rs_row_for_school = rs.loc[rs_key]
            if isinstance(rs_row_for_school, pd.DataFrame):
                rs_row_for_school = rs_row_for_school.iloc[0]
            returning_pct = _returning_minutes_pct(rs_row_for_school)

        roster_rows = []
        for _, member in grp.iterrows():
            pid = int(member["player_id"])
            row: dict[str, Any] = {"player_id": pid}

            ps_key = (pid, school_id)
            if ps_key in ps.index:
                ps_row = ps.loc[ps_key]
                row["min_pct"]         = float(ps_row.get("min_pct", 0) or 0)
                row["usage_rate"]      = float(ps_row.get("usage_rate", 20) or 20)
                row["three_point_rate"] = float(ps_row.get("three_point_rate", 0.30) or 0.30)
                row["off_reb_pct"]     = float(ps_row.get("off_reb_pct", 0.25) or 0.25)
                row["position"]        = str(ps_row.get("position", "SG"))
            else:
                # No prior stats: slot baseline
                fill = _slot_fill(slot_baselines, tier, "SG")
                row.update({"min_pct": 8.0, "usage_rate": fill["usage_rate"],
                            "three_point_rate": fill["three_point_rate"],
                            "off_reb_pct": fill["off_reb_pct"], "position": "SG"})

            if not he.empty and pid in he.index:
                row["off_adj_rapm"] = float(he.loc[pid, "off_adj_rapm"])
                row["def_adj_rapm"] = float(he.loc[pid, "def_adj_rapm"])
            else:
                row["off_adj_rapm"] = np.nan
                row["def_adj_rapm"] = np.nan

            roster_rows.append(row)

        freshman_rows = build_freshman_prior_rows(
            rs_row_for_school, tier, slot_baselines, school_name=school_name
        )
        roster_rows.extend(freshman_rows)
        n_freshman_priors = len(freshman_rows)

        # A: accumulate audit record for this school
        total_freshman_min_pct = sum(r["min_pct"] for r in freshman_rows)
        freshman_audit.append({
            "school_id":            school_id,
            "school_name":          school_name,
            "tier":                 tier,
            "n_freshman_priors":    n_freshman_priors,
            "total_freshman_min_pct": round(total_freshman_min_pct, 2),
        })

        feats = build_roster_features(roster_rows, tier, adj_tempo, returning_pct, slot_baselines)
        result[school_id] = {
            "features":           feats,
            "adj_em":             adj_em,
            "tier":               tier,
            "adj_tempo":          adj_tempo,
            "roster_rows":        roster_rows,
            "returning_pct":      returning_pct,
            "n_freshman_priors":  n_freshman_priors,
        }

    return result, freshman_audit


def build_candidate_roster(
    baseline_info: dict,
    candidate_pt_row: pd.Series,
    candidate_proj_row: pd.Series,
    slot_baselines: dict,
) -> tuple[list[dict], float]:
    """Insert candidate into baseline roster, displace minutes per playing_time output.

    displaced_minutes format from playing_time.py:
        {"replacement_slot": float, "same_position_depth": float, "flexible_bench": float}

    Returns (modified_roster_rows, returning_pct_unchanged).
    """
    rows = [r.copy() for r in baseline_info["roster_rows"]]
    tier = baseline_info["tier"]

    cand_minutes = float(candidate_pt_row.get("expected_minutes", 12.0))
    cand_min_pct = (cand_minutes / STANDARD_TEAM_MINUTES) * 100.0
    cand_usage = float(candidate_pt_row.get("expected_usage", 20.0))

    # Derive candidate RAPM from neutral projection (value_per_100 splits roughly 55/45 off/def)
    cand_value = float(candidate_proj_row.get("value_per_100", 0.0))
    cand_off_rapm = cand_value * 0.55
    cand_def_rapm = cand_value * 0.45

    cand_row: dict[str, Any] = {
        "player_id":        int(candidate_pt_row.get("player_id", -1)),
        "min_pct":          cand_min_pct,
        "usage_rate":       cand_usage,
        "three_point_rate": _safe_float(candidate_proj_row.get("three_point_rate"), 0.35),
        "off_reb_pct":      _safe_float(candidate_proj_row.get("off_reb_pct"), 0.25),
        "position":         str(candidate_proj_row.get("position", "SG") or "SG"),
        "off_adj_rapm":     cand_off_rapm,
        "def_adj_rapm":     cand_def_rapm,
    }

    # Displaced minutes: reduce baseline players proportionally by category
    displaced = candidate_pt_row.get("displaced_minutes") or {}
    if isinstance(displaced, str):
        try:
            displaced = json.loads(displaced)
        except (json.JSONDecodeError, TypeError):
            displaced = {}

    total_displaced = (
        float(displaced.get("replacement_slot", 0))
        + float(displaced.get("same_position_depth", 0))
        + float(displaced.get("flexible_bench", 0))
    )

    if total_displaced > 0 and rows:
        # Sort by min_pct ascending — reduce from weakest players first
        rows_sorted = sorted(rows, key=lambda r: r.get("min_pct", 0))
        remaining = total_displaced / STANDARD_TEAM_MINUTES * 100.0  # convert to min_pct units
        for row in rows_sorted:
            if remaining <= 0:
                break
            reduction = min(row.get("min_pct", 0), remaining)
            row["min_pct"] = max(0.0, row.get("min_pct", 0) - reduction)
            remaining -= reduction

    rows.append(cand_row)
    return rows, baseline_info["returning_pct"]


# ---------------------------------------------------------------------------
# Counterfactual computation
# ---------------------------------------------------------------------------

def predict_adj_o_d(
    features: dict,
    models: TeamRatingModels,
) -> tuple[float, float]:
    X = np.array([[features[f] for f in ROSTER_FEATURES]])
    Xs_off = models.off_scaler.transform(X)
    Xs_def = models.def_scaler.transform(X)
    adj_o = float(models.off_model.predict(Xs_off)[0])
    adj_d = float(models.def_model.predict(Xs_def)[0])
    return adj_o, adj_d


def predict_adj_o_d_batch(
    feature_matrix: np.ndarray,
    models: TeamRatingModels,
) -> tuple[np.ndarray, np.ndarray]:
    """Batch predict adj_o and adj_d. feature_matrix shape: (n, 14)."""
    Xs_off = models.off_scaler.transform(feature_matrix)
    Xs_def = models.def_scaler.transform(feature_matrix)
    return models.off_model.predict(Xs_off), models.def_model.predict(Xs_def)


def analytical_ci(
    delta_adj_em: float,
    models: TeamRatingModels,
    n_freshman_priors: int = 0,
) -> tuple[float, float]:
    """80% CI via Gaussian approximation (replaces 200-sample bootstrap).

    AdjEM is offense minus defense, so combine both residual variances plus the
    baseline/candidate comparison variance.

    Step E: each freshman prior adds FRESHMAN_VARIANCE_PER_PLAYER to total variance,
    reflecting the higher uncertainty when the baseline relies on unmatched freshmen.
    """
    base_variance = 2.0 * (models.off_resid_std ** 2 + models.def_resid_std ** 2)
    freshman_variance = n_freshman_priors * FRESHMAN_VARIANCE_PER_PLAYER
    sigma = np.sqrt(base_variance + freshman_variance)
    z80 = 1.2816  # 80% two-sided = 10th/90th percentile
    return float(delta_adj_em - z80 * sigma), float(delta_adj_em + z80 * sigma)


def compute_counterfactual(
    baseline_features: dict,
    candidate_features: dict,
    models: TeamRatingModels,
) -> dict:
    """Delta between candidate and baseline team ratings."""
    bl_adj_o, bl_adj_d = predict_adj_o_d(baseline_features, models)
    ca_adj_o, ca_adj_d = predict_adj_o_d(candidate_features, models)

    delta_adj_o = ca_adj_o - bl_adj_o
    delta_adj_d = ca_adj_d - bl_adj_d  # positive means worse defense (higher AdjD = worse)
    delta_adj_em = delta_adj_o - delta_adj_d  # AdjEM = AdjO - AdjD; higher = better

    return {
        "baseline_adj_o":  round(bl_adj_o, 3),
        "baseline_adj_d":  round(bl_adj_d, 3),
        "baseline_adj_em": round(bl_adj_o - bl_adj_d, 3),
        "projected_adj_o": round(ca_adj_o, 3),
        "projected_adj_d": round(ca_adj_d, 3),
        "projected_adj_em": round(ca_adj_o - ca_adj_d, 3),
        "delta_adj_o":     round(delta_adj_o, 3),
        "delta_adj_d":     round(-delta_adj_d, 3),  # flip: negative AdjD change = better defense
        "delta_adj_em":    round(delta_adj_em, 3),
    }


def build_confidence_interval(
    baseline_rows: list[dict],
    candidate_rows: list[dict],
    candidate_pt_row: pd.Series,
    models: TeamRatingModels,
    slot_baselines: dict,
    conference_tier: int,
    adj_tempo: float,
    returning_pct: float,
    n_boot: int = N_BOOTSTRAP,
    rng_seed: int = 42,
) -> tuple[float, float]:
    """Bootstrap 80% CI on delta_adj_em by perturbing player RAPM."""
    rng = np.random.default_rng(rng_seed)
    off_std = max(models.off_resid_std, 0.5)
    deltas: list[float] = []

    for _ in range(n_boot):
        def perturb(rows: list[dict]) -> list[dict]:
            out = []
            for r in rows:
                r2 = r.copy()
                noise = float(rng.normal(0, off_std * 0.3))
                r2["off_adj_rapm"] = r.get("off_adj_rapm", 0.0) + noise
                r2["def_adj_rapm"] = r.get("def_adj_rapm", 0.0) + noise * 0.5
                out.append(r2)
            return out

        bl_feats = build_roster_features(perturb(baseline_rows), conference_tier, adj_tempo, returning_pct, slot_baselines)
        ca_feats = build_roster_features(perturb(candidate_rows), conference_tier, adj_tempo, returning_pct, slot_baselines)
        ct = compute_counterfactual(bl_feats, ca_feats, models)
        deltas.append(ct["delta_adj_em"])

    arr = np.array(deltas)
    return float(np.percentile(arr, 10)), float(np.percentile(arr, 90))


def build_explanation_payload(
    baseline_features: dict,
    candidate_features: dict,
    models: TeamRatingModels,
    candidate_pt_row: pd.Series,
    delta_result: dict,
) -> dict:
    """Ridge coefficient × scaled feature delta decomposition."""
    raw_deltas = np.array([
        candidate_features.get(f, 0.0) - baseline_features.get(f, 0.0)
        for f in ROSTER_FEATURES
    ], dtype=float)
    off_scale = np.where(models.off_scaler.scale_ == 0, 1.0, models.off_scaler.scale_)
    def_scale = np.where(models.def_scaler.scale_ == 0, 1.0, models.def_scaler.scale_)
    off_feature_deltas = dict(zip(ROSTER_FEATURES, raw_deltas / off_scale))
    def_feature_deltas = dict(zip(ROSTER_FEATURES, raw_deltas / def_scale))

    off_coefs = dict(zip(ROSTER_FEATURES, models.off_model.coef_))
    def_coefs = dict(zip(ROSTER_FEATURES, models.def_model.coef_))

    def off_attr(features: list[str]) -> float:
        return sum(off_coefs.get(f, 0) * off_feature_deltas.get(f, 0) for f in features)

    def def_attr(features: list[str]) -> float:
        # negate: lower AdjD = better defense
        return -sum(def_coefs.get(f, 0) * def_feature_deltas.get(f, 0) for f in features)

    talent_features  = ["weighted_off_impact", "weighted_def_impact", "top1_off_impact", "top2_impact"]
    spacing_features = ["three_pt_coverage", "pg_creation"]
    rim_features     = ["rim_protection"]
    reb_features     = ["rebounding_coverage"]
    depth_features   = ["bench_depth_impact", "usage_concentration"]
    cont_features    = ["returning_minutes_pct"]

    displaced = candidate_pt_row.get("displaced_minutes") or {}
    if isinstance(displaced, str):
        try:
            displaced = json.loads(displaced)
        except (json.JSONDecodeError, TypeError):
            displaced = {}

    return {
        "candidate_off_contribution":  round(off_attr(talent_features), 3),
        "candidate_def_contribution":  round(def_attr(talent_features), 3),
        "spacing_delta":               round(off_attr(spacing_features), 3),
        "rim_protection_delta":        round(def_attr(rim_features), 3),
        "rebounding_delta":            round(off_attr(reb_features), 3),
        "bench_depth_delta":           round(off_attr(depth_features), 3),
        "continuity_delta":            round(off_attr(cont_features), 3),
        "candidate_minutes":           round(float(candidate_pt_row.get("expected_minutes", 0)), 1),
        "candidate_usage_role":        str(candidate_pt_row.get("usage_role", "rotation")),
        "displaced_minutes":           displaced,
        "delta_adj_o":                 delta_result.get("delta_adj_o", 0.0),
        "delta_adj_d":                 delta_result.get("delta_adj_d", 0.0),
        "delta_adj_em":                delta_result.get("delta_adj_em", 0.0),
        "baseline_adj_o":              delta_result.get("baseline_adj_o", 0.0),
        "baseline_adj_d":              delta_result.get("baseline_adj_d", 0.0),
    }


# ---------------------------------------------------------------------------
# Percentile + rank helpers
# ---------------------------------------------------------------------------

def compute_national_percentiles(
    records: list[dict],
    school_meta: pd.DataFrame,
) -> list[dict]:
    """Add national_percentile and conference_rank to each record in-place."""
    # Projected AdjEM per school (from baseline + any delta for the winning candidate)
    # For each record we have projected_adj_em; the school's projected standing uses
    # its baseline_adj_em from that same record (candidate-specific delta doesn't
    # change the school's "standing" — standing = baseline).  Use baseline_adj_em.
    school_baseline: dict[int, float] = {}
    for r in records:
        sid = r["school_id"]
        bl = r.get("baseline_adj_em", r.get("current_adj_em", 0.0))
        # Keep max across candidates (a school appears once per candidate)
        if sid not in school_baseline or bl > school_baseline[sid]:
            school_baseline[sid] = bl

    all_ems = np.array(list(school_baseline.values()))

    conf_map: dict[int, str] = {}
    if not school_meta.empty and "school_id" in school_meta.columns:
        for _, row in school_meta.iterrows():
            conf_map[int(row["school_id"])] = str(row.get("conference", ""))

    conf_groups: dict[str, list[float]] = {}
    for sid, em in school_baseline.items():
        conf = conf_map.get(sid, "")
        conf_groups.setdefault(conf, []).append(em)

    conf_rank_map: dict[int, int] = {}
    for conf, ems in conf_groups.items():
        sorted_ems = sorted(ems, reverse=True)
        for sid, em in school_baseline.items():
            if conf_map.get(sid, "") == conf:
                rank = sorted_ems.index(em) + 1
                conf_rank_map[sid] = rank

    for r in records:
        sid = r["school_id"]
        bl = school_baseline.get(sid, 0.0)
        pct = int(np.mean(all_ems <= bl) * 100)
        r["national_percentile"] = max(1, min(100, pct))
        r["conference_rank"] = conf_rank_map.get(sid, 1)

    return records


# ---------------------------------------------------------------------------
# DB write
# ---------------------------------------------------------------------------

def _num(rec: dict, key: str, default: float) -> float:
    """rec.get(key, default) only falls back when the key is *missing* — not
    when a pandas LEFT JOIN miss left a present-but-NaN value (real bug found
    in production: NaN silently stored in a NOT NULL float column, then
    serialized as the bare JSON token `NaN`, which browsers can't parse —
    crashed FitScorePage's Team Rating Projection panel)."""
    val = rec.get(key, default)
    return default if val is None or (isinstance(val, float) and np.isnan(val)) else val


def build_team_rating_rows(
    records: list[dict],
    model_version: str = MODEL_VERSION,
    computed_at: datetime | None = None,
    expires_days: int = EXPIRES_DAYS,
) -> list[tuple]:
    """Build the tuple rows upsert_team_rating_projections writes via execute_values.

    Pulled out as its own pure function so the NaN-sanitization in _num is
    testable without a DB connection.
    """
    now = computed_at or datetime.now(timezone.utc)
    expires = now + timedelta(days=expires_days)

    rows = []
    for r in records:
        explanation = r.get("explanation") or {}
        minutes_dist = r.get("minutes_distribution") or {}
        rows.append((
            r["player_id"],
            r["school_id"],
            r["season"],
            round(_num(r, "current_adj_em", 0.0), 3),
            round(_num(r, "projected_adj_em", 0.0), 3),
            round(_num(r, "delta_adj_em", 0.0), 3),
            round(_num(r, "baseline_adj_o", 0.0), 3),
            round(_num(r, "baseline_adj_d", 0.0), 3),
            round(_num(r, "projected_adj_o", 0.0), 3),
            round(_num(r, "projected_adj_d", 0.0), 3),
            round(_num(r, "ci_lower", -1.0), 3),
            round(_num(r, "ci_upper", 1.0), 3),
            int(_num(r, "national_percentile", 50)),
            int(_num(r, "conference_rank", 5)),
            round(_num(r, "expected_minutes_input", 0.0), 1),
            r.get("candidate_usage_role", "rotation"),
            json.dumps(explanation),
            json.dumps(minutes_dist),
            model_version,
            now,
            expires,
        ))
    return rows


def upsert_team_rating_projections(
    engine: Engine,
    records: list[dict],
    model_version: str = MODEL_VERSION,
) -> int:
    """Upsert records into team_rating_projections. Returns row count written."""
    if not records:
        return 0

    rows = build_team_rating_rows(records, model_version=model_version)

    with engine.connect() as conn:
        raw = conn.connection.connection  # type: ignore[attr-defined]
        with raw.cursor() as cur:
            # page_size default (100) meant ~4,573 round trips for a ~457,345-row
            # write instead of ~458 -- same drift as destination_projection.py's
            # sibling upsert, fixed 2026-07-23.
            execute_values(cur, _UPSERT_SQL, rows, page_size=1000)
        raw.commit()

    log.info("Upserted %d rows into team_rating_projections", len(rows))
    return len(rows)


# ---------------------------------------------------------------------------
# On-demand minutes-override counterfactual
# ---------------------------------------------------------------------------
#
# scripts/run_team_rating_projection.py refits `TeamRatingModels` from scratch
# every batch run and never persists a load-back-and-reuse artifact — fine for
# a scheduled job, not for a live "what if this player got more minutes?"
# request. load_champion_models() below loads the same Ridge off/def
# models + slot_baselines the batch script already logs to MLflow
# (team_rating_models/*.pkl, slot_baselines.json) without refitting.
#
# load_single_school_context()/compute_team_rating_override() are a
# school-scoped rebuild of load_inference_data()/build_school_baselines()'s
# per-school output, not a shortcut around them — conference tier assignment
# and slot-baseline fallback are the same formulas, just queried for one
# school_id instead of all ~365. season_adj_ems still needs every school's
# adj_em (a single float column, cheap) since tier is a population percentile.

_SEASON_ADJ_EMS_SQL = """
SELECT adj_em FROM team_season_stats
WHERE season = :target_season AND adj_em IS NOT NULL
"""

_SCHOOL_META_ONE_SQL = """
SELECT s.id AS school_id, s.name AS school_name, s.conference,
       tss.season, tss.adj_em, tss.adj_tempo
FROM schools s
JOIN team_season_stats tss ON tss.school_id = s.id
WHERE tss.season = :target_season AND s.id = :school_id
"""

_BASELINE_MEMBERS_ONE_SQL = """
SELECT rbm.player_id, rbm.school_id, rbm.baseline_status
FROM roster_baseline_members rbm
WHERE rbm.season = :season AND rbm.school_id = :school_id
"""

_PRIOR_STATS_ONE_SQL = """
SELECT
    pss.player_id, pss.school_id,
    pss.min_pct, pss.usage_rate, pss.three_point_rate, pss.off_reb_pct,
    p.position
FROM player_season_stats pss
JOIN players p ON p.id = pss.player_id
WHERE pss.season = :prior_season
  AND pss.school_id = :school_id
  AND pss.games_played >= :min_games
  AND pss.min_pct IS NOT NULL AND pss.min_pct > 0
"""

_ROSTER_STATE_ONE_SQL = """
SELECT school_id, season,
       returning_minutes_by_position, departing_minutes_by_position,
       open_minutes_by_position, class_balance, returning_player_impact
FROM roster_state_features
WHERE season = :season AND school_id = :school_id
"""

_HE_RAPM_ONE_SQL = """
SELECT player_id, off_adj_rapm, def_adj_rapm
FROM hoop_explorer_player_stats
WHERE player_id = ANY(:player_ids) AND season = :season
"""

_CANDIDATE_PLAYING_TIME_ONE_SQL = """
SELECT player_id, school_id, expected_minutes, expected_minutes_share,
       minutes_ci_lower, minutes_ci_upper, expected_usage, usage_role,
       displaced_minutes, role_fit
FROM playing_time_projections
WHERE season = :season AND model_version = :pt_model_version
  AND player_id = :player_id AND school_id = :school_id
LIMIT 1
"""

_CANDIDATE_NEUTRAL_PROJ_ONE_SQL = """
SELECT player_id, value_per_100, model_version
FROM player_projections
WHERE school_id IS NULL AND projection_mode = 'neutral' AND season = :target_season
  AND model_version = ANY(:model_versions) AND player_id = :player_id
ORDER BY CASE WHEN model_version = :preferred_version THEN 0
              ELSE array_position(CAST(:model_versions AS text[]), model_version) END
LIMIT 1
"""

_CANDIDATE_PRIOR_STATS_ONE_SQL = """
SELECT player_id, position, three_point_rate, off_reb_pct
FROM player_season_stats
WHERE player_id = :player_id
ORDER BY season DESC
LIMIT 1
"""


def load_champion_models(
    client: MlflowClient, model_name: str = "team-rating-scorer"
) -> TeamRatingModels:
    """Load the @champion Ridge off/def models + slot_baselines from MLflow.

    Reconstructs the same TeamRatingModels scripts/run_team_rating_projection.py
    fits fresh every batch run, from the artifacts that run already logs
    (team_rating_models/off_model.pkl, def_model.pkl, slot_baselines.json) —
    no refit, safe to call per-request (mlflow caches the artifact download
    locally after the first call).
    """
    mv = client.get_model_version_by_alias(model_name, "champion")
    run_id = mv.run_id
    run = client.get_run(run_id)

    off_local = mlflow.artifacts.download_artifacts(
        run_id=run_id, artifact_path="team_rating_models/off_model.pkl"
    )
    def_local = mlflow.artifacts.download_artifacts(
        run_id=run_id, artifact_path="team_rating_models/def_model.pkl"
    )
    with open(off_local, "rb") as f:
        off = pickle.load(f)
    with open(def_local, "rb") as f:
        deff = pickle.load(f)

    slot_local = mlflow.artifacts.download_artifacts(
        run_id=run_id, artifact_path="slot_baselines.json"
    )
    with open(slot_local) as f:
        raw_slots = json.load(f)
    slot_baselines = {ast.literal_eval(k): v for k, v in raw_slots.items()}

    return TeamRatingModels(
        off_model=off["model"], off_scaler=off["scaler"],
        def_model=deff["model"], def_scaler=deff["scaler"],
        off_resid_std=float(run.data.metrics.get("off_resid_std", 0.0)),
        def_resid_std=float(run.data.metrics.get("def_resid_std", 0.0)),
        slot_baselines=slot_baselines,
    )


def load_single_school_context(
    engine: Engine, school_id: int, target_season: int, prior_season: int
) -> dict:
    """School-scoped counterpart to load_inference_data(), for one school_id."""
    with engine.connect() as conn:
        season_adj = pd.read_sql_query(
            text(_SEASON_ADJ_EMS_SQL), conn, params={"target_season": prior_season}
        )
        school_meta = pd.read_sql_query(
            text(_SCHOOL_META_ONE_SQL), conn,
            params={"target_season": prior_season, "school_id": school_id},
        )
        baseline_members = pd.read_sql_query(
            text(_BASELINE_MEMBERS_ONE_SQL), conn,
            params={"season": prior_season, "school_id": school_id},
        )
        prior_stats = pd.read_sql_query(
            text(_PRIOR_STATS_ONE_SQL), conn,
            params={"prior_season": prior_season, "school_id": school_id, "min_games": MIN_TRAIN_GAMES},
        )
        roster_state = pd.read_sql_query(
            text(_ROSTER_STATE_ONE_SQL), conn,
            params={"season": prior_season, "school_id": school_id},
        )
        returner_ids = baseline_members["player_id"].unique().tolist()
        he_rapm = (
            pd.read_sql_query(
                text(_HE_RAPM_ONE_SQL), conn,
                params={"player_ids": returner_ids, "season": prior_season},
            )
            if returner_ids else pd.DataFrame()
        )

    school_adj_em = 0.0
    if not school_meta.empty and pd.notna(school_meta["adj_em"].iloc[0]):
        school_adj_em = float(school_meta["adj_em"].iloc[0])

    return {
        "school_meta": school_meta,
        "baseline_members": baseline_members,
        "prior_stats": prior_stats,
        "roster_state": roster_state,
        "he_rapm": he_rapm,
        "season_adj_ems": season_adj["adj_em"].dropna().to_numpy(dtype=float),
        "school_adj_em": school_adj_em,
    }


def load_candidate_context(
    engine: Engine, player_id: int, school_id: int, target_season: int
) -> tuple[pd.Series, pd.Series] | None:
    """Real playing_time_projections + player_projections rows for one pair.

    Same inputs build_candidate_roster() needs in the batch path, fetched
    directly for a single (player, school) instead of via the population-wide
    join. Returns None if the pair has no scored playing_time_projections row
    (the same hard-gate the batch script enforces before scoring any pair).
    """
    with engine.connect() as conn:
        pt_row = pd.read_sql_query(
            text(_CANDIDATE_PLAYING_TIME_ONE_SQL), conn,
            params={
                "season": target_season, "pt_model_version": PLAYING_TIME_MODEL_VERSION,
                "player_id": player_id, "school_id": school_id,
            },
        )
        if pt_row.empty:
            return None
        neutral_row = pd.read_sql_query(
            text(_CANDIDATE_NEUTRAL_PROJ_ONE_SQL), conn,
            params={
                "target_season": target_season, "model_versions": NEUTRAL_MODEL_PRIORITY,
                "preferred_version": NEUTRAL_MODEL_PRIORITY[0], "player_id": player_id,
            },
        )
        prior_row = pd.read_sql_query(
            text(_CANDIDATE_PRIOR_STATS_ONE_SQL), conn, params={"player_id": player_id}
        )

    cand_value = float(neutral_row["value_per_100"].iloc[0]) if not neutral_row.empty else 0.0
    cand_proj = pd.Series({
        "value_per_100": cand_value,
        "position": prior_row["position"].iloc[0] if not prior_row.empty else "SG",
        "three_point_rate": (
            float(prior_row["three_point_rate"].iloc[0]) if not prior_row.empty
            and pd.notna(prior_row["three_point_rate"].iloc[0]) else 0.35
        ),
        "off_reb_pct": (
            float(prior_row["off_reb_pct"].iloc[0]) if not prior_row.empty
            and pd.notna(prior_row["off_reb_pct"].iloc[0]) else 0.25
        ),
    })
    return pt_row.iloc[0], cand_proj


def scale_displaced_minutes(
    displaced_raw: Any,
    stored_minutes: float,
    minutes_override: float,
) -> dict[str, float]:
    """Scale a stored displaced_minutes payload for a minutes override.

    displaced_minutes (how much build_candidate_roster() removes from
    returning players) was computed by the batch playing-time run for the
    *stored* expected_minutes — passing it through unscaled means a 0 MPG
    override still displaces the original amount, and a higher override only
    displaces the original amount too (real bug, caught in review). Scaling
    by the same ratio the override applies to minutes means 0 MPG reproduces
    the untouched baseline roster and a bigger override displaces
    proportionally more.
    """
    if isinstance(displaced_raw, str):
        try:
            displaced_raw = json.loads(displaced_raw)
        except (json.JSONDecodeError, TypeError):
            displaced_raw = {}
    if not isinstance(displaced_raw, dict):
        displaced_raw = {}

    if stored_minutes > 0:
        scale = max(0.0, minutes_override) / stored_minutes
    else:
        # No baseline displacement to scale from (candidate wasn't projected
        # any minutes at all) — nothing recorded to proportion against, so
        # don't fabricate a displacement.
        scale = 0.0
    return {k: float(v) * scale for k, v in displaced_raw.items()}


def compute_team_rating_override(
    engine: Engine,
    models: TeamRatingModels,
    player_id: int,
    school_id: int,
    target_season: int,
    prior_season: int,
    minutes_override: float,
    usage_override: float | None = None,
) -> dict | None:
    """On-demand counterfactual for one pair with a coach-supplied minutes override.

    Same build_candidate_roster -> build_roster_features -> predict_adj_o_d ->
    compute_counterfactual pipeline scripts/run_team_rating_projection.py runs
    for every (player, school) pair, scoped to a single school so it's cheap
    enough to run on a live request. Returns None if the pair has no scored
    playing_time_projections row yet.
    """
    candidate = load_candidate_context(engine, player_id, school_id, target_season)
    if candidate is None:
        return None
    pt_row, cand_proj = candidate

    ctx = load_single_school_context(engine, school_id, target_season, prior_season)
    tier = _conference_tier(ctx["school_adj_em"], ctx["season_adj_ems"])
    adj_tempo = 68.0
    if not ctx["school_meta"].empty and pd.notna(ctx["school_meta"]["adj_tempo"].iloc[0]):
        adj_tempo = float(ctx["school_meta"]["adj_tempo"].iloc[0])
    school_name = (
        str(ctx["school_meta"]["school_name"].iloc[0]) if not ctx["school_meta"].empty else ""
    )

    ps = ctx["prior_stats"].set_index("player_id") if not ctx["prior_stats"].empty else pd.DataFrame()
    he = ctx["he_rapm"].set_index("player_id") if not ctx["he_rapm"].empty else pd.DataFrame()

    roster_rows: list[dict] = []
    for _, member in ctx["baseline_members"].iterrows():
        pid = int(member["player_id"])
        row: dict[str, Any] = {"player_id": pid}
        if pid in ps.index:
            ps_row = ps.loc[pid]
            row["min_pct"] = float(ps_row.get("min_pct", 0) or 0)
            row["usage_rate"] = float(ps_row.get("usage_rate", 20) or 20)
            row["three_point_rate"] = float(ps_row.get("three_point_rate", 0.30) or 0.30)
            row["off_reb_pct"] = float(ps_row.get("off_reb_pct", 0.25) or 0.25)
            row["position"] = str(ps_row.get("position", "SG"))
        else:
            fill = _slot_fill(models.slot_baselines, tier, "SG")
            row.update({
                "min_pct": 8.0, "usage_rate": fill["usage_rate"],
                "three_point_rate": fill["three_point_rate"],
                "off_reb_pct": fill["off_reb_pct"], "position": "SG",
            })
        if pid in he.index:
            row["off_adj_rapm"] = float(he.loc[pid, "off_adj_rapm"])
            row["def_adj_rapm"] = float(he.loc[pid, "def_adj_rapm"])
        else:
            row["off_adj_rapm"] = np.nan
            row["def_adj_rapm"] = np.nan
        roster_rows.append(row)

    rs_row = ctx["roster_state"].iloc[0] if not ctx["roster_state"].empty else None
    returning_pct = _returning_minutes_pct(rs_row)
    freshman_rows = build_freshman_prior_rows(
        rs_row, tier, models.slot_baselines, school_name=school_name
    )
    roster_rows_with_freshmen = roster_rows + freshman_rows

    baseline_info = {
        "roster_rows": roster_rows_with_freshmen,
        "tier": tier,
        "adj_tempo": adj_tempo,
        "returning_pct": returning_pct,
    }
    baseline_features = build_roster_features(
        roster_rows_with_freshmen, tier, adj_tempo, returning_pct, models.slot_baselines
    )

    pt_override = pt_row.copy()
    pt_override["expected_minutes"] = float(minutes_override)
    pt_override["displaced_minutes"] = scale_displaced_minutes(
        pt_row.get("displaced_minutes"),
        stored_minutes=float(pt_row.get("expected_minutes", 0.0) or 0.0),
        minutes_override=float(minutes_override),
    )
    if usage_override is not None:
        pt_override["expected_usage"] = float(usage_override)

    candidate_rows, cand_returning_pct = build_candidate_roster(
        baseline_info, pt_override, cand_proj, models.slot_baselines
    )
    candidate_features = build_roster_features(
        candidate_rows, tier, adj_tempo, cand_returning_pct, models.slot_baselines
    )

    delta = compute_counterfactual(baseline_features, candidate_features, models)
    ci_lower, ci_upper = analytical_ci(
        delta["delta_adj_em"], models, n_freshman_priors=len(freshman_rows)
    )
    delta["ci_lower"] = round(ci_lower, 3)
    delta["ci_upper"] = round(ci_upper, 3)
    delta["expected_minutes_override"] = float(minutes_override)
    return delta
