"""2-Stage Recommendation Engine.

Stage 1 — vectorized rank score → Top-50 candidates.
Stage 2 — re-rank with user preference weights + risk penalty → Top-10 picks.

Availability filtering is owned by the caller (SQL WHERE clause in
run_recommendations.py) — the engine receives a pre-filtered pool.

scheme_fit (Model 3), gap_match (Model 4), role_fit (playing-time model), and
team_impact_fit (Model 6, Team Rating Projection delta_adj_em, normalized) are
wired today. Program fit is descoped from the active roadmap (2026-07-11) and
is not a planned column here. Player projection and confidence-aware risk
penalties remain future extensions.
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

__all__ = [
    "CANDIDATE_SQL",
    "MODEL_VERSION",
    "apply_user_filters",
    "calculate_overall_fit",
    "fixed_team_impact_preferences",
    "explain_candidate_ranking",
    "generate_top_50_candidates",
    "refine_to_top_10",
    "team_impact_fit",
]

MODEL_VERSION = "rec-v1.2"

# ── SQL: candidate pool ───────────────────────────────────────────────────────
# Availability filtering (is_portal_candidate) is handled here in SQL; ranking
# is Stage 1's job (generate_top_50_candidates(), below).
#
# Season semantics (verified against the live DB 2026-07-11): player_team_fit_scores.season
# carries real scheme_fit/gap_match/role_fit together at season=2027 (role_fit's
# sync_role_fit_scores() upserted directly into the same season Scheme
# Fit/Gap Matching already cover) — same season team_rating_projections uses.
# The join below matches season directly; a `ptf.season + 1` offset
# (destination_projection.py's convention, a *different* pair of tables)
# returns zero rows here.
# Destination-mode player_projections join (issue #61: "include player
# projection data in the dashboard view") — pinned to the known production
# model_version, same convention PLAYING_TIME_MODEL_VERSION uses elsewhere,
# rather than a priority-list/DISTINCT ON (there's exactly one destination
# model in production today; add priority-list resolution here if/when a
# second one exists).
DESTINATION_PROJECTION_MODEL_VERSION = "player-destination-proj-v1"

# Destination rows own dashboard value/minutes; strength/weakness chips come from
# neutral projection explanation.value_drivers (destination explanations are
# context-delta payloads and do not carry value_drivers — see
# destination_projection.build_explanation_payload).
NEUTRAL_PROJECTION_MODEL_PRIORITY = (
    "player-proj-phase2a-fcast-v1",
    "player-projection-phase2a-v2",
    "player-projection-phase2a-v1",
    "player-projection-shrinkage-v2",
)

CANDIDATE_SQL = f"""
SELECT
    ptf.player_id,
    ptf.school_id,
    p.full_name     AS player_name,
    p.position,
    ptf.scheme_fit,
    ptf.gap_match,
    ptf.role_fit,
    ptf.overall_fit,
    ptf.is_portal_candidate,
    trp.delta_adj_em,
    pr.value_per_100,
    pr.projected_minutes,
    pr.projected_usage,
    np.biggest_strength,
    np.biggest_weakness,
    arch.archetype_label,
    origin.origin_conference,
    origin.origin_region,
    stats.usage_rate, stats.fg3_pct, stats.ft_pct, stats.rim_pct, stats.assist_rate,
    stats.tov_pct, stats.off_reb_pct, stats.def_reb_pct, stats.steal_pct,
    stats.block_pct, stats.min_pct
FROM player_team_fit_scores ptf
JOIN players p
    ON p.id = ptf.player_id
LEFT JOIN team_rating_projections trp
    ON trp.player_id  = ptf.player_id
   AND trp.school_id  = ptf.school_id
   AND trp.season     = ptf.season
   AND trp.expires_at > now()
LEFT JOIN player_projections pr
    ON pr.player_id       = ptf.player_id
   AND pr.school_id       = ptf.school_id
   AND pr.season          = ptf.season
   AND pr.projection_mode = 'destination'
   AND pr.model_version   = '{DESTINATION_PROJECTION_MODEL_VERSION}'
LEFT JOIN LATERAL (
    SELECT
        n.explanation->'value_drivers'->'top_positive'->0 AS biggest_strength,
        n.explanation->'value_drivers'->'top_negative'->0 AS biggest_weakness
    FROM player_projections n
    WHERE n.player_id = ptf.player_id
      AND n.school_id IS NULL
      AND n.projection_mode = 'neutral'
      AND n.season = ptf.season
      AND n.model_version IN {NEUTRAL_PROJECTION_MODEL_PRIORITY!r}
      AND n.explanation ? 'value_drivers'
    ORDER BY
      CASE n.model_version
        WHEN 'player-proj-phase2a-fcast-v1' THEN 0
        WHEN 'player-projection-phase2a-v2' THEN 1
        WHEN 'player-projection-phase2a-v1' THEN 2
        ELSE 3
      END
    LIMIT 1
) np ON true
-- Recruiting-filter context (Settings' UserFilters, applied by apply_user_filters()
-- below) — each is the player's most-recent row, not tied to ptf.season, same
-- "latest season" convention players.py's /search and /{{player_id}} routes use.
LEFT JOIN LATERAL (
    SELECT pa2.archetype_label
    FROM player_archetypes pa2
    WHERE pa2.player_id = ptf.player_id
    ORDER BY pa2.season DESC
    LIMIT 1
) arch ON true
LEFT JOIN LATERAL (
    SELECT sch.conference AS origin_conference, sch.region AS origin_region
    FROM player_school_seasons pss
    JOIN schools sch ON sch.id = pss.school_id
    WHERE pss.player_id = ptf.player_id
    ORDER BY pss.season DESC
    LIMIT 1
) origin ON true
LEFT JOIN LATERAL (
    SELECT s.usage_rate, s.fg3_pct, s.ft_pct, s.rim_pct, s.assist_rate, s.tov_pct,
           s.off_reb_pct, s.def_reb_pct, s.steal_pct, s.block_pct, s.min_pct
    FROM player_season_stats s
    WHERE s.player_id = ptf.player_id
    ORDER BY s.season DESC
    LIMIT 1
) stats ON true
WHERE ptf.school_id          = :school_id
  AND ptf.season             = :season
  AND ptf.is_portal_candidate = true
"""

# StatKey values are exactly the player_season_stats column names selected
# above — kept in sync manually, same convention as api/schemas/user.py's
# StatKey docstring already documents for players.py's /search endpoint.
_MIN_STAT_COLUMNS = frozenset({
    "usage_rate", "fg3_pct", "ft_pct", "rim_pct", "assist_rate", "tov_pct",
    "off_reb_pct", "def_reb_pct", "steal_pct", "block_pct", "min_pct",
})


def apply_user_filters(pool: pd.DataFrame, filters: Optional[dict]) -> pd.DataFrame:
    """Restrict the CANDIDATE_SQL pool to a program's saved recruiting filters
    (user_preferences.filters, UserFilters shape) before Stage 1 ranking runs.

    nil_budget_min/max is intentionally not applied — no real NIL data source
    exists yet, same reason Program Fit itself is descoped (see CLAUDE.md).
    Saved but inert, exactly like Program Fit's own placeholder convention.
    """
    if not filters or pool.empty:
        return pool
    out = pool
    if filters.get("positions"):
        out = out[out["position"].isin(filters["positions"])]
    if filters.get("conferences"):
        out = out[out["origin_conference"].isin(filters["conferences"])]
    if filters.get("recruiting_regions"):
        out = out[out["origin_region"].isin(filters["recruiting_regions"])]
    if filters.get("target_archetypes"):
        out = out[out["archetype_label"].isin(filters["target_archetypes"])]
    for threshold in filters.get("min_stats") or []:
        stat = threshold["stat"]
        if stat not in _MIN_STAT_COLUMNS:
            continue
        out = out[out[stat].fillna(float("-inf")) >= threshold["min_value"]]
    return out

# AdjEM points; ~2.5x Team Rating Projection's fold em_rmse of ~1.8-2.0 —
# clip range wide enough that only genuinely extreme deltas saturate 0/100.
DELTA_ADJ_EM_CLIP: float = 5.0

# Neutral score for rows with no matching team_rating_projections row (LEFT
# JOIN miss, or team rating data not yet fresh for the target season) —
# matches the 50.0 "no signal" convention already used for the program_fit
# placeholder elsewhere in this codebase.
TEAM_IMPACT_FIT_NEUTRAL: float = 50.0

DEFAULT_FIT_WEIGHTS: dict = {
    "scheme_fit":      0.25,
    "gap_match":        0.30,
    "role_fit":         0.25,
    "team_impact_fit":  0.20,
    # placeholders — uncomment and re-proportion when future signals land:
    # 'player_projection': 0.0, # future (separate from adjusted_projection)
    # 'data_confidence': 0.0,   # future
    # 'risk_tolerance': 0.0,    # future
}


def team_impact_fit(delta_adj_em: pd.Series) -> pd.Series:
    """Normalize Team Rating Projection's ``delta_adj_em`` to a 0-100 fit column.

    Fixed calibration (not per-pool min-max) so ``0`` delta always maps to the
    same neutral ``50.0`` used elsewhere for "no signal" placeholders, and the
    scale is stable across schools/runs rather than shifting with whatever
    happens to be in a given Top-50 pool. Values are clipped to
    ``±DELTA_ADJ_EM_CLIP`` before rescaling.

    NaN input (e.g. before the caller fills LEFT JOIN misses) propagates as
    NaN. Callers must ``fillna(0.0)`` on the raw ``delta_adj_em`` before
    calling this function, or fill this function's output with
    ``TEAM_IMPACT_FIT_NEUTRAL``. Do not use ``TEAM_IMPACT_FIT_NEUTRAL`` as a
    raw delta: it is an output-scale value and would clip to a score of 100.
    """
    clipped = delta_adj_em.clip(-DELTA_ADJ_EM_CLIP, DELTA_ADJ_EM_CLIP)
    return ((clipped + DELTA_ADJ_EM_CLIP) / (2 * DELTA_ADJ_EM_CLIP)) * 100


def fixed_team_impact_preferences(
    scheme_weight: float,
    gap_weight: float,
    role_weight: float,
) -> dict[str, float]:
    """Reserve the default 20% team-impact share in Stage 2 preferences.

    The three user-controlled weights are relative preferences for the
    remaining 80%. If all three are zero, use the Stage 1 default proportions
    so team impact cannot accidentally become 100% after normalization.
    """
    user_weights = {
        "scheme_fit_weight": float(scheme_weight),
        "gap_match_weight": float(gap_weight),
        "role_fit_weight": float(role_weight),
    }
    if any(weight < 0 for weight in user_weights.values()):
        raise ValueError("Stage 2 preference weights must be non-negative")

    raw_sum = sum(user_weights.values())
    if raw_sum <= 0:
        user_weights = {
            "scheme_fit_weight": DEFAULT_FIT_WEIGHTS["scheme_fit"],
            "gap_match_weight": DEFAULT_FIT_WEIGHTS["gap_match"],
            "role_fit_weight": DEFAULT_FIT_WEIGHTS["role_fit"],
        }
        raw_sum = sum(user_weights.values())

    team_impact_share = DEFAULT_FIT_WEIGHTS["team_impact_fit"]
    scale = (1.0 - team_impact_share) / raw_sum
    return {
        **{col: weight * scale for col, weight in user_weights.items()},
        "team_impact_fit_weight": team_impact_share,
    }

_RISK_CONFIG: dict = {
    "low":    {"confidence_floor": 0.70, "penalty": 2.0},
    "medium": {"confidence_floor": 0.50, "penalty": 1.0},
    "high":   {"confidence_floor": 0.00, "penalty": 0.0},
}


def _normalize_available_weights(df: pd.DataFrame, raw_weights: dict) -> dict:
    """Keep weights for present score columns and normalize them to sum to 1.0."""
    usable_weights: dict[str, float] = {}
    missing_cols: list[str] = []

    for col, raw_value in raw_weights.items():
        weight = float(raw_value)
        if weight < 0:
            raise ValueError(f"Weights must be non-negative; got {col}={weight}")
        if col not in df.columns:
            missing_cols.append(col)
            continue
        if weight > 0:
            usable_weights[col] = weight

    if missing_cols:
        logger.debug("Ignoring preference weights for unavailable columns: %s", missing_cols)

    total = sum(usable_weights.values())
    if total <= 0:
        raise ValueError("At least one positive preference weight must reference an available column")

    return {col: weight / total for col, weight in usable_weights.items()}


def calculate_overall_fit(
    df: pd.DataFrame,
    weights: Optional[dict] = None,
) -> pd.Series:
    """Weighted sum of fit sub-scores.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain every key in *weights* as a column (values in [0, 100]).
    weights : dict, optional
        Mapping of column name → weight. Defaults to DEFAULT_FIT_WEIGHTS.
        Must sum to 1.0 (tolerance 1e-6).

    Returns
    -------
    pd.Series
        Overall fit score in [0, 100], same index as *df*.

    Raises
    ------
    ValueError
        If weights do not sum to 1.0.
    """
    if weights is None:
        weights = DEFAULT_FIT_WEIGHTS

    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"Weights must sum to 1.0; got {total:.8f}")

    score = sum(df[col] * w for col, w in weights.items())
    return score.clip(0, 100)


def generate_top_50_candidates(
    df: pd.DataFrame,
    weights: Optional[dict] = None,
) -> pd.DataFrame:
    """Stage 1: vectorized rank score → Top-50.

    Availability filtering is the caller's responsibility (SQL WHERE clause).
    The engine expects a pre-filtered pool of available players only.

    Parameters
    ----------
    df : pd.DataFrame
        Pre-filtered candidate pool (available players only). Required columns:
        every key in *weights*.
        Current fit columns: ``scheme_fit``, ``gap_match``, ``role_fit``,
        ``team_impact_fit`` (see :func:`team_impact_fit` — caller must already
        have normalized ``delta_adj_em`` and filled missing rows with
        ``TEAM_IMPACT_FIT_NEUTRAL``).
        Future columns (when predictions table is ready): ``player_projection``,
        ``data_confidence``.
    weights : dict, optional
        Fit sub-score weights forwarded to :func:`calculate_overall_fit`.
        Defaults to DEFAULT_FIT_WEIGHTS.

    Returns
    -------
    pd.DataFrame
        At most 50 rows sorted descending by ``stage1_rank_score``, index reset.
        Appended columns: ``overall_fit``, ``stage1_rank_score``.
        Future columns: ``adjusted_projection``.
    """
    pool = df.copy()
    pool["overall_fit"] = calculate_overall_fit(pool, weights)
    pool["stage1_rank_score"] = pool["overall_fit"] / 100
    # future — extend when the predictions table is ready:
    # pool["adjusted_projection"] = pool["player_projection"] * pool["data_confidence"]
    # pool["stage1_rank_score"] = pool["adjusted_projection"] + (pool["overall_fit"] / 100)

    top50 = (
        pool.sort_values("stage1_rank_score", ascending=False)
        .head(50)
        .reset_index(drop=True)
    )
    logger.info("Stage 1 complete: %d candidates → %d selected", len(pool), len(top50))
    return top50


def _rank_stage_2_candidates(
    df_top_50: pd.DataFrame,
    user_preferences: Optional[dict] = None,
    risk_tolerance: str = "medium",
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Score and rank every Stage 2 candidate through the canonical path.

    Keeping the complete ranking separate from the Top-10 truncation lets the
    explanation endpoint report why an otherwise eligible player missed the
    cutoff without reimplementing production ranking math.

    Parameters
    ----------
    df_top_50 : pd.DataFrame
        Output of :func:`generate_top_50_candidates`. Required columns:
        any column referenced by user_preferences keys (minus ``_weight`` suffix).
        Current fit columns: ``scheme_fit``, ``gap_match``, ``role_fit``,
        ``team_impact_fit``.
        Future columns (when predictions table ready):
        ``adjusted_projection``, ``data_confidence``.
    user_preferences : dict, optional
        Mapping of ``<col>_weight`` keys to raw (un-normalised) weights.
        Supported keys: ``scheme_fit_weight``, ``gap_match_weight``,
        ``role_fit_weight``, ``team_impact_fit_weight``. Missing/future columns
        are ignored and remaining positive weights are normalized.
        Defaults to DEFAULT_FIT_WEIGHTS.
    risk_tolerance : str
        One of ``'low'``, ``'medium'``, ``'high'``.  Controls the confidence
        penalty applied to low-confidence candidates.

    Returns the complete ranked frame and the normalized component weights.
    """
    if risk_tolerance not in _RISK_CONFIG:
        raise ValueError(
            f"Unknown risk_tolerance {risk_tolerance!r}; "
            f"expected one of {list(_RISK_CONFIG)}"
        )

    if user_preferences is None:
        user_preferences = {
            f"{col}_weight": weight
            for col, weight in DEFAULT_FIT_WEIGHTS.items()
        }

    # Strip _weight suffix and normalize against the columns available in this pool.
    raw_weights = {
        key.removesuffix("_weight"): val
        for key, val in user_preferences.items()
    }
    normalized_weights = _normalize_available_weights(df_top_50, raw_weights)

    df = df_top_50.copy()
    df["personalized_fit"] = calculate_overall_fit(df, normalized_weights)
    df["confidence_penalty"] = 0.0
    df["final_rec_score"] = df["personalized_fit"] / 100
    # future — uncomment when predictions table ready:
    # risk = _RISK_CONFIG[risk_tolerance]
    # df["confidence_penalty"] = (
    #     risk["confidence_floor"] - df["data_confidence"]
    # ).clip(lower=0) * risk["penalty"]
    # df["final_rec_score"] = (
    #     df["adjusted_projection"]
    #     + (df["personalized_fit"] / 100)
    #     - df["confidence_penalty"]
    # )

    ranked = df.sort_values("final_rec_score", ascending=False).reset_index(drop=True)
    ranked.insert(0, "final_rank", range(1, len(ranked) + 1))
    return ranked, normalized_weights


def refine_to_top_10(
    df_top_50: pd.DataFrame,
    user_preferences: Optional[dict] = None,
    risk_tolerance: str = "medium",
) -> pd.DataFrame:
    """Stage 2: re-rank Top-50 with user preference weights + risk penalty → Top 10.

    The complete ordering is produced by :func:`_rank_stage_2_candidates`,
    which is also used by the ranking explanation path.
    """
    ranked, _ = _rank_stage_2_candidates(
        df_top_50,
        user_preferences=user_preferences,
        risk_tolerance=risk_tolerance,
    )
    top10 = ranked.head(10).copy()

    logger.info("Stage 2 complete: %d candidates → %d selected", len(ranked), len(top10))
    return top10


def explain_candidate_ranking(
    candidate_pool: pd.DataFrame,
    player_id: int,
    *,
    stage1_weights: dict | None = None,
    user_preferences: dict | None = None,
    risk_tolerance: str = "medium",
) -> dict:
    """Explain one candidate's passage through the Top-50/Top-10 funnel."""
    if candidate_pool.empty or player_id not in set(candidate_pool["player_id"].astype(int)):
        return {
            "version": 1,
            "method": "two_stage_rank_funnel",
            "eligible": False,
            "selection_stage": "not_in_eligible_pool",
            "selected": False,
            "reason": "Player is not in the scored, available portal-candidate pool.",
        }

    top50 = generate_top_50_candidates(candidate_pool, weights=stage1_weights)
    pool_scored = candidate_pool.copy()
    pool_scored["overall_fit"] = calculate_overall_fit(pool_scored, stage1_weights)
    pool_scored["stage1_rank_score"] = pool_scored["overall_fit"] / 100
    pool_scored = pool_scored.sort_values("stage1_rank_score", ascending=False).reset_index(drop=True)
    pool_scored["stage1_rank"] = range(1, len(pool_scored) + 1)
    player_stage1 = pool_scored.loc[pool_scored["player_id"].astype(int) == player_id].iloc[0]
    stage1_cutoff = float(pool_scored.iloc[min(49, len(pool_scored) - 1)]["stage1_rank_score"])

    component_weights = stage1_weights or DEFAULT_FIT_WEIGHTS
    weakest_component = min(
        component_weights,
        key=lambda component: float(player_stage1[component]) * float(component_weights[component]),
    )
    base = {
        "version": 1,
        "method": "two_stage_rank_funnel",
        "eligible": True,
        "player_id": int(player_id),
        "risk_tolerance": risk_tolerance,
        "stage1_rank": int(player_stage1["stage1_rank"]),
        "stage1_score": round(float(player_stage1["stage1_rank_score"]), 6),
        "stage1_cutoff": round(stage1_cutoff, 6),
        "stage1_margin": round(float(player_stage1["stage1_rank_score"]) - stage1_cutoff, 6),
        "weakest_component": weakest_component,
        "weakest_component_score": round(float(player_stage1[weakest_component]), 3),
        "components": {
            component: round(float(player_stage1[component]), 3)
            for component in component_weights
        },
        "stage1_weights": {
            component: round(float(weight), 6)
            for component, weight in component_weights.items()
        },
    }
    if int(player_stage1["stage1_rank"]) > 50:
        return {
            **base,
            "selection_stage": "top_50_excluded",
            "selected": False,
            "reason": (
                f"Ranked {int(player_stage1['stage1_rank'])} in Stage 1, "
                f"{abs(base['stage1_margin']):.4f} below the Top-50 cutoff; "
                f"weakest weighted component was {weakest_component}."
            ),
        }

    ranked, normalized = _rank_stage_2_candidates(
        top50,
        user_preferences=user_preferences,
        risk_tolerance=risk_tolerance,
    )
    player_final = ranked.loc[ranked["player_id"].astype(int) == player_id].iloc[0]
    cutoff = float(ranked.iloc[min(9, len(ranked) - 1)]["final_rec_score"])
    final_rank = int(player_final["final_rank"])
    next_margin = None
    if final_rank < len(ranked):
        next_margin = float(player_final["final_rec_score"] - ranked.iloc[final_rank]["final_rec_score"])
    selected = final_rank <= 10
    return {
        **base,
        "selection_stage": "selected" if selected else "top_10_excluded",
        "selected": selected,
        "final_rank": final_rank,
        "personalized_fit": round(float(player_final["personalized_fit"]), 3),
        "confidence_penalty": round(float(player_final["confidence_penalty"]), 6),
        "personalized_weights": {
            component: round(float(weight), 6) for component, weight in normalized.items()
        },
        "final_score": round(float(player_final["final_rec_score"]), 6),
        "top_10_cutoff": round(cutoff, 6),
        "top_10_margin": round(float(player_final["final_rec_score"]) - cutoff, 6),
        "margin_to_next_rank": round(next_margin, 6) if next_margin is not None else None,
        "reason": (
            f"Selected at rank {final_rank}; margin to the next rank is {next_margin:.4f}."
            if selected and next_margin is not None
            else f"Selected at rank {final_rank}."
            if selected
            else f"Ranked {final_rank} after personalization, "
            f"{abs(float(player_final['final_rec_score']) - cutoff):.4f} below the Top-10 cutoff."
        ),
    }
