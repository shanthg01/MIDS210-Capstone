"""Canonical calibration for the four production fit components.

Raw model scores do not share a scale: cosine Scheme Fit is concentrated near
100, reliability-adjusted Gap Match is concentrated near zero, and Role Fit is
an opportunity score.  This module converts each component's within-school
candidate rank to a common, stable interpretation before aggregation.

The calibration job is the final model step.  Model writers keep ownership of
the raw columns; ``scripts/run_fit_calibration.py`` persists the calibrated
columns and canonical ``overall_fit`` afterwards.
"""

from __future__ import annotations

from statistics import NormalDist

import numpy as np
import pandas as pd

MODEL_VERSION = "fit-cal-v1"

COMPONENTS = ("scheme_fit", "gap_match", "role_fit", "team_impact_fit")
DEFAULT_FIT_WEIGHTS: dict[str, float] = {
    "scheme_fit": 0.25,
    "gap_match": 0.30,
    "role_fit": 0.25,
    "team_impact_fit": 0.20,
}

SCORE_CENTER = 50.0
SCORE_SCALE = 20.0
SCORE_FLOOR = 10.0
SCORE_CEILING = 90.0
_NORMAL = NormalDist()


def percentile_to_score(percentile: float) -> float:
    """Map an empirical percentile to the shared 10--90 fit scale.

    The normal-score mapping gives 50 to the median, about 37/63 to the
    quartiles, and about 17/83 to the 5th/95th percentiles.  Clipping avoids
    presenting tiny reference-population differences as literal 0 or 100.
    """
    p = float(np.clip(percentile, 1e-6, 1.0 - 1e-6))
    score = SCORE_CENTER + SCORE_SCALE * _NORMAL.inv_cdf(p)
    return float(np.clip(score, SCORE_FLOOR, SCORE_CEILING))


def calibrate_series(values: pd.Series) -> pd.Series:
    """Calibrate one school's candidate scores using average ranks for ties."""
    numeric = pd.to_numeric(values, errors="coerce")
    valid = numeric.notna()
    out = pd.Series(np.nan, index=values.index, dtype=float)
    if not valid.any():
        return out

    ranked = numeric.loc[valid].rank(method="average")
    percentiles = (ranked - 0.5) / len(ranked)
    out.loc[valid] = percentiles.map(percentile_to_score)
    return out


def calibrate_by_school(
    frame: pd.DataFrame,
    *,
    school_col: str = "school_id",
    components: tuple[str, ...] = COMPONENTS,
) -> pd.DataFrame:
    """Add ``calibrated_<component>`` columns within each destination school."""
    result = frame.copy()
    for component in components:
        result[f"calibrated_{component}"] = result.groupby(school_col, group_keys=False)[
            component
        ].transform(calibrate_series)
    return result


def confidence_adjust(score: pd.Series, confidence: pd.Series) -> pd.Series:
    """Shrink uncertain information toward neutral rather than toward failure."""
    s = pd.to_numeric(score, errors="coerce").fillna(SCORE_CENTER)
    q = pd.to_numeric(confidence, errors="coerce").fillna(0.0).clip(0.0, 1.0)
    return (SCORE_CENTER + q * (s - SCORE_CENTER)).clip(SCORE_FLOOR, SCORE_CEILING)


def canonical_overall(
    frame: pd.DataFrame,
    weights: dict[str, float] | None = None,
) -> pd.Series:
    """Compute the persisted, non-personalized Overall Fit score."""
    weights = weights or DEFAULT_FIT_WEIGHTS
    if set(weights) != set(COMPONENTS):
        raise ValueError(f"Weights must cover exactly {COMPONENTS}")
    if any(weight < 0 for weight in weights.values()):
        raise ValueError("Weights must be non-negative")
    if not np.isclose(sum(weights.values()), 1.0):
        raise ValueError("Weights must sum to 1.0")

    score = sum(frame[component] * weight for component, weight in weights.items())
    return score.clip(0.0, 100.0)


def normalized_weights(weights: dict[str, float]) -> dict[str, float]:
    """Validate and normalize user-entered weights for Personalized Fit."""
    if set(weights) != set(COMPONENTS):
        raise ValueError(f"Weights must cover exactly {COMPONENTS}")
    if any(float(weight) < 0 for weight in weights.values()):
        raise ValueError("Weights must be non-negative")
    total = sum(float(weight) for weight in weights.values())
    if total <= 0:
        raise ValueError("At least one fit weight must be positive")
    return {component: float(weight) / total for component, weight in weights.items()}
