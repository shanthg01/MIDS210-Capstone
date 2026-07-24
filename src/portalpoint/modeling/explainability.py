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


def cosine_contributions(
    left: Sequence[float] | NDArray[np.float64],
    right: Sequence[float] | NDArray[np.float64],
    *,
    output_scale: float = 1.0,
) -> NDArray[np.float64]:
    """Return signed, additive per-dimension cosine contributions.

    The returned vector sums to ``cosine_similarity(left, right)`` multiplied
    by ``output_scale``. A zero-norm input follows scikit-learn's convention
    and returns all-zero contributions; callers own any model-specific
    fallback or score clipping and should expose that as a separate adjustment.
    """
    left_arr = np.asarray(left, dtype=np.float64)
    right_arr = np.asarray(right, dtype=np.float64)
    if left_arr.ndim != 1 or right_arr.ndim != 1:
        raise ValueError("Cosine contribution inputs must be one-dimensional")
    if left_arr.shape != right_arr.shape:
        raise ValueError("Cosine contribution inputs must have the same shape")
    if np.any(~np.isfinite(left_arr)) or np.any(~np.isfinite(right_arr)):
        raise ValueError("Cosine contribution inputs must be finite")
    if not np.isfinite(output_scale):
        raise ValueError("output_scale must be finite")

    denominator = float(np.linalg.norm(left_arr) * np.linalg.norm(right_arr))
    if denominator == 0.0:
        return np.zeros_like(left_arr, dtype=np.float64)
    return left_arr * right_arr / denominator * float(output_scale)


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


def cluster_confidence(
    distances: Sequence[float] | NDArray[np.float64],
    *,
    silhouette: float | None = None,
) -> dict[str, Any]:
    """Explain one K-Means assignment from its centroid distances.

    Confidence is the separation between the nearest and second-nearest
    centroid.  It is intentionally not presented as a calibrated probability.
    A silhouette value can be supplied when the caller has cohort labels.
    """
    values = np.asarray(distances, dtype=np.float64)
    if values.ndim != 1 or values.size < 2:
        raise ValueError("Cluster confidence requires at least two centroid distances")
    if np.any(~np.isfinite(values)) or np.any(values < 0):
        raise ValueError("Centroid distances must be finite and non-negative")

    order = np.argsort(values)
    nearest_index, second_index = int(order[0]), int(order[1])
    nearest, second = float(values[nearest_index]), float(values[second_index])
    # Two zero distances are a perfect tie, not perfect confidence.  Since the
    # distances are sorted and non-negative, a zero second-nearest distance
    # implies the nearest distance is also zero.
    ratio = nearest / second if second > 0 else 1.0
    confidence = float(np.clip(1.0 - ratio, 0.0, 1.0))
    result: dict[str, Any] = {
        "assigned_cluster_id": nearest_index,
        "second_nearest_cluster_id": second_index,
        "distance_to_centroid": round(nearest, 6),
        "distance_to_second_centroid": round(second, 6),
        "distance_ratio": round(ratio, 6),
        "confidence": round(confidence, 6),
        "is_ambiguous": ratio >= 0.8,
    }
    if silhouette is not None and np.isfinite(silhouette):
        result["silhouette"] = round(float(silhouette), 6)
    return result


def kalman_uncertainty_explain(
    posterior_variance: float,
    process_variance: float,
    observation_variance: float,
    *,
    persistence: float | None = None,
) -> dict[str, Any]:
    """Return a compact uncertainty/signal-noise explanation for one skill.

    ``confidence`` compares posterior uncertainty with observation noise. It
    stays in [0, 1] and is a relative precision measure, not an empirical
    coverage probability. ``process_share`` describes how much of Q + R is
    modelled movement rather than observation noise.
    """
    values = np.asarray(
        [posterior_variance, process_variance, observation_variance],
        dtype=np.float64,
    )
    if np.any(~np.isfinite(values)) or np.any(values < 0):
        raise ValueError("Kalman variances must be finite and non-negative")

    posterior, process, observation = map(float, values)
    precision_total = posterior + observation
    confidence = observation / precision_total if precision_total > 0 else 0.0
    noise_total = process + observation
    process_share = process / noise_total if noise_total > 0 else 0.0
    result: dict[str, Any] = {
        "posterior_variance": round(posterior, 6),
        "posterior_std": round(float(np.sqrt(posterior)), 6),
        "process_variance": round(process, 6),
        "observation_variance": round(observation, 6),
        "process_share": round(process_share, 6),
        "observation_noise_share": round(1.0 - process_share, 6),
        "confidence": round(confidence, 6),
        "confidence_label": (
            "high" if confidence >= 0.75 else "medium" if confidence >= 0.5 else "low"
        ),
    }
    if persistence is not None:
        if not np.isfinite(persistence):
            raise ValueError("Kalman persistence must be finite")
        result["persistence"] = round(float(persistence), 6)
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
