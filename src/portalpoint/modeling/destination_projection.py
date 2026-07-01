"""Destination-Adjusted Player Projection.

Translates neutral per-100 talent estimates (from player_projections) into
school-specific projected production for portal candidates evaluated against
every D1 destination — the core context-aware projection surface.

Architecture: four conservative context deltas applied on top of the neutral
value_per_100 anchor, each independently capped and summed within a total cap:

  1. role_usage_delta        -- usage-role change at destination vs. source
  2. style_skill_fit_delta   -- player skill x destination team style interaction
  3. roster_context_delta    -- roster gap / crowding at destination position
  4. competition_level_delta -- competition tier change adjustment

The neutral value is always the anchor. Deltas shift it conservatively; they
cannot dominate the projection. See docs/models/destination_projection_plan.md
for the full design rationale and validation plan.

Inference population:
  portal candidates (is_portal_candidate = true)
    × all D1 destination schools with usable roster context
  inner-joined to playing_time_projections (hard gate on opportunity estimate)

Program-facing query pattern:
  SELECT * FROM player_projections
  WHERE school_id = :program_school_id
    AND projection_mode = 'destination'
    AND model_version = 'player-destination-proj-v1'
  ORDER BY value_per_100 DESC
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from scipy.stats import spearmanr

import numpy as np
import pandas as pd
from psycopg2.extras import execute_values
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from sqlalchemy import Engine, text

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL_VERSION = "player-destination-proj-v1"
EXPIRES_DAYS = 7  # destination rows refresh more frequently — portal context changes daily

# Neutral model version priority: try forecast first, fall back to same-season Phase 2a,
# then Phase 0. The playing_time model already fetches its own neutral projection; this
# priority applies only to the feature-construction join here.
NEUTRAL_MODEL_PRIORITY = [
    "player-proj-phase2a-fcast-v1",
    "player-projection-phase2a-v1",
    "player-projection-shrinkage-v2",
]

PLAYING_TIME_MODEL_VERSION = "playing-time-rotation-v2"

# Hard caps on each delta and their sum (plan doc §4)
DELTA_CAPS: dict[str, float] = {
    "role_usage_delta": 0.75,
    "style_skill_fit_delta": 0.50,
    "roster_context_delta": 0.50,
    "competition_level_delta": 0.75,
    "total_context_delta": 1.50,
}

# Competition tier bucket boundaries (adj_em national percentile per season)
# Tier 1 = high-major, Tier 4 = low-major
TIER_PERCENTILE_CUTS = [0.75, 0.45, 0.20]  # top-25%, 25-55%, 55-80%, bottom-20%

# Style interaction weights: max contribution of each interaction to style_skill_fit_delta
# before the aggregate cap. Kept small — style compatibility amplifies existing talent,
# not substitutes for it.
STYLE_SKILL_INTERACTION_WEIGHTS = {
    "shooting_3p_x_team_threepr": 0.12,
    "passing_creation_x_open_usage": 0.10,
    "shot_creation_x_usage_crowding": -0.10,  # crowding suppresses
    "block_rim_x_def_rim_need": 0.09,
    "off_reb_x_rim_style": 0.08,
    "def_reb_x_frontcourt_need": 0.07,
}

# Roster context: gap_match score → roster_context_delta (3-piece linear mapping)
# gap_match 0-100; delta bounded by DELTA_CAPS["roster_context_delta"]
# Anchored at gap_match=50 → delta=0 (neutral roster situation)
ROSTER_CONTEXT_BREAKPOINTS = [
    (0.0, -0.50),   # gap_match = 0  (worst possible fit) → -0.50 cap
    (25.0, -0.25),
    (50.0, 0.00),   # neutral
    (75.0, 0.20),
    (100.0, 0.50),  # gap_match = 100 (perfect need match) → +0.50 cap
]

# Minimum rows in training set before falling back to zero delta
MIN_ROLE_USAGE_TRAIN_ROWS = 30
MIN_COMPETITION_TIER_ROWS = 20


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DestinationModels:
    """Fitted model artifacts for the destination projection."""
    role_usage_model: Ridge | None = None
    role_usage_scaler: StandardScaler | None = None
    role_usage_residual_std: float = 0.0
    role_usage_features: list[str] = field(default_factory=list)
    tier_matrix: pd.DataFrame | None = None          # 4×4 (from_tier, to_tier) → mean delta
    tier_matrix_std: pd.DataFrame | None = None      # same shape, residual std per cell
    ci_calibration_scale: float = 1.0
    train_seasons: list[int] = field(default_factory=list)
    n_train_rows: int = 0


# ---------------------------------------------------------------------------
# SQL queries
# ---------------------------------------------------------------------------

_NEUTRAL_PROJECTION_SQL = """
SELECT DISTINCT ON (player_id)
    pp.player_id,
    pp.season                   AS neutral_season,
    pp.value_per_100,
    pp.value_ci_lower,
    pp.value_ci_upper,
    pp.projected_rates,
    pp.projected_box_score,
    pp.skill_states,
    pp.skill_percentiles,
    pp.uncertainty,
    pp.model_version            AS neutral_model_version,
    pp.explanation              AS neutral_explanation
FROM player_projections pp
WHERE pp.school_id IS NULL
  AND pp.projection_mode = 'neutral'
  AND pp.season = :target_season
  AND pp.player_id = ANY(:player_ids)
  AND pp.model_version = ANY(:model_versions)
ORDER BY
    player_id,
    CASE
        WHEN model_version = :preferred_version THEN 0
        ELSE array_position(CAST(:model_versions AS text[]), model_version)
    END
"""

_PORTAL_CANDIDATES_SQL = """
SELECT DISTINCT pts.player_id
FROM player_team_fit_scores pts
WHERE pts.season = :season
  AND pts.is_portal_candidate = true
"""

_PLAYING_TIME_SQL = """
SELECT
    pt.player_id,
    pt.school_id,
    pt.season,
    pt.expected_minutes,
    pt.expected_minutes_share,
    pt.minutes_ci_lower,
    pt.minutes_ci_upper,
    pt.expected_usage,
    pt.usage_role,
    pt.usage_role_confidence,
    pt.displaced_minutes,
    pt.data_quality_flags,
    pt.role_fit
FROM playing_time_projections pt
WHERE pt.season = :season
  AND pt.model_version = :pt_model_version
  AND pt.player_id = ANY(:player_ids)
"""

_TEAM_CONTEXT_SQL = """
SELECT
    tss.school_id,
    tss.season,
    tss.adj_em,
    tss.adj_o,
    tss.adj_d,
    tss.adj_tempo,
    tss.three_point_rate,
    tss.assist_rate,
    tsp.offense_cluster_id,
    tsp.defense_cluster_id,
    tsp.system_label,
    tsp.offense_memberships,
    tsp.defense_memberships,
    het.off_threepr,
    het.off_twoprimr,
    het.off_style_rim_attack_pct,
    het.def_style_rim_attack_pct,
    het.def_orb,
    het.off_orb
FROM team_season_stats tss
LEFT JOIN team_system_profiles tsp
    ON tsp.school_id = tss.school_id AND tsp.season = tss.season
LEFT JOIN hoop_explorer_team_stats het
    ON het.school_id = tss.school_id AND het.season = tss.season
WHERE tss.season = :season
"""

_PAIRWISE_FIT_SQL = """
SELECT
    pts.player_id,
    pts.school_id,
    pts.gap_match,
    pts.scheme_fit
FROM player_team_fit_scores pts
WHERE pts.season = :season
  AND pts.player_id = ANY(:player_ids)
"""

_ROSTER_STATE_SQL = """
SELECT
    rsf.school_id,
    rsf.season,
    rsf.returning_minutes_by_position,
    rsf.departing_minutes_by_position,
    rsf.incoming_transfer_minutes_by_position,
    rsf.open_minutes_by_position,
    rsf.open_usage_by_position,
    rsf.returning_production,
    rsf.returning_player_impact
FROM roster_state_features rsf
WHERE rsf.season = :season
"""

# Training: transfers → actual destination usage from player_season_stats.
# post_usage_rate is NULL in transfers table — derive from actual destination stats.
# Joins neutral projection from the SOURCE season (season - 1) as the feature input,
# and HE labels from the DESTINATION season as the value target.
_TRANSFER_TRAINING_SQL = """
WITH transfer_rows AS (
    SELECT
        t.player_id,
        t.from_school_id,
        t.to_school_id,
        t.season + 1            AS dest_season,
        t.season                AS source_season,
        t.pre_usage_rate        AS source_usage_rate,
        p.position              AS position
    FROM transfers t
    JOIN players p ON p.id = t.player_id
    WHERE t.season + 1 = ANY(:train_seasons)
      AND t.pre_usage_rate IS NOT NULL
      AND t.player_id IS NOT NULL
),
dest_stats AS (
    SELECT player_id, school_id, season, usage_rate AS dest_usage_rate, min_pct AS dest_min_pct
    FROM player_season_stats
),
source_proj AS (
    -- Neutral projection for the SOURCE season (year they last played at source school)
    SELECT DISTINCT ON (player_id, season)
        player_id, season AS proj_season,
        value_per_100, value_ci_lower, value_ci_upper,
        skill_states, skill_percentiles, model_version
    FROM player_projections
    WHERE school_id IS NULL
      AND projection_mode = 'neutral'
      AND model_version = ANY(:model_versions)
    ORDER BY player_id, season,
        CASE WHEN model_version = :preferred_version THEN 0
             ELSE array_position(CAST(:model_versions AS text[]), model_version) END
),
dest_labels AS (
    -- No season filter here — JOIN condition restricts to dest_season (= portal year + 1)
    SELECT player_id, season, off_adj_rapm, def_adj_rapm, adj_rapm_margin
    FROM hoop_explorer_player_stats
),
team_tiers AS (
    SELECT
        school_id,
        season,
        adj_em,
        adj_tempo,
        three_point_rate,
        CASE
            WHEN percent_rank() OVER (PARTITION BY season ORDER BY adj_em) >= 0.75 THEN 1
            WHEN percent_rank() OVER (PARTITION BY season ORDER BY adj_em) >= 0.45 THEN 2
            WHEN percent_rank() OVER (PARTITION BY season ORDER BY adj_em) >= 0.20 THEN 3
            ELSE 4
        END AS competition_tier
    FROM team_season_stats
)
SELECT
    tr.player_id,
    tr.from_school_id,
    tr.to_school_id,
    tr.dest_season,
    tr.source_season,
    tr.source_usage_rate,
    tr.position,
    ds.dest_usage_rate,
    ds.dest_min_pct,
    sp.value_per_100            AS neutral_value,
    sp.value_ci_lower,
    sp.value_ci_upper,
    sp.skill_states,
    sp.skill_percentiles,
    sp.model_version            AS neutral_model_version,
    dl.off_adj_rapm             AS dest_off_rapm,
    dl.def_adj_rapm             AS dest_def_rapm,
    dl.adj_rapm_margin          AS dest_total_rapm,
    st.adj_em                   AS source_adj_em,
    st.competition_tier         AS source_tier,
    dt.adj_em                   AS dest_adj_em,
    dt.competition_tier         AS dest_tier,
    dt.three_point_rate         AS dest_three_point_rate
FROM transfer_rows tr
JOIN dest_stats ds
    ON ds.player_id = tr.player_id
   AND ds.school_id = tr.to_school_id
   AND ds.season = tr.dest_season
JOIN source_proj sp
    ON sp.player_id = tr.player_id
   AND sp.proj_season = tr.source_season
JOIN dest_labels dl
    ON dl.player_id = tr.player_id
   AND dl.season = tr.dest_season
LEFT JOIN team_tiers st
    ON st.school_id = tr.from_school_id
   AND st.season = tr.source_season
LEFT JOIN team_tiers dt
    ON dt.school_id = tr.to_school_id
   AND dt.season = tr.dest_season
WHERE ds.dest_usage_rate IS NOT NULL
"""

_UPSERT_DESTINATION_SQL = """
INSERT INTO player_projections
    (player_id, school_id, season, projection_mode,
     value_per_100, value_ci_lower, value_ci_upper,
     projected_minutes, projected_usage,
     projected_box_score, projected_rates,
     skill_states, skill_percentiles, uncertainty,
     explanation, model_version, computed_at, expires_at)
VALUES %s
ON CONFLICT (player_id, school_id, season, model_version) WHERE school_id IS NOT NULL DO UPDATE SET
    projection_mode   = EXCLUDED.projection_mode,
    value_per_100     = EXCLUDED.value_per_100,
    value_ci_lower    = EXCLUDED.value_ci_lower,
    value_ci_upper    = EXCLUDED.value_ci_upper,
    projected_minutes = EXCLUDED.projected_minutes,
    projected_usage   = EXCLUDED.projected_usage,
    projected_box_score = EXCLUDED.projected_box_score,
    projected_rates   = EXCLUDED.projected_rates,
    skill_states      = EXCLUDED.skill_states,
    skill_percentiles = EXCLUDED.skill_percentiles,
    uncertainty       = EXCLUDED.uncertainty,
    explanation       = EXCLUDED.explanation,
    computed_at       = EXCLUDED.computed_at,
    expires_at        = EXCLUDED.expires_at
"""


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_portal_candidates(engine: Engine, fit_context_season: int) -> list[int]:
    """Return player_ids flagged is_portal_candidate for the given season."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(_PORTAL_CANDIDATES_SQL), {"season": fit_context_season}
        ).fetchall()
    player_ids = [int(r[0]) for r in rows]
    log.info("Portal candidates: %d players for season %d", len(player_ids), fit_context_season)
    return player_ids


def load_neutral_projections(
    engine: Engine,
    player_ids: list[int],
    target_season: int,
    model_priority: list[str] = NEUTRAL_MODEL_PRIORITY,
) -> pd.DataFrame:
    """Load best available target-season neutral projection per player."""
    if not player_ids:
        return pd.DataFrame()
    with engine.connect() as conn:
        df = pd.read_sql(
            text(_NEUTRAL_PROJECTION_SQL),
            conn,
            params={
                "player_ids": player_ids,
                "target_season": target_season,
                "model_versions": model_priority,
                "preferred_version": model_priority[0],
            },
        )
    log.info(
        "Neutral projections: %d rows for target season %d (versions: %s)",
        len(df),
        target_season,
        df["neutral_model_version"].value_counts().to_dict() if not df.empty else {},
    )
    return df


def load_playing_time_projections(
    engine: Engine,
    player_ids: list[int],
    target_season: int,
) -> pd.DataFrame:
    """Load playing_time_projections for target season. Returns empty if none exist."""
    if not player_ids:
        return pd.DataFrame()
    with engine.connect() as conn:
        df = pd.read_sql(
            text(_PLAYING_TIME_SQL),
            conn,
            params={
                "season": target_season,
                "pt_model_version": PLAYING_TIME_MODEL_VERSION,
                "player_ids": player_ids,
            },
        )
    log.info(
        "Playing time projections: %d rows, %d unique schools",
        len(df),
        df["school_id"].nunique() if not df.empty else 0,
    )
    return df


def load_destination_team_context(engine: Engine, season: int) -> pd.DataFrame:
    """Load team quality, pace, style for all schools in a season."""
    with engine.connect() as conn:
        df = pd.read_sql(text(_TEAM_CONTEXT_SQL), conn, params={"season": season})
    log.info("Team context: %d schools for season %d", len(df), season)
    return df


def load_pairwise_fit_context(
    engine: Engine, player_ids: list[int], fit_context_season: int
) -> pd.DataFrame:
    """Load scheme_fit and gap_match for player×school pairs."""
    if not player_ids:
        return pd.DataFrame()
    with engine.connect() as conn:
        df = pd.read_sql(
            text(_PAIRWISE_FIT_SQL),
            conn,
            params={"season": fit_context_season, "player_ids": player_ids},
        )
    log.info("Pairwise fit context: %d rows", len(df))
    return df


def load_roster_state_features(engine: Engine, roster_season: int) -> pd.DataFrame:
    """Load roster_state_features for all schools."""
    with engine.connect() as conn:
        df = pd.read_sql(
            text(_ROSTER_STATE_SQL), conn, params={"season": roster_season}
        )
    log.info("Roster state features: %d school-seasons", len(df))
    return df


def load_historical_transfer_outcomes(
    engine: Engine,
    train_seasons: list[int],
    model_priority: list[str] = NEUTRAL_MODEL_PRIORITY,
) -> pd.DataFrame:
    """Load historical transfer rows with source/destination stats for delta model training."""
    if not train_seasons:
        return pd.DataFrame()
    with engine.connect() as conn:
        df = pd.read_sql(
            text(_TRANSFER_TRAINING_SQL),
            conn,
            params={
                "train_seasons": train_seasons,
                "model_versions": model_priority,
                "preferred_version": model_priority[0],
            },
        )
    log.info(
        "Historical transfer training rows: %d (seasons %s)",
        len(df),
        sorted(df["dest_season"].unique().tolist()) if not df.empty else [],
    )
    return df


def load_source_player_stats(
    engine: Engine, player_ids: list[int], source_season: int
) -> pd.DataFrame:
    """Load source-season player stats for usage/minutes baseline."""
    if not player_ids:
        return pd.DataFrame()
    sql = text("""
        SELECT pss.player_id, pss.season, pss.school_id AS source_school_id,
               pss.usage_rate AS source_usage_rate,
               pss.min_pct AS source_min_pct,
               pss.games_played AS source_games_played,
               p.position
        FROM player_season_stats pss
        JOIN players p ON p.id = pss.player_id
        WHERE pss.season = :season
          AND pss.player_id = ANY(:player_ids)
          AND pss.min_pct >= 10
        ORDER BY pss.player_id, pss.min_pct DESC
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"season": source_season, "player_ids": player_ids})
    # Keep highest-minutes row per player (their primary school)
    df = df.sort_values("source_min_pct", ascending=False).drop_duplicates("player_id")
    log.info("Source player stats: %d players for season %d", len(df), source_season)
    return df


# ---------------------------------------------------------------------------
# Tier assignment
# ---------------------------------------------------------------------------

def assign_competition_tiers(
    adj_em_series: pd.Series, season_series: pd.Series
) -> pd.Series:
    """Assign competition tier 1-4 based on adj_em percentile within each season.

    Tier 1 = high-major (top 25%), Tier 4 = low-major (bottom 20%).
    Derived per-season so tier boundaries shift with conference realignment.
    """
    tiers = pd.Series(index=adj_em_series.index, dtype="Int64")
    for season, grp in adj_em_series.groupby(season_series):
        n = len(grp)
        if n <= 1:
            pctile = pd.Series(0.5, index=grp.index)
        else:
            pctile = (grp.rank(method="average") - 1.0) / (n - 1.0)
        season_tiers = pd.Series(4, index=grp.index, dtype="Int64")
        season_tiers[pctile >= TIER_PERCENTILE_CUTS[0]] = 1
        season_tiers[(pctile >= TIER_PERCENTILE_CUTS[1]) & (pctile < TIER_PERCENTILE_CUTS[0])] = 2
        season_tiers[(pctile >= TIER_PERCENTILE_CUTS[2]) & (pctile < TIER_PERCENTILE_CUTS[1])] = 3
        tiers[grp.index] = season_tiers
    return tiers


# ---------------------------------------------------------------------------
# Training example construction
# ---------------------------------------------------------------------------

def _skill_state_feature(skill_states_json: Any, skill: str, default: float = 0.0) -> float:
    """Extract a single skill value from a skill_states JSON dict."""
    if not skill_states_json:
        return default
    if isinstance(skill_states_json, str):
        try:
            skill_states_json = json.loads(skill_states_json)
        except (json.JSONDecodeError, TypeError):
            return default
    return float(skill_states_json.get(skill, default))


def _skill_pctile_feature(skill_percentiles_json: Any, skill: str, default: float = 50.0) -> float:
    """Extract a skill percentile from skill_percentiles JSON."""
    if not skill_percentiles_json:
        return default
    if isinstance(skill_percentiles_json, str):
        try:
            skill_percentiles_json = json.loads(skill_percentiles_json)
        except (json.JSONDecodeError, TypeError):
            return default
    return float(skill_percentiles_json.get(skill, default))


def _usage_fraction_series(values: pd.Series, default: float = 0.20) -> pd.Series:
    """Return usage rates on 0-1 scale whether input is 0.20 or 20.0."""
    s = pd.to_numeric(values, errors="coerce").fillna(default)
    positive = s[s > 0]
    if not positive.empty and positive.median() > 1.0:
        s = s / 100.0
    return s.clip(0.0, 0.75)


_ROLE_USAGE_SKILL_FEATURES = [
    "shooting_3p",
    "free_throw_touch",
    "shot_creation_usage",
    "passing_creation",
    "turnover_avoidance",
    "offensive_rebounding",
    "defensive_rebounding",
    "steal_disruption",
    "block_rim_protection",
]

_POSITION_DUMMIES = ["PG", "s-PG", "CG", "WG", "WF", "S-PF", "PF/C", "C"]


def _build_role_usage_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build design matrix features for the role/usage Ridge model.

    Features:
      - usage_delta: dest_usage - source_usage (the causal input for the role effect)
      - per-skill state values (11 skills)
      - position dummies
      - source value_per_100 (controls for talent level)
    """
    source_usage = _usage_fraction_series(
        df.get("source_usage_rate", pd.Series(0.20, index=df.index)),
        default=0.20,
    )
    dest_usage = _usage_fraction_series(
        df.get("dest_usage_rate", pd.Series(0.20, index=df.index)),
        default=0.20,
    )
    features = pd.DataFrame(index=df.index)
    features["usage_delta"] = dest_usage - source_usage
    features["source_usage_rate"] = source_usage
    features["neutral_value"] = pd.to_numeric(
        df.get("neutral_value", pd.Series(0.0, index=df.index)), errors="coerce"
    ).fillna(0.0)

    def _parse_skill_states(val: Any) -> dict:
        if not val:
            return {}
        if isinstance(val, str):
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return {}
        if isinstance(val, dict):
            return val
        return {}

    skill_states = (
        df["skill_states"].map(_parse_skill_states)
        if "skill_states" in df.columns
        else pd.Series([{}] * len(df), index=df.index)
    )
    for skill in _ROLE_USAGE_SKILL_FEATURES:
        features[f"skill_{skill}"] = [
            float(state.get(skill, 0) or 0) for state in skill_states.tolist()
        ]

    position = (
        df.get("position", pd.Series("", index=df.index))
        .fillna("")
        .astype(str)
    )
    for pos in _POSITION_DUMMIES:
        features[f"pos_{pos.replace('/', '_').replace('-', '_')}"] = (position == pos).astype(float)

    return features.fillna(0.0)


def build_destination_training_examples(
    transfers_df: pd.DataFrame,
) -> pd.DataFrame:
    """Augment raw transfer rows with derived training targets and features.

    Returns the input dataframe with added columns:
      - value_delta: dest_off_rapm - neutral_value (what the context added)
      - usage_delta: dest_usage_rate - source_usage_rate
      - source_tier, dest_tier: competition tier labels
    """
    if transfers_df.empty:
        return transfers_df

    df = transfers_df.copy()

    # Value delta: how much did the destination context change realized value?
    df["value_delta"] = df["dest_total_rapm"].fillna(
        df["dest_off_rapm"] - df["dest_def_rapm"]
    ) - df["neutral_value"]

    # Usage delta: post minus pre
    df["usage_delta"] = (
        _usage_fraction_series(df["dest_usage_rate"], default=0.20)
        - _usage_fraction_series(df["source_usage_rate"], default=0.20)
    )

    # Competition tiers (per-season percentile of adj_em)
    # Prefer SQL-computed tiers from the full team-season distribution. The fallback
    # exists for unit tests and ad hoc frames that only carry adj_em columns.
    if "source_tier" not in df.columns or "dest_tier" not in df.columns:
        source_em = df["source_adj_em"].fillna(0.0)
        dest_em = df["dest_adj_em"].fillna(0.0)
        combined_em = pd.concat([source_em, dest_em])
        q = [combined_em.quantile(c) for c in TIER_PERCENTILE_CUTS]

        def _tier(val):
            if val >= q[0]:
                return 1
            if val >= q[1]:
                return 2
            if val >= q[2]:
                return 3
            return 4

        if "source_tier" not in df.columns:
            df["source_tier"] = source_em.apply(_tier)
        if "dest_tier" not in df.columns:
            df["dest_tier"] = dest_em.apply(_tier)

    df["source_tier"] = df["source_tier"].astype("Int64")
    df["dest_tier"] = df["dest_tier"].astype("Int64")
    df["tier_delta"] = df["source_tier"] - df["dest_tier"]  # positive = moving up (harder)

    log.info(
        "Training examples: %d rows, value_delta mean=%.3f std=%.3f",
        len(df),
        df["value_delta"].mean(),
        df["value_delta"].std(),
    )
    return df


# ---------------------------------------------------------------------------
# Delta component: Role / Usage
# ---------------------------------------------------------------------------

def fit_role_usage_model(training_df: pd.DataFrame) -> tuple[Ridge | None, StandardScaler | None, list[str], float]:
    """Fit Ridge regression for role/usage delta from historical transfer outcomes.

    Returns (model, scaler, feature_names, residual_std).
    Returns (None, None, [], 0.0) if insufficient training data.

    Target: role_usage_target when provided, otherwise value_delta. The production
    pipeline sets role_usage_target to value_delta net of the competition-tier
    adjustment so tier context is not learned once in Ridge and added again.
    """
    target_col = "role_usage_target" if "role_usage_target" in training_df.columns else "value_delta"
    df = training_df.dropna(subset=[target_col, "source_usage_rate", "dest_usage_rate"])
    df = df[df["dest_usage_rate"].notna() & df["source_usage_rate"].notna()]

    if len(df) < MIN_ROLE_USAGE_TRAIN_ROWS:
        log.warning(
            "Role/usage model: only %d clean training rows (need %d) — falling back to linear heuristic",
            len(df), MIN_ROLE_USAGE_TRAIN_ROWS,
        )
        return None, None, [], 0.0

    X_df = _build_role_usage_features(df)
    y = df[target_col].values
    feature_names = list(X_df.columns)

    scaler = StandardScaler()
    X = scaler.fit_transform(X_df.values)

    model = Ridge(alpha=5.0)
    model.fit(X, y)

    preds = model.predict(X)
    residual_std = float(np.std(y - preds))
    log.info(
        "Role/usage model fit: n=%d, residual_std=%.3f, features=%d",
        len(df), residual_std, len(feature_names),
    )
    return model, scaler, feature_names, residual_std


def compute_role_usage_delta(
    df: pd.DataFrame,
    model: Ridge | None,
    scaler: StandardScaler | None,
    feature_names: list[str],
    source_usage_col: str = "source_usage_rate",
    dest_usage_col: str = "expected_usage",
    linear_coef: float = 1.5,
) -> pd.Series:
    """Apply role/usage model to inference frame.

    Falls back to usage_delta * linear_coef when model is None.
    linear_coef should be estimated from data via estimate_usage_value_coef().
    """
    if df.empty:
        return pd.Series(dtype=float)

    df_work = df.copy()
    df_work["source_usage_rate"] = df_work[source_usage_col].fillna(
        df_work.get("prior_usage_rate", pd.Series(0.20, index=df_work.index))
    )
    df_work["dest_usage_rate"] = df_work[dest_usage_col].fillna(0.20)
    df_work["neutral_value"] = df_work["value_per_100"].fillna(0.0)
    # skill_states comes from neutral projection join
    df_work["skill_states"] = df_work.get("skill_states", pd.Series([{}] * len(df_work), index=df_work.index))

    if model is not None and scaler is not None:
        X_df = _build_role_usage_features(df_work)
        # Align to training feature set — zero-fill any missing column
        X_aligned = pd.DataFrame(0.0, index=X_df.index, columns=feature_names)
        for col in feature_names:
            if col in X_df.columns:
                X_aligned[col] = X_df[col]
        X = scaler.transform(X_aligned.values)
        delta = pd.Series(model.predict(X), index=df.index)
    else:
        usage_delta = (
            _usage_fraction_series(df_work["dest_usage_rate"], default=0.20)
            - _usage_fraction_series(df_work["source_usage_rate"], default=0.20)
        )
        delta = usage_delta * linear_coef

    return delta.clip(
        -DELTA_CAPS["role_usage_delta"], DELTA_CAPS["role_usage_delta"]
    )


# ---------------------------------------------------------------------------
# Delta component: Style / Skill Fit
# ---------------------------------------------------------------------------

def compute_style_skill_fit_delta(df: pd.DataFrame) -> pd.Series:
    """Rule-based style × skill interaction delta.

    Six explicit interactions, each bounded by its interaction weight,
    aggregated and bounded by DELTA_CAPS["style_skill_fit_delta"].

    Inputs expected in df (all nullable — falls back to 0.0 if missing):
      skill_pctile_shooting_3p, skill_pctile_passing_creation,
      skill_pctile_shot_creation_usage, skill_pctile_block_rim_protection,
      skill_pctile_offensive_rebounding, skill_pctile_defensive_rebounding,
      team_off_threepr, team_usage_crowding, team_def_rim_need,
      team_off_rim_pct, team_frontcourt_need
    """
    if df.empty:
        return pd.Series(dtype=float)

    def _col(name: str, default: float = 0.0) -> pd.Series:
        if name in df.columns:
            return df[name].fillna(default)
        return pd.Series(default, index=df.index)

    # Percentile columns: default to 50.0 (neutral) so a missing column contributes
    # zero delta rather than a maximum-negative value.
    def _pctile(name: str) -> pd.Series:
        return _col(name, 50.0)

    # 1. 3P shooting skill × team 3PA rate
    # High 3P shooter in a high-3PA system → amplified spacing/shooting value
    shooting_score = (
        (_pctile("skill_pctile_shooting_3p") - 50.0) / 50.0  # -1 to +1
        * _col("team_off_threepr", 0.35)                       # team's 3PA rate (0-1)
        * STYLE_SKILL_INTERACTION_WEIGHTS["shooting_3p_x_team_threepr"]
    )

    # 2. Passing creation × open ball-handler usage
    # Open usage = expected_usage at destination is meaningfully lower than typical primary-creator
    # Proxy: if dest usage < 0.25, there's room for a distributor to thrive
    dest_usage = _usage_fraction_series(_col("expected_usage", 0.20), default=0.20)
    open_usage_signal = (0.25 - dest_usage.clip(0, 0.35)) / 0.25  # 0→1 as usage opens up
    passing_score = (
        (_pctile("skill_pctile_passing_creation") - 50.0) / 50.0
        * open_usage_signal.clip(0, 1)
        * STYLE_SKILL_INTERACTION_WEIGHTS["passing_creation_x_open_usage"]
    )

    # 3. Shot creation × usage crowding (negative: crowded = suppressed creation value)
    usage_crowding = _col("team_usage_crowding", 0.0)  # positive = more crowded
    creation_crowding_score = (
        (_pctile("skill_pctile_shot_creation_usage") - 50.0) / 50.0
        * usage_crowding.clip(0, 1)
        * STYLE_SKILL_INTERACTION_WEIGHTS["shot_creation_x_usage_crowding"]  # negative weight
    )

    # 4. Block/rim protection × defensive rim need
    rim_def_need = _col("team_def_rim_need", 0.0)  # 0-1, higher = more rim protection needed
    block_score = (
        (_pctile("skill_pctile_block_rim_protection") - 50.0) / 50.0
        * rim_def_need.clip(0, 1)
        * STYLE_SKILL_INTERACTION_WEIGHTS["block_rim_x_def_rim_need"]
    )

    # 5. Offensive rebounding × rim-heavy offensive style
    off_rim_style = _col("team_off_rim_pct", 0.30)  # team rim attempt share
    off_reb_score = (
        (_pctile("skill_pctile_offensive_rebounding") - 50.0) / 50.0
        * (off_rim_style - 0.30).clip(0, 0.4) / 0.4  # above-average rim teams
        * STYLE_SKILL_INTERACTION_WEIGHTS["off_reb_x_rim_style"]
    )

    # 6. Defensive rebounding × frontcourt need
    frontcourt_need = _col("team_frontcourt_need", 0.0)  # 0-1
    def_reb_score = (
        (_pctile("skill_pctile_defensive_rebounding") - 50.0) / 50.0
        * frontcourt_need.clip(0, 1)
        * STYLE_SKILL_INTERACTION_WEIGHTS["def_reb_x_frontcourt_need"]
    )

    total = (
        shooting_score + passing_score + creation_crowding_score
        + block_score + off_reb_score + def_reb_score
    )
    return total.clip(
        -DELTA_CAPS["style_skill_fit_delta"], DELTA_CAPS["style_skill_fit_delta"]
    )


# ---------------------------------------------------------------------------
# Delta component: Roster Context
# ---------------------------------------------------------------------------

def compute_roster_context_delta(df: pd.DataFrame) -> pd.Series:
    """Monotonic mapping from gap_match score to roster_context_delta.

    gap_match 0-100 → delta -0.50 to +0.50 via 5-breakpoint piecewise-linear
    interpolation anchored at gap_match=50 → delta=0.

    Uses gap_match from df["gap_match"] (from player_team_fit_scores).
    Falls back to 0.0 if gap_match is missing.
    """
    if df.empty:
        return pd.Series(dtype=float)

    gm = df["gap_match"].fillna(50.0).clip(0, 100) if "gap_match" in df.columns else pd.Series(50.0, index=df.index)

    bp_x = np.array([p[0] for p in ROSTER_CONTEXT_BREAKPOINTS])
    bp_y = np.array([p[1] for p in ROSTER_CONTEXT_BREAKPOINTS])
    delta = pd.Series(np.interp(gm.values, bp_x, bp_y), index=df.index)

    return delta.clip(
        -DELTA_CAPS["roster_context_delta"], DELTA_CAPS["roster_context_delta"]
    )


# ---------------------------------------------------------------------------
# Delta component: Competition Level
# ---------------------------------------------------------------------------

def build_competition_tier_matrix(training_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build 4×4 tier transition matrix: conservative delta and empirical std.

    Observed transfer destinations are heavily selection-biased: players who land
    at high-major programs are often already positive outliers versus the neutral
    model. For all-pairs counterfactual scoring, use a monotonic basketball prior
    for the mean transition effect and only blend in empirical cell means when
    they agree with the expected direction.

    - Moving up in competition (larger tier number -> smaller tier number) is a
      conservative negative adjustment.
    - Moving down is a smaller positive adjustment.
    - Same-tier transitions are neutral.

    Cells with fewer than MIN_COMPETITION_TIER_ROWS rows still contribute to std
    only after regularization.
    Returns (mean_matrix, std_matrix) indexed by (source_tier, dest_tier) 1-4.
    """
    tiers = [1, 2, 3, 4]
    mean_mat = pd.DataFrame(0.0, index=tiers, columns=tiers)
    std_mat = pd.DataFrame(0.5, index=tiers, columns=tiers)  # default wide std

    if training_df.empty or "source_tier" not in training_df.columns:
        return mean_mat, std_mat

    df = training_df.dropna(subset=["value_delta", "source_tier", "dest_tier"])
    for st in tiers:
        for dt in tiers:
            cell = df[(df["source_tier"] == st) & (df["dest_tier"] == dt)]["value_delta"]
            n = len(cell)
            tier_delta = st - dt
            if tier_delta > 0:
                prior = -0.25 * tier_delta
            elif tier_delta < 0:
                prior = 0.15 * abs(tier_delta)
            else:
                prior = 0.0

            if n >= MIN_COMPETITION_TIER_ROWS:
                empirical = float(cell.mean())
                std_mat.loc[st, dt] = float(cell.std())
            else:
                # Regularize toward 0 with weight proportional to available n
                weight = n / MIN_COMPETITION_TIER_ROWS
                empirical = float(cell.mean()) * weight if n > 0 else 0.0

            if tier_delta > 0:
                empirical_same_direction = min(empirical, 0.0)
            elif tier_delta < 0:
                empirical_same_direction = max(empirical, 0.0)
            else:
                empirical_same_direction = 0.0

            mean_mat.loc[st, dt] = float(
                np.clip(
                    0.8 * prior + 0.2 * empirical_same_direction,
                    -DELTA_CAPS["competition_level_delta"],
                    DELTA_CAPS["competition_level_delta"],
                )
            )
            log.debug(
                "Tier %d→%d: n=%d prior=%.3f empirical=%.3f final=%.3f std=%.3f",
                st, dt, n, prior, empirical, mean_mat.loc[st, dt], std_mat.loc[st, dt],
            )

    log.info("Competition tier matrix built (rows=%d):\n%s", len(df), mean_mat.round(3))
    return mean_mat, std_mat


def compute_competition_level_delta(
    df: pd.DataFrame,
    mean_matrix: pd.DataFrame,
    std_matrix: pd.DataFrame,
) -> pd.Series:
    """Look up tier-transition delta from tier matrix.

    Expects df["source_tier"] and df["dest_tier"] (1-4).
    Falls back to 0.0 for missing tiers.
    """
    if df.empty:
        return pd.Series(dtype=float)

    source_tier = df["source_tier"].fillna(2).astype(int).clip(1, 4) if "source_tier" in df.columns else pd.Series(2, index=df.index)
    dest_tier = df["dest_tier"].fillna(2).astype(int).clip(1, 4) if "dest_tier" in df.columns else pd.Series(2, index=df.index)

    delta = pd.Series(index=df.index, dtype=float)
    for idx in df.index:
        st = int(source_tier.loc[idx])
        dt = int(dest_tier.loc[idx])
        try:
            delta.loc[idx] = float(mean_matrix.loc[st, dt])
        except (KeyError, TypeError):
            delta.loc[idx] = 0.0

    return delta.clip(
        -DELTA_CAPS["competition_level_delta"], DELTA_CAPS["competition_level_delta"]
    )


# ---------------------------------------------------------------------------
# Delta caps
# ---------------------------------------------------------------------------

def apply_delta_caps(df: pd.DataFrame) -> pd.DataFrame:
    """Enforce per-delta caps and total cap. Modifies in place and returns df."""
    for col in ["role_usage_delta", "style_skill_fit_delta", "roster_context_delta", "competition_level_delta"]:
        if col in df.columns:
            cap = DELTA_CAPS[col]
            df[col] = df[col].clip(-cap, cap)

    delta_cols = [c for c in ["role_usage_delta", "style_skill_fit_delta", "roster_context_delta", "competition_level_delta"] if c in df.columns]
    if delta_cols:
        total = df[delta_cols].sum(axis=1)
        total_cap = DELTA_CAPS["total_context_delta"]
        scale = (total_cap / total.abs().clip(lower=total_cap)).clip(upper=1.0)
        for col in delta_cols:
            df[col] = df[col] * scale
        df["total_context_delta"] = df[delta_cols].sum(axis=1)
    else:
        df["total_context_delta"] = 0.0

    return df


# ---------------------------------------------------------------------------
# Value translation and uncertainty
# ---------------------------------------------------------------------------

def translate_neutral_to_destination_value(df: pd.DataFrame) -> pd.DataFrame:
    """Sum neutral value + all deltas → destination_adjusted_value_per_100."""
    df = df.copy()
    df["destination_value_per_100"] = (
        df["value_per_100"]
        + df.get("total_context_delta", 0.0)
    )
    return df


def propagate_destination_uncertainty(
    df: pd.DataFrame,
    role_usage_residual_std: float,
    ci_calibration_scale: float = 1.0,
) -> pd.DataFrame:
    """Widen neutral CI by playing-time, delta-model, and staleness uncertainty sources.

    Conformal scale applied so that produced intervals aim for 80% nominal coverage.
    """
    df = df.copy()

    neutral_var = (
        ((df["value_ci_upper"].fillna(df["destination_value_per_100"]) -
          df["value_ci_lower"].fillna(df["destination_value_per_100"])) / 2.0) ** 2
    )

    pt_var = (
        ((df.get("minutes_ci_upper", df.get("expected_minutes", 0)) -
          df.get("minutes_ci_lower", df.get("expected_minutes", 0))).fillna(0.0)
         / (df.get("expected_minutes", 1.0).fillna(1.0).clip(lower=1.0))) ** 2
        * 0.25  # scale to value units (approximate)
    )

    delta_var = pd.Series(role_usage_residual_std ** 2, index=df.index)

    # Staleness and data quality penalty
    dqf = df.get("data_quality_flags", pd.Series([None] * len(df), index=df.index))
    staleness_multiplier = pd.Series(
        [1.5 if (isinstance(x, (dict, list, str)) and bool(x)) else 1.0 for x in dqf.tolist()],
        index=df.index, dtype=float,
    )

    role_conf = df.get("usage_role_confidence", pd.Series(0.75, index=df.index)).fillna(0.75)
    role_multiplier = (1.0 + (1.0 - role_conf.clip(0, 1)) * 0.25).clip(1.0, 1.25)

    if {"source_tier", "dest_tier"}.issubset(df.columns):
        tier_jump = (df["source_tier"].fillna(2).astype(float) - df["dest_tier"].fillna(2).astype(float)).abs()
    else:
        tier_jump = pd.Series(0.0, index=df.index)
    tier_multiplier = (1.0 + 0.15 * tier_jump.clip(0, 3)).clip(1.0, 1.45)

    games = df.get("source_games_played", pd.Series(30.0, index=df.index)).fillna(30.0)
    sparse_history_multiplier = pd.Series(
        np.where(games < 15, 1.15, 1.0), index=df.index, dtype=float
    )

    uncertainty_multiplier = (
        staleness_multiplier
        * role_multiplier
        * tier_multiplier
        * sparse_history_multiplier
        * ci_calibration_scale
    )
    df["uncertainty_multiplier"] = uncertainty_multiplier
    df["uncertainty_components"] = [
        {
            "data_quality_multiplier": round(float(staleness_multiplier.loc[idx]), 4),
            "role_confidence_multiplier": round(float(role_multiplier.loc[idx]), 4),
            "tier_jump_multiplier": round(float(tier_multiplier.loc[idx]), 4),
            "sparse_history_multiplier": round(float(sparse_history_multiplier.loc[idx]), 4),
            "ci_calibration_scale": round(float(ci_calibration_scale), 4),
        }
        for idx in df.index
    ]

    total_std = (np.sqrt(neutral_var + pt_var + delta_var) * uncertainty_multiplier).clip(lower=0.1)

    CI_Z = 1.2816  # ~80% interval, consistent with Phase 0
    df["value_ci_lower"] = df["destination_value_per_100"] - CI_Z * total_std
    df["value_ci_upper"] = df["destination_value_per_100"] + CI_Z * total_std

    return df


def calibrate_ci_scale(
    pred_df: pd.DataFrame, actual_col: str = "dest_total_rapm", nominal_coverage: float = 0.80
) -> float:
    """Compute conformal CI scale factor targeting nominal_coverage.

    Returns scale > 1.0 if current intervals are too narrow, < 1.0 (clamped to 1.0) if too wide.
    Falls back to 1.0 if insufficient labeled rows.
    """
    df = pred_df.dropna(subset=[actual_col, "value_ci_lower", "value_ci_upper"])
    if len(df) < 20:
        log.warning("CI calibration: only %d labeled rows — using scale=1.0", len(df))
        return 1.0

    covered = ((df[actual_col] >= df["value_ci_lower"]) & (df[actual_col] <= df["value_ci_upper"])).mean()
    log.info("CI calibration: pre-scale coverage=%.3f (target=%.2f)", covered, nominal_coverage)

    if covered >= nominal_coverage:
        return 1.0
    # Scale intervals wider: find scale such that coverage ≈ nominal
    # Simple bisection over scale in [1.0, 5.0]
    ci_half = (df["value_ci_upper"] - df["value_ci_lower"]) / 2.0
    center = (df["value_ci_upper"] + df["value_ci_lower"]) / 2.0
    for scale in np.linspace(1.0, 5.0, 40):
        cov = ((df[actual_col] >= center - ci_half * scale) & (df[actual_col] <= center + ci_half * scale)).mean()
        if cov >= nominal_coverage:
            log.info("CI calibration scale selected: %.3f (achieved coverage=%.3f)", scale, cov)
            return float(scale)
    return 5.0


# ---------------------------------------------------------------------------
# Rate and box score translation
# ---------------------------------------------------------------------------

def translate_rates_to_destination_stats(
    df: pd.DataFrame,
    team_context_df: pd.DataFrame,
) -> pd.DataFrame:
    """Convert neutral per-100/per-40 rates to destination per-game stats.

    Uses destination pace (adj_tempo) and expected_minutes.
    Falls back to neutral projected_box_score if pace is unavailable.
    """
    df = df.copy()
    # Join destination pace
    if "adj_tempo" not in df.columns and not team_context_df.empty:
        pace_lookup = team_context_df.set_index("school_id")["adj_tempo"].to_dict()
        df["adj_tempo"] = df["school_id"].map(pace_lookup)

    pace = df.get("adj_tempo", pd.Series(68.0, index=df.index)).fillna(68.0)
    minutes = df.get("expected_minutes", pd.Series(20.0, index=df.index)).fillna(20.0)

    # Team possessions available while the player is on the floor.
    possessions = pace * (minutes / 40.0)

    raw_boxes = df["projected_box_score"].tolist() if "projected_box_score" in df.columns else [None] * len(df)
    # projected_box_score stores per-40-minute rates (pts_per_40, reb_per_40, …).
    # Convert to per-game using minutes, not possessions.
    # possessions is used only for destination_total_value (a per-100-possession metric).
    per_40_to_per_game = (minutes / 40.0).tolist()

    source_usage = _usage_fraction_series(
        df.get("source_usage_rate", pd.Series(0.20, index=df.index)),
        default=0.20,
    ).clip(0.05, 0.50)
    dest_usage = _usage_fraction_series(
        df.get("expected_usage", pd.Series(0.20, index=df.index)),
        default=0.20,
    ).clip(0.05, 0.50)
    usage_factor = (dest_usage / source_usage).clip(0.80, 1.20)

    passing_pct = df.get("skill_pctile_passing_creation", pd.Series(50.0, index=df.index)).fillna(50.0)
    open_usage_signal = ((0.25 - dest_usage.clip(0, 0.35)) / 0.25).clip(0, 1)
    passing_context = ((passing_pct - 50.0) / 50.0 * open_usage_signal * 0.10).clip(-0.08, 0.08)
    assist_factor = (1.0 + 0.5 * (usage_factor - 1.0) + passing_context).clip(0.85, 1.15)
    turnover_factor = (1.0 + 0.75 * (usage_factor - 1.0)).clip(0.85, 1.20)

    positions = df.get("position", pd.Series("", index=df.index)).fillna("").astype(str).str.upper()
    is_big = positions.isin(["PF", "C", "PF/C", "S-PF"])
    frontcourt_need = df.get("team_frontcourt_need", pd.Series(0.0, index=df.index)).fillna(0.0).clip(0, 1)
    rim_need = df.get("team_def_rim_need", pd.Series(0.0, index=df.index)).fillna(0.0).clip(0, 1)
    rebound_factor = pd.Series(
        np.where(is_big, 1.0 + 0.08 * frontcourt_need, 1.0),
        index=df.index,
        dtype=float,
    ).clip(1.0, 1.08)
    block_factor = pd.Series(
        np.where(is_big, 1.0 + 0.08 * rim_need, 1.0),
        index=df.index,
        dtype=float,
    ).clip(1.0, 1.08)

    def _parse_box(x):
        if isinstance(x, str):
            try:
                return json.loads(x)
            except (json.JSONDecodeError, TypeError):
                return {}
        return x or {}

    parsed_boxes = [_parse_box(x) for x in raw_boxes]

    def _rate_multiplier(key: str, i: int) -> float:
        lower_key = key.lower()
        if lower_key.startswith("pts"):
            return float(usage_factor.iloc[i])
        if lower_key.startswith("ast"):
            return float(assist_factor.iloc[i])
        if lower_key.startswith("tov") or lower_key.startswith("to_"):
            return float(turnover_factor.iloc[i])
        if lower_key.startswith("reb") or lower_key.startswith("oreb") or lower_key.startswith("dreb"):
            return float(rebound_factor.iloc[i])
        if lower_key.startswith("blk"):
            return float(block_factor.iloc[i])
        return 1.0

    df["destination_box_score"] = [
        {
            k.replace("_per_40", "_per_game"): round(
                float(v or 0) * _rate_multiplier(k, i) * per_40_to_per_game[i], 2
            )
            for k, v in box.items()
        }
        if box else {}
        for i, box in enumerate(parsed_boxes)
    ]
    df["box_score_adjustments"] = [
        {
            "usage_factor": round(float(usage_factor.iloc[i]), 4),
            "assist_factor": round(float(assist_factor.iloc[i]), 4),
            "turnover_factor": round(float(turnover_factor.iloc[i]), 4),
            "rebound_factor": round(float(rebound_factor.iloc[i]), 4),
            "block_factor": round(float(block_factor.iloc[i]), 4),
        }
        for i in range(len(df))
    ]
    df["projected_possessions"] = possessions
    df["destination_total_value"] = (
        df["destination_value_per_100"] * possessions / 100.0
    )
    return df


# ---------------------------------------------------------------------------
# Inference frame construction
# ---------------------------------------------------------------------------

def build_destination_inference_frame(
    neutral_df: pd.DataFrame,
    pt_df: pd.DataFrame,
    team_context_df: pd.DataFrame,
    fit_context_df: pd.DataFrame,
    roster_df: pd.DataFrame,
    source_stats_df: pd.DataFrame,
    target_season: int,
    source_season: int,
) -> pd.DataFrame:
    """Join all inputs into one flat inference frame.

    Returns one row per (player_id, school_id) with all features needed for
    delta computation. Pairs missing PT rows are excluded (hard gate).

    Logs coverage: attempted pairs, dropped-PT pairs, final frame size.
    """
    if pt_df.empty:
        log.warning("Playing time projections empty — zero destination rows possible")
        return pd.DataFrame()
    if neutral_df.empty:
        log.warning("Neutral projections empty — zero destination rows possible")
        return pd.DataFrame()

    # PT is the spine — inner join ensures we only score pairs with opportunity estimate
    frame = pt_df.copy()
    frame["season"] = target_season
    n_pt_pairs = len(frame)

    # Neutral projection (one row per player — joined by player_id)
    neutral_cols = [
        "player_id", "neutral_season", "value_per_100", "value_ci_lower", "value_ci_upper",
        "projected_rates", "projected_box_score", "skill_states", "skill_percentiles",
        "uncertainty", "neutral_model_version",
    ]
    avail_neutral_cols = [c for c in neutral_cols if c in neutral_df.columns]
    frame = frame.merge(neutral_df[avail_neutral_cols], on="player_id", how="inner")
    n_after_neutral = len(frame)
    if n_pt_pairs > n_after_neutral:
        log.info(
            "Coverage: dropped %d pairs (no neutral projection) — %d remaining",
            n_pt_pairs - n_after_neutral, n_after_neutral,
        )

    # Source stats (prior usage, position)
    if not source_stats_df.empty:
        src_cols = [
            "player_id", "source_school_id", "source_usage_rate",
            "source_min_pct", "source_games_played", "position",
        ]
        avail_src = [c for c in src_cols if c in source_stats_df.columns]
        frame = frame.merge(source_stats_df[avail_src], on="player_id", how="left")

    # Team context (pace, style, tier)
    if not team_context_df.empty:
        tc_cols = [c for c in team_context_df.columns if c in [
            "school_id", "adj_em", "adj_o", "adj_d", "adj_tempo", "three_point_rate",
            "assist_rate", "off_threepr", "off_twoprimr", "off_style_rim_attack_pct",
            "def_style_rim_attack_pct", "def_orb", "off_orb", "system_label",
            "offense_memberships", "defense_memberships",
        ]]
        # Rename to avoid collision with season column
        tc = team_context_df[tc_cols].copy()
        frame = frame.merge(tc, on="school_id", how="left")

    # Pairwise fit context (gap_match, scheme_fit)
    if not fit_context_df.empty:
        fit_cols = [c for c in fit_context_df.columns if c in ["player_id", "school_id", "gap_match", "scheme_fit", "breakdown"]]
        frame = frame.merge(fit_context_df[fit_cols], on=["player_id", "school_id"], how="left")

    # Roster state features (extract position-specific open minutes)
    if not roster_df.empty:
        rst_cols = [c for c in roster_df.columns if c != "id"]
        frame = frame.merge(roster_df[rst_cols], on="school_id", how="left", suffixes=("", "_roster"))

    # Derived style interaction features
    frame = _add_style_interaction_features(frame, source_stats_df)

    # Competition tiers use the full school-season distribution, not the duplicated
    # player×school inference frame. Source/destination both use the context season
    # carried by team_context_df.
    if not team_context_df.empty and {"school_id", "adj_em", "season"}.issubset(team_context_df.columns):
        tier_df = team_context_df[["school_id", "season", "adj_em"]].drop_duplicates().copy()
        tier_df["competition_tier"] = assign_competition_tiers(tier_df["adj_em"], tier_df["season"])
        tier_lookup = tier_df.set_index("school_id")["competition_tier"].to_dict()
        frame["dest_tier"] = frame["school_id"].map(tier_lookup).astype("Int64")
        if "source_school_id" in frame.columns:
            frame["source_tier"] = frame["source_school_id"].map(tier_lookup).astype("Int64")

    if "source_tier" not in frame.columns:
        frame["source_tier"] = pd.Series([pd.NA] * len(frame), index=frame.index, dtype="Int64")
    if "dest_tier" not in frame.columns:
        frame["dest_tier"] = pd.Series([pd.NA] * len(frame), index=frame.index, dtype="Int64")

    log.info(
        "Inference frame: %d (player, school) pairs | %d unique players | %d unique schools",
        len(frame),
        frame["player_id"].nunique(),
        frame["school_id"].nunique(),
    )
    return frame


def _add_style_interaction_features(
    frame: pd.DataFrame, source_stats_df: pd.DataFrame
) -> pd.DataFrame:
    """Extract per-row skill percentiles and team style context into flat columns."""

    def _safe_json(val: Any) -> dict:
        if not val:
            return {}
        if isinstance(val, str):
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return {}
        if isinstance(val, dict):
            return val
        return {}

    # Skill percentiles → flat columns
    skills_of_interest = [
        "shooting_3p", "passing_creation", "shot_creation_usage",
        "block_rim_protection", "offensive_rebounding", "defensive_rebounding",
    ]
    for skill in skills_of_interest:
        col = f"skill_pctile_{skill}"
        if col not in frame.columns:
            frame[col] = frame["skill_percentiles"].apply(
                lambda x: float(_safe_json(x).get(skill, 50.0))
            )

    # Team style features (already joined from hoop_explorer_team_stats)
    if "off_threepr" in frame.columns:
        frame["team_off_threepr"] = frame["off_threepr"].fillna(0.35)
    elif "three_point_rate" in frame.columns:
        frame["team_off_threepr"] = frame["three_point_rate"].fillna(0.35)
    else:
        frame["team_off_threepr"] = 0.35

    if "off_style_rim_attack_pct" in frame.columns:
        frame["team_off_rim_pct"] = frame["off_style_rim_attack_pct"].fillna(0.30)
    elif "off_twoprimr" in frame.columns:
        frame["team_off_rim_pct"] = frame["off_twoprimr"].fillna(0.30)
    else:
        frame["team_off_rim_pct"] = 0.30

    # Defensive rim need: how much does this team need rim protection?
    # Proxy: low def_orb (struggle on defensive glass) and high opponent rim attack
    def_orb = frame.get("def_orb", pd.Series(0.27, index=frame.index)).fillna(0.27)
    frame["team_def_rim_need"] = (1.0 - def_orb.clip(0.15, 0.40) / 0.40).clip(0, 1)

    # Usage crowding: expected_usage relative to avg (high = more crowded at destination)
    dest_usage = _usage_fraction_series(
        frame.get("expected_usage", pd.Series(0.20, index=frame.index)),
        default=0.20,
    )
    frame["team_usage_crowding"] = ((dest_usage - 0.20) / 0.10).clip(0, 1)

    # Frontcourt need: low incoming big minutes relative to departed big minutes
    # Use open_usage_by_position if available (jsonb column from roster_state_features)
    def _big_open_usage(val: Any) -> float:
        d = _safe_json(val)
        return float(d.get("C", 0.0) or 0.0) + float(d.get("PF", 0.0) or 0.0)

    if "open_usage_by_position" in frame.columns:
        frame["team_frontcourt_need"] = frame["open_usage_by_position"].apply(_big_open_usage).clip(0, 1)
    else:
        frame["team_frontcourt_need"] = 0.0

    return frame


# ---------------------------------------------------------------------------
# Explanation payload
# ---------------------------------------------------------------------------

def build_explanation_payload(df: pd.DataFrame, source_season: int, target_season: int) -> pd.Series:
    """Build a JSON explanation dict per row summarizing all delta components."""
    def _col(name: str, default: float = 0.0) -> pd.Series:
        return df[name].fillna(default) if name in df.columns else pd.Series(default, index=df.index, dtype=float)

    # Vectorized: compute all scalar fields as arrays, zip into dicts at end
    f_r4 = (
        _col("value_per_100").round(4).tolist(),
        _col("role_usage_delta").round(4).tolist(),
        _col("style_skill_fit_delta").round(4).tolist(),
        _col("roster_context_delta").round(4).tolist(),
        _col("competition_level_delta").round(4).tolist(),
        _col("total_context_delta").round(4).tolist(),
        _col("destination_value_per_100").round(4).tolist(),
        _col("gap_match", 50.0).round(2).tolist(),
        _col("scheme_fit", 50.0).round(2).tolist(),
        _col("source_usage_rate").round(4).tolist(),
        _col("expected_usage").round(4).tolist(),
        _col("expected_minutes").round(2).tolist(),
        _col("projected_possessions").round(2).tolist(),
        _col("destination_total_value").round(4).tolist(),
        _col("uncertainty_multiplier", 1.0).round(4).tolist(),
        _col("team_off_threepr", 0.35).round(4).tolist(),
        _col("team_usage_crowding", 0.0).round(4).tolist(),
        _col("team_def_rim_need", 0.0).round(4).tolist(),
        _col("team_frontcourt_need", 0.0).round(4).tolist(),
    )
    src_tiers = _col("source_tier", 2.0).astype(int).tolist()
    dst_tiers = _col("dest_tier", 2.0).astype(int).tolist()
    neutral_mvs = (df["neutral_model_version"].fillna("").astype(str).tolist()
                   if "neutral_model_version" in df.columns else [""] * len(df))
    dqf_col = (df["data_quality_flags"] if "data_quality_flags" in df.columns
               else pd.Series([None] * len(df), index=df.index))
    dqf = [x or [] for x in dqf_col.tolist()]
    uncertainty_components_col = (
        df["uncertainty_components"] if "uncertainty_components" in df.columns
        else pd.Series([{}] * len(df), index=df.index)
    )
    uncertainty_components = [x or {} for x in uncertainty_components_col.tolist()]
    box_adjustments_col = (
        df["box_score_adjustments"] if "box_score_adjustments" in df.columns
        else pd.Series([{}] * len(df), index=df.index)
    )
    box_adjustments = [x or {} for x in box_adjustments_col.tolist()]
    n = len(df)

    payloads = [
        {
            "neutral_value_per_100": f_r4[0][i],
            "role_usage_delta": f_r4[1][i],
            "style_skill_fit_delta": f_r4[2][i],
            "roster_context_delta": f_r4[3][i],
            "competition_level_delta": f_r4[4][i],
            "total_context_delta": f_r4[5][i],
            "destination_adjusted_value_per_100": f_r4[6][i],
            "gap_match": f_r4[7][i],
            "scheme_fit": f_r4[8][i],
            "source_usage_rate": f_r4[9][i],
            "dest_expected_usage": f_r4[10][i],
            "dest_expected_minutes": f_r4[11][i],
            "projected_possessions": f_r4[12][i],
            "projected_total_value": f_r4[13][i],
            "uncertainty_adjustment": f_r4[14][i],
            "source_tier": src_tiers[i],
            "dest_tier": dst_tiers[i],
            "source_season": source_season,
            "target_season": target_season,
            "neutral_model_version": neutral_mvs[i],
            "minutes_source_model_version": PLAYING_TIME_MODEL_VERSION,
            "playing_time_model_version": PLAYING_TIME_MODEL_VERSION,
            "team_style_features_used": {
                "team_off_threepr": f_r4[15][i],
                "team_usage_crowding": f_r4[16][i],
                "team_def_rim_need": f_r4[17][i],
                "team_frontcourt_need": f_r4[18][i],
            },
            "box_score_adjustments": box_adjustments[i],
            "uncertainty_components": uncertainty_components[i],
            "data_quality_flags": dqf[i],
        }
        for i in range(n)
    ]
    return pd.Series(payloads, index=df.index)


# ---------------------------------------------------------------------------
# DB write
# ---------------------------------------------------------------------------

def build_destination_projection_records(
    df: pd.DataFrame,
    explanation_series: pd.Series,
    source_season: int,
    target_season: int,
) -> list[tuple]:
    """Build upsert tuples for player_projections (destination mode).

    Order must match _UPSERT_DESTINATION_SQL column list.
    """
    now = datetime.now(tz=timezone.utc)
    expires = now + timedelta(days=EXPIRES_DAYS)

    def _col(name: str, default=None):
        return df[name].tolist() if name in df.columns else [default] * len(df)

    def _json_col(name: str) -> list[str]:
        col = df[name] if name in df.columns else pd.Series([None] * len(df), index=df.index)
        return [json.dumps(x or {}) for x in col.tolist()]

    player_ids = [int(x) for x in df["player_id"].tolist()]
    school_ids = [int(x) for x in df["school_id"].tolist()]
    dest_vals = [float(x or 0) for x in _col("destination_value_per_100", 0)]
    ci_lowers = [float(x or 0) for x in _col("value_ci_lower", 0)]
    ci_uppers = [float(x or 0) for x in _col("value_ci_upper", 0)]
    exp_mins = _col("expected_minutes")   # nullable float — keep as-is
    exp_usages = _col("expected_usage")   # nullable float — keep as-is
    box_scores = _json_col("destination_box_score")
    proj_rates = _json_col("projected_rates")
    skill_states = _json_col("skill_states")
    skill_pcts = _json_col("skill_percentiles")
    uncertainties = _json_col("uncertainty")
    explanations = [json.dumps(explanation_series.iloc[i] if i < len(explanation_series) else {})
                    for i in range(len(df))]

    return list(zip(
        player_ids, school_ids,
        [int(target_season)] * len(df),
        ["destination"] * len(df),
        dest_vals, ci_lowers, ci_uppers,
        exp_mins, exp_usages,
        box_scores, proj_rates, skill_states, skill_pcts, uncertainties,
        explanations,
        [MODEL_VERSION] * len(df),
        [now] * len(df),
        [expires] * len(df),
    ))


def upsert_destination_projections(engine: Engine, records: list[tuple]) -> int:
    """Upsert destination rows to player_projections. Returns rows written."""
    if not records:
        log.info("No destination projection records to write")
        return 0

    raw_conn = engine.raw_connection()
    try:
        with raw_conn.cursor() as cur:
            execute_values(cur, _UPSERT_DESTINATION_SQL, records)
        raw_conn.commit()
    finally:
        raw_conn.close()

    log.info("Upserted %d destination projection rows", len(records))
    return len(records)


# ---------------------------------------------------------------------------
# Data-driven coefficient estimation
# ---------------------------------------------------------------------------

def estimate_usage_value_coef(
    neutral_df: pd.DataFrame,
    source_stats_df: pd.DataFrame,
    fallback: float = 1.5,
) -> float:
    """OLS slope of value_per_100 ~ source_usage_rate across current portal candidates.

    Selection-biased (better players get more usage), but gives a data-grounded
    calibration vs a hardcoded constant. Falls back to `fallback` if n < 50.
    """
    if neutral_df.empty or source_stats_df.empty:
        return fallback
    merged = (
        neutral_df[["player_id", "value_per_100"]]
        .merge(source_stats_df[["player_id", "source_usage_rate"]], on="player_id")
        .dropna(subset=["value_per_100", "source_usage_rate"])
    )
    merged["source_usage_rate"] = _usage_fraction_series(
        merged["source_usage_rate"], default=0.20
    )
    merged = merged[merged["source_usage_rate"].between(0.05, 0.50)]
    if len(merged) < 50:
        log.info(
            "Usage coefficient: only %d overlap rows, using fallback=%.2f",
            len(merged), fallback,
        )
        return fallback
    X = merged["source_usage_rate"].values
    y = merged["value_per_100"].values
    X_c = X - X.mean()
    denom = float(np.dot(X_c, X_c))
    coef = float(np.dot(X_c, y) / denom) if denom > 1e-10 else fallback
    log.info("Usage→value coefficient estimated from data: %.3f (n=%d)", coef, len(merged))
    return coef


# ---------------------------------------------------------------------------
# Rolling-origin cross-validation
# ---------------------------------------------------------------------------

def run_rolling_origin_cv(
    training_df: pd.DataFrame,
    min_eval_rows: int = 3,
) -> dict[str, float]:
    """3-fold rolling-origin CV for the role/usage delta model.

    Fold structure: eval = one dest_season, train = all prior dest_seasons.
    Returns per-fold RMSE/R² and total_resid_std (mean fold RMSE used as
    the maybe_promote gate metric). Returns cv_skipped=1.0 if insufficient data.
    """
    if training_df.empty or "dest_season" not in training_df.columns:
        return {"cv_skipped": 1.0, "total_resid_std": 999.0}

    clean = training_df.dropna(
        subset=["value_delta", "source_usage_rate", "dest_usage_rate", "dest_season"]
    )
    seasons = sorted(clean["dest_season"].unique())

    if len(seasons) < 2:
        log.info("CV skipped: only %d distinct dest_seasons in training data", len(seasons))
        return {"cv_skipped": 1.0, "total_resid_std": 999.0}

    fold_results: dict[str, float] = {}
    fold_rmses: list[float] = []

    for fold_idx, eval_season in enumerate(seasons[1:], 1):
        train_seasons_cv = [s for s in seasons if s < eval_season]
        train = clean[clean["dest_season"].isin(train_seasons_cv)]
        eval_df = clean[clean["dest_season"] == eval_season].copy()

        if len(eval_df) < min_eval_rows:
            log.info(
                "CV fold %d (eval=%d): only %d eval rows, skipping",
                fold_idx, eval_season, len(eval_df),
            )
            continue

        model, scaler, feature_names, _ = fit_role_usage_model(train)

        # Map dest_usage_rate → expected_usage for compute_role_usage_delta
        eval_df["expected_usage"] = eval_df["dest_usage_rate"]
        eval_df["value_per_100"] = eval_df.get("neutral_value", pd.Series(0.0, index=eval_df.index))
        preds = compute_role_usage_delta(eval_df, model, scaler, feature_names)

        actuals = eval_df["value_delta"].values
        residuals = actuals - preds.values
        rmse = float(np.sqrt(np.mean(residuals ** 2)))
        ss_res = float(np.sum(residuals ** 2))
        ss_tot = float(np.sum((actuals - actuals.mean()) ** 2))
        r2 = float(1.0 - ss_res / ss_tot) if ss_tot > 1e-10 else 0.0

        fold_results[f"fold{fold_idx}_rmse"] = rmse
        fold_results[f"fold{fold_idx}_r2"] = r2
        fold_results[f"fold{fold_idx}_n_eval"] = float(len(eval_df))
        fold_rmses.append(rmse)

        log.info(
            "CV fold %d (train=%s eval=%d): n_train=%d n_eval=%d rmse=%.3f r2=%.3f",
            fold_idx, train_seasons_cv, eval_season, len(train), len(eval_df), rmse, r2,
        )

    if fold_rmses:
        fold_results["total_resid_std"] = float(np.mean(fold_rmses))
    else:
        fold_results["cv_skipped"] = 1.0
        fold_results["total_resid_std"] = 999.0

    return fold_results


def compute_cohort_validation(
    training_df: pd.DataFrame,
    min_cohort_n: int = 20,
) -> dict[str, Any]:
    """Slice-based held-out validation using rolling-origin CV predictions.

    Loops the same fold structure as run_rolling_origin_cv, collects held-out
    (actual, predicted) pairs, then reports RMSE and Spearman correlation for:
    - Tier direction: up (player moves to harder competition), same, down
    - Position group: guard (PG/SG), wing (SF), big (PF/C)
    - Usage context: high-usage scaling down, usage increase at destination
    """
    if training_df.empty or "dest_season" not in training_df.columns:
        return {}

    clean = training_df.dropna(
        subset=["value_delta", "source_usage_rate", "dest_usage_rate", "dest_season"]
    )
    seasons = sorted(clean["dest_season"].unique())
    if len(seasons) < 2:
        return {}

    # Collect held-out predictions from all CV folds
    records: list[pd.DataFrame] = []
    for eval_season in seasons[1:]:
        train_seasons_cv = [s for s in seasons if s < eval_season]
        train = clean[clean["dest_season"].isin(train_seasons_cv)]
        eval_df = clean[clean["dest_season"] == eval_season].copy()
        if len(eval_df) < 3:
            continue
        model, scaler, feature_names, _ = fit_role_usage_model(train)
        eval_df["expected_usage"] = eval_df["dest_usage_rate"]
        eval_df["value_per_100"] = eval_df.get(
            "neutral_value", pd.Series(0.0, index=eval_df.index)
        )
        preds = compute_role_usage_delta(eval_df, model, scaler, feature_names)
        eval_df = eval_df.copy()
        eval_df["_predicted"] = preds.values
        records.append(eval_df)

    if not records:
        return {}

    pool = pd.concat(records, ignore_index=True)
    results: dict[str, Any] = {}

    def _cohort_stats(mask: pd.Series, name: str) -> None:
        sub = pool[mask.reindex(pool.index, fill_value=False)]
        n = len(sub)
        results[f"cohort_{name}_n"] = n
        if n < min_cohort_n:
            results[f"cohort_{name}_spearman"] = None
            results[f"cohort_{name}_rmse"] = None
            log.info("Cohort %s: n=%d (below min_cohort_n=%d) — skipped", name, n, min_cohort_n)
            return
        corr, _ = spearmanr(sub["value_delta"], sub["_predicted"])
        rmse = float(np.sqrt(np.mean((sub["value_delta"].values - sub["_predicted"].values) ** 2)))
        results[f"cohort_{name}_spearman"] = round(float(corr), 3)
        results[f"cohort_{name}_rmse"] = round(rmse, 3)
        log.info(
            "Cohort %-30s n=%-4d Spearman=%+.3f RMSE=%.3f",
            name, n, corr, rmse,
        )

    # Tier direction (positive tier_delta = moving to harder competition)
    if {"source_tier", "dest_tier"}.issubset(pool.columns):
        tier_delta = (
            pool["source_tier"].fillna(2).astype(float)
            - pool["dest_tier"].fillna(2).astype(float)
        )
        _cohort_stats(tier_delta > 0, "tier_up")
        _cohort_stats(tier_delta == 0, "tier_same")
        _cohort_stats(tier_delta < 0, "tier_down")

    # Position groups
    if "position" in pool.columns:
        pos = pool["position"].fillna("").astype(str).str.upper()
        _cohort_stats(pos.isin(["PG", "SG"]), "guard")
        _cohort_stats(pos.isin(["SF"]), "wing")
        _cohort_stats(pos.isin(["PF", "C", "PF/C", "S-PF"]), "big")

    # Usage context
    src_u = _usage_fraction_series(pool["source_usage_rate"], default=0.20)
    dst_u = _usage_fraction_series(pool["dest_usage_rate"], default=0.20)
    usage_delta = dst_u - src_u
    _cohort_stats((src_u > 0.25) & (usage_delta < -0.05), "high_usage_scaling_down")
    _cohort_stats(usage_delta > 0.05, "usage_increase")

    return results


# ---------------------------------------------------------------------------
# High-level pipeline entry point (called by run_destination_projection.py)
# ---------------------------------------------------------------------------

def run_destination_projection(
    engine: Engine,
    *,
    target_season: int,
    source_season: int,
    train_seasons: list[int],
    player_id_subset: list[int] | None = None,
    school_id_subset: list[int] | None = None,
    portal_only: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """End-to-end destination projection pipeline.

    Returns a metrics dict for MLflow logging.
    """
    # --- 1. Candidate pool ---
    if player_id_subset:
        candidate_ids = player_id_subset
        log.info("Using explicit player_id_subset: %d players", len(candidate_ids))
    elif portal_only:
        candidate_ids = load_portal_candidates(engine, fit_context_season=source_season)
    else:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT DISTINCT player_id FROM player_projections WHERE school_id IS NULL AND projection_mode='neutral'")
            ).fetchall()
        candidate_ids = [int(r[0]) for r in rows]
        log.info("All-player mode: %d players", len(candidate_ids))

    if not candidate_ids:
        log.warning("No candidate players — aborting")
        return {"n_candidates": 0, "n_records_written": 0}

    # --- 2. Load all inputs ---
    neutral_df = load_neutral_projections(engine, candidate_ids, target_season)
    pt_df = load_playing_time_projections(engine, candidate_ids, target_season)

    if pt_df.empty:
        log.warning(
            "playing_time_projections is empty for season=%d model=%s — "
            "run run_playing_time.py first",
            target_season, PLAYING_TIME_MODEL_VERSION,
        )
        return {"n_candidates": len(candidate_ids), "n_records_written": 0, "skipped": "no_pt_rows"}

    team_context_df = load_destination_team_context(engine, source_season)
    fit_context_df = load_pairwise_fit_context(engine, candidate_ids, source_season)
    roster_df = load_roster_state_features(engine, source_season)
    source_stats_df = load_source_player_stats(engine, candidate_ids, source_season)

    # Apply school_id filter if requested
    if school_id_subset:
        pt_df = pt_df[pt_df["school_id"].isin(school_id_subset)]
        log.info("School filter applied: %d PT rows remain", len(pt_df))

    # --- 3. Historical training data for delta models ---
    training_df = load_historical_transfer_outcomes(engine, train_seasons)
    training_df = build_destination_training_examples(training_df)

    # --- 4. Fit delta models ---
    tier_mean_mat, tier_std_mat = build_competition_tier_matrix(training_df)
    if not training_df.empty:
        training_df = training_df.copy()
        training_df["competition_level_delta"] = compute_competition_level_delta(
            training_df, tier_mean_mat, tier_std_mat
        )
        training_df["role_usage_target"] = (
            training_df["value_delta"] - training_df["competition_level_delta"]
        )
    role_model, role_scaler, role_feature_names, role_residual_std = fit_role_usage_model(training_df)

    # --- 4a. Data-driven usage coefficient + rolling-origin CV ---
    usage_coef = estimate_usage_value_coef(neutral_df, source_stats_df)
    cv_metrics = run_rolling_origin_cv(training_df)
    log.info("CV metrics: %s", cv_metrics)
    cohort_metrics = compute_cohort_validation(training_df)
    log.info("Cohort validation: %s", cohort_metrics)

    # --- 5. Build inference frame ---
    frame = build_destination_inference_frame(
        neutral_df=neutral_df,
        pt_df=pt_df,
        team_context_df=team_context_df,
        fit_context_df=fit_context_df,
        roster_df=roster_df,
        source_stats_df=source_stats_df,
        target_season=target_season,
        source_season=source_season,
    )

    if frame.empty:
        log.warning("Inference frame empty — check that neutral projections and PT rows overlap")
        return {"n_candidates": len(candidate_ids), "n_records_written": 0, "skipped": "empty_frame"}

    # --- 6. Compute deltas ---
    frame["role_usage_delta"] = compute_role_usage_delta(
        frame, role_model, role_scaler, role_feature_names, linear_coef=usage_coef
    )
    frame["style_skill_fit_delta"] = compute_style_skill_fit_delta(frame)
    frame["roster_context_delta"] = compute_roster_context_delta(frame)
    frame["competition_level_delta"] = compute_competition_level_delta(
        frame, tier_mean_mat, tier_std_mat
    )

    # --- 7. Caps ---
    frame = apply_delta_caps(frame)

    # --- 8. Final value ---
    frame = translate_neutral_to_destination_value(frame)

    # --- 9. CI calibration on training set (if labeled rows available) ---
    ci_scale = 1.0
    if not training_df.empty and "dest_total_rapm" in training_df.columns:
        # Build a training-time prediction frame for calibration
        # (rough proxy: neutral value + role_delta on training rows)
        train_pred = training_df[["dest_total_rapm", "neutral_value"]].copy()
        train_pred["destination_value_per_100"] = train_pred["neutral_value"]
        train_pred["value_ci_lower"] = train_pred["neutral_value"] - 2.0
        train_pred["value_ci_upper"] = train_pred["neutral_value"] + 2.0
        ci_scale = calibrate_ci_scale(train_pred, actual_col="dest_total_rapm")

    # --- 10. Uncertainty propagation ---
    frame = propagate_destination_uncertainty(frame, role_residual_std, ci_scale)

    # --- 11. Rate / box translation ---
    frame = translate_rates_to_destination_stats(frame, team_context_df)

    # --- 12. Explanation ---
    explanation = build_explanation_payload(frame, source_season, target_season)

    # --- 13. Write ---
    records = build_destination_projection_records(frame, explanation, source_season, target_season)

    n_written = 0
    if not dry_run:
        n_written = upsert_destination_projections(engine, records)
    else:
        log.info("Dry run — would write %d destination projection rows", len(records))
        n_written = len(records)

    metrics = {
        "n_candidates": len(candidate_ids),
        "n_pt_pairs": len(pt_df),
        "n_inference_rows": len(frame),
        "n_records_written": n_written,
        "n_train_rows": len(training_df),
        "role_residual_std": role_residual_std,
        "usage_value_coef": usage_coef,
        "ci_calibration_scale": ci_scale,
        "delta_role_mean": float(frame["role_usage_delta"].mean()),
        "delta_style_mean": float(frame["style_skill_fit_delta"].mean()),
        "delta_roster_mean": float(frame["roster_context_delta"].mean()),
        "delta_competition_mean": float(frame["competition_level_delta"].mean()),
        "delta_total_mean": float(frame["total_context_delta"].mean()),
        "delta_total_std": float(frame["total_context_delta"].std()),
        "destination_value_mean": float(frame["destination_value_per_100"].mean()),
        "destination_value_std": float(frame["destination_value_per_100"].std()),
        **cv_metrics,
        **cohort_metrics,
    }
    log.info("Destination projection complete: %s", metrics)
    return metrics
