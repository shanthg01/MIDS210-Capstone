"""Playing Time / Rotation model.

The model owns opportunity: expected minutes, usage, uncertainty, and the
derived Role Fit score that gets synced back onto player_team_fit_scores.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

import numpy as np
import pandas as pd
from psycopg2.extras import execute_values
from sklearn.ensemble import (
    ExtraTreesClassifier,
    ExtraTreesRegressor,
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
)
from sklearn.metrics import mean_absolute_error, mean_squared_error

from portalpoint.modeling.minutes import minutes_per_game_from_min_pct, minutes_share_from_min_pct

try:
    from lightgbm import LGBMClassifier, LGBMRegressor
except ImportError:  # pragma: no cover - kept for local fallback in minimal envs
    LGBMClassifier = None
    LGBMRegressor = None

MODEL_VERSION = "playing-time-rotation-v2"
PLAYER_PROJECTION_MODEL_VERSION = "player-proj-phase2a-fcast-v1"
DEFAULT_MODEL_FAMILY = "hist_gradient_boosting"
MODEL_FAMILIES = {"extra_trees", "hist_gradient_boosting", "lightgbm"}
MIN_TRAIN_GAMES = 5  # matches player_projection.py's MIN_GAMES label-reliability convention
STANDARD_TEAM_MINUTES = 200.0
MAX_PLAYER_MINUTES = 40.0
SYNTHETIC_SCHOOL_ID_FLOOR = 9_900_000
# Server-side guard against a query that hangs indefinitely (e.g. Issue #53 follow-up:
# a chunk silently hung for 10+ hours with no traceback — the connection had no
# statement_timeout, so a dead/half-open connection blocked forever on a socket read
# instead of erroring out). Generous because sync_role_fit_scores writes into
# player_team_fit_scores (~15GB, low cache-hit ratio on the current RDS instance
# class) and can legitimately take many minutes per chunk.
RAW_CONNECTION_STATEMENT_TIMEOUT_MS = 600_000

# PERF: prepare_playing_time_frame/derive_usage_role/compute_role_fit_score/
# allocate_displaced_minutes/uncertainty_multiplier/data_quality_flags previously used
# per-row .apply(axis=1)/.iterrows()+JSON-dict parsing (same wall Scheme Fit hit going
# all-pairs, see CLAUDE.md TODO #5). Vectorized into whole-frame numpy/pandas twins
# (derive_usage_roles, compute_role_fit_scores, uncertainty_multipliers,
# allocate_displaced_minutes_batch, data_quality_flags_batch) — the scalar per-row
# functions are now thin single-row wrappers around them, kept for tests/callers that
# genuinely have one row. Verified bit-for-bit identical output against a pre-refactor
# real-data snapshot before/after.

NUMERIC_FEATURES = [
    "value_per_100",
    "projection_ci_width",
    "prior_minutes",
    "prior_minutes_share",
    "prior_usage_rate",
    "prior_games_played",
    "prior_points_per_game",
    "prior_rebounds_per_game",
    "prior_assists_per_game",
    "prior_college_seasons",
    "is_no_prior_college_season",
    "career_season_index",
    "years_since_first_observed",
    "position_is_pg",
    "position_is_sg",
    "position_is_sf",
    "position_is_pf",
    "position_is_c",
    "position_group_guard",
    "position_group_wing",
    "position_group_forward",
    "position_group_big",
    "height_inches",
    "team_adj_em",
    "team_adj_o",
    "team_adj_d",
    "team_adj_tempo",
    "roster_player_count",
    "same_school_baseline",
    "roster_open_minutes",
    "returning_minutes_share_position",
    "open_minutes_share_position",
    "returning_minutes_position",
    "departing_minutes_position",
    "incoming_transfer_minutes_position",
    "open_usage_position",
    "same_position_usage_total",
    "same_position_usage_max",
    "same_position_usage_mean",
    "prior_team_usage_total",
    "prior_team_minutes_mean",
    "same_position_minutes_mean",
    "position_crowding_ratio",
    "opportunity_to_prior_minutes_ratio",
    "open_minutes_minus_returning_position",
    "departing_minus_incoming_transfer_minutes",
    "returning_production_per_rotation_player",
    "returning_production",
    "returning_player_impact",
    "prior_team_minutes",
    "prior_team_rotation_hhi",
    "prior_team_rotation_players",
    "same_position_prior_minutes",
    "same_position_prior_max_minutes",
    "same_position_prior_count",
    "position_opportunity_delta",
    "position_confidence",
    "archetype_confidence",
    "is_transfer",
    "candidate_changes_school",
    "is_portal_candidate",
    "transfer_match_confidence",
    "sample_reliability",
    "projection_reliability",
    "roster_reliability",
]

PLAYING_TIME_EXCLUDED_FEATURES = {"gap_match", "scheme_fit"}
MINUTES_FEATURES = list(NUMERIC_FEATURES)
USAGE_FEATURES = [
    feature
    for feature in NUMERIC_FEATURES
    if feature
    not in {
        "prior_college_seasons",
        "is_no_prior_college_season",
        "career_season_index",
        "years_since_first_observed",
    }
]

FIT_SCORE_COLUMNS = ["player_id", "school_id", "season", "gap_match", "scheme_fit"]

TRAINING_SQL = """
WITH neutral_projection AS (
    SELECT DISTINCT ON (player_id, season)
        player_id,
        season,
        value_per_100,
        value_ci_lower,
        value_ci_upper,
        skill_percentiles,
        uncertainty,
        computed_at,
        model_version
    FROM player_projections
    WHERE school_id IS NULL
      AND projection_mode = 'neutral'
    ORDER BY
        player_id,
        season,
        CASE WHEN model_version = %(projection_model_version)s THEN 0 ELSE 1 END,
        computed_at DESC
),
roster_counts AS (
    SELECT school_id, season, count(*) AS roster_player_count
    FROM roster_baseline_members
    GROUP BY school_id, season
),
prior_team_context AS (
    SELECT
        school_id,
        season + 1 AS season,
        sum(min_pct / 100.0 * 40.0) AS prior_team_minutes,
        CASE
            WHEN sum(min_pct / 100.0 * 40.0) > 0
                THEN sum(power(min_pct / 100.0 * 40.0, 2)) / power(sum(min_pct / 100.0 * 40.0), 2)
            ELSE NULL
        END AS prior_team_rotation_hhi,
        count(*) FILTER (WHERE min_pct >= 50) AS prior_team_rotation_players,
        sum(usage_rate) AS prior_team_usage_total
    FROM player_season_stats
    WHERE min_pct IS NOT NULL
    GROUP BY school_id, season
),
prior_position_context AS (
    SELECT
        pss.school_id,
        pss.season + 1 AS season,
        p.position,
        sum(pss.min_pct / 100.0 * 40.0) AS same_position_prior_minutes,
        max(pss.min_pct / 100.0 * 40.0) AS same_position_prior_max_minutes,
        count(*) AS same_position_prior_count,
        sum(pss.usage_rate) AS same_position_usage_total,
        max(pss.usage_rate) AS same_position_usage_max
    FROM player_season_stats pss
    JOIN players p ON p.id = pss.player_id
    WHERE pss.min_pct IS NOT NULL
    GROUP BY pss.school_id, pss.season, p.position
),
player_history AS (
    SELECT
        cur.player_id,
        cur.season,
        count(DISTINCT prev.season) AS prior_college_seasons,
        min(hist.season) AS first_observed_season
    FROM (SELECT DISTINCT player_id, season FROM player_season_stats) cur
    LEFT JOIN (SELECT DISTINCT player_id, season FROM player_season_stats) prev
        ON prev.player_id = cur.player_id
       AND prev.season < cur.season
    LEFT JOIN (SELECT DISTINCT player_id, season FROM player_season_stats) hist
        ON hist.player_id = cur.player_id
    GROUP BY cur.player_id, cur.season
),
transfer_pairs AS (
    SELECT player_id, from_school_id, to_school_id, season, match_confidence
    FROM transfer_portal_events
    WHERE player_id IS NOT NULL
      AND match_status = 'matched'
),
hep_dedup AS (
    SELECT DISTINCT ON (player_id, season)
        player_id,
        season,
        pos_confidence_pg,
        pos_confidence_sg,
        pos_confidence_sf,
        pos_confidence_pf,
        pos_confidence_c
    FROM hoop_explorer_player_stats
    WHERE player_id IS NOT NULL
    ORDER BY player_id, season, updated_at DESC, id DESC
)
SELECT
    pss.player_id,
    pss.school_id,
    pss.season,
    p.position,
    p.height_inches,
    pss.min_pct AS actual_min_pct,
    pss.usage_rate AS actual_usage_rate,
    pss.games_played AS actual_games_played,
    prior.school_id AS prior_school_id,
    prior.min_pct AS prior_min_pct,
    prior.usage_rate AS prior_usage_rate,
    prior.games_played AS prior_games_played,
    prior.points_per_game AS prior_points_per_game,
    prior.rebounds_per_game AS prior_rebounds_per_game,
    prior.assists_per_game AS prior_assists_per_game,
    ph.prior_college_seasons,
    ph.first_observed_season,
    np.value_per_100,
    np.value_ci_lower,
    np.value_ci_upper,
    np.skill_percentiles,
    np.uncertainty,
    np.model_version AS neutral_projection_model_version,
    fit.gap_match,
    fit.scheme_fit,
    tss.adj_em AS team_adj_em,
    tss.adj_o AS team_adj_o,
    tss.adj_d AS team_adj_d,
    tss.adj_tempo AS team_adj_tempo,
    rc.roster_player_count,
    rsf.returning_minutes_by_position,
    rsf.departing_minutes_by_position,
    rsf.incoming_transfer_minutes_by_position,
    rsf.open_minutes_by_position,
    rsf.open_usage_by_position,
    rsf.returning_production,
    rsf.returning_player_impact,
    ptc.prior_team_minutes,
    ptc.prior_team_rotation_hhi,
    ptc.prior_team_rotation_players,
    ptc.prior_team_usage_total,
    ppc.same_position_prior_minutes,
    ppc.same_position_prior_max_minutes,
    ppc.same_position_prior_count,
    ppc.same_position_usage_total,
    ppc.same_position_usage_max,
    hep.pos_confidence_pg,
    hep.pos_confidence_sg,
    hep.pos_confidence_sf,
    hep.pos_confidence_pf,
    hep.pos_confidence_c,
    pa.archetype_label,
    pa.confidence AS archetype_confidence,
    tp.match_confidence AS transfer_match_confidence,
    CASE
        WHEN prior.school_id IS NOT NULL AND prior.school_id <> pss.school_id THEN 1
        ELSE 0
    END AS is_transfer,
    CASE
        WHEN prior.school_id IS NOT NULL AND prior.school_id <> pss.school_id THEN 1
        ELSE 0
    END AS candidate_changes_school,
    CASE WHEN tp.player_id IS NOT NULL THEN 1 ELSE 0 END AS is_portal_candidate,
    CASE WHEN rbm.id IS NOT NULL THEN 1 ELSE 0 END AS same_school_baseline
FROM player_season_stats pss
JOIN players p ON p.id = pss.player_id
LEFT JOIN player_season_stats prior
    ON prior.player_id = pss.player_id
   AND prior.season = pss.season - 1
LEFT JOIN neutral_projection np
    ON np.player_id = pss.player_id
   AND np.season = pss.season
LEFT JOIN player_team_fit_scores fit
    ON fit.player_id = pss.player_id
   AND fit.school_id = pss.school_id
   AND fit.season = pss.season
LEFT JOIN team_season_stats tss
    ON tss.school_id = pss.school_id
   AND tss.season = pss.season
LEFT JOIN roster_counts rc
    ON rc.school_id = pss.school_id
   AND rc.season = pss.season
LEFT JOIN roster_state_features rsf
    ON rsf.school_id = pss.school_id
   AND rsf.season = pss.season
LEFT JOIN prior_team_context ptc
    ON ptc.school_id = pss.school_id
   AND ptc.season = pss.season
LEFT JOIN prior_position_context ppc
    ON ppc.school_id = pss.school_id
   AND ppc.season = pss.season
   AND ppc.position = p.position
LEFT JOIN player_history ph
    ON ph.player_id = pss.player_id
   AND ph.season = pss.season
LEFT JOIN hep_dedup hep
    ON hep.player_id = pss.player_id
   AND hep.season = pss.season
LEFT JOIN player_archetypes pa
    ON pa.player_id = pss.player_id
   AND pa.season = pss.season
LEFT JOIN transfer_pairs tp
    ON tp.player_id = pss.player_id
   AND tp.to_school_id = pss.school_id
   AND tp.season = pss.season
LEFT JOIN roster_baseline_members rbm
    ON rbm.player_id = pss.player_id
   AND rbm.school_id = pss.school_id
   AND rbm.season = pss.season
WHERE pss.season = ANY(%(seasons)s)
  AND pss.min_pct IS NOT NULL
  AND pss.games_played >= %(min_games)s
  AND pss.school_id < %(synthetic_school_id_floor)s
    """

INFERENCE_SQL = """
WITH neutral_projection AS (
    SELECT DISTINCT ON (player_id, season)
        player_id,
        season,
        value_per_100,
        value_ci_lower,
        value_ci_upper,
        skill_percentiles,
        uncertainty,
        computed_at,
        model_version
    FROM player_projections
    WHERE school_id IS NULL
      AND projection_mode = 'neutral'
      AND expires_at > now()
      AND season = %(target_season)s
    ORDER BY
        player_id,
        season,
        CASE WHEN model_version = %(projection_model_version)s THEN 0 ELSE 1 END,
        computed_at DESC
),
latest_player_stats AS (
    SELECT DISTINCT ON (player_id)
        player_id,
        school_id,
        season,
        min_pct,
        usage_rate,
        games_played,
        points_per_game,
        rebounds_per_game,
        assists_per_game
    FROM player_season_stats
    WHERE season = %(source_season)s
    ORDER BY player_id, games_played DESC
),
roster_counts AS (
    SELECT school_id, season, count(*) AS roster_player_count
    FROM roster_baseline_members
    WHERE season = %(roster_season)s
    GROUP BY school_id, season
),
prior_team_context AS (
    SELECT
        school_id,
        season + 1 AS season,
        sum(min_pct / 100.0 * 40.0) AS prior_team_minutes,
        CASE
            WHEN sum(min_pct / 100.0 * 40.0) > 0
                THEN sum(power(min_pct / 100.0 * 40.0, 2)) / power(sum(min_pct / 100.0 * 40.0), 2)
            ELSE NULL
        END AS prior_team_rotation_hhi,
        count(*) FILTER (WHERE min_pct >= 50) AS prior_team_rotation_players,
        sum(usage_rate) AS prior_team_usage_total
    FROM player_season_stats
    WHERE season = %(source_season)s
      AND min_pct IS NOT NULL
    GROUP BY school_id, season
),
prior_position_context AS (
    SELECT
        pss.school_id,
        pss.season + 1 AS season,
        p.position,
        sum(pss.min_pct / 100.0 * 40.0) AS same_position_prior_minutes,
        max(pss.min_pct / 100.0 * 40.0) AS same_position_prior_max_minutes,
        count(*) AS same_position_prior_count,
        sum(pss.usage_rate) AS same_position_usage_total,
        max(pss.usage_rate) AS same_position_usage_max
    FROM player_season_stats pss
    JOIN players p ON p.id = pss.player_id
    WHERE pss.season = %(source_season)s
      AND pss.min_pct IS NOT NULL
    GROUP BY pss.school_id, pss.season, p.position
),
player_history AS (
    SELECT
        player_id,
        %(source_season)s AS season,
        count(DISTINCT season) FILTER (WHERE season <= %(source_season)s) AS prior_college_seasons,
        min(season) AS first_observed_season
    FROM player_season_stats
    WHERE season <= %(source_season)s
    GROUP BY player_id
),
latest_snapshots AS (
    SELECT DISTINCT ON (school_id)
        id AS roster_snapshot_id,
        school_id,
        season
    FROM roster_snapshots
    WHERE season = %(roster_season)s
    ORDER BY school_id, snapshot_date DESC, id DESC
),
transfer_rows AS (
    SELECT DISTINCT ON (player_id)
        player_id,
        season,
        from_school_id,
        to_school_id,
        match_confidence
    FROM transfer_portal_events
    WHERE season = %(source_season)s
      AND player_id IS NOT NULL
      AND match_status = 'matched'
    ORDER BY player_id, updated_at DESC NULLS LAST, id DESC
),
hep_dedup AS (
    SELECT DISTINCT ON (player_id, season)
        player_id,
        season,
        pos_confidence_pg,
        pos_confidence_sg,
        pos_confidence_sf,
        pos_confidence_pf,
        pos_confidence_c
    FROM hoop_explorer_player_stats
    WHERE player_id IS NOT NULL
    ORDER BY player_id, season, updated_at DESC, id DESC
)
SELECT
    fit.player_id,
    fit.school_id,
    %(target_season)s AS season,
    %(source_season)s AS source_stat_season,
    %(roster_season)s AS roster_context_season,
    %(fit_context_season)s AS fit_context_season,
    %(team_context_season)s AS team_context_season,
    fit.gap_match,
    fit.scheme_fit,
    fit.program_fit,
    fit.weight_gap,
    fit.weight_scheme,
    fit.weight_role,
    fit.weight_program,
    fit.breakdown AS fit_breakdown,
    CASE
        WHEN fit.is_portal_candidate OR tr.player_id IS NOT NULL THEN true
        ELSE false
    END AS is_portal_candidate,
    p.position,
    p.height_inches,
    lps.school_id AS prior_school_id,
    lps.min_pct AS prior_min_pct,
    lps.usage_rate AS prior_usage_rate,
    lps.games_played AS prior_games_played,
    lps.points_per_game AS prior_points_per_game,
    lps.rebounds_per_game AS prior_rebounds_per_game,
    lps.assists_per_game AS prior_assists_per_game,
    ph.prior_college_seasons,
    ph.first_observed_season,
    np.value_per_100,
    np.value_ci_lower,
    np.value_ci_upper,
    np.skill_percentiles,
    np.uncertainty,
    np.model_version AS neutral_projection_model_version,
    tss.adj_em AS team_adj_em,
    tss.adj_o AS team_adj_o,
    tss.adj_d AS team_adj_d,
    tss.adj_tempo AS team_adj_tempo,
    rc.roster_player_count,
    rsf.returning_minutes_by_position,
    rsf.departing_minutes_by_position,
    rsf.incoming_transfer_minutes_by_position,
    rsf.open_minutes_by_position,
    rsf.open_usage_by_position,
    rsf.returning_production,
    rsf.returning_player_impact,
    ptc.prior_team_minutes,
    ptc.prior_team_rotation_hhi,
    ptc.prior_team_rotation_players,
    ptc.prior_team_usage_total,
    ppc.same_position_prior_minutes,
    ppc.same_position_prior_max_minutes,
    ppc.same_position_prior_count,
    ppc.same_position_usage_total,
    ppc.same_position_usage_max,
    hep.pos_confidence_pg,
    hep.pos_confidence_sg,
    hep.pos_confidence_sf,
    hep.pos_confidence_pf,
    hep.pos_confidence_c,
    pa.archetype_label,
    pa.confidence AS archetype_confidence,
    tr.match_confidence AS transfer_match_confidence,
    ls.roster_snapshot_id,
    CASE
        WHEN lps.school_id IS NOT NULL AND lps.school_id <> fit.school_id THEN 1
        ELSE 0
    END AS is_transfer,
    CASE
        WHEN lps.school_id IS NOT NULL AND lps.school_id <> fit.school_id THEN 1
        ELSE 0
    END AS candidate_changes_school,
    CASE WHEN rbm.id IS NOT NULL THEN 1 ELSE 0 END AS same_school_baseline
FROM player_team_fit_scores fit
JOIN players p ON p.id = fit.player_id
LEFT JOIN latest_player_stats lps
    ON lps.player_id = fit.player_id
LEFT JOIN neutral_projection np
    ON np.player_id = fit.player_id
   AND np.season = %(target_season)s
LEFT JOIN team_season_stats tss
    ON tss.school_id = fit.school_id
   AND tss.season = %(team_context_season)s
LEFT JOIN roster_counts rc
    ON rc.school_id = fit.school_id
   AND rc.season = %(roster_season)s
LEFT JOIN roster_state_features rsf
    ON rsf.school_id = fit.school_id
   AND rsf.season = %(roster_season)s
LEFT JOIN prior_team_context ptc
    ON ptc.school_id = fit.school_id
   AND ptc.season = %(source_season)s + 1
LEFT JOIN prior_position_context ppc
    ON ppc.school_id = fit.school_id
   AND ppc.season = %(source_season)s + 1
   AND ppc.position = p.position
LEFT JOIN player_history ph
    ON ph.player_id = fit.player_id
   AND ph.season = %(source_season)s
LEFT JOIN hep_dedup hep
    ON hep.player_id = fit.player_id
   AND hep.season = %(source_season)s
LEFT JOIN player_archetypes pa
    ON pa.player_id = fit.player_id
   AND pa.season = %(source_season)s
LEFT JOIN transfer_rows tr
    ON tr.player_id = fit.player_id
LEFT JOIN latest_snapshots ls
    ON ls.school_id = fit.school_id
   AND ls.season = %(roster_season)s
LEFT JOIN roster_baseline_members rbm
    ON rbm.player_id = fit.player_id
   AND rbm.school_id = fit.school_id
   AND rbm.season = %(roster_season)s
WHERE fit.season = %(fit_context_season)s
  AND fit.school_id = ANY(%(school_ids)s)
"""

UPSERT_PLAYING_TIME_SQL = """
INSERT INTO playing_time_projections
    (player_id, school_id, season, roster_snapshot_id,
     expected_minutes, expected_minutes_share, minutes_ci_lower, minutes_ci_upper,
     expected_usage, usage_role, usage_role_confidence,
     starter_probability, rotation_probability, displaced_minutes,
     opportunity_drivers, data_quality_flags, scenario_overrides,
     role_fit, model_version, computed_at, expires_at)
VALUES %s
ON CONFLICT ON CONSTRAINT uq_playing_time_projection DO UPDATE SET
    roster_snapshot_id = EXCLUDED.roster_snapshot_id,
    expected_minutes = EXCLUDED.expected_minutes,
    expected_minutes_share = EXCLUDED.expected_minutes_share,
    minutes_ci_lower = EXCLUDED.minutes_ci_lower,
    minutes_ci_upper = EXCLUDED.minutes_ci_upper,
    expected_usage = EXCLUDED.expected_usage,
    usage_role = EXCLUDED.usage_role,
    usage_role_confidence = EXCLUDED.usage_role_confidence,
    starter_probability = EXCLUDED.starter_probability,
    rotation_probability = EXCLUDED.rotation_probability,
    displaced_minutes = EXCLUDED.displaced_minutes,
    opportunity_drivers = EXCLUDED.opportunity_drivers,
    data_quality_flags = EXCLUDED.data_quality_flags,
    scenario_overrides = EXCLUDED.scenario_overrides,
    role_fit = EXCLUDED.role_fit,
    computed_at = EXCLUDED.computed_at,
    expires_at = EXCLUDED.expires_at
"""

SYNC_ROLE_FIT_SQL = """
INSERT INTO player_team_fit_scores
    (player_id, school_id, season, overall_fit,
     gap_match, scheme_fit, role_fit, program_fit,
     weight_gap, weight_scheme, weight_role, weight_program,
     breakdown, is_portal_candidate, model_version, computed_at, expires_at)
SELECT
    data.player_id,
    data.school_id,
    data.season,
    round((
        data.weight_gap * data.gap_match
        + data.weight_scheme * data.scheme_fit
        + data.weight_role * data.role_fit
        + data.weight_program * data.program_fit
    )::numeric, 2) AS overall_fit,
    data.gap_match,
    data.scheme_fit,
    data.role_fit,
    data.program_fit,
    data.weight_gap,
    data.weight_scheme,
    data.weight_role,
    data.weight_program,
    jsonb_set(
        COALESCE(data.fit_breakdown::jsonb, '{}'::jsonb),
        '{role_fit}',
        data.role_breakdown::jsonb,
        true
    ) AS breakdown,
    data.is_portal_candidate,
    data.model_version,
    data.computed_at,
    data.expires_at
FROM (VALUES %s) AS data(
    player_id, school_id, season,
    gap_match, scheme_fit, role_fit, program_fit,
    weight_gap, weight_scheme, weight_role, weight_program,
    fit_breakdown, role_breakdown, is_portal_candidate,
    model_version, computed_at, expires_at
)
ON CONFLICT ON CONSTRAINT uq_fit_score DO UPDATE SET
    gap_match = EXCLUDED.gap_match,
    scheme_fit = EXCLUDED.scheme_fit,
    role_fit = EXCLUDED.role_fit,
    program_fit = EXCLUDED.program_fit,
    overall_fit = EXCLUDED.overall_fit,
    weight_gap = EXCLUDED.weight_gap,
    weight_scheme = EXCLUDED.weight_scheme,
    weight_role = EXCLUDED.weight_role,
    weight_program = EXCLUDED.weight_program,
    breakdown = EXCLUDED.breakdown,
    is_portal_candidate = EXCLUDED.is_portal_candidate,
    model_version = EXCLUDED.model_version,
    computed_at = EXCLUDED.computed_at,
    expires_at = EXCLUDED.expires_at
"""


@dataclass(frozen=True)
class PlayingTimeModels:
    minutes_model: Any
    usage_model: Any
    lower_model: Any | None
    upper_model: Any | None
    feature_medians: dict[str, float]
    train_metrics: dict[str, float]
    model_family: str = DEFAULT_MODEL_FAMILY
    minutes_features: Sequence[str] = tuple(MINUTES_FEATURES)
    usage_features: Sequence[str] = tuple(USAGE_FEATURES)
    rotation_model: Any | None = None
    starter_model: Any | None = None
    heavy_minutes_model: Any | None = None
    high_usage_model: Any | None = None
    freshman_minutes_share_by_group: dict[str, float] = field(default_factory=dict)
    freshman_usage_by_group: dict[str, float] = field(default_factory=dict)


class TreeQuantileRegressor:
    def __init__(self, quantile: float, *, random_state: int):
        self.quantile = quantile
        self.model = ExtraTreesRegressor(
            n_estimators=350,
            min_samples_leaf=8,
            max_features=0.75,
            bootstrap=True,
            random_state=random_state,
            n_jobs=-1,
        )

    def fit(
        self,
        x: np.ndarray,
        y: np.ndarray,
        sample_weight: np.ndarray | None = None,
    ) -> "TreeQuantileRegressor":
        self.model.fit(x, y, sample_weight=sample_weight)
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        tree_predictions = np.column_stack([tree.predict(x) for tree in self.model.estimators_])
        return np.quantile(tree_predictions, self.quantile, axis=1)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _numeric_column_or_default(df: pd.DataFrame, name: str, default: Any) -> np.ndarray:
    """Numeric column as a float ndarray, robust to a missing column.

    `df.get(name, default)` does NOT behave like a per-row fallback when `name` isn't a
    column — pandas mirrors dict.get() exactly, returning the bare `default` object
    verbatim, so a chained `.fillna()`/`.to_numpy()` crashes (`AttributeError`) the moment a
    caller's frame is missing that column. `default` can be a scalar or an array the same
    length as `df` (e.g. a per-row fallback like `minutes / 28.0`).
    """
    if name in df.columns:
        values = pd.to_numeric(df[name], errors="coerce").to_numpy(dtype=np.float64)
    else:
        values = np.full(len(df), np.nan, dtype=np.float64)
    return np.where(np.isnan(values), default, values)


def _json_dict(value: Any) -> dict:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def clamp(value: float, low: float, high: float) -> float:
    return float(max(low, min(high, value)))


def position_group(position: str | None) -> str:
    pos = (position or "").upper()
    if pos in {"PG", "SG", "G"}:
        return "guard"
    if pos in {"SF", "F"}:
        return "wing"
    if pos == "PF":
        return "forward"
    if pos == "C":
        return "big"
    return "unknown"


def _raw_connection_with_timeout(engine):
    """engine.raw_connection() with a server-side statement_timeout set.

    Without this, a query stuck on a dead/half-open connection (e.g. after a
    tunneled SSH connection silently drops) blocks forever with no exception —
    see RAW_CONNECTION_STATEMENT_TIMEOUT_MS.
    """
    raw_conn = engine.raw_connection()
    with raw_conn.cursor() as cur:
        cur.execute(f"SET statement_timeout = {RAW_CONNECTION_STATEMENT_TIMEOUT_MS}")
    return raw_conn


def read_sql_frame(engine, sql: str, params: dict[str, Any]) -> pd.DataFrame:
    raw_conn = _raw_connection_with_timeout(engine)
    try:
        with raw_conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
    finally:
        raw_conn.close()
    return pd.DataFrame(rows, columns=cols)


def open_minutes_for_position(raw: Any, position: str | None) -> float:
    data = _json_dict(raw)
    if not data:
        return 0.0
    pos = (position or "").upper()
    group = position_group(pos)
    candidates = [pos, pos.lower(), group, group.lower(), "total", "all"]
    for key in candidates:
        if key in data:
            return clamp(_as_float(data[key]), 0.0, STANDARD_TEAM_MINUTES)
    vals = [_as_float(v) for v in data.values() if isinstance(v, (int, float))]
    return clamp(float(np.mean(vals)) if vals else 0.0, 0.0, STANDARD_TEAM_MINUTES)


def position_json_value(
    raw: Any, position: str | None, *, default: float = np.nan, high: float = STANDARD_TEAM_MINUTES
) -> float:
    data = _json_dict(raw)
    if not data:
        return default
    pos = (position or "").upper()
    group = position_group(pos)
    candidates = [pos, pos.lower(), group, group.lower(), "total", "all"]
    for key in candidates:
        if key in data:
            return clamp(_as_float(data[key], default), 0.0, high)
    vals = [_as_float(v, np.nan) for v in data.values() if isinstance(v, (int, float))]
    vals = [v for v in vals if not np.isnan(v)]
    return clamp(float(np.mean(vals)) if vals else default, 0.0, high)


def max_position_confidence(row: pd.Series) -> float:
    vals = [
        _as_float(row.get("pos_confidence_pg"), np.nan),
        _as_float(row.get("pos_confidence_sg"), np.nan),
        _as_float(row.get("pos_confidence_sf"), np.nan),
        _as_float(row.get("pos_confidence_pf"), np.nan),
        _as_float(row.get("pos_confidence_c"), np.nan),
    ]
    vals = [v for v in vals if not np.isnan(v)]
    return clamp(max(vals) if vals else 0.5, 0.0, 1.0)


def prepare_playing_time_frame(df: pd.DataFrame, *, include_labels: bool) -> pd.DataFrame:
    out = df.copy()
    if out.empty:
        return out

    for col in [
        "prior_min_pct",
        "prior_usage_rate",
        "prior_games_played",
        "value_per_100",
        "value_ci_lower",
        "value_ci_upper",
        "prior_college_seasons",
        "first_observed_season",
        "is_transfer",
        "candidate_changes_school",
        "is_portal_candidate",
        "transfer_match_confidence",
        "archetype_confidence",
        "same_school_baseline",
        "roster_player_count",
        "same_position_usage_total",
        "same_position_usage_max",
        "prior_team_usage_total",
        "prior_team_minutes",
        "prior_team_rotation_players",
        "same_position_prior_minutes",
        "same_position_prior_count",
        "returning_production",
    ]:
        if col not in out.columns:
            out[col] = np.nan

    out["prior_minutes"] = pd.Series(
        [minutes_per_game_from_min_pct(v) for v in out["prior_min_pct"]],
        index=out.index,
        dtype="float64",
    ).fillna(0.0)
    out["prior_minutes_share"] = pd.Series(
        [minutes_share_from_min_pct(v) for v in out["prior_min_pct"]],
        index=out.index,
        dtype="float64",
    ).fillna(0.0)
    out["projection_ci_width"] = (
        pd.to_numeric(out["value_ci_upper"], errors="coerce")
        - pd.to_numeric(out["value_ci_lower"], errors="coerce")
    ).clip(lower=0.0)
    out["prior_college_seasons"] = pd.to_numeric(
        out["prior_college_seasons"],
        errors="coerce",
    ).fillna(0.0)
    out["is_no_prior_college_season"] = (out["prior_college_seasons"] <= 0).astype(float)
    out["career_season_index"] = (out["prior_college_seasons"] + 1.0).clip(lower=1.0, upper=8.0)
    first_observed = pd.to_numeric(out["first_observed_season"], errors="coerce")
    current_season = pd.to_numeric(out["season"], errors="coerce")
    out["years_since_first_observed"] = (
        (current_season - first_observed).clip(lower=0.0, upper=8.0).fillna(0.0)
    )
    normalized_position = (
        out.get("position", pd.Series("", index=out.index)).fillna("").astype(str).str.upper()
    )
    for pos in ["PG", "SG", "SF", "PF", "C"]:
        out[f"position_is_{pos.lower()}"] = (normalized_position == pos).astype(float)
    groups = normalized_position.apply(position_group)
    for group in ["guard", "wing", "forward", "big"]:
        out[f"position_group_{group}"] = (groups == group).astype(float)
    # Vectorized position-confidence: max across the 5 pos_confidence_* columns directly,
    # rather than a per-row Python function call (max_position_confidence) via .apply(axis=1)
    # — that pattern reconstructs a full pd.Series per row just to find a max of 5 numbers.
    confidence_cols = [
        "pos_confidence_pg",
        "pos_confidence_sg",
        "pos_confidence_sf",
        "pos_confidence_pf",
        "pos_confidence_c",
    ]
    present_confidence_cols = [c for c in confidence_cols if c in out.columns]
    if present_confidence_cols:
        confidence_values = out[present_confidence_cols].apply(pd.to_numeric, errors="coerce")
        out["position_confidence"] = confidence_values.max(axis=1).clip(0.0, 1.0).fillna(0.5)
    else:
        out["position_confidence"] = 0.5

    # Vectorized position-keyed JSON lookups: zip() over raw column values instead of
    # .apply(axis=1) — .apply(axis=1) rebuilds a full pd.Series per row before the lambda
    # ever runs; zip() hands open_minutes_for_position/position_json_value the raw values
    # directly, same per-row dict-parsing logic, no per-row Series-construction overhead.
    positions = out.get("position", pd.Series("", index=out.index))
    open_minutes_by_position = out.get(
        "open_minutes_by_position", pd.Series(None, index=out.index)
    )
    out["roster_open_minutes"] = [
        open_minutes_for_position(v, p) for v, p in zip(open_minutes_by_position, positions)
    ]
    out["returning_minutes_position"] = [
        position_json_value(v, p)
        for v, p in zip(
            out.get("returning_minutes_by_position", pd.Series(None, index=out.index)), positions
        )
    ]
    out["departing_minutes_position"] = [
        position_json_value(v, p)
        for v, p in zip(
            out.get("departing_minutes_by_position", pd.Series(None, index=out.index)), positions
        )
    ]
    out["incoming_transfer_minutes_position"] = [
        position_json_value(v, p)
        for v, p in zip(
            out.get("incoming_transfer_minutes_by_position", pd.Series(None, index=out.index)),
            positions,
        )
    ]
    out["open_usage_position"] = [
        position_json_value(v, p, high=100.0)
        for v, p in zip(
            out.get("open_usage_by_position", pd.Series(None, index=out.index)), positions
        )
    ]
    out["position_opportunity_delta"] = (
        out["departing_minutes_position"].fillna(0.0)
        + out["incoming_transfer_minutes_position"].fillna(0.0)
        - out["returning_minutes_position"].fillna(0.0)
    )
    out["returning_minutes_share_position"] = (
        out["returning_minutes_position"].fillna(0.0) / 100.0
    ).clip(0.0, 5.0)
    out["open_minutes_share_position"] = (out["roster_open_minutes"].fillna(0.0) / 100.0).clip(
        0.0, 5.0
    )
    same_position_prior_minutes = pd.to_numeric(
        out["same_position_prior_minutes"], errors="coerce"
    ).fillna(0.0)
    same_position_prior_count = pd.to_numeric(out["same_position_prior_count"], errors="coerce")
    out["same_position_minutes_mean"] = (
        same_position_prior_minutes / same_position_prior_count.replace(0, np.nan)
    ).fillna(0.0)
    out["same_position_usage_mean"] = (
        pd.to_numeric(out["same_position_usage_total"], errors="coerce").fillna(0.0)
        / pd.to_numeric(out["same_position_prior_count"], errors="coerce").replace(0, np.nan)
    ).fillna(0.0)
    out["prior_team_minutes_mean"] = (
        pd.to_numeric(out["prior_team_minutes"], errors="coerce").fillna(0.0)
        / pd.to_numeric(out["prior_team_rotation_players"], errors="coerce").replace(0, np.nan)
    ).fillna(0.0)
    out["position_crowding_ratio"] = (
        out["returning_minutes_position"].fillna(0.0)
        / (out["roster_open_minutes"].fillna(0.0).clip(lower=1.0))
    ).clip(0.0, 10.0)
    out["opportunity_to_prior_minutes_ratio"] = (
        (out["roster_open_minutes"].fillna(0.0) * 0.4)
        / pd.to_numeric(out["prior_minutes"], errors="coerce").fillna(0.0).clip(lower=1.0)
    ).clip(0.0, 20.0)
    out["open_minutes_minus_returning_position"] = out["roster_open_minutes"].fillna(0.0) - out[
        "returning_minutes_position"
    ].fillna(0.0)
    out["departing_minus_incoming_transfer_minutes"] = out["departing_minutes_position"].fillna(
        0.0
    ) - out["incoming_transfer_minutes_position"].fillna(0.0)
    out["returning_production_per_rotation_player"] = (
        pd.to_numeric(out["returning_production"], errors="coerce").fillna(0.0)
        / pd.to_numeric(out["prior_team_rotation_players"], errors="coerce").replace(0, np.nan)
    ).fillna(0.0)
    out["sample_reliability"] = (
        (pd.to_numeric(out["prior_games_played"], errors="coerce").fillna(0.0) / 20.0).clip(0, 1)
        * np.sqrt(
            (pd.to_numeric(out["prior_min_pct"], errors="coerce").fillna(0.0) / 55.0).clip(0, 1)
        )
    ).clip(0.0, 1.0)
    out["projection_reliability"] = (
        1.0 / (1.0 + out["projection_ci_width"].fillna(8.0) / 8.0)
    ).clip(0.2, 1.0)
    out["roster_reliability"] = np.where(out["roster_player_count"].notna(), 0.9, 0.45)
    transfer_fallback = pd.Series(
        np.where(out["is_transfer"].fillna(0).astype(int) == 1, 0.5, 1.0),
        index=out.index,
    )
    out["transfer_match_confidence"] = pd.to_numeric(
        out["transfer_match_confidence"], errors="coerce"
    ).fillna(transfer_fallback)
    out["archetype_confidence"] = pd.to_numeric(
        out["archetype_confidence"], errors="coerce"
    ).fillna(0.45)
    out["same_school_baseline"] = pd.to_numeric(
        out["same_school_baseline"], errors="coerce"
    ).fillna(0.0)
    out["is_transfer"] = pd.to_numeric(out["is_transfer"], errors="coerce").fillna(0.0)
    out["candidate_changes_school"] = pd.to_numeric(
        out["candidate_changes_school"],
        errors="coerce",
    ).fillna(out["is_transfer"])
    out["is_portal_candidate"] = pd.to_numeric(
        out["is_portal_candidate"],
        errors="coerce",
    ).fillna(0.0)

    for col in NUMERIC_FEATURES:
        if col not in out.columns:
            out[col] = np.nan
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["missing_feature_count"] = out[NUMERIC_FEATURES].isna().sum(axis=1)

    if include_labels:
        out["actual_minutes_share"] = out["actual_min_pct"].apply(minutes_share_from_min_pct)
        out["actual_minutes"] = out["actual_min_pct"].apply(minutes_per_game_from_min_pct)
        out["actual_usage_rate"] = pd.to_numeric(out["actual_usage_rate"], errors="coerce")

    return out


def feature_medians(
    df: pd.DataFrame, feature_columns: Sequence[str] = NUMERIC_FEATURES
) -> dict[str, float]:
    medians: dict[str, float] = {}
    for col in feature_columns:
        values = pd.to_numeric(df[col], errors="coerce").dropna()
        medians[col] = 0.0 if values.empty else float(values.median())
    return medians


def feature_matrix(
    df: pd.DataFrame,
    medians: dict[str, float],
    feature_columns: Sequence[str] = NUMERIC_FEATURES,
) -> np.ndarray:
    work = df.copy()
    for col in feature_columns:
        work[col] = pd.to_numeric(work[col], errors="coerce").fillna(medians.get(col, 0.0))
    return work[list(feature_columns)].to_numpy(dtype=np.float64)


def _validate_model_family(model_family: str) -> str:
    if model_family not in MODEL_FAMILIES:
        raise ValueError(
            f"Unknown playing-time model family {model_family!r}; "
            f"expected one of {sorted(MODEL_FAMILIES)}"
        )
    if model_family == "lightgbm" and (LGBMClassifier is None or LGBMRegressor is None):
        raise RuntimeError("lightgbm model family requested, but lightgbm is not installed")
    return model_family


def _fit_binary_classifier(
    x: np.ndarray,
    y: np.ndarray,
    *,
    random_state: int,
    model_family: str,
) -> Any | None:
    if len(np.unique(y)) < 2:
        return None
    if model_family == "extra_trees":
        model = ExtraTreesClassifier(
            n_estimators=350,
            min_samples_leaf=8,
            max_features=0.75,
            bootstrap=True,
            class_weight="balanced_subsample",
            random_state=random_state,
            n_jobs=-1,
        )
    elif model_family == "lightgbm":
        model = LGBMClassifier(
            objective="binary",
            n_estimators=260,
            learning_rate=0.035,
            num_leaves=31,
            min_child_samples=35,
            subsample=0.9,
            colsample_bytree=0.9,
            reg_lambda=0.2,
            random_state=random_state,
            n_jobs=-1,
            verbosity=-1,
        )
    else:
        model = HistGradientBoostingClassifier(
            max_iter=120,
            learning_rate=0.05,
            l2_regularization=0.05,
            random_state=random_state,
        )
    model.fit(x, y)
    return model


def _make_regressor(*, model_family: str, target: str, random_state: int) -> Any:
    if model_family == "extra_trees":
        if target == "lower":
            return TreeQuantileRegressor(0.10, random_state=random_state)
        if target == "upper":
            return TreeQuantileRegressor(0.90, random_state=random_state)
        return ExtraTreesRegressor(
            n_estimators=450,
            min_samples_leaf=6,
            max_features=0.75,
            bootstrap=True,
            random_state=random_state,
            n_jobs=-1,
        )

    if model_family == "lightgbm":
        common = {
            "n_estimators": 420,
            "learning_rate": 0.028,
            "num_leaves": 31,
            "min_child_samples": 35,
            "subsample": 0.9,
            "colsample_bytree": 0.9,
            "reg_lambda": 0.25,
            "random_state": random_state,
            "n_jobs": -1,
            "verbosity": -1,
        }
        if target == "lower":
            return LGBMRegressor(objective="quantile", alpha=0.10, **common)
        if target == "upper":
            return LGBMRegressor(objective="quantile", alpha=0.90, **common)
        return LGBMRegressor(objective="regression", **common)

    if target == "minutes":
        return HistGradientBoostingRegressor(
            max_iter=220,
            learning_rate=0.045,
            l2_regularization=0.04,
            random_state=random_state,
        )
    if target == "usage":
        return HistGradientBoostingRegressor(
            max_iter=190,
            learning_rate=0.045,
            l2_regularization=0.04,
            random_state=random_state,
        )
    return HistGradientBoostingRegressor(
        loss="quantile",
        quantile=0.10 if target == "lower" else 0.90,
        max_iter=160,
        learning_rate=0.05,
        l2_regularization=0.05,
        random_state=random_state,
    )


def _positive_probability(
    model: Any | None, x: np.ndarray, fallback: np.ndarray | float
) -> np.ndarray:
    if model is None:
        return (
            np.full(x.shape[0], fallback, dtype=np.float64)
            if np.isscalar(fallback)
            else np.asarray(fallback, dtype=np.float64)
        )
    classes = list(model.classes_)
    if 1 not in classes:
        return np.zeros(x.shape[0], dtype=np.float64)
    idx = classes.index(1)
    return np.clip(model.predict_proba(x)[:, idx], 0.0, 1.0)


def survival_minutes_share(
    p_rotation: np.ndarray, p_starter: np.ndarray, p_heavy: np.ndarray
) -> np.ndarray:
    p_rotation = np.clip(p_rotation, 0.0, 1.0)
    p_starter = np.minimum(np.clip(p_starter, 0.0, 1.0), p_rotation)
    p_heavy = np.minimum(np.clip(p_heavy, 0.0, 1.0), p_starter)
    return np.clip(
        0.10 * (1.0 - p_rotation)
        + 0.45 * (p_rotation - p_starter)
        + 0.70 * (p_starter - p_heavy)
        + 0.875 * p_heavy,
        0.0,
        0.95,
    )


def role_probability_features(
    models: "PlayingTimeModels",
    x_minutes: np.ndarray,
    *,
    x_usage: np.ndarray | None = None,
    base_share: np.ndarray | None = None,
    base_usage: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    usage_matrix = x_minutes if x_usage is None else x_usage
    p_rotation = _positive_probability(
        models.rotation_model,
        x_minutes,
        (base_share >= 0.30) if base_share is not None else 0.5,
    )
    p_starter = _positive_probability(
        models.starter_model,
        x_minutes,
        (base_share >= 0.60) if base_share is not None else 0.25,
    )
    p_heavy = _positive_probability(
        models.heavy_minutes_model,
        x_minutes,
        (base_share >= 0.80) if base_share is not None else 0.08,
    )
    p_high_usage = _positive_probability(
        models.high_usage_model,
        usage_matrix,
        (base_usage >= 26.0) if base_usage is not None else 0.10,
    )
    p_starter = np.minimum(p_starter, p_rotation)
    p_heavy = np.minimum(p_heavy, p_starter)
    return p_rotation, p_starter, p_heavy, p_high_usage


def append_role_probability_features(
    x: np.ndarray,
    p_rotation: np.ndarray,
    p_starter: np.ndarray,
    p_heavy: np.ndarray,
    p_high_usage: np.ndarray,
) -> np.ndarray:
    return np.column_stack([x, p_rotation, p_starter, p_heavy, p_high_usage])


def neutral_projection_fallback_rate(df: pd.DataFrame) -> float:
    """Fraction of rows whose joined `player_projections` row is not the forecast model.

    `TRAINING_SQL`'s `neutral_projection` CTE prefers `PLAYER_PROJECTION_MODEL_VERSION`
    (a strict one-step-ahead forecast: source season S-1 production -> projection for
    season S) but falls back to any other available model_version when that forecast row
    is missing. Same-season model versions (e.g. Phase 0/Phase 2a's own-season output) are
    computed from that season's own actual production, so a fallback row's `value_per_100`
    would leak the answer into predicting that season's `actual_minutes_share`/
    `actual_usage_rate`. A non-trivial fallback rate here means real leakage exposure, not
    just missing data.
    """
    if df.empty or "neutral_projection_model_version" not in df.columns:
        return 0.0
    version = df["neutral_projection_model_version"]
    fallback = version.notna() & (version != PLAYER_PROJECTION_MODEL_VERSION)
    return float(fallback.mean())


NEUTRAL_PROJECTION_FEATURE_COLUMNS = (
    "value_per_100",
    "value_ci_lower",
    "value_ci_upper",
    "skill_percentiles",
    "uncertainty",
)


def null_leaking_neutral_projection_features(df: pd.DataFrame) -> pd.DataFrame:
    """Blank out same-season-leakage-risk projection features on fallback rows.

    Dropping fallback rows outright isn't viable — `neutral_projection_fallback_rate`
    can be the entire training/inference set whenever the forecast model
    (`PLAYER_PROJECTION_MODEL_VERSION`) hasn't been backfilled for the seasons in scope
    (confirmed locally: 100% fallback before that backfill ran). The row's own
    `actual_minutes_share`/`actual_usage_rate` label is real ground truth either way —
    only the *joined* same-season projection features carry the leak. Nulling just those
    columns lets the row fall back to median-imputation in `feature_matrix`, the same path
    already used for "no projection joined at all", instead of losing the row or training
    on a leaked signal.
    """
    if df.empty or "neutral_projection_model_version" not in df.columns:
        return df
    out = df.copy()
    version = out["neutral_projection_model_version"]
    fallback = version.notna() & (version != PLAYER_PROJECTION_MODEL_VERSION)
    for col in NEUTRAL_PROJECTION_FEATURE_COLUMNS:
        if col in out.columns:
            out.loc[fallback, col] = np.nan
    return out


def build_training_examples(engine, seasons: Sequence[int]) -> pd.DataFrame:
    params = {
        "seasons": list(seasons),
        "min_games": MIN_TRAIN_GAMES,
        "projection_model_version": PLAYER_PROJECTION_MODEL_VERSION,
        "synthetic_school_id_floor": SYNTHETIC_SCHOOL_ID_FLOOR,
    }
    raw = read_sql_frame(engine, TRAINING_SQL, params)
    raw = null_leaking_neutral_projection_features(raw)
    frame = prepare_playing_time_frame(raw, include_labels=True)
    return frame.dropna(subset=["actual_minutes_share", "actual_usage_rate"]).reset_index(drop=True)


def build_inference_pairs(
    engine,
    season: int,
    school_ids: Sequence[int],
    *,
    source_season: int | None = None,
    roster_season: int | None = None,
    fit_context_season: int | None = None,
    team_context_season: int | None = None,
) -> pd.DataFrame:
    source = int(source_season if source_season is not None else season)
    roster = int(roster_season if roster_season is not None else source)
    fit_context = int(fit_context_season if fit_context_season is not None else roster)
    team_context = int(team_context_season if team_context_season is not None else source)
    params = {
        "target_season": int(season),
        "source_season": source,
        "roster_season": roster,
        "fit_context_season": fit_context,
        "team_context_season": team_context,
        "school_ids": list(map(int, school_ids)),
        "projection_model_version": PLAYER_PROJECTION_MODEL_VERSION,
    }
    raw = read_sql_frame(engine, INFERENCE_SQL, params)
    raw = null_leaking_neutral_projection_features(raw)
    return prepare_playing_time_frame(raw, include_labels=False)


POSITION_GROUPS = ("guard", "wing", "forward", "big", "unknown")


def freshman_position_group_priors(
    train_df: pd.DataFrame, y_minutes: np.ndarray, y_usage: np.ndarray
) -> tuple[dict[str, float], dict[str, float]]:
    """Position-group median minutes-share/usage among true freshmen in the training set.

    Used by `apply_freshman_prior_shrinkage` as the prior to blend a freshman/newcomer's
    prediction toward, since their `is_no_prior_college_season=1` rows have nothing for the
    roster-opportunity features to anchor on beyond `roster_open_minutes` (no
    recruiting-rank signal is ingested anywhere in this repo). Computed from the training
    freshman rows themselves (not all players at that position), since "what does a
    typical true freshman at this position actually do" is the relevant prior — not diluted
    by veteran medians.
    """
    is_freshman = (
        pd.to_numeric(train_df.get("is_no_prior_college_season", 0.0), errors="coerce")
        .fillna(0.0)
        .to_numpy()
        >= 0.5
    )
    overall_share = float(y_minutes[is_freshman].mean()) if is_freshman.any() else float(
        np.mean(y_minutes)
    )
    overall_usage = float(y_usage[is_freshman].mean()) if is_freshman.any() else float(
        np.mean(y_usage)
    )
    groups = (
        train_df.get("position", pd.Series("", index=train_df.index))
        .fillna("")
        .astype(str)
        .apply(position_group)
        .to_numpy()
    )
    minutes_share_by_group: dict[str, float] = {}
    usage_by_group: dict[str, float] = {}
    for grp in POSITION_GROUPS:
        grp_mask = is_freshman & (groups == grp)
        if grp_mask.any():
            minutes_share_by_group[grp] = float(y_minutes[grp_mask].mean())
            usage_by_group[grp] = float(y_usage[grp_mask].mean())
        else:
            minutes_share_by_group[grp] = overall_share
            usage_by_group[grp] = overall_usage
    return minutes_share_by_group, usage_by_group


FRESHMAN_PRIOR_BLEND = 0.45


def apply_freshman_prior_shrinkage(
    df: pd.DataFrame,
    share: np.ndarray,
    usage: np.ndarray,
    minutes_share_by_group: dict[str, float],
    usage_by_group: dict[str, float],
) -> tuple[np.ndarray, np.ndarray]:
    """Blend freshman/newcomer predictions toward a position-group historical prior.

    Partial mitigation, not a fix — a real fix needs recruiting-rank/class data this repo
    doesn't ingest yet (see docs/models/playing_time_rotation_model_plan.md's known
    limitations). This only softens reliance on `roster_open_minutes` extrapolation for
    rows with zero prior college seasons, blended `FRESHMAN_PRIOR_BLEND` of the way toward
    what true freshmen at the same position group actually did historically, lightly scaled
    by destination quality (`team_adj_em`) since a stronger program's freshmen tend to play
    a bit more than a weak program's, all else equal.
    """
    if not minutes_share_by_group or not usage_by_group:
        return share, usage
    is_freshman = (
        pd.to_numeric(df.get("is_no_prior_college_season", 0.0), errors="coerce")
        .fillna(0.0)
        .to_numpy()
        >= 0.5
    )
    if not is_freshman.any():
        return share, usage

    groups = (
        df.get("position", pd.Series("", index=df.index))
        .fillna("")
        .astype(str)
        .apply(position_group)
    )
    fallback_share = float(np.mean(list(minutes_share_by_group.values())))
    fallback_usage = float(np.mean(list(usage_by_group.values())))
    prior_share = groups.map(minutes_share_by_group).fillna(fallback_share).to_numpy()
    prior_usage = groups.map(usage_by_group).fillna(fallback_usage).to_numpy()

    adj_em = pd.to_numeric(df.get("team_adj_em", 0.0), errors="coerce").fillna(0.0).to_numpy()
    quality_scale = np.clip(1.0 + adj_em / 100.0, 0.7, 1.3)
    prior_share = np.clip(prior_share * quality_scale, 0.0, 0.95)
    prior_usage = np.clip(prior_usage * quality_scale, 0.0, 100.0)

    blended_share = np.where(
        is_freshman,
        (1.0 - FRESHMAN_PRIOR_BLEND) * share + FRESHMAN_PRIOR_BLEND * prior_share,
        share,
    )
    blended_usage = np.where(
        is_freshman,
        (1.0 - FRESHMAN_PRIOR_BLEND) * usage + FRESHMAN_PRIOR_BLEND * prior_usage,
        usage,
    )
    return blended_share, blended_usage


def fit_minutes_usage_models(
    train_df: pd.DataFrame,
    *,
    model_family: str = DEFAULT_MODEL_FAMILY,
    apply_freshman_shrinkage: bool = False,
) -> PlayingTimeModels:
    """`apply_freshman_shrinkage` defaults False — measured on real rolling-origin holdout
    data to *increase* minutes_mae/minutes_rmse (~+11%) vs. leaving freshman rows to the
    tree models' own features. Same situation as Player Projection's Gap B context
    adjustment (see docs/models/player_projection_state_space_plan.md §22): a
    theoretically-motivated mitigation for a real, acknowledged limitation (no
    recruiting-rank signal for true freshmen) that empirically underperforms on this data.
    Available and tested (`freshman_position_group_priors`/`apply_freshman_prior_shrinkage`)
    for future investigation, not enabled by default.
    """
    if train_df.empty:
        raise ValueError("No playing-time training rows available")
    model_family = _validate_model_family(model_family)
    minutes_features = tuple(MINUTES_FEATURES)
    usage_features = tuple(USAGE_FEATURES)
    medians = feature_medians(train_df)
    x_minutes = feature_matrix(train_df, medians, minutes_features)
    x_usage = feature_matrix(train_df, medians, usage_features)
    y_minutes = train_df["actual_minutes_share"].astype(float).clip(0.0, 1.0).to_numpy()
    y_usage = train_df["actual_usage_rate"].astype(float).fillna(0.0).clip(0.0, 100.0).to_numpy()
    y_rotation = (y_minutes >= 0.30).astype(int)
    y_starter = (y_minutes >= 0.60).astype(int)
    y_heavy = (y_minutes >= 0.80).astype(int)
    y_high_usage = (y_usage >= 26.0).astype(int)

    rotation_model = _fit_binary_classifier(
        x_minutes,
        y_rotation,
        random_state=46,
        model_family=model_family,
    )
    starter_model = _fit_binary_classifier(
        x_minutes,
        y_starter,
        random_state=47,
        model_family=model_family,
    )
    heavy_minutes_model = _fit_binary_classifier(
        x_minutes,
        y_heavy,
        random_state=48,
        model_family=model_family,
    )
    high_usage_model = _fit_binary_classifier(
        x_usage,
        y_high_usage,
        random_state=49,
        model_family=model_family,
    )
    staged = PlayingTimeModels(
        minutes_model=None,
        usage_model=None,
        lower_model=None,
        upper_model=None,
        feature_medians=medians,
        train_metrics={},
        model_family=model_family,
        minutes_features=minutes_features,
        usage_features=usage_features,
        rotation_model=rotation_model,
        starter_model=starter_model,
        heavy_minutes_model=heavy_minutes_model,
        high_usage_model=high_usage_model,
    )
    p_rotation = _positive_probability(staged.rotation_model, x_minutes, y_rotation.mean())
    p_starter = _positive_probability(staged.starter_model, x_minutes, y_starter.mean())
    p_heavy = _positive_probability(staged.heavy_minutes_model, x_minutes, y_heavy.mean())
    p_high_usage = _positive_probability(staged.high_usage_model, x_usage, y_high_usage.mean())
    p_starter = np.minimum(p_starter, p_rotation)
    p_heavy = np.minimum(p_heavy, p_starter)
    x_minutes_aug = append_role_probability_features(
        x_minutes,
        p_rotation,
        p_starter,
        p_heavy,
        p_high_usage,
    )
    x_usage_aug = append_role_probability_features(
        x_usage,
        p_rotation,
        p_starter,
        p_heavy,
        p_high_usage,
    )

    minutes_model = _make_regressor(model_family=model_family, target="minutes", random_state=42)
    usage_model = _make_regressor(model_family=model_family, target="usage", random_state=43)
    lower_model = _make_regressor(model_family=model_family, target="lower", random_state=44)
    upper_model = _make_regressor(model_family=model_family, target="upper", random_state=45)

    minute_weights = 1.0 + 0.35 * y_starter + 0.70 * y_heavy
    usage_weights = 1.0 + 0.50 * y_high_usage
    minutes_model.fit(x_minutes_aug, y_minutes, sample_weight=minute_weights)
    usage_model.fit(x_usage_aug, y_usage, sample_weight=usage_weights)
    lower_model.fit(x_minutes_aug, y_minutes)
    upper_model.fit(x_minutes_aug, y_minutes)

    pred_minutes = np.clip(minutes_model.predict(x_minutes_aug), 0.0, 0.95)
    pred_usage = np.clip(usage_model.predict(x_usage_aug), 0.0, 100.0)
    metrics = {
        "train_minutes_share_mae": float(mean_absolute_error(y_minutes, pred_minutes)),
        "train_minutes_share_rmse": float(mean_squared_error(y_minutes, pred_minutes) ** 0.5),
        "train_usage_mae": float(mean_absolute_error(y_usage, pred_usage)),
        "train_usage_rmse": float(mean_squared_error(y_usage, pred_usage) ** 0.5),
        "train_rotation_rate": float(y_rotation.mean()),
        "train_starter_rate": float(y_starter.mean()),
        "train_heavy_minutes_rate": float(y_heavy.mean()),
        "train_high_usage_rate": float(y_high_usage.mean()),
        "n_training_rows": float(len(train_df)),
    }
    freshman_minutes_share_by_group: dict[str, float] = {}
    freshman_usage_by_group: dict[str, float] = {}
    if apply_freshman_shrinkage:
        freshman_minutes_share_by_group, freshman_usage_by_group = freshman_position_group_priors(
            train_df, y_minutes, y_usage
        )
    return PlayingTimeModels(
        minutes_model,
        usage_model,
        lower_model,
        upper_model,
        medians,
        metrics,
        model_family,
        minutes_features,
        usage_features,
        rotation_model,
        starter_model,
        heavy_minutes_model,
        high_usage_model,
        freshman_minutes_share_by_group,
        freshman_usage_by_group,
    )


def uncertainty_multipliers(df: pd.DataFrame) -> np.ndarray:
    """Vectorized form of `uncertainty_multiplier` — same formula, whole-frame at once.

    Replaces a per-row `for _, row in df.iterrows(): uncertainty_multiplier(row)` loop in
    `predict_minutes_usage` — `iterrows()` is the most expensive row-iteration pattern in
    pandas (materializes a full `pd.Series` per row, dtype-merging across mixed column
    types). `uncertainty_multiplier(row)` stays as a single-row convenience wrapper around
    this for tests/callers that genuinely have one row.
    """
    n = len(df)
    if n == 0:
        return np.zeros(0, dtype=np.float64)

    def col(name: str, default: float) -> np.ndarray:
        return _numeric_column_or_default(df, name, default)

    sample_rel = np.clip(col("sample_reliability", 0.0), 0.0, 1.0)
    proj_rel = np.clip(col("projection_reliability", 0.5), 0.0, 1.0)
    roster_rel = np.clip(col("roster_reliability", 0.5), 0.0, 1.0)
    pos_rel = np.clip(col("position_confidence", 0.5), 0.0, 1.0)
    transfer_conf = np.clip(col("transfer_match_confidence", 1.0), 0.0, 1.0)
    missing = col("missing_feature_count", 0.0)
    multiplier = (
        0.60
        + 0.55 * (1.0 - sample_rel)
        + 0.25 * (1.0 - proj_rel)
        + 0.20 * (1.0 - roster_rel)
        + 0.18 * (1.0 - pos_rel)
        + 0.18 * (1.0 - transfer_conf)
        + np.minimum(0.25, missing * 0.025)
    )
    return np.clip(multiplier, 0.60, 2.10)


def uncertainty_multiplier(row: pd.Series) -> float:
    return float(uncertainty_multipliers(pd.DataFrame([row]))[0])


def data_quality_flags_batch(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Vectorized form of `data_quality_flags` — whole-frame at once, returns one dict per row.

    `build_playing_time_records` previously called `data_quality_flags(pd.Series(r))` inside
    its per-row loop, reconstructing a full `pd.Series` from a namedtuple-derived dict on
    every row just to read a handful of scalar fields — the same `.apply(axis=1)`-shaped cost
    even though it wasn't literally `.apply()`. This computes every row's flags up front as
    plain arrays, leaving the loop to just zip them into per-row dicts (dict construction
    itself is the only genuinely row-shaped part — JSON output has to be row-shaped).
    """
    n = len(df)
    if n == 0:
        return []

    def col(name: str, default: float) -> np.ndarray:
        return _numeric_column_or_default(df, name, default)

    low_sample = col("sample_reliability", 0.0) < 0.45
    wide_projection = col("projection_ci_width", 0.0) > 7.5
    low_position_confidence = col("position_confidence", 1.0) < 0.65
    missing_roster_context = col("roster_reliability", 0.0) < 0.6
    low_transfer_match_confidence = col("transfer_match_confidence", 1.0) < 0.75
    missing_feature_count = col("missing_feature_count", 0.0)
    multipliers = uncertainty_multipliers(df)

    return [
        {
            "low_sample": bool(low_sample[i]),
            "wide_player_projection": bool(wide_projection[i]),
            "low_position_confidence": bool(low_position_confidence[i]),
            "missing_roster_context": bool(missing_roster_context[i]),
            "low_transfer_match_confidence": bool(low_transfer_match_confidence[i]),
            "missing_feature_count": int(missing_feature_count[i]),
            "uncertainty_multiplier": round(float(multipliers[i]), 3),
        }
        for i in range(n)
    ]


def data_quality_flags(row: pd.Series) -> dict[str, Any]:
    return data_quality_flags_batch(pd.DataFrame([row]))[0]


def predict_minutes_usage(models: PlayingTimeModels, df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = df.copy()
    x_minutes = feature_matrix(out, models.feature_medians, models.minutes_features)
    x_usage = feature_matrix(out, models.feature_medians, models.usage_features)
    p_rotation, p_starter, p_heavy, p_high_usage = role_probability_features(
        models,
        x_minutes,
        x_usage=x_usage,
    )
    x_minutes_aug = append_role_probability_features(
        x_minutes,
        p_rotation,
        p_starter,
        p_heavy,
        p_high_usage,
    )
    x_usage_aug = append_role_probability_features(
        x_usage,
        p_rotation,
        p_starter,
        p_heavy,
        p_high_usage,
    )
    share = np.clip(models.minutes_model.predict(x_minutes_aug), 0.0, 0.95)
    usage = np.clip(models.usage_model.predict(x_usage_aug), 0.0, 100.0)
    share, usage = apply_freshman_prior_shrinkage(
        out, share, usage, models.freshman_minutes_share_by_group, models.freshman_usage_by_group
    )
    lower_share = (
        np.clip(models.lower_model.predict(x_minutes_aug), 0.0, 0.95)
        if models.lower_model
        else share - 0.10
    )
    upper_share = (
        np.clip(models.upper_model.predict(x_minutes_aug), 0.0, 0.95)
        if models.upper_model
        else share + 0.10
    )

    out["expected_minutes_share"] = share
    out["expected_minutes"] = share * MAX_PLAYER_MINUTES
    out["expected_usage"] = usage
    out["rotation_probability_model"] = p_rotation
    out["starter_probability_model"] = p_starter
    out["heavy_minutes_probability"] = p_heavy
    out["high_usage_probability"] = p_high_usage
    base_lower = np.minimum(lower_share, share) * MAX_PLAYER_MINUTES
    base_upper = np.maximum(upper_share, share) * MAX_PLAYER_MINUTES

    # Vectorized CI bounds — was previously a python `for _, row in out.iterrows()` loop
    # calling uncertainty_multiplier(row) per row; iterrows() is the single most expensive
    # row-iteration pattern in pandas (full pd.Series materialized per row).
    expected = out["expected_minutes"].to_numpy(dtype=np.float64)
    half_width = np.maximum(np.maximum(expected - base_lower, base_upper - expected), 1.5)
    half_width = half_width * uncertainty_multipliers(out)
    out["minutes_ci_lower"] = np.round(np.clip(expected - half_width, 0.0, MAX_PLAYER_MINUTES), 2)
    out["minutes_ci_upper"] = np.round(np.clip(expected + half_width, 0.0, MAX_PLAYER_MINUTES), 2)
    return out


def compress_usage_to_roster_budget(
    df: pd.DataFrame, *, pull_strength: float = 0.6
) -> pd.DataFrame:
    """Soft-pull each candidate's usage toward the team's actual open usage at their position.

    `expected_usage` from `predict_minutes_usage` is scored independently per
    (player, school) candidate row — intentional for counterfactual scoring ("what would
    this player's usage look like at this school"), but unconstrained it can put
    starter-level usage on every candidate simultaneously (PR #43's own acknowledged
    follow-up). The fix is anchored per-row to `open_usage_position` (a real
    per-team-per-position open-usage figure from `roster_state_features`), not a sum across
    every candidate row for a school — a school's inference candidate pool is the *entire*
    eligible player population scored against it (thousands of rows), not a short list of
    real competitors, so summing all of them to one ~100 budget crushes nearly every
    candidate toward zero regardless of how good any individual prediction was (confirmed
    on real data: usage_mae roughly tripled in a holdout run before this per-row redesign).
    Only the excess above the position's open-usage figure is pulled down, scaled by
    `pull_strength` and loosened for candidates the model is more confident will actually
    play real minutes here (`rotation_probability_model * expected_minutes_share`) — since
    `open_usage_position` is itself a rough average, not a hard ceiling, a confident
    candidate can still legitimately exceed it. Rows without reliable roster context
    (`roster_reliability < 0.6`, i.e. no roster snapshot joined) are left uncompressed
    rather than crushed toward a meaningless default-zero cap. Raw, uncompressed usage is
    preserved in `expected_usage_raw` so the counterfactual signal isn't lost — only
    `expected_usage` (what gets written to `playing_time_projections` and used downstream
    by `derive_usage_role`/`compute_role_fit_score`) changes.
    """
    if df.empty or "expected_usage" not in df.columns:
        return df
    out = df.copy()
    out["expected_usage_raw"] = out["expected_usage"].astype(float)

    cap = pd.to_numeric(out.get("open_usage_position"), errors="coerce")
    roster_reliable = pd.to_numeric(out.get("roster_reliability"), errors="coerce").fillna(
        0.0
    ) >= 0.6
    has_cap = cap.notna() & roster_reliable

    rotation_prob = pd.to_numeric(
        out.get("rotation_probability_model", 0.5), errors="coerce"
    ).fillna(0.5).clip(0.0, 1.0)
    minutes_share = pd.to_numeric(
        out.get("expected_minutes_share", 0.5), errors="coerce"
    ).fillna(0.5).clip(0.0, 1.0)
    confidence = (rotation_prob * minutes_share).clip(0.0, 1.0)
    effective_pull = pull_strength * (1.0 - 0.5 * confidence)

    excess = (out["expected_usage_raw"] - cap.fillna(0.0)).clip(lower=0.0)
    reduction = np.where(has_cap, excess * effective_pull, 0.0)
    out["expected_usage"] = (out["expected_usage_raw"] - reduction).clip(lower=0.0)
    return out


ROLE_CATEGORIES = (
    "primary_creator",
    "secondary_creator",
    "connector",
    "play_finisher",
    "spacing_specialist",
    "rim_runner_rebounder",
    "defensive_specialist",
    "depth",
)


def derive_usage_roles(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized form of `derive_usage_role` — same role-scoring rules, whole-frame at once.

    `derive_usage_role(row)` stays as a single-row wrapper for tests/callers with one row.
    `ROLE_CATEGORIES`'s order matches the original dict's insertion order exactly —
    `np.argmax` returns the first index on ties, replicating the stability of Python's
    `sorted(role_scores.items(), key=..., reverse=True)` (ties keep their original relative
    order; `reverse=True` only flips the comparison, not tie order).
    """
    n = len(df)
    if n == 0:
        return np.array([], dtype=object), np.array([], dtype=np.float64)

    usage = _numeric_column_or_default(df, "expected_usage", 0.0)
    minutes = _numeric_column_or_default(df, "expected_minutes", 0.0)
    archetype = (
        df.get("archetype_label", pd.Series("", index=df.index))
        .fillna("")
        .astype(str)
        .str.lower()
    )
    skill_percentiles_col = df.get("skill_percentiles", pd.Series(None, index=df.index))
    pct_dicts = [_json_dict(v) for v in skill_percentiles_col]
    shooting_3p = np.array([_as_float(d.get("shooting_3p")) for d in pct_dicts])
    passing_creation = np.array([_as_float(d.get("passing_creation")) for d in pct_dicts])
    steal_disruption = np.array([_as_float(d.get("steal_disruption")) for d in pct_dicts])
    block_rim_protection = np.array([_as_float(d.get("block_rim_protection")) for d in pct_dicts])

    scores = {role: np.zeros(n, dtype=np.float64) for role in ROLE_CATEGORIES}

    scores["primary_creator"] += np.where((usage >= 25) & (minutes >= 24), 3.0, 0.0)
    scores["secondary_creator"] += np.where(usage >= 20, 2.0, 0.0)

    connector_mask = (usage >= 14) & (usage <= 22)
    scores["connector"] += np.where(connector_mask, 1.5, 0.0)
    scores["play_finisher"] += np.where(connector_mask, 1.0, 0.0)

    scores["depth"] += np.where((usage < 15) | (minutes < 10), 2.0, 0.0)

    spacing_mask = (
        archetype.str.contains("shoot").to_numpy()
        | archetype.str.contains("spacing").to_numpy()
        | (shooting_3p >= 65)
    )
    scores["spacing_specialist"] += np.where(spacing_mask, 2.5, 0.0)

    creator_mask = (
        archetype.str.contains("playmaker").to_numpy()
        | archetype.str.contains("creator").to_numpy()
        | (passing_creation >= 65)
    )
    scores["primary_creator"] += np.where(creator_mask, 1.5, 0.0)
    scores["secondary_creator"] += np.where(creator_mask, 1.5, 0.0)

    rim_mask = (
        archetype.str.contains("big").to_numpy()
        | archetype.str.contains("rim").to_numpy()
        | archetype.str.contains("rebound").to_numpy()
    )
    scores["rim_runner_rebounder"] += np.where(rim_mask, 2.5, 0.0)

    defense_mask = (
        archetype.str.contains("def").to_numpy()
        | (steal_disruption >= 70)
        | (block_rim_protection >= 70)
    )
    scores["defensive_specialist"] += np.where(defense_mask, 2.0, 0.0)

    scores["depth"] -= np.where(minutes >= 18, 1.5, 0.0)

    score_matrix = np.column_stack([scores[role] for role in ROLE_CATEGORIES])
    top_idx = np.argmax(score_matrix, axis=1)
    top_role = np.array(ROLE_CATEGORIES, dtype=object)[top_idx]
    sorted_scores = np.sort(score_matrix, axis=1)
    top_score = sorted_scores[:, -1]
    runner_up = sorted_scores[:, -2]

    sample_rel = _numeric_column_or_default(df, "sample_reliability", 0.0)
    confidence_base = 0.45 + 0.12 * np.maximum(top_score - runner_up, 0.0)
    confidence = np.clip(confidence_base + 0.20 * sample_rel, 0.25, 0.95)
    return top_role, confidence


def derive_usage_role(row: pd.Series) -> tuple[str, float]:
    roles, confidences = derive_usage_roles(pd.DataFrame([row]))
    return roles[0], float(confidences[0])


def allocate_displaced_minutes_batch(df: pd.DataFrame) -> list[dict[str, float]]:
    """Vectorized form of `allocate_displaced_minutes` — whole-frame at once.

    Same `pd.Series(r)`-per-row reconstruction cost as `data_quality_flags` had — see that
    function's docstring.
    """
    n = len(df)
    if n == 0:
        return []

    expected = _numeric_column_or_default(df, "expected_minutes", 0.0)
    open_minutes = np.clip(
        _numeric_column_or_default(df, "roster_open_minutes", 0.0),
        0.0,
        STANDARD_TEAM_MINUTES,
    )
    replacement = np.minimum(
        expected, np.where(open_minutes > 0, open_minutes / 5.0, expected * 0.35)
    )
    remaining = np.maximum(expected - replacement, 0.0)
    same_position = remaining * 0.65
    flexible = remaining - same_position

    replacement_r = np.round(replacement, 2)
    same_position_r = np.round(same_position, 2)
    flexible_r = np.round(flexible, 2)
    return [
        {
            "replacement_slot": float(replacement_r[i]),
            "same_position_depth": float(same_position_r[i]),
            "flexible_bench": float(flexible_r[i]),
        }
        for i in range(n)
    ]


def allocate_displaced_minutes(row: pd.Series) -> dict[str, float]:
    return allocate_displaced_minutes_batch(pd.DataFrame([row]))[0]


def starter_probability(minutes: float, ci_lower: float, ci_upper: float) -> float:
    if ci_upper <= ci_lower:
        return 1.0 if minutes >= 24.0 else 0.0
    spread = max((ci_upper - ci_lower) / 2.0, 1.0)
    return clamp(1.0 / (1.0 + np.exp(-(minutes - 24.0) / spread)), 0.0, 1.0)


def rotation_probability(minutes: float, ci_lower: float, ci_upper: float) -> float:
    if ci_upper <= ci_lower:
        return 1.0 if minutes >= 12.0 else 0.0
    spread = max((ci_upper - ci_lower) / 2.0, 1.0)
    return clamp(1.0 / (1.0 + np.exp(-(minutes - 12.0) / spread)), 0.0, 1.0)


def compute_role_fit_scores(df: pd.DataFrame) -> np.ndarray:
    """Vectorized form of `compute_role_fit_score` — same formula, whole-frame at once.

    Was `scored.apply(pt.compute_role_fit_score, axis=1)` in `score_frame()` — pure scalar
    arithmetic per row, so the only cost was the `.apply(axis=1)` row-reconstruction
    overhead itself. `compute_role_fit_score(row)` stays as a single-row wrapper around
    this for tests/callers that genuinely have one row.
    """
    n = len(df)
    if n == 0:
        return np.zeros(0, dtype=np.float64)

    def col(name: str, default) -> np.ndarray:
        return _numeric_column_or_default(df, name, default)

    minutes = col("expected_minutes", 0.0)
    usage = col("expected_usage", 0.0)
    ci_upper = col("minutes_ci_upper", minutes)
    ci_lower = col("minutes_ci_lower", minutes)
    ci_width = ci_upper - ci_lower
    crowding = np.maximum(col("roster_player_count", 12.0) - 12.0, 0.0)
    open_minutes = np.clip(col("roster_open_minutes", 0.0), 0.0, STANDARD_TEAM_MINUTES)
    rotation_prob = np.clip(col("rotation_probability_model", minutes / 28.0), 0.0, 1.0)
    starter_prob = np.clip(col("starter_probability_model", minutes / 32.0), 0.0, 1.0)

    minutes_score = np.clip(minutes / 28.0 * 100.0, 0.0, 100.0)
    usage_score = np.clip(100.0 - np.abs(usage - 20.0) * 2.0, 40.0, 100.0)
    opportunity_score = np.clip((open_minutes / 80.0) * 100.0, 35.0, 100.0)
    role_path_score = np.clip(65.0 * rotation_prob + 35.0 * starter_prob, 0.0, 100.0)
    uncertainty_penalty = np.clip(ci_width / 16.0 * 18.0, 0.0, 25.0)
    crowding_penalty = np.clip(crowding * 1.25, 0.0, 15.0)
    score = (
        0.48 * minutes_score
        + 0.22 * role_path_score
        + 0.18 * usage_score
        + 0.12 * opportunity_score
        - uncertainty_penalty
        - crowding_penalty
    )
    return np.round(np.clip(score, 0.0, 100.0), 2)


def compute_role_fit_score(row: pd.Series) -> float:
    return float(compute_role_fit_scores(pd.DataFrame([row]))[0])


def build_playing_time_records(
    scored_df: pd.DataFrame,
    *,
    model_version: str = MODEL_VERSION,
    expires_days: int = 7,
) -> tuple[list[tuple], list[tuple]]:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=expires_days)
    projection_records: list[tuple] = []
    fit_sync_records: list[tuple] = []

    # Precomputed whole-frame once, outside the loop — neither data_quality_flags nor
    # compute_role_fit_score actually reads usage_role/usage_role_confidence, so (unlike the
    # role/role_conf fallback below, which genuinely needs a per-row value) there was never
    # a reason to reconstruct a pd.Series per row for these. That per-row Series
    # reconstruction (`pd.Series({**r, ...})`) was exactly the apply(axis=1)-shaped cost this
    # vectorization pass targets, just inside an itertuples() loop instead of .apply().
    displaced_batch = allocate_displaced_minutes_batch(scored_df)
    flags_batch = data_quality_flags_batch(scored_df)
    role_fit_batch = compute_role_fit_scores(scored_df)

    for i, row in enumerate(scored_df.itertuples(index=False)):
        r = row._asdict()
        # `score_frame()` (scripts/run_playing_time.py) already computes usage_role/
        # usage_role_confidence/role_fit before calling this function. Reuse those
        # instead of recomputing — same deterministic output, half the row-apply cost.
        if pd.notna(r.get("usage_role")) and pd.notna(r.get("usage_role_confidence")):
            role = r["usage_role"]
            role_conf = float(r["usage_role_confidence"])
        else:
            role, role_conf = derive_usage_role(pd.Series(r))
        displaced = displaced_batch[i]
        sp = (
            clamp(float(r["starter_probability_model"]), 0.0, 1.0)
            if pd.notna(r.get("starter_probability_model"))
            else starter_probability(
                r["expected_minutes"], r["minutes_ci_lower"], r["minutes_ci_upper"]
            )
        )
        rp = (
            clamp(float(r["rotation_probability_model"]), 0.0, 1.0)
            if pd.notna(r.get("rotation_probability_model"))
            else rotation_probability(
                r["expected_minutes"], r["minutes_ci_lower"], r["minutes_ci_upper"]
            )
        )
        flags = flags_batch[i]
        role_fit = float(r["role_fit"]) if pd.notna(r.get("role_fit")) else float(role_fit_batch[i])
        opportunity = {
            "target_season": int(r["season"]),
            "source_stat_season": int(_as_float(r.get("source_stat_season"), r["season"])),
            "roster_context_season": int(_as_float(r.get("roster_context_season"), r["season"])),
            "fit_context_season": int(_as_float(r.get("fit_context_season"), r["season"])),
            "team_context_season": int(_as_float(r.get("team_context_season"), r["season"])),
            "neutral_projection_model_version": r.get("neutral_projection_model_version"),
            "roster_open_minutes": round(_as_float(r.get("roster_open_minutes"), 0.0), 2),
            "returning_minutes_position": round(
                _as_float(r.get("returning_minutes_position"), 0.0), 2
            ),
            "same_position_prior_minutes": round(
                _as_float(r.get("same_position_prior_minutes"), 0.0), 2
            ),
            "position_crowding_ratio": round(_as_float(r.get("position_crowding_ratio"), 0.0), 3),
            "opportunity_to_prior_minutes_ratio": round(
                _as_float(r.get("opportunity_to_prior_minutes_ratio"), 0.0), 3
            ),
            "prior_team_rotation_hhi": round(_as_float(r.get("prior_team_rotation_hhi"), 0.0), 3),
            "prior_team_rotation_players": round(
                _as_float(r.get("prior_team_rotation_players"), 0.0), 1
            ),
            "prior_minutes": round(_as_float(r.get("prior_minutes"), 0.0), 2),
            "sample_reliability": round(_as_float(r.get("sample_reliability"), 0.0), 3),
            "projection_reliability": round(_as_float(r.get("projection_reliability"), 0.0), 3),
            "rotation_probability_model": round(
                _as_float(r.get("rotation_probability_model"), 0.0), 3
            ),
            "starter_probability_model": round(
                _as_float(r.get("starter_probability_model"), 0.0), 3
            ),
            "heavy_minutes_probability": round(
                _as_float(r.get("heavy_minutes_probability"), 0.0), 3
            ),
            "high_usage_probability": round(_as_float(r.get("high_usage_probability"), 0.0), 3),
            "candidate_changes_school": bool(
                _as_float(r.get("candidate_changes_school"), 0.0) >= 0.5
            ),
            "is_portal_candidate": bool(_as_float(r.get("is_portal_candidate"), 0.0) >= 0.5),
            "expected_usage_raw": round(
                _as_float(r.get("expected_usage_raw"), r.get("expected_usage", 0.0)), 2
            ),
        }
        role_breakdown = {
            "projected_minutes": round(float(r["expected_minutes"]), 2),
            "confidence_interval": [
                round(float(r["minutes_ci_lower"]), 2),
                round(float(r["minutes_ci_upper"]), 2),
            ],
            "minutes_ci_lower": round(float(r["minutes_ci_lower"]), 2),
            "minutes_ci_upper": round(float(r["minutes_ci_upper"]), 2),
            "expected_usage": round(float(r["expected_usage"]), 2),
            "usage_role": role,
            "usage_role_confidence": round(role_conf, 3),
            "starter_probability": round(sp, 3),
            "rotation_probability": round(rp, 3),
            "heavy_minutes_probability": round(
                _as_float(r.get("heavy_minutes_probability"), 0.0), 3
            ),
            "high_usage_probability": round(_as_float(r.get("high_usage_probability"), 0.0), 3),
            "depth_chart_position": 1
            if r["expected_minutes"] >= 24
            else 2
            if r["expected_minutes"] >= 16
            else 3,
            "displaced_minutes": displaced,
            "opportunity_drivers": opportunity,
            "data_quality_flags": flags,
        }
        projection_records.append(
            (
                int(r["player_id"]),
                int(r["school_id"]),
                int(r["season"]),
                int(r["roster_snapshot_id"]) if pd.notna(r.get("roster_snapshot_id")) else None,
                round(float(r["expected_minutes"]), 4),
                round(float(r["expected_minutes_share"]), 6),
                round(float(r["minutes_ci_lower"]), 4),
                round(float(r["minutes_ci_upper"]), 4),
                round(float(r["expected_usage"]), 4),
                role,
                round(role_conf, 6),
                round(sp, 6),
                round(rp, 6),
                json.dumps(displaced),
                json.dumps(opportunity),
                json.dumps(flags),
                None,
                role_fit,
                model_version,
                now,
                expires,
            )
        )
        fit_sync_records.append(
            (
                int(r["player_id"]),
                int(r["school_id"]),
                int(r["season"]),
                round(_as_float(r.get("gap_match"), 50.0), 4),
                round(_as_float(r.get("scheme_fit"), 50.0), 4),
                role_fit,
                round(_as_float(r.get("program_fit"), 50.0), 4),
                _as_float(r.get("weight_gap"), 0.20),
                _as_float(r.get("weight_scheme"), 0.30),
                _as_float(r.get("weight_role"), 0.25),
                _as_float(r.get("weight_program"), 0.25),
                json.dumps(_json_dict(r.get("fit_breakdown"))),
                json.dumps(role_breakdown),
                bool(_as_float(r.get("is_portal_candidate"), 0.0) >= 0.5),
                model_version,
                now,
                expires,
            )
        )
    return projection_records, fit_sync_records


def upsert_playing_time_projections(engine, records: Sequence[tuple], page_size: int = 1000) -> int:
    if not records:
        return 0
    raw_conn = _raw_connection_with_timeout(engine)
    try:
        with raw_conn.cursor() as cur:
            execute_values(cur, UPSERT_PLAYING_TIME_SQL, records, page_size=page_size)
        raw_conn.commit()
    finally:
        raw_conn.close()
    return len(records)


def sync_role_fit_scores(engine, records: Sequence[tuple], page_size: int = 1000) -> int:
    if not records:
        return 0
    raw_conn = _raw_connection_with_timeout(engine)
    try:
        with raw_conn.cursor() as cur:
            execute_values(cur, SYNC_ROLE_FIT_SQL, records, page_size=page_size)
        raw_conn.commit()
    finally:
        raw_conn.close()
    return len(records)


def _distribution_total_variation(
    actual: pd.Series, predicted: pd.Series, bins: Sequence[float]
) -> float:
    actual_counts, _ = np.histogram(actual.astype(float), bins=bins)
    predicted_counts, _ = np.histogram(predicted.astype(float), bins=bins)
    if actual_counts.sum() == 0 or predicted_counts.sum() == 0:
        return 0.0
    actual_share = actual_counts / actual_counts.sum()
    predicted_share = predicted_counts / predicted_counts.sum()
    return float(0.5 * np.abs(actual_share - predicted_share).sum())


def _tail_metrics(
    actual: pd.Series, predicted: pd.Series, threshold: float, prefix: str
) -> dict[str, float]:
    actual_tail = actual.astype(float) >= threshold
    predicted_tail = predicted.astype(float) >= threshold
    true_positive = int((actual_tail & predicted_tail).sum())
    predicted_positive = int(predicted_tail.sum())
    actual_positive = int(actual_tail.sum())
    return {
        f"{prefix}_actual_rate": float(actual_tail.mean()),
        f"{prefix}_predicted_rate": float(predicted_tail.mean()),
        f"{prefix}_precision": float(true_positive / predicted_positive)
        if predicted_positive
        else 0.0,
        f"{prefix}_recall": float(true_positive / actual_positive) if actual_positive else 0.0,
    }


def _probability_brier(
    df: pd.DataFrame,
    probability_col: str,
    actual: pd.Series,
    threshold: float,
    metric_name: str,
) -> dict[str, float]:
    if probability_col not in df.columns:
        return {}
    probability = pd.to_numeric(df[probability_col], errors="coerce")
    mask = probability.notna()
    if not mask.any():
        return {}
    target = (actual.loc[mask] >= threshold).astype(float)
    prob = probability.loc[mask].clip(0.0, 1.0)
    return {metric_name: float(np.mean((prob - target) ** 2))}


def validate_predictions(scored_df: pd.DataFrame) -> dict[str, float]:
    labeled = scored_df.dropna(subset=["actual_minutes", "actual_usage_rate"])
    if labeled.empty:
        return {}
    actual_minutes = labeled["actual_minutes"].astype(float)
    pred_minutes = labeled["expected_minutes"].astype(float)
    actual_usage = labeled["actual_usage_rate"].astype(float)
    pred_usage = labeled["expected_usage"].astype(float)
    covered = (actual_minutes >= labeled["minutes_ci_lower"].astype(float)) & (
        actual_minutes <= labeled["minutes_ci_upper"].astype(float)
    )
    interval_width = labeled["minutes_ci_upper"].astype(float) - labeled["minutes_ci_lower"].astype(
        float
    )
    minutes_error = pred_minutes - actual_minutes
    usage_error = pred_usage - actual_usage
    metrics = {
        "minutes_mae": float(mean_absolute_error(actual_minutes, pred_minutes)),
        "minutes_rmse": float(mean_squared_error(actual_minutes, pred_minutes) ** 0.5),
        "minutes_bias": float(minutes_error.mean()),
        "usage_mae": float(mean_absolute_error(actual_usage, pred_usage)),
        "usage_rmse": float(mean_squared_error(actual_usage, pred_usage) ** 0.5),
        "usage_bias": float(usage_error.mean()),
        "interval_coverage": float(covered.mean()),
        "interval_coverage_error_80pct": float(abs(covered.mean() - 0.80)),
        "avg_interval_width": float(interval_width.mean()),
        "median_interval_width": float(interval_width.median()),
        "interval_width_to_mae_ratio": float(
            interval_width.mean() / max(mean_absolute_error(actual_minutes, pred_minutes), 1e-6)
        ),
        "minutes_distribution_tvd": _distribution_total_variation(
            actual_minutes,
            pred_minutes,
            [0, 8, 16, 24, 32, 40.01],
        ),
        "usage_distribution_tvd": _distribution_total_variation(
            actual_usage,
            pred_usage,
            [0, 14, 18, 22, 26, 100],
        ),
        "actual_minutes_mean": float(actual_minutes.mean()),
        "predicted_minutes_mean": float(pred_minutes.mean()),
        "actual_usage_mean": float(actual_usage.mean()),
        "predicted_usage_mean": float(pred_usage.mean()),
        "n_validation_rows": float(len(labeled)),
    }
    metrics.update(_tail_metrics(actual_minutes, pred_minutes, 24.0, "minutes_24_plus"))
    metrics.update(_tail_metrics(actual_minutes, pred_minutes, 32.0, "minutes_32_plus"))
    metrics.update(_tail_metrics(actual_usage, pred_usage, 26.0, "usage_26_plus"))
    metrics.update(
        _probability_brier(
            labeled,
            "rotation_probability_model",
            actual_minutes,
            12.0,
            "rotation_brier_12mpg",
        )
    )
    metrics.update(
        _probability_brier(
            labeled,
            "starter_probability_model",
            actual_minutes,
            24.0,
            "starter_brier_24mpg",
        )
    )
    metrics.update(
        _probability_brier(
            labeled,
            "heavy_minutes_probability",
            actual_minutes,
            32.0,
            "heavy_minutes_brier_32mpg",
        )
    )
    metrics.update(
        _probability_brier(
            labeled,
            "high_usage_probability",
            actual_usage,
            26.0,
            "high_usage_brier_26",
        )
    )
    return metrics
