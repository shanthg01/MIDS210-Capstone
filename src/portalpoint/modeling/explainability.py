"""Shared, model-agnostic explainability calculations.

Keep this module pure: no database access, MLflow calls, or model-runner side
effects. Model-specific code can use these calculations while retaining
ownership of its persisted/API payload shape.
"""
from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray


def shrinkage_weight(
    prior_weight: float | NDArray[np.float64],
    observed_weight: float | NDArray[np.float64],
) -> float | NDArray[np.float64]:
    """Return the fraction of a shrinkage estimate supplied by observations.

    For ``estimate = prior + w * (observed - prior)``, this returns ``w``:
    ``observed_weight / (prior_weight + observed_weight)``. Both inputs must
    be finite and non-negative. A zero-total row returns zero, meaning there
    is no observed-data contribution.
    """
    prior = np.asarray(prior_weight, dtype=np.float64)
    observed = np.asarray(observed_weight, dtype=np.float64)
    if np.any(~np.isfinite(prior)) or np.any(~np.isfinite(observed)):
        raise ValueError("Shrinkage weights must be finite")
    if np.any(prior < 0) or np.any(observed < 0):
        raise ValueError("Shrinkage weights must be non-negative")

    total = prior + observed
    result = np.divide(
        observed,
        total,
        out=np.zeros_like(total, dtype=np.float64),
        where=total > 0,
    )
    if result.ndim == 0:
        return float(result)
    return result


def tree_shap_explain(
    model: Any,
    X: NDArray[np.float64],
    feature_names: Sequence[str],
    *,
    top_n: int = 5,
    output_scale: float = 1.0,
    intermediate_features: Collection[str] = (),
) -> list[dict[str, Any]]:
    """Return compact, additive TreeSHAP explanations for a regression batch.

    ``output_scale`` converts model-native output into its public unit. Playing
    Time, for example, predicts a 0-1 minutes share and exposes 0-40 minutes.
    Only the largest absolute contributions are persisted, but additivity is
    verified against the complete SHAP vector before it is compacted.
    """
    import shap

    matrix = np.asarray(X, dtype=np.float64)
    if matrix.ndim != 2:
        raise ValueError("TreeSHAP input must be a two-dimensional matrix")
    if matrix.shape[1] != len(feature_names):
        raise ValueError(
            f"TreeSHAP received {matrix.shape[1]} columns but "
            f"{len(feature_names)} feature names"
        )
    if top_n < 1:
        raise ValueError("top_n must be at least 1")
    if not np.isfinite(output_scale):
        raise ValueError("output_scale must be finite")

    explanation = shap.TreeExplainer(model)(matrix, check_additivity=True)
    values = np.asarray(explanation.values, dtype=np.float64)
    base_values = np.asarray(explanation.base_values, dtype=np.float64)
    predictions = np.asarray(model.predict(matrix), dtype=np.float64)
    if values.shape != matrix.shape:
        raise ValueError(
            "tree_shap_explain supports scalar regression outputs only; "
            f"received SHAP values with shape {values.shape}"
        )
    base_values = np.broadcast_to(base_values.reshape(-1), predictions.shape)
    reconstructed = base_values + values.sum(axis=1)
    if not np.allclose(reconstructed, predictions, rtol=1e-5, atol=1e-6):
        max_error = float(np.max(np.abs(reconstructed - predictions)))
        raise ValueError(f"TreeSHAP additivity check failed; max error={max_error:.6g}")

    intermediate = set(intermediate_features)
    scaled_values = values * float(output_scale)
    results: list[dict[str, Any]] = []
    for row_idx in range(matrix.shape[0]):
        order = np.argsort(np.abs(scaled_values[row_idx]))[::-1][:top_n]
        drivers = [
            {
                "feature": str(feature_names[col_idx]),
                "feature_value": round(float(matrix[row_idx, col_idx]), 6),
                "contribution": round(float(scaled_values[row_idx, col_idx]), 6),
                "feature_kind": (
                    "intermediate_probability"
                    if feature_names[col_idx] in intermediate
                    else "raw"
                ),
            }
            for col_idx in order
        ]
        displayed_contribution = sum(driver["contribution"] for driver in drivers)
        total_contribution = float(scaled_values[row_idx].sum())
        results.append(
            {
                "base_value": round(float(base_values[row_idx] * output_scale), 6),
                "raw_model_output": round(float(predictions[row_idx] * output_scale), 6),
                "other_contribution": round(
                    total_contribution - displayed_contribution,
                    6,
                ),
                "drivers": drivers,
            }
        )
    return results
