from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import HistGradientBoostingRegressor

from portalpoint.api.schemas.playing_time import PlayingTimeExplanation
from portalpoint.db.models import PlayingTimeProjection
from portalpoint.modeling import playing_time as pt
from scripts.run_playing_time import explanation_positions, write_shap_summary_artifacts


def _models() -> pt.PlayingTimeModels:
    rng = np.random.default_rng(41)
    train = rng.normal(size=(120, 6))
    minutes_model = HistGradientBoostingRegressor(max_iter=30, random_state=41).fit(
        train, 0.5 + 0.08 * train[:, 0] - 0.03 * train[:, 1] + 0.3 * train[:, 2]
    )
    usage_model = HistGradientBoostingRegressor(max_iter=30, random_state=42).fit(
        train, 20.0 + 2.0 * train[:, 0] + train[:, 1] + 4.0 * train[:, 5]
    )
    return pt.PlayingTimeModels(
        minutes_model=minutes_model,
        usage_model=usage_model,
        lower_model=None,
        upper_model=None,
        feature_medians={"value_per_100": 0.0, "prior_minutes": 0.0},
        train_metrics={},
        minutes_features=("value_per_100", "prior_minutes"),
        usage_features=("value_per_100", "prior_minutes"),
    )


def test_explanation_reconstructs_raw_models_and_tracks_postprocessing() -> None:
    models = _models()
    frame = pd.DataFrame([{"value_per_100": 1.5, "prior_minutes": 18.0}])
    scored = pt.predict_minutes_usage(models, frame)
    scored["expected_usage_raw"] = scored["expected_usage"]
    scored["expected_usage"] = scored["expected_usage"] - 1.25
    explained = pt.attach_playing_time_explanations(models, scored, top_n=3)
    payload = explained.iloc[0]["explanation"]

    minutes = payload["targets"]["expected_minutes"]
    usage = payload["targets"]["expected_usage"]
    assert minutes["raw_model_output"] == pytest.approx(
        scored.iloc[0]["_minutes_model_output_share"] * pt.MAX_PLAYER_MINUTES,
        abs=1e-6,
    )
    assert minutes["final_output"] == pytest.approx(scored.iloc[0]["expected_minutes"])
    assert usage["raw_model_output"] == pytest.approx(scored.iloc[0]["_usage_model_output"])
    assert payload["postprocessing"]["usage_roster_compression"] == pytest.approx(-1.25)
    assert any(
        driver["feature_kind"] == "intermediate_probability"
        for driver in minutes["drivers"] + usage["drivers"]
    )
    assert PlayingTimeExplanation.model_validate(payload).method == "tree_shap"


def test_prediction_and_explanation_share_identical_feature_order() -> None:
    models = _models()
    frame = pd.DataFrame([{"value_per_100": 2.0, "prior_minutes": 16.0}])
    inputs = pt.build_playing_time_inference_inputs(models, frame)
    assert inputs.minutes_feature_names[-4:] == pt.ROLE_PROBABILITY_FEATURES
    assert inputs.usage_feature_names[-4:] == pt.ROLE_PROBABILITY_FEATURES
    assert inputs.x_minutes_augmented.shape[1] == len(inputs.minutes_feature_names)
    assert inputs.x_usage_augmented.shape[1] == len(inputs.usage_feature_names)


def test_inference_input_refactor_preserves_prediction_math() -> None:
    models = _models()
    frame = pd.DataFrame(
        [
            {"value_per_100": 1.0, "prior_minutes": 15.0},
            {"value_per_100": -0.5, "prior_minutes": 8.0},
        ]
    )
    x_minutes = pt.feature_matrix(frame, models.feature_medians, models.minutes_features)
    x_usage = pt.feature_matrix(frame, models.feature_medians, models.usage_features)
    probabilities = pt.role_probability_features(models, x_minutes, x_usage=x_usage)
    expected_minutes_matrix = pt.append_role_probability_features(x_minutes, *probabilities)
    expected_usage_matrix = pt.append_role_probability_features(x_usage, *probabilities)
    expected_minutes = np.clip(models.minutes_model.predict(expected_minutes_matrix), 0, 0.95) * 40
    expected_usage = np.clip(models.usage_model.predict(expected_usage_matrix), 0, 100)

    scored = pt.predict_minutes_usage(models, frame)
    np.testing.assert_allclose(scored["expected_minutes"], expected_minutes)
    np.testing.assert_allclose(scored["expected_usage"], expected_usage)


def test_shap_summary_artifacts_are_written_from_bounded_sample(tmp_path) -> None:
    models = _models()
    frame = pd.DataFrame(
        [
            {"value_per_100": value, "prior_minutes": 12.0 + value}
            for value in np.linspace(-2.0, 2.0, 12)
        ]
    )
    scored = pt.predict_minutes_usage(models, frame)
    paths = write_shap_summary_artifacts(models, scored, tmp_path)
    assert {path.name for path in paths} == {
        "playing_time_expected_minutes_shap_summary.png",
        "playing_time_expected_usage_shap_summary.png",
    }
    assert all(path.stat().st_size > 0 for path in paths)


def test_portal_scope_limits_persisted_explanations() -> None:
    scored = pd.DataFrame({"is_portal_candidate": [1, 0, True, None]})
    np.testing.assert_array_equal(explanation_positions(scored, "portal"), [0, 2])
    np.testing.assert_array_equal(explanation_positions(scored, "all"), [0, 1, 2, 3])


def test_build_records_persists_playing_time_explanation() -> None:
    scored = pd.DataFrame(
        [
            {
                "player_id": 42,
                "school_id": 9_900_302,
                "season": 2027,
                "roster_snapshot_id": None,
                "expected_minutes": 22.0,
                "expected_minutes_share": 0.55,
                "minutes_ci_lower": 17.0,
                "minutes_ci_upper": 28.0,
                "expected_usage": 19.5,
                "usage_role": "connector",
                "usage_role_confidence": 0.7,
                "role_fit": 70.0,
                "explanation": {"version": 1, "method": "tree_shap"},
            }
        ]
    )
    projection_records, _ = pt.build_playing_time_records(scored)
    assert json.loads(projection_records[0][18]) == {
        "version": 1,
        "method": "tree_shap",
    }


def test_build_records_uses_null_when_explanation_is_skipped() -> None:
    scored = pd.DataFrame(
        [
            {
                "player_id": 42,
                "school_id": 9_900_302,
                "season": 2027,
                "roster_snapshot_id": None,
                "expected_minutes": 22.0,
                "expected_minutes_share": 0.55,
                "minutes_ci_lower": 17.0,
                "minutes_ci_upper": 28.0,
                "expected_usage": 19.5,
                "usage_role": "connector",
                "usage_role_confidence": 0.7,
                "role_fit": 70.0,
            }
        ]
    )
    projection_records, _ = pt.build_playing_time_records(scored)
    assert projection_records[0][18] is None


def test_playing_time_persistence_contract_includes_explanation() -> None:
    assert "explanation" in PlayingTimeProjection.__table__.c
    assert "explanation = EXCLUDED.explanation" in pt.UPSERT_PLAYING_TIME_SQL
