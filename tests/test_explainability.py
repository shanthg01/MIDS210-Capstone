from __future__ import annotations

import numpy as np
import pytest
from lightgbm import LGBMRegressor
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor

from portalpoint.modeling.explainability import (
    cosine_contributions,
    shrinkage_weight,
    tree_shap_explain,
)


def test_cosine_contributions_are_signed_and_additive() -> None:
    left = np.array([2.0, -1.0, 3.0])
    right = np.array([4.0, 5.0, -2.0])

    contributions = cosine_contributions(left, right, output_scale=100.0)

    expected_score = np.dot(left, right) / (np.linalg.norm(left) * np.linalg.norm(right)) * 100.0
    assert contributions.sum() == pytest.approx(expected_score)
    assert contributions[0] > 0
    assert contributions[1] < 0


def test_cosine_contributions_return_zero_for_zero_norm() -> None:
    contributions = cosine_contributions([0.0, 0.0], [1.0, 2.0], output_scale=100.0)
    np.testing.assert_array_equal(contributions, [0.0, 0.0])


@pytest.mark.parametrize(
    ("left", "right", "message"),
    [
        ([[1.0, 2.0]], [[1.0, 2.0]], "one-dimensional"),
        ([1.0], [1.0, 2.0], "same shape"),
        ([np.nan], [1.0], "finite"),
    ],
)
def test_cosine_contributions_reject_invalid_inputs(left, right, message) -> None:
    with pytest.raises(ValueError, match=message):
        cosine_contributions(left, right)


def test_shrinkage_weight_returns_observed_fraction() -> None:
    assert shrinkage_weight(prior_weight=8.0, observed_weight=2.0) == pytest.approx(0.2)


def test_shrinkage_weight_supports_arrays_and_zero_total() -> None:
    result = shrinkage_weight(
        prior_weight=np.array([8.0, 8.0, 0.0]),
        observed_weight=np.array([2.0, 8.0, 0.0]),
    )
    np.testing.assert_allclose(result, [0.2, 0.5, 0.0])


@pytest.mark.parametrize(
    ("prior_weight", "observed_weight"),
    [(-1.0, 2.0), (1.0, -2.0), (np.nan, 1.0), (1.0, np.inf)],
)
def test_shrinkage_weight_rejects_invalid_inputs(
    prior_weight: float,
    observed_weight: float,
) -> None:
    with pytest.raises(ValueError):
        shrinkage_weight(prior_weight, observed_weight)


def test_tree_shap_explain_is_additive_and_compact() -> None:
    rng = np.random.default_rng(12)
    X = rng.normal(size=(120, 3))
    y = 2.0 * X[:, 0] - X[:, 1] + 0.25 * X[:, 2]
    model = HistGradientBoostingRegressor(max_iter=40, random_state=12).fit(X, y)

    rows = tree_shap_explain(
        model,
        X[:4],
        ["raw_a", "role_probability", "raw_b"],
        top_n=2,
        output_scale=40.0,
        intermediate_features={"role_probability"},
    )

    assert len(rows) == 4
    assert all(len(row["drivers"]) == 2 for row in rows)
    assert rows[0]["raw_model_output"] == pytest.approx(model.predict(X[:1])[0] * 40.0)
    for row in rows:
        displayed = sum(driver["contribution"] for driver in row["drivers"])
        assert row["base_value"] + displayed + row["other_contribution"] == pytest.approx(
            row["raw_model_output"], abs=3e-6
        )
    kinds = {
        driver["feature"]: driver["feature_kind"]
        for row in rows
        for driver in row["drivers"]
    }
    if "role_probability" in kinds:
        assert kinds["role_probability"] == "intermediate_probability"


def test_tree_shap_explain_rejects_feature_name_mismatch() -> None:
    X = np.arange(40, dtype=float).reshape(20, 2)
    model = HistGradientBoostingRegressor(max_iter=5).fit(X, X[:, 0])
    with pytest.raises(ValueError, match="feature names"):
        tree_shap_explain(model, X[:2], ["only_one_name"])


@pytest.mark.parametrize(
    "model",
    [
        ExtraTreesRegressor(n_estimators=20, random_state=9),
        LGBMRegressor(n_estimators=20, random_state=9, verbosity=-1),
    ],
    ids=["extra_trees", "lightgbm"],
)
def test_tree_shap_explain_supports_other_playing_time_families(model) -> None:
    rng = np.random.default_rng(9)
    X = rng.normal(size=(100, 4))
    y = X[:, 0] - 0.5 * X[:, 1]
    model.fit(X, y)
    rows = tree_shap_explain(model, X[:3], ["a", "b", "c", "d"])
    assert len(rows) == 3
    assert rows[0]["raw_model_output"] == pytest.approx(model.predict(X[:1])[0])
