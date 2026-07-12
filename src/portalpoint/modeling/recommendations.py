"""2-Stage Recommendation Engine.

Stage 1 — vectorized rank score → Top-50 candidates.
Stage 2 — re-rank with user preference weights + risk penalty → Top-10 picks.

Availability filtering is owned by the caller (SQL WHERE clause in
run_recommendations.py) — the engine receives a pre-filtered pool.

scheme_fit (Model 3), gap_match (Model 4), and role_fit (playing-time model)
are wired today. Program fit, player projection, team-rating impact, and
confidence-aware risk penalties remain future extensions.
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

__all__ = [
    "calculate_overall_fit",
    "generate_top_50_candidates",
    "refine_to_top_10",
]

DEFAULT_FIT_WEIGHTS: dict = {
    "scheme_fit": 0.30,
    "gap_match":  0.35,
    "role_fit":   0.35,
    # placeholders — uncomment and re-proportion when future signals land:
    # 'program_fit': 0.0,
    # 'player_projection': 0.0, # future (separate from adjusted_projection)
    # 'data_confidence': 0.0,   # future
    # 'team_rating': 0.0,       # future
    # 'risk_tolerance': 0.0,    # future
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
        Current fit columns: ``scheme_fit``, ``gap_match``, ``role_fit``.
        Future columns (when predictions + team_rating_projections tables ready):
        ``player_projection``, ``data_confidence``, ``team_rating_delta``.
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
    # future — extend when predictions + team_rating_projections tables are ready:
    # pool["adjusted_projection"] = pool["player_projection"] * pool["data_confidence"]
    # pool["stage1_rank_score"] = pool["adjusted_projection"] + pool["team_rating_delta"] + (pool["overall_fit"] / 100)

    top50 = (
        pool.sort_values("stage1_rank_score", ascending=False)
        .head(50)
        .reset_index(drop=True)
    )
    logger.info("Stage 1 complete: %d candidates → %d selected", len(pool), len(top50))
    return top50


def refine_to_top_10(
    df_top_50: pd.DataFrame,
    user_preferences: Optional[dict] = None,
    risk_tolerance: str = "medium",
) -> pd.DataFrame:
    """Stage 2: re-rank Top-50 with user preference weights + risk penalty → Top 10.

    Parameters
    ----------
    df_top_50 : pd.DataFrame
        Output of :func:`generate_top_50_candidates`. Required columns:
        any column referenced by user_preferences keys (minus ``_weight`` suffix).
        Current fit columns: ``scheme_fit``, ``gap_match``, ``role_fit``.
        Future columns (when predictions table ready):
        ``adjusted_projection``, ``team_rating_delta``, ``data_confidence``.
    user_preferences : dict, optional
        Mapping of ``<col>_weight`` keys to raw (un-normalised) weights.
        Supported keys: ``scheme_fit_weight``, ``gap_match_weight``,
        ``role_fit_weight``. Missing/future columns are ignored and remaining
        positive weights are normalized.
        Defaults to DEFAULT_FIT_WEIGHTS.
    risk_tolerance : str
        One of ``'low'``, ``'medium'``, ``'high'``.  Controls the confidence
        penalty applied to low-confidence candidates.

    Returns
    -------
    pd.DataFrame
        At most 10 rows sorted descending by ``final_rec_score``.
        Appended columns: ``personalized_fit``, ``confidence_penalty`` (0.0
        until predictions table ready), ``final_rec_score``.
        ``final_rank`` (1-based) is inserted as the first column.

    Raises
    ------
    ValueError
        If *risk_tolerance* is not a recognised value.
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

    risk = _RISK_CONFIG[risk_tolerance]
    confidence_floor: float = risk["confidence_floor"]
    penalty_rate: float = risk["penalty"]

    df = df_top_50.copy()
    df["personalized_fit"] = calculate_overall_fit(df, normalized_weights)
    df["confidence_penalty"] = 0.0
    df["final_rec_score"] = df["personalized_fit"] / 100
    # future — uncomment when predictions table ready:
    # df["confidence_penalty"] = (confidence_floor - df["data_confidence"]).clip(lower=0) * penalty_rate
    # df["final_rec_score"] = df["adjusted_projection"] + df["team_rating_delta"] + (df["personalized_fit"] / 100) - df["confidence_penalty"]

    top10 = (
        df.sort_values("final_rec_score", ascending=False)
        .head(10)
        .reset_index(drop=True)
    )
    top10.insert(0, "final_rank", range(1, len(top10) + 1))

    logger.info("Stage 2 complete: %d candidates → %d selected", len(df), len(top10))
    return top10
